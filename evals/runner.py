"""
L1 eval harness — text in, tool calls out. No audio, no telephony.

Two modes:

  validate  Replays each case's hand-written `calls` against the real tools and
            checks `assert_final`. This proves the LABEL is correct. Run it
            every time cases are added — an unverified golden label poisons the
            whole suite and you won't notice for weeks.

  score     Feeds each `user` turn to a model adapter and compares the tool
            calls it emits against the label. Wire the adapter when the voice
            stack lands; the case format does not change.

Metrics are defined in docs/eval-harness-spec.md. Denominators are explicit on
purpose: "98% modifier accuracy" means nothing without one.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass, field

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lakewood import orders as oe  # noqa: E402

TOOLS = {f.__name__: f for f in oe.TOOLS}


# Error codes that mean a call named an item/topping/size/gourmet number that
# does not exist on the menu — i.e. the label (or, once `score` is wired, the
# model) invented a SKU rather than refusing or asking. Distinct from other
# error codes (BAD_STATE, UNAVAILABLE, etc.) which are real refusals, not
# hallucinations.
_HALLUCINATION_CODES = {"ITEM_NOT_FOUND", "TOPPING_NOT_FOUND",
                        "GOURMET_NOT_FOUND", "SIZE_NOT_FOUND", "NO_MATCH"}


@dataclass
class Result:
    case_id: str
    passed: bool
    detail: str = ""
    tags: list = field(default_factory=list)
    calls_expected: int = 0
    calls_ok: int = 0
    mods_expected: int = 0
    mods_ok: int = 0
    has_assert_final: bool = False
    hallucinated_calls: int = 0     # unexpected NOT_FOUND/NO_MATCH-type errors
    unexpected_error_calls: int = 0  # any status=="error" without expect_error
    forbidden_call_hits: int = 0    # a call used a tool listed in that turn's forbid
    refs_resolved: list = field(default_factory=list)  # "step.field" strings, values omitted
    # T-018: a case that failed because the PROVIDER broke (timeout, HTTP
    # error, malformed response) is an infrastructure failure, never an
    # accuracy result — score()'s summary must count these separately from
    # a completed-but-wrong answer. Always False for validate()/rule_based.
    provider_failed: bool = False


class RefError(Exception):
    """A `$ref` in a case's args could not be resolved. Fails the case — never
    resolves to None or a guessed value."""


def resolve_args(args: dict, results_by_name: dict) -> dict:
    """
    Resolve `{"$ref": "step.field[.nested...]"}` values in a call's `args`
    against structured results captured from earlier calls in the same case.

    `step` is either a prior call's explicit `as:` label or (if unlabeled)
    its tool name — later calls with the same name/label overwrite earlier
    ones, so a `$ref` always sees the most recent matching result at the
    point it is resolved. This is the entire binding language: no arbitrary
    Python eval, no expression syntax beyond dotted-path dict lookup.
    """
    resolved = {}
    for key, val in (args or {}).items():
        if isinstance(val, dict) and set(val.keys()) == {"$ref"}:
            ref = val["$ref"]
            if not isinstance(ref, str) or "." not in ref or ref.startswith(".") \
                    or ref.endswith("."):
                raise RefError(f"malformed $ref {ref!r} — expected 'step.field[.field...]'")
            step, _, path = ref.partition(".")
            if step not in results_by_name:
                raise RefError(
                    f"$ref {ref!r} references step {step!r}, which has not run "
                    f"yet (known steps so far: {sorted(results_by_name)})")
            cur = results_by_name[step]
            walked = step
            for part in path.split("."):
                if not isinstance(cur, dict):
                    raise RefError(
                        f"$ref {ref!r} — {walked!r} is not structured as a dict "
                        f"(got {type(cur).__name__}), cannot look up {part!r}")
                if part not in cur:
                    raise RefError(
                        f"$ref {ref!r} — field {part!r} not found under {walked!r} "
                        f"(available: {sorted(cur)})")
                cur = cur[part]
                walked = f"{walked}.{part}"
            resolved[key] = cur
        else:
            resolved[key] = val
    return resolved


def _load(path_glob: str) -> list[dict]:
    cases = []
    for path in sorted(glob.glob(path_glob)):
        with open(path) as f:
            loaded = yaml.safe_load(f) or []
        for c in loaded:
            c["_source_file"] = os.path.basename(path)  # T-030: catalog display only
        cases.extend(loaded)
    return cases


def _new_session(setup: dict) -> oe.Session:
    s = oe.Session(call_id="EVAL", store_id="STORE-001",
                   from_number="+12035550000")
    oe.UNAVAILABLE.clear()
    oe.UNAVAILABLE.update(setup.get("unavailable", []))
    if setup.get("order_type"):
        oe.set_order_type(s, setup["order_type"])
    return s


def validate(cases: list[dict]) -> list[Result]:
    """Replay labels against the real tools. Catches mislabeled goldens."""
    out = []
    for c in cases:
        s = _new_session(c.get("setup", {}))
        detail, passed = "", True
        n_calls = n_mods = 0
        hallucinated = unexpected_errors = forbidden_hits = 0
        results_by_name: dict = {}
        refs_resolved: list = []

        for turn in c.get("turns", []):
            # T-019/F14: mirrors lakewood/chat.py::run_turn's one-increment-
            # per-customer-utterance semantics, since validate() drives the
            # real tools directly rather than through run_turn. Without this,
            # confirm_order's same-turn check would see turn=0 forever and
            # reject every case that legitimately confirms in a later turn.
            s.turn += 1
            if turn.get("screen"):
                oe.screen_utterance(s, turn["user"])
            turn_tools_called = []
            disambiguation_seen = None

            for call in turn.get("calls", []):
                n_calls += 1
                turn_tools_called.append(call["tool"])
                if call["tool"] == "add_modifier":
                    n_mods += 1
                fn = TOOLS.get(call["tool"])
                if fn is None:
                    passed, detail = False, f"unknown tool {call['tool']}"
                    break
                raw_args = call.get("args", {})
                try:
                    args = resolve_args(raw_args, results_by_name)
                except RefError as e:
                    passed, detail = False, f"{call['tool']}: {e}"
                    break
                refs_resolved += [f"{k}<-{v['$ref']}" for k, v in raw_args.items()
                                  if isinstance(v, dict) and "$ref" in v]
                r = fn(s, **args)
                step_name = call.get("as", call["tool"])
                results_by_name[step_name] = r
                if "needs_disambiguation" in r:
                    disambiguation_seen = r["needs_disambiguation"]
                want_err = call.get("expect_error")
                if want_err:
                    if r.get("code") != want_err:
                        passed, detail = False, \
                            f"{call['tool']} expected {want_err}, got {r.get('code')}"
                elif r.get("status") == "error":
                    passed, detail = False, f"{call['tool']} -> {r.get('code')}"
                    unexpected_errors += 1
                    if r.get("code") in _HALLUCINATION_CODES:
                        hallucinated += 1
                if not passed:
                    break

            # forbid: this turn's own calls must never touch a forbidden tool.
            # Trivially true today by construction (the author writes `calls`
            # directly), but it is real protection against a future edit that
            # accidentally reintroduces a forbidden call (e.g. a premature
            # confirm_order), and is the mechanism `score` mode will reuse to
            # check a MODEL's emitted calls once that's wired.
            forbidden = set(turn.get("forbid", [])) & set(turn_tools_called)
            if forbidden:
                passed, detail = False, f"forbidden tool called: {sorted(forbidden)}"
                forbidden_hits += len(forbidden)

            if "expect_disambiguation" in turn:
                want = turn["expect_disambiguation"]
                if disambiguation_seen is None:
                    passed, detail = False, "expect_disambiguation set but no call returned needs_disambiguation"
                elif disambiguation_seen != want:
                    passed, detail = False, \
                        f"needs_disambiguation {disambiguation_seen} != {want}"

            if not passed:
                break

        has_final = bool(c.get("assert_final"))
        if passed:
            passed, detail = _check_assert_final(s, c.get("assert_final", {}))

        out.append(Result(c["id"], passed, detail, tags=c.get("tags", []),
                          calls_expected=n_calls, calls_ok=n_calls if passed else 0,
                          mods_expected=n_mods, mods_ok=n_mods if passed else 0,
                          has_assert_final=has_final,
                          hallucinated_calls=hallucinated,
                          unexpected_error_calls=unexpected_errors,
                          forbidden_call_hits=forbidden_hits,
                          refs_resolved=refs_resolved))
    return out


def _check_assert_final(s: oe.Session, exp: dict) -> tuple[bool, str]:
    """Shared by validate() and score(): check final state/money against a
    case's `assert_final` block. Checks final CART STATE, not the call
    sequence that produced it — per this file's own guidance, several valid
    tool paths reach the same correct cart."""
    if "state" in exp and s.state != exp["state"]:
        return False, f"state {s.state} != {exp['state']}"
    money_keys = [k for k in ("subtotal", "total", "service_charge") if k in exp]
    if money_keys and exp.get("subtotal") is not None:
        q = s.order.quote()
        for k in money_keys:
            if exp[k] is not None and q[k] != exp[k]:
                return False, f"{k} {q[k]} != {exp[k]}"
    return True, ""


def score(cases: list[dict], make_adapter, trace_log: list | None = None) -> list[Result]:
    """
    Interpreter/model scoring — feeds each case's real `user` turns to an
    adapter and checks the SAME `assert_final` validate() does, so several
    valid tool paths to the same correct cart all count as a pass.

    `make_adapter(session, trace_log=...) -> turn_fn(utterance) -> int` —
    called once per case with that case's fresh Session; `turn_fn` drives
    the session for one utterance via the real tool surface (however the
    adapter chooses — `lakewood.chat.rule_based_adapter` reuses
    `lakewood.chat.run_turn_traced`, the exact code path the interactive
    sandbox and the T-030 live viewer both use) and returns how many tool
    calls it made, for the summary only.

    This measures whatever adapter is passed in — a deterministic rule-based
    interpreter, a real LLM, a stub. The caller is responsible for labeling
    the result accordingly; this function makes no accuracy claim on its
    own. See docs/EVALS.md "score mode" for the caveat this exists to avoid
    people skipping.

    `trace_log` (T-030), if given, is extended in place with one dict per
    case: `{case_id, tags, assert_final, turns, passed, detail,
    provider_failed}`, `turns` being whatever `make_adapter`'s own
    `trace_log` param populated (see `lakewood.chat.run_turn_traced`).
    Purely additive and optional — `None` (the default; every existing
    caller, including the T-016 ratchet gate) costs nothing beyond a single
    `is not None` check per case and produces byte-identical `Result`s
    either way (see `tests/test_trace_capture.py`).
    """
    # Lazy import: score() is the only function that touches a provider, and
    # llm_provider.py is stdlib-only/no project imports, so this costs
    # nothing extra when score() itself isn't called (validate()'s own path
    # never imports this module at all).
    from lakewood.llm_provider import ProviderCallError

    out = []
    for c in cases:
        s = _new_session(c.get("setup", {}))
        turns_trace = [] if trace_log is not None else None
        turn_fn = make_adapter(s, trace_log=turns_trace)
        detail, passed = "", True
        n_calls = 0
        provider_failed = False

        for turn in c.get("turns", []):
            if turn.get("screen"):
                oe.screen_utterance(s, turn["user"])
            try:
                n_calls += turn_fn(turn["user"])
            except ProviderCallError as e:
                passed, detail = False, f"provider failure on {turn['user']!r}: {e}"
                provider_failed = True
                break
            except Exception as e:
                passed, detail = False, f"adapter raised on {turn['user']!r}: {e}"
                break

        has_final = bool(c.get("assert_final"))
        if passed:
            passed, detail = _check_assert_final(s, c.get("assert_final", {}))

        out.append(Result(c["id"], passed, detail, tags=c.get("tags", []),
                          calls_expected=n_calls, calls_ok=n_calls if passed else 0,
                          has_assert_final=has_final, provider_failed=provider_failed))

        if trace_log is not None:
            trace_log.append({
                "case_id": c["id"], "tags": c.get("tags", []),
                "assert_final": c.get("assert_final"),
                "turns": turns_trace, "passed": passed, "detail": detail,
                "provider_failed": provider_failed,
            })
    return out


def _pct(ok: int, of: int) -> str:
    return "n/a" if of == 0 else f"{ok}/{of} ({100*ok/of:.0f}%)"


# T-018 Part 4: tag-bucketed category breakdown for `score` mode, reusing
# the same case tags `validate()`'s buckets already use (see docs/EVALS.md
# "Corpus composition"). A case can land in more than one bucket — these are
# reporting lenses over the same 71 cases, not a partition, exactly like
# validate()'s existing item/mod/correction buckets. Mapped from the real
# tags present in evals/cases/*.yaml today, not invented categories.
_CATEGORY_TAGS = {
    "entity extraction": {"multi_item", "quantity", "size", "specialty",
                          "gourmet", "item_not_found", "gourmet_not_found",
                          "topping_not_found"},
    "modifier scope": {"modifier", "half", "portion", "intensity",
                       "negation", "removal", "lite"},
    "item selection": {"happy", "cheese", "specialty", "gourmet", "86",
                       "asr_collision"},
    "correction handling": {"correction"},
    "ambiguity handling": {"asr_collision"},
    "tool sequencing": {"confirmation", "multi_turn"},
    "state/confirmation integrity": {"confirmation", "stale_quote", "cart_changed"},
    "hallucinated menu items": {"adversarial", "invalid", "item_not_found",
                                "topping_not_found", "gourmet_not_found"},
}


def _print_score_category_breakdown(results: list[Result]) -> None:
    print("\n-- category breakdown (tag-bucketed; a case may fall in more "
          "than one bucket, or none) --")
    for label, tag_set in _CATEGORY_TAGS.items():
        picked = [r for r in results if tag_set & set(r.tags)]
        ok = sum(1 for r in picked if r.passed)
        print(f"  {label + ':':<32} {_pct(ok, len(picked))}")
    final_cases = [r for r in results if r.has_assert_final]
    pricing_ok = sum(1 for r in final_cases if r.passed)
    print(f"  {'pricing (final-cart money check):':<32} {_pct(pricing_ok, len(final_cases))}")


def _percentile(sorted_values: list, pct: float) -> float:
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, int(round(pct / 100 * (len(sorted_values) - 1))))
    return sorted_values[idx]


def _print_score_provider_usage(usage_log: list, call_log: list, provider) -> None:
    print("\n-- provider usage across this run --")
    if usage_log:
        n_req = len(usage_log)
        tin = sum(r.input_tokens for r in usage_log)
        tout = sum(r.output_tokens for r in usage_log)
        lat = sorted(r.latency_seconds for r in usage_log)
        tlat = sum(lat)
        print(f"  requests: {n_req}  prompt tokens: {tin}  completion tokens: {tout}  "
              f"total tokens: {tin + tout}")
        print(f"  latency — total: {tlat:.2f}s  mean: {tlat/n_req:.2f}s  "
              f"median: {statistics.median(lat):.2f}s  p95: {_percentile(lat, 95):.2f}s  "
              f"max: {max(lat):.2f}s")
        # T-018: cost is captured VERBATIM from the provider's own returned
        # usage.cost when present — never computed from a hardcoded rate
        # table (see ProviderResponse.cost_usd docstring, docs/EVALS.md "No
        # cost estimate without a verified applicable rate"). A request
        # whose response omitted the field is counted as "no cost reported,"
        # never silently treated as $0.
        costed = [r.cost_usd for r in usage_log if r.cost_usd is not None]
        if costed:
            missing = n_req - len(costed)
            note = f" ({missing} of {n_req} requests reported no cost field)" if missing else ""
            print(f"  provider-reported cost: ${sum(costed):.6f} across {len(costed)}/{n_req} requests{note}")
        else:
            print(f"  cost: unavailable — {type(provider).__name__}/{provider.model} "
                  f"returned no usage.cost on any request this run")
    else:
        print("  no successful provider responses recorded (every call in "
              "this run failed before returning any usage data)")

    if call_log:
        schema_violations = sum(
            1 for c in call_log if c["result"].get("code") in {"BAD_ARGS", "UNKNOWN_TOOL"})
        hallucinated = sum(
            1 for c in call_log if c["result"].get("code") in _HALLUCINATION_CODES)
        print(f"  schema violations (rejected before domain state): {schema_violations}")
        print(f"  hallucinated-SKU calls: {hallucinated}")
        print(f"  total tool calls executed: {len(call_log)}")


_TRACE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "traces")


def _write_trace_file(trace_log: list, adapter: str, provider) -> str:
    """T-030: writes one JSONL file per `score` run — a real structured
    trace of every case-run, by default, with no separate script. First
    line is a `_meta` record (adapter/model/timestamp); every following
    line is one case's full trace (see `score()`'s docstring for the
    shape). Never writes a credential: `ProviderResponse` (see
    `llm_provider.py`) has no credential field to begin with, and nothing
    here reads the environment. Plain JSONL on purpose — greppable without
    the viewer running, per this task's own constraint.
    """
    os.makedirs(_TRACE_DIR, exist_ok=True)
    ts = time.strftime("%Y%m%dT%H%M%S")
    model = getattr(provider, "model", None)
    path = os.path.join(_TRACE_DIR, f"{ts}_{adapter}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        meta = {"_meta": {"adapter": adapter, "provider": type(provider).__name__
                          if provider else None, "model": model, "timestamp": ts}}
        f.write(json.dumps(meta, default=str) + "\n")
        for entry in trace_log:
            f.write(json.dumps(entry, default=str) + "\n")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="validate",
                    choices=["validate", "score"])
    ap.add_argument("--cases", default=os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "cases", "*.yaml"))
    ap.add_argument("--adapter", default="rule_based",
                    choices=["rule_based", "llm"],
                    help="score mode only. 'rule_based' (default): "
                         "deterministic, no network/credentials, measures "
                         "interpreter coverage, never model accuracy. 'llm': "
                         "real configured provider API calls (requires its API key), "
                         "billed tool rounds per case turn — this is the only "
                         "mode whose result is an actual model-accuracy number.")
    a = ap.parse_args()

    cases = _load(a.cases)
    if a.mode == "score":
        from lakewood.chat import llm_adapter, rule_based_adapter  # keep validate() lean
        call_log: list = []
        usage_log: list = []
        trace_log: list = []
        provider = None
        if a.adapter == "llm":
            from lakewood.llm_provider import make_provider, ProviderConfigError
            try:
                provider = make_provider()
            except ProviderConfigError as e:
                ap.error(str(e))
            adapter_factory = lambda s, trace_log=None: llm_adapter(  # noqa: E731
                s, call_log=call_log, usage_log=usage_log, trace_log=trace_log)
            label = f"REAL {type(provider).__name__} / {provider.model} (first AI baseline, not production accuracy)"
        else:
            adapter_factory = lambda s, trace_log=None: rule_based_adapter(  # noqa: E731
                s, call_log=call_log, trace_log=trace_log)
            label = "rule-based interpreter, NOT a model — measures phrasing coverage only"
        results = score(cases, adapter_factory, trace_log=trace_log)
        bad = [r for r in results if not r.passed]
        provider_failures = [r for r in bad if r.provider_failed]
        for r in bad:
            kind = "PROVIDER-FAIL" if r.provider_failed else "FAIL"
            print(f"  {kind} {r.case_id}: {r.detail}")
        n = len(results)
        print(f"\n[{label}]")
        print(f"{n - len(bad)}/{n} cases reached the labeled final cart state")
        if provider_failures:
            print(f"  of which {len(provider_failures)} case(s) failed on a PROVIDER/"
                  f"infrastructure error, not a wrong answer — not an accuracy result: "
                  f"{[r.case_id for r in provider_failures]}")
        _print_score_category_breakdown(results)
        if a.adapter == "llm":
            _print_score_provider_usage(usage_log, call_log, provider)
        trace_path = _write_trace_file(trace_log, a.adapter, provider)
        print(f"\ntrace: {trace_path}")
        if bad:
            sys.exit(1)
        return

    results = validate(cases)
    bad = [r for r in results if not r.passed]
    for r in bad:
        print(f"  FAIL {r.case_id}: {r.detail}")

    n = len(results)
    calls = sum(r.calls_expected for r in results)
    mods = sum(r.mods_expected for r in results)
    print(f"\n{n - len(bad)}/{n} cases valid "
          f"({calls} tool calls, {mods} modifier calls labeled)")

    def _bucket(tag_set: set) -> tuple[int, int]:
        picked = [r for r in results if tag_set & set(r.tags)]
        return sum(1 for r in picked if r.passed), len(picked)

    item_ok, item_n = _bucket({"multi_item", "quantity", "size", "specialty",
                               "86", "item_not_found", "gourmet_not_found",
                               "happy", "gourmet", "cheese"})
    mod_ok, mod_n = _bucket({"modifier", "half", "portion", "intensity"})
    corr_ok, corr_n = _bucket({"correction"})
    final_cases = [r for r in results if r.has_assert_final]
    final_ok = sum(1 for r in final_cases if r.passed)
    total_hallucinated = sum(r.hallucinated_calls for r in results)
    total_unexpected_err = sum(r.unexpected_error_calls for r in results)
    total_forbidden = sum(r.forbidden_call_hits for r in results)
    total_refs = sum(len(r.refs_resolved) for r in results)

    print("\n-- category correctness (label pass-rate, not model accuracy) --")
    print(f"  item-selection-related cases:   {_pct(item_ok, item_n)}")
    print(f"  modifier-scope-related cases:   {_pct(mod_ok, mod_n)}")
    print(f"  correction-handling cases:      {_pct(corr_ok, corr_n)}")
    print(f"  final-cart exact match:         {_pct(final_ok, len(final_cases))}")
    print(f"  hallucinated-SKU calls:         {total_hallucinated}")
    print(f"  unexpected/premature error calls: {total_unexpected_err} "
          f"(forbidden-tool hits: {total_forbidden})")
    print(f"  runtime $ref values resolved:   {total_refs}")
    for r in results:
        if r.refs_resolved:
            print(f"    {r.case_id}: {', '.join(r.refs_resolved)}")

    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
