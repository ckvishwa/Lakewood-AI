"""
LAKEWOOD TEXT ORDER TEST — headless text sandbox.

Run:  python -m lakewood.chat            (interactive, rule-based, default)
      python -m lakewood.chat --debug    (also prints tool/state/cart traces)
      LAKEWOOD_INTERPRETER=llm python -m lakewood.chat   (configured LLM provider)

Text in -> interpreter -> structured ToolCall(s) -> orders.TOOLS ->
deterministic engine -> structured result -> a short reply built ONLY from
what a tool returned. This module never computes a price, never invents a
menu item, and never mutates `sess.order`/`sess.lines` directly — every
mutation happens inside the real tool functions it calls, whether the
interpreter or this executor is the one that ran them (see
lakewood/interpreter.py::Interpretation for why that split exists).

`LAKEWOOD_INTERPRETER` selects the interpreter: `rule_based` (default,
deterministic, no network/credentials) or `llm` (configured provider call,
requires the selected provider API key). Anything else, or `llm` with no key, exits
with a clear error — never a silent fallback to a different interpreter or
provider.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field

from . import orders as oe
from .interpreter import (
    ChatState, Interpretation, LLMInterpreter, RuleBasedInterpreter,
    TextInterpreter, TOOLS, substitute_last_line,
)
from .llm_provider import ProviderCallError, ProviderConfigError
from .config import CONFIG, store_for_did
from .persistence.memory_repository import InMemorySessionRepository
from .persistence.repository import SessionRepository
from .persistence.service import (
    confirm_and_persist, create_session, resume_or_create, save_progress,
)


def make_interpreter() -> TextInterpreter:
    name = os.environ.get("LAKEWOOD_INTERPRETER", "rule_based")
    if name == "rule_based":
        return RuleBasedInterpreter()
    if name == "llm":
        try:
            return LLMInterpreter()
        except ProviderConfigError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
    print(f"LAKEWOOD_INTERPRETER={name!r} is not implemented in this build. "
          f"Valid values: 'rule_based' (default, no credentials needed) or "
          f"'llm' (set LAKEWOOD_LLM_PROVIDER and its API key).",
          file=sys.stderr)
    sys.exit(1)


def session_snapshot(sess: oe.Session) -> dict:
    """T-030: a point-in-time, JSON-serializable view of session state, for
    tracing/replay only. Read-only — calls `Order.quote()` directly (never
    the `request_quote` TOOL, which is FSM-guarded and would raise BAD_STATE
    outside BUILDING/QUOTED/AWAITING_CONFIRMATION); never mutates anything,
    never gated. Used identically by eval-run trace capture and the live
    viewer (T-030 Part 3) — one snapshot shape, not two.
    """
    q = sess.order.quote()
    return {
        "state": sess.state,
        "subtotal": q["subtotal"],
        "total": q["total"],
        "cart": q["lines"],
        "quote_id": sess.quote_id,
        "unresolved_lookups": list(sess.unresolved_lookups),
        "pending_disambiguations": [
            {"query": e["query"], "ask_count": e["ask_count"],
             "candidates": [h.get("name") or h.get("number") for h in e["candidates"]]}
            for e in sess.pending_disambiguations
        ],
    }


def run_turn_traced(chat: ChatState, interpreter: TextInterpreter, text: str,
                    debug: bool = False) -> tuple[TurnResult, dict]:
    """T-030: the one place a turn's execution and its structured trace are
    produced together. `evals/runner.py`'s trace capture (Part 1) and the
    live viewer (Part 3) both call this — never `run_turn` directly when a
    trace is wanted — so there is exactly one turn-tracing code path, not
    two forked copies of it.
    """
    result = run_turn(chat, interpreter, text, debug=debug)
    trace = {
        "utterance": text,
        "calls": result.calls,
        "reply": result.reply,
        "state_after": session_snapshot(chat.session),
    }
    return result, trace


def rule_based_adapter(session: oe.Session, call_log: list | None = None,
                       trace_log: list | None = None):
    """
    `evals/runner.py::score()` adapter factory: `adapter(session) ->
    turn_fn(utterance) -> int`. Deterministic, no-network, no-credentials —
    measures rule-based interpreter coverage, never model accuracy.

    `call_log`, if given, is extended in place with every executed
    `{"tool","args","result"}` from every turn of this case — T-018's
    schema-violation/hallucinated-SKU accounting reuses this rather than
    adding a second parallel execution path. `trace_log` (T-030), if given,
    is extended in place with one full per-turn trace dict from
    `run_turn_traced`. Both optional and purely additive: existing callers
    passing just `session` (e.g. the T-016 ratchet gate) are unaffected —
    passing neither costs nothing beyond `run_turn`'s own work.
    """
    chat = ChatState(session=session)
    interpreter = RuleBasedInterpreter()

    def _turn(utterance: str) -> int:
        result, trace = run_turn_traced(chat, interpreter, utterance)
        if call_log is not None:
            call_log.extend(result.calls)
        if trace_log is not None:
            trace_log.append(trace)
        return len(result.calls)

    return _turn


def llm_adapter(session: oe.Session, call_log: list | None = None,
                usage_log: list | None = None, trace_log: list | None = None):
    """Real configured provider; propagate infrastructure failures to scoring.

    `call_log` — see `rule_based_adapter`. `usage_log`, if given, is extended
    in place with every `ProviderResponse` from every HTTP round of every
    turn (T-018 Part 2: token/latency/cost accounting) — `LLMInterpreter`
    itself only keeps the LAST turn's responses (`interpret()` resets
    `self.responses` per call), so this adapter is what accumulates them
    across a whole case for the eval-run summary. `trace_log` (T-030) — see
    `rule_based_adapter`; per-turn entries also carry that turn's own
    `provider_responses` (tokens/latency/cost only — never raw content, and
    `ProviderResponse` has no credential field to begin with, so there is
    nothing to redact).
    """
    chat = ChatState(session=session)
    interpreter = LLMInterpreter()

    def _turn(utterance: str) -> int:
        result, trace = run_turn_traced(chat, interpreter, utterance)
        if usage_log is not None:
            usage_log.extend(interpreter.responses)
        if call_log is not None:
            call_log.extend(result.calls)
        if trace_log is not None:
            trace["provider_responses"] = [
                {"input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                 "latency_seconds": r.latency_seconds, "cost_usd": r.cost_usd}
                for r in interpreter.responses
            ]
            trace_log.append(trace)
        if interpreter.last_provider_error:
            raise ProviderCallError(interpreter.last_provider_error)
        return len(result.calls)

    return _turn


def new_session() -> ChatState:
    s = oe.Session(call_id="CHAT", store_id="STORE-001", from_number="+10000000000")
    oe.set_order_type(s, "pickup")
    return ChatState(session=s)


@dataclass
class PersistentChat:
    """Application-layer text call path.  It owns no order logic: it only
    resolves server-bound identity, invokes T-037 orchestration, and saves the
    same Session that ``run_turn`` mutates."""
    repo: SessionRepository
    store_id: str
    call_id: str
    from_number: str
    chat: ChatState | None
    resume_offer: oe.Session | None = None

    def _executor(self, name, args, fn, session):
        if name == "confirm_order":
            # Authorization values remain server-injected; the interpreter schema
            # never exposes either quote_id or this stable call/quote retry key.
            key = args.get("idempotency_key") or f"confirm:{session.call_id}:{args['quote_id']}"
            args["idempotency_key"] = key
            return confirm_and_persist(self.repo, session, args["quote_id"], key)
        result = fn(session, **args)
        if result.get("status") == "ok" and name not in {"search_menu", "check_availability", "get_store_info"}:
            save_progress(self.repo, session)
        return result

    def _state(self, session):
        # Functions are immutable under deepcopy; unlike a bound method, this
        # closure retains the live application/repository when LLMInterpreter
        # deep-copies ChatState for its staged protocol.
        return ChatState(session, tool_executor=lambda name, args, fn, staged: self._executor(name, args, fn, staged))

    @classmethod
    def start(cls, repo: SessionRepository, inbound_did: str, call_id: str,
              from_number: str) -> "PersistentChat":
        store_id = store_for_did(inbound_did)
        session, offer = resume_or_create(repo, store_id, call_id, from_number)
        # An offered cart is deliberately not attached until the caller says so.
        obj = cls(repo, store_id, call_id, from_number, None,
                   session if offer else None)
        if offer is None:
            obj.chat = obj._state(session)
        return obj

    def accept_resume(self) -> None:
        if self.resume_offer is None:
            return
        self.chat = self._state(self.resume_offer)
        save_progress(self.repo, self.chat.session)  # persists stale-quote invalidation
        self.resume_offer = None

    def decline_resume(self) -> None:
        if self.resume_offer is not None:
            self.repo.delete_session(self.store_id, self.resume_offer.call_id)
        self.chat = self._state(create_session(self.store_id, self.call_id, self.from_number))
        self.resume_offer = None

    def run_turn(self, interpreter: TextInterpreter, text: str, debug: bool = False) -> "TurnResult":
        if self.chat is None:
            raise RuntimeError("resume must be accepted or declined before a customer turn")
        return run_turn(self.chat, interpreter, text, debug=debug)


def _format_hit_name(h: dict) -> str:
    if h["kind"] == "gourmet":
        return f"#{h['number']} {h['name']}"
    return h["name"].title()


def _reply_for_search_menu(chat: ChatState, r: dict) -> str:
    hits = r.get("results", [])
    chat.pending_clarification = hits
    names = [_format_hit_name(h) for h in hits[:4]]
    if len(names) == 1:
        return f"Did you mean {names[0]}? What size would you like?"
    return "Did you mean " + ", ".join(names[:-1]) + f", or {names[-1]}?"


def _build_batched_clarification(pending: list[dict]) -> str:
    """T-023/F18: ask about every outstanding disambiguation in one reply,
    not just whichever one happened last. Deliberately batched rather than
    one-at-a-time — the guard holds any unanswered item open and re-asks, so
    a customer answering only one of several loses nothing (see ADR-012)."""
    parts = []
    for entry in pending:
        names = [_format_hit_name(h) for h in entry["candidates"][:4]]
        if len(names) == 1:
            parts.append(f'for "{entry["query"]}", did you mean {names[0]}')
        else:
            parts.append(f'for "{entry["query"]}", '
                         + ", ".join(names[:-1]) + f" or {names[-1]}")
    if len(parts) == 1:
        return parts[0][0].upper() + parts[0][1:] + "?"
    return "Also, " + "; and ".join(parts) + "?"


def _reply_for(tool: str, r: dict) -> str:
    if tool == "request_quote":
        return r.get("readback", f"Your total is ${r.get('total', '?')}.")
    if tool == "confirm_order":
        return f"You're all set — order {r['order_id']}, total ${r['total']}. Thanks!"
    if tool in ("add_item", "add_modifier", "remove_modifier", "update_item"):
        return f"Got it — {r['description'].lower()}. Anything else?"
    if tool == "remove_item":
        return "Removed. Anything else?"
    if tool == "apply_coupon":
        return f"Applied {r['coupon']} — ${r['discount']} off. Anything else?"
    if tool == "begin_confirmation":
        # F16: begin_confirmation always returns a real readback now — this
        # is Defense 3, spoken verbatim so the customer can catch anything
        # the interpreter got wrong or dropped before it's ever confirmed.
        return r.get("readback", "One moment...")
    return "Got it."


@dataclass
class TurnResult:
    reply: str
    calls: list = field(default_factory=list)  # [{"tool", "args", "result"}]


def _apply_completion_guard(chat: ChatState, result: TurnResult) -> TurnResult:
    """T-023/F18: a turn cannot end silently while an outstanding
    disambiguation (F17) remains open — regardless of what else this turn
    did, regardless of which interpreter/provider is driving. The single
    funnel every `run_turn` exit passes through, so it applies identically
    everywhere (see ADR-012). Batches every outstanding item into one
    question (`_build_batched_clarification`) rather than nagging one at a
    time. Capped: `MAX_DISAMBIGUATION_ASKS` unanswered asks on the SAME
    outstanding item transfers to a human rather than looping forever —
    same shape as `Session.note_parse_failure`'s existing cap.
    """
    pending = chat.session.pending_disambiguations
    if not pending:
        return result
    if any(e["ask_count"] >= oe.MAX_DISAMBIGUATION_ASKS for e in pending):
        transfer = oe.transfer_to_human(chat.session, "repeated_misunderstanding")
        chat.session.pending_disambiguations.clear()
        return TurnResult(
            transfer.get("say", "Let me transfer you to someone who can help with that."),
            result.calls)
    question = _build_batched_clarification(pending)
    for entry in pending:
        entry["ask_count"] += 1
    base = result.reply
    if base and base != "Got it.":
        reply = base.rstrip(".!?") + ". " + question
    else:
        reply = question
    return TurnResult(reply, result.calls)


def _finish_turn(chat: ChatState, made: list, debug: bool) -> TurnResult:
    """Shared tail: given the calls a turn actually made (however they were
    executed — see Interpretation's two execution paths), build the reply
    and optional debug trace. `made` entries are `{"tool","args","result"}`.

    T-019/P2 fix: the reply reflects the LAST entry in `made`, never the
    first error or first search_menu-ok encountered. `LLMInterpreter` can
    self-correct across several internal rounds within one turn — an early
    NO_MATCH it fully recovers from is not what actually happened this turn,
    and reporting it as the reply told a correct cart it had failed. Only
    `RuleBasedInterpreter` ever assumed the first entry was also the last;
    it never retries within a turn, so this changes nothing for it.
    """
    if debug:
        for entry in made:
            print(f"[TOOL] {entry['tool']}({entry['args']}) -> {entry['result']}")
        s = chat.session
        print(f"[STATE] {s.state}")
        print(f"[CART]  {[s._describe(l) for l in s.order.lines]}")
        try:
            print(f"[TOTAL] {s.order.total()} cents")
        except Exception:
            pass

    if not made:
        return _apply_completion_guard(chat, TurnResult("Got it.", made))

    tool, r = made[-1]["tool"], made[-1]["result"]
    if not isinstance(r, dict):
        return _apply_completion_guard(chat, TurnResult("Got it.", made))
    if tool == "search_menu" and r.get("status") == "ok":
        return _apply_completion_guard(chat, TurnResult(_reply_for_search_menu(chat, r), made))
    if r.get("status") == "error":
        return _apply_completion_guard(
            chat, TurnResult(r.get("message", "Sorry, that didn't work."), made))
    return _apply_completion_guard(chat, TurnResult(_reply_for(tool, r), made))


def run_turn(chat: ChatState, interpreter: TextInterpreter, text: str,
            debug: bool = False, repository: SessionRepository | None = None) -> TurnResult:
    # T-019/F14: one increment per customer utterance, regardless of how many
    # internal tool-call rounds the interpreter uses to answer it. This is
    # what "same turn" means for the confirmation gate — see orders.py
    # Session.turn. evals/runner.py::validate() mirrors this per labeled
    # case turn, since it drives the real tools directly without run_turn.
    chat.session.turn += 1
    screened = oe.screen_utterance(chat.session, text)
    if screened is not None:
        return TurnResult(screened.get("say", "Let me transfer you."))

    result: Interpretation = interpreter.interpret(chat, text)

    if result.already_executed:
        # The interpreter ran these itself (LLMInterpreter — the Anthropic
        # tool-use protocol needs each call's real result before the turn
        # can complete). Never re-run them.
        return _finish_turn(chat, result.already_executed, debug)

    if not result.calls:
        return _apply_completion_guard(
            chat, TurnResult(result.say or "Sorry, I didn't catch that."))

    made: list = []
    last_new_line_id = None
    for call in result.calls:
        args = substitute_last_line(call.args, last_new_line_id)
        fn = TOOLS.get(call.tool)
        if fn is None:
            return _apply_completion_guard(chat, TurnResult(
                f"(internal error: interpreter named an unknown tool {call.tool!r})", made))
        if chat.tool_executor is not None:
            r = chat.tool_executor(call.tool, args, fn, chat.session)
        elif call.tool == "confirm_order" and repository is not None:
            r = confirm_and_persist(repository, chat.session, args["quote_id"],
                                    args.get("idempotency_key"))
        else:
            r = fn(chat.session, **args)
        made.append({"tool": call.tool, "args": args, "result": r})
        if repository is not None and call.tool != "confirm_order" and r.get("status") == "ok":
            save_progress(repository, chat.session)
        if call.tool == "add_item" and r.get("status") == "ok":
            last_new_line_id = r["line_id"]
            chat.last_line_id = last_new_line_id
        if r.get("status") == "error" or (call.tool == "search_menu" and r.get("status") == "ok"):
            break  # let _finish_turn build the clarification/refusal reply

    return _finish_turn(chat, made, debug)


def main():
    ap = argparse.ArgumentParser(description="Lakewood text order sandbox")
    ap.add_argument("--debug", "-d", action="store_true",
                    help="print [TOOL]/[STATE]/[CART]/[TOTAL] traces")
    args = ap.parse_args()

    interpreter = make_interpreter()
    # The sandbox deliberately uses the zero-dependency repository; production
    # supplies the configured Postgres implementation at this same boundary.
    call = PersistentChat.start(InMemorySessionRepository(), CONFIG.inbound_did,
                                "CHAT", "+10000000000")

    print("LAKEWOOD TEXT ORDER TEST")
    if call.resume_offer is not None:
        print("We still have a previous order. Pick that back up, or start fresh?")
    print("(type 'quit' to exit)\n")
    while True:
        try:
            text = input("You > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in ("quit", "exit"):
            break
        if call.chat is None:
            if text.lower() in ("yes", "yes please", "pick it up"):
                call.accept_resume()
                print("Assistant > Welcome back — your prior cart is ready to review.\n")
            elif text.lower() in ("no", "nope", "start over", "start fresh"):
                call.decline_resume()
                print("Assistant > Sure — starting a new order.\n")
            else:
                print("Assistant > Would you like to resume the prior order or start fresh?\n")
            continue
        result = call.run_turn(interpreter, text, debug=args.debug)
        print(f"Assistant > {result.reply}\n")


if __name__ == "__main__":
    main()
