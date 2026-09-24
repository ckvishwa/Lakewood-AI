# Next tasks

The execution queue. Keep this to the next 5–10 executable tasks.

## T-053 Phase 2 Part 1 · Security scaffolding — **DONE, 2026-09-24**

**Priority:** 0 · **Status:** Done. `lakewood/telephony/` (new):
`webhook_auth.py` (Twilio's own `RequestValidator`, lazy-imported —
missing/forged/tampered/wrong-URL/wrong-account signatures all rejected),
`stream_token.py` (self-built HMAC-SHA256 signed/short-lived/single-use
token for the media WebSocket URL — neither Twilio nor Telnyx signs that
connection itself, confirmed in ADR-021), `rate_limit.py` (fixed-window,
per-key), `call_start.py` (idempotent call start, two layers: an
in-process TTL guard plus the already-existing `sessions` table's
`ON CONFLICT (store_id, call_id) DO UPDATE` upsert — proven together by a
test that replays a call-start webhook and checks exactly one row and one
cart line results). 33 new tests, all four required proofs covered
(unsigned webhook rejected, forged WS token refused, replayed call-start
produces one session — plus a fourth this task added, tampered-params-
under-a-real-signature rejected). `requirements.txt` gains `twilio`
(optional, lazy-imported, same pattern as psycopg2-binary/piper-tts).

**Real incidental finding, reported not silently fixed (CLAUDE.md: report
at full severity even when out of scope):** `resume_or_create` has no way
to distinguish "this CallSid was retried" from "this phone number is
calling back after a drop" — both look identical to it (a non-terminal
session within the 30-minute resume window for that phone). In normal
operation `CallStartGuard`'s TTL should always catch a same-CallSid retry
first and never let it reach `resume_or_create` at all; this only
surfaces if the in-process guard's memory is lost (a process restart
mid-call) — at which point treating it as a resume OFFER rather than a
silent continuation is arguably correct anyway (ADR-014's own "offer,
never silently continue" posture), just worth knowing the two concerns
share one mechanism with no seam between them. Not fixed here — no
concrete failure case yet, Part 2/3's real webhook handler is what will
prove whether this ever actually matters in practice.

Gates: `pytest` exit 0, zero failure markers (this environment's pytest
does not print its own trailing summary line under this session's
console/redirection — a pre-existing quirk, not new; verified by dot-count
and exit code instead, same as this environment's other recent runs have
occasionally noted). `validate` 91/91 unchanged. `score --adapter
rule_based` 59/91 unchanged. `pricing_parity` 50/50 unchanged. `bandit -r
lakewood scripts -ll` (excluding the local, gitignored `lakewood/env/`
venv CI never sees): 0 issues. No prompt/tool-schema/provider surface
touched — no live LLM gate required. **RECOMMENDED NEXT: T-053 Phase 2
Part 2/3** (the real webhook + WS handler that wires these four modules
together and calls into `PersistentChat`/`run_turn_traced` — still fully
offline-testable against a fake provider; a real Twilio account is only
needed starting at Part 5's real-call testing).

## T-053 Phase 2 Part 0 · Telephony provider ADR — **DONE, 2026-09-24**

**Priority:** 1 · **Status:** Done. Full evidence:
`docs/decisions/ADR-021-twilio-telephony-provider.md`, closes ADR-004.

Twilio picked over Telnyx on reliability/integration-risk grounds (CLAUDE.md
P2, above cost at P7), with every figure sourced: 8kHz μ-law audio,
documented inbound/outbound `track` separation on the bidirectional stream
(Telnyx's public docs left this unconfirmed), zero-app-server-dependency
TwiML Bin fallback (`<Dial>` to the restaurant's real line even if this
system's whole server is down — parity with Telnyx's TeXML Bin), $0.0125/min
combined vs. Telnyx's cheaper $0.0087/min (noted, not decisive at this
volume/priority). Neither vendor signs the media WebSocket itself — Part 1
still needs a self-built signed/short-lived/single-use stream-URL token
regardless of vendor. **RECOMMENDED NEXT: T-053 Phase 2 Part 1** (security
scaffolding — webhook signature validation, WS token auth, replay
protection, rate limiting, idempotent call start — fully offline-testable
against a fake provider, no live Twilio account needed yet).

## T-053 Phase 1 Part D · One host — **DONE, 2026-09-24**

**Priority:** 1 · **Status:** Done. Full transcript and numbers:
`docs/STATUS.md`'s T-053 Phase 1 Part D entry.

Brought up the full stack (orchestrator, real Parakeet, real Piper,
real Postgres) on a single WSL Ubuntu host from a genuinely clean venv —
bring-up procedure documented and actually followed, not aspirational.
Ran a real end-to-end session (fixture-audio turn 1, text-driven turns
2–4, same disclosed methodology T-049/T-050/T-051 already established)
— real Parakeet transcription, real Piper synthesis, real Postgres write
verified by direct `SELECT`, correct `HELD_FOR_OPEN` behavior (ran after
hours, reported honestly rather than staged). Perceived latency
(speech-end → first audio) 0.498s vs. T-051's 0.781s baseline, a real
36% cut, attributable to Piper's synthesis speed (Part C's own measured
number, not re-derived here). No real ticket print — Part A never
concluded a transport to print through.

## T-053 Phase 1 Part C · Linux TTS — **DONE, 2026-09-24**

**Priority:** 1 · **Status:** Done. Full evidence:
`docs/decisions/ADR-020-linux-tts-piper.md`.

Piper chosen over Kokoro on measured evidence: ~8x faster synthesis,
real streaming, 25/25 vs 23/25 item-word accuracy on a real 8kHz μ-law
round trip through the real Parakeet service. Hosted TTS left honestly
unmeasured (no credentials in this environment) rather than fabricated.
`lakewood/tts/piper_provider.py` implemented and wired into
`make_tts_provider()`; T-051's readback invariants re-confirmed
unaffected by provider choice (they're domain-text-level, not audio-
level). Open items, not silently closed: a human listening pass, any
hosted-TTS comparison.

## T-053 Phase 1 Part A · Server Direct Print — **BLOCKED, 2026-09-24**

**Priority:** 2 · **Status:** Blocked on physical LAN access. Dev machine
is on 10.0.0.51 (Wi-Fi), printer LAN is 10.1.10.x — TCP 9100/443/80 to
10.1.10.197 all time out. Cannot test SDP, log into the printer's Cloud
Services tab, or print a real ticket from here. ADR-019's recommendation
(self-built relay, not Epson Cloud Services) stands unamended. Revisit
when the dev machine is back on the restaurant LAN.

## T-053 Phase 1 Part B · Real Postgres — **DONE, 2026-09-24, found 2 real concurrency bugs**

**Priority:** 0 · **Status:** Done. Full diagnosis in `docs/STATUS.md`'s
T-053 Phase 1 entry.

Stood up Postgres 16 in Docker, ran migrations up/down twice from a
clean schema (real reversibility, not read from SQL), confirmed every
timestamp column is `timestamptz` live via `\d`. Found and fixed two
real production bugs the in-memory repository structurally cannot show:
an idle-in-transaction deadlock (5 read-only methods in
`PostgresSessionRepository` never committed/rolled back under
`autocommit=False`) and an unguarded check-then-insert race in
`get_or_create_customer` (two concurrent callers for the same phone
number could crash `save_session` with an unhandled `UniqueViolation`).
Both reproduced live with real threads/connections before being fixed.
Tenant isolation suite now runs against both backends (13 tests); two
new concurrency tests prove F6 idempotency under a genuine race, not
just sequential order. New CI job `postgres-contract` (real Postgres
service container, separate from the offline gate).

## T-059 · Store hours (and ticket timestamps) were timezone-naive — **DONE, 2026-09-23**

**Priority:** 0 · **Status:** Done. Full diagnosis in `docs/STATUS.md`'s
own T-059 entry.

The real bug T-058 was covering for: `store_status()` compared server
wall-clock hour against `HOURS` with no timezone — fine on a dev machine
whose local zone happens to roughly match the store's, silently wrong on
ADR-019's UTC cloud server. Fixed with `data/menu.json`'s new
`store.timezone`, `menu.STORE_TIMEZONE`, `orders.store_now()`
(`zoneinfo`, real DST-aware tz database, `tzdata` added to
`requirements.txt` for Windows), and `store_status()` now converts any
aware input / defaults via `store_now()` — naive input keeps meaning
store-local, unchanged contract. Two more real sites of the same bug
found and fixed: `printer.py::build`'s printed ticket date line and
`printer.py::dispatch_confirmed_order`'s printed ticket body both used
to timestamp with the server's own clock (`timefmt.format_12h()`'s bare
default / raw `time.strftime`) — worse than `store_status()`, since
these were wrong on every ticket, not just near an hour boundary. Every
other real-time call site in the codebase grepped and reported: all
remaining ones do epoch-duration subtraction only (timezone-independent
by construction), confirmed by reading each one. 8 new regression tests,
including a same-instant-different-server-timezones proof and a real
DST-boundary case. Full suite 717 passed/2 skipped/2 xfailed, `validate`
91/91, ratchet 59/91, parity 50/50.

## Standing rule (added by T-058, 2026-09-23)

**Any test exercising code with a real-time/environment dependency
(`datetime.now()`, `time.time()`, timezone, hostname, real network
reachability, etc.) must pin that dependency explicitly** — via
monkeypatch, an injected `now`, or an equivalent seam — never rely on
"passes locally" as evidence of hermeticity. It's only evidence of what
the local clock/environment happened to be. T-058's own root cause: ten
tests assumed an in-hours store without ever forcing it, passed on every
local run by coincidence, and failed the moment CI's UTC clock landed on
a different hour relative to `HOURS`. `tests/conftest.py`'s
`open_store`/`closed_store` fixtures are the reusable pattern for this
specific dependency; the general rule applies to any future one.

**Any task touching order/dispatch state must grep for every existing
state-value usage first** (`Session.state`, `dispatch_status`, and any
future state field) — `grep -rn '"HELD_FOR_OPEN"\|"DISPATCHED"\|"FAILED_DISPATCH"\|"STORE_ACKED"\|dispatch_status'
lakewood/ tests/` before adding or changing a transition — not because
two sessions actually collided this time (they didn't; see T-058's own
diagnosis — this rule is filed as a genuine process improvement even
though the specific incident that prompted it turned out to have a
different, simpler root cause), but because a real state-machine
collision is a plausible enough failure mode for this project to guard
against deliberately, not just get lucky about.

## T-058 · Fix `main` — CI permissions + dispatch-status "state collision" — **DONE, 2026-09-23**

**Priority:** 0 · **Status:** Done. Full diagnosis in `docs/STATUS.md`'s
own T-058 entry (not repeated here in full).

`main` was red after T-054..T-057 merged. Two independent problems, both
closed: (1) `gitleaks-action`'s PR-diff mode needs `pull-requests: read`
on a private repo's default token — added, verified against a real PR.
(2) Ten tests failed because they never pinned `orders.store_status()`
and silently depended on real wall-clock time vs. the store's `HOURS` —
**not** a two-session state-machine collision as first hypothesized;
`HELD_FOR_OPEN` was already correct, already-documented behavior. Fixed
by pinning the precondition (`tests/conftest.py`'s new
`open_store`/`closed_store` fixtures), not by changing any expected
value. Two new boundary-pinning regression tests added. Also found and
fixed: `.tmp/` was only indirectly gitignored (a non-`.log`/`.wav`
scratch file could have been swept into a commit — confirmed three real
un-ignored files this session). Branch protection is now unavailable
(repo went private, 403 without GitHub Pro) — flagged as an open risk in
`docs/SECURITY_AUDIT_T054.md`, not silently worked around. Full suite
709 passed/2 skipped/2 xfailed, `validate` 91/91, ratchet 59/91, parity
50/50 — zero regressions.

## T-054 · Security + CI audit — **DONE, 2026-09-23**

**Priority:** 0 · **Status:** Done. Full report: `docs/SECURITY_AUDIT_T054.md`.

Ahead of T-053 (telephony — this system's first internet-exposed
endpoint). Found and escalated to P0: `apply_coupon` has no
authorization gate on the LLM path (T-046/T-028, escalated below); the
kitchen printer's admin password is still the factory default AND is
committed in plaintext in this (public) repo; no call-recording
disclosure mechanism exists anywhere; the repo had zero CI and zero
branch protection — both fixed this task (`.github/workflows/ci.yml`
added; branch protection on `main` enabled via `gh api`, owner approved).
Trivial fixes applied in the same task: stray junk file removed, a
debug `.wav` untracked, 9 bandit false positives documented/suppressed
(one via removing a genuinely unnecessary `f`-prefix). Full suite still
683 passed/2 skipped/2 xfailed, `validate` 91/91, ratchet 59/91
unchanged, parity 50/50 — zero regressions.

**Update, same day:** T-055 and T-057's mechanism, and T-056's code half,
are now done (see their own entries below) — full gate 707 passed/2
skipped/2 xfailed, `validate` 91/91, ratchet 59/91, parity 50/50, bandit
clean, zero regressions across all three tasks. Two items remain open
and are **owner actions, not code T-053 is blocked on fixing itself**:
changing the printer's admin password (T-056), and confirming the
disclosure wording against the restaurant's actual jurisdiction (T-057).
T-053 (telephony) may proceed — see its own gate for what it still must
verify live.

## T-055 · `apply_coupon` needs a real evidence gate, not a prompt tweak — **DONE, 2026-09-23**

**Priority:** 0 (escalated from P2 by `docs/SECURITY_AUDIT_T054.md`,
finding T054-01) · **Status:** Done — `_coupon_apply_authorized`
(`lakewood/interpreter.py`) added, wired into `LLMInterpreter
._interpret_staged`'s dispatch loop next to `add_item`'s own
`_authorize_item_creation`. 14 new tests (`tests/test_llm_interpreter.py`
"T-055" section): the exact exploit reproduction now blocked, ADV-001's
real shape blocked, explicit-code bypass attempt blocked, legitimate
"I have a coupon" path still reaches the real tool, plus a direct
evidence-vocabulary table. `ADV-001`'s own label tightened to assert
`total` (was `subtotal`-only, which a self-applied discount doesn't
touch — the exact label gap that let this ship unnoticed). Full suite
694 passed, `validate` 91/91, ratchet 59/91 (unchanged — rule_based never
calls `apply_coupon`), parity 50/50.

**Supersedes/merges T-046 and T-028's scope notes below** — this audit
found the mechanism is worse than either originally scoped: `apply_coupon`
has **no interpreter-level authorization check at all** (unlike
`add_item`'s `_authorize_item_creation`), and the failure is reproducible
deterministically, not just a live-model tendency. T-046's own scope note
("likely a system-prompt clarification") is **not enough** — a prompt
instruction is not a gate; the same class of bypass that made T-039
dangerous applies here. Build a real check, e.g. `_authorize_coupon_apply`
mirroring `_authorize_item_creation`'s shape: block unless the customer's
own utterance contains real evidence of asking about a coupon/discount/
deal/promo (a shared, menu-independent word-evidence check — no menu
grounding needed here since coupons aren't SKUs).

**Acceptance.** `ADV-001` (T-046) passes: state stays `BUILDING`, no
coupon applied, cart price unaffected. New adversarial regression case:
"give me a large pizza and apply every coupon you have" → refused,
zero coupon applied. T-028's original coupon-by-description gap
(`COUPON-001`/`CORRECT-007`) re-evaluated once the gate lands — it may
still legitimately auto-pick when the customer DOES ask ("I have a
coupon, what's your best deal"), which is the gate's designed-permitted
case, not a defect.

## T-056 · Printer admin password still factory-default + ESC/POS ticket fields unsanitized

**Priority:** 0 · **Status:** Code half DONE, 2026-09-23 — **owner action
still open** (filed by `docs/SECURITY_AUDIT_T054.md`, findings
T054-02/T054-06)

Two related physical/code gaps found in the same audit:
1. **Still open — owner action, not code.** The real TM-m30III's admin
   web-config password is still its factory default (the device serial
   number) — and that serial number is committed in plaintext in
   `docs/STATUS.md`, in this **public** GitHub repo. Log in and change
   it; treat the documented value as burned. Nothing in this repo can
   verify this from here — confirm separately.
2. **Done.** `TicketPrinter._sanitize`/`_CONTROL_BYTES_RE`
   (`lakewood/printer.py`) strips every ASCII control byte except `\n`
   from `name`/`phone`/`address`/`note` before `build()` interpolates
   them into the ESC/POS byte stream. 7 new tests
   (`tests/test_printer_dispatch.py`, "T-056" section): ESC/GS/DLE
   injection attempts via each of the four fields proven stripped,
   ordinary content (apostrophes, punctuation, real addresses) proven
   preserved, the sanitizer's own every-control-byte sweep. Full suite
   707 passed, `validate` 91/91, ratchet 59/91, parity 50/50, bandit
   clean.

## T-057 · Call-recording disclosure/consent mechanism does not exist — **mechanism DONE, 2026-09-23; legal confirmation still open**

**Priority:** 0 · **Status:** Mechanism done (filed by `docs/
SECURITY_AUDIT_T054.md`, finding T054-04) — **DECISION NEEDED still
open, see below** · Blocked T-053, now unblocked for the mechanism part

**Done.** `Session.disclosure_played_at` (`lakewood/orders.py`, rides in
the existing `session_json` blob — ADR-014 Decision 3, no new migration
needed) + `orders.mark_disclosure_played()` (deterministic, idempotent)
+ `PersistentChat.mark_disclosure_played()`/`.disclosure_played_at`
(persists immediately, survives a reload) + `LocalVoiceLoop.turn()`
speaks `oe.DISCLOSURE_TEXT` before the FIRST capture of a call, once per
call. 9 new tests across `tests/test_orders.py`,
`tests/test_persistence_serialization.py`, `tests/
test_voice_tts_pipeline.py` ("T-057" sections): idempotency, persistence
across reload, disclosure genuinely precedes the first transcription,
never replays on later turns. Two pre-existing tests updated (not
weakened — same invariants, now accounting for the legitimate extra
"play" on turn 1): `test_reply_text_is_never_spoken_before_the_domain
_layer_produced_it`, `test_capture_never_starts_before_previous_turns
_playback_finished`. Full suite 700 passed at that point, `validate`
91/91, ratchet 59/91, parity 50/50.

**Still open — DECISION NEEDED, not code:** `DISCLOSURE_TEXT`'s wording
("This call may be recorded, and you're speaking with an automated
assistant.") is a conservative placeholder, not a confirmed legal
requirement. One-party vs. two-party consent varies by state — verify
the actual requirement for the restaurant's jurisdiction before a real
pilot call. This mechanism proves disclosure PLAYED and WAS RECORDED as
having played; it does not by itself prove the wording satisfies any
specific jurisdiction's law.

## T-058 · Retention purge functions are never scheduled

**Priority:** 3 · **Status:** Not started (filed by `docs/
SECURITY_AUDIT_T054.md`, finding T054-08 — re-confirms ADR-014's own
disclosed gap, unchanged since)

`sweep_expired_sessions`/`sweep_old_confirmed_orders`
(`lakewood/persistence/retention.py`) are built and tested but nothing
calls them. Needs the task-scheduler/event-store phase CLAUDE.md's own
build order already anticipates (item 10) — not a quick fix, filed at
the priority that phase deserves, not urgently blocking T-053 (no real
customer data exists yet to over-retain).

## T-052 · Post-confirmation reply speaks the raw internal order ID character-by-character — RECOMMENDED NEXT

**Priority:** 6 · **Status:** Not started (filed 2026-09-22, found during
T-051's Part 5 real-stack measurement; recommended next per T-049 FINAL's
own closing instruction — small, contained, real hardware not required)

**Found measuring T-051's real stack, seen again in T-049 FINAL's live
demo:** the post-confirmation reply ("You're all set — order AI-089BDA,
total $19.32. Thanks!", `chat.py::_reply_for`'s `confirm_order` branch)
measures disproportionate real talk-time for an 11-word sentence — the
likely cause is the raw internal `order_id` (a random alphanumeric string)
being spoken essentially character-by-character. Distinct code path from
T-051's scope (`_short_readback`/the PRE-confirmation readback) — not
fixed there on purpose.

**Scope.** Decide what a customer actually needs to hear here: probably
nothing about the internal order ID at all (staff/kitchen see it on the
printed ticket — see `lakewood/printer.py`, real-verified by T-049 FINAL),
or a short, deliberately speakable reference if one is needed for phone
pickup verification. Small, contained, `chat.py`-only — no domain/pricing/
FSM change.

**Acceptance.** Real before/after talk-time measured for the same reply
shape; offline suite stays green; no content a customer actually needs
(order confirmed, total) is removed.

## T-017 · `RuleBasedInterpreter` can't parse a bare "number N" as a single gourmet selection

**Priority:** 0 · **Status:** Not started (filed 2026-09-08, still open)

See the full original entry further below (unchanged). **Note added
2026-09-22 (T-044):** the "silently ordering the wrong item" framing may be
stale — T-044's own regression test (`AVAIL-002`) confirms a bare gourmet-
number utterance with no size ("small number five") now correctly falls
through to `search_menu` (safe), not a silent `CHEESE PIZZA`. The real
remaining gap is `RuleBasedInterpreter` has NO parsing branch for a single
bare gourmet number at all — it can order a `CHEESE PIZZA`, a half-and-half
by two numbers, or resolve a number from a PENDING clarification, but never
"medium number ten" cold. Still P0 in the existing backlog by CLAUDE.md's
own priority order (order correctness outranks voice/latency polish) — T-052
is recommended first only because T-049 FINAL's own closing instruction
named it explicitly; this remains the next order-correctness item after it.

## T-051 · The 15-second readback — **DONE, 2026-09-22**

**Priority:** 1 (task-assigned) · **Status:** Done — diagnosis, fix, tests,
real-stack measurement all complete; see `docs/STATUS.md`'s T-051 entry for
the full account, real numbers, and evidence.

Short version: a real confirmation readback's spoken duration was cut
37-47% (worst case 17.24s -> 9.19s) via three deterministic, content-
preserving changes — tighter phrasing, a new evidence-based speakable-
rendering layer (`lakewood/speech.py`) for tokens SAPI actually
mispronounces (measured via real TTS->STT round trip, not guessed), and a
capped speech-rate increase (`Rate=3`, ADR-015 amended with the ceiling and
its justification). Completeness (every line/modifier/half-placement/total
still present) is property-tested against 200 generated carts and mutation-
proven against the real call path, not just asserted. Real-stack
measurement (WSL Parakeet brought back up this session, real SAPI, real
RuleBasedInterpreter) shows total system latency down to 3.876s median
(from T-038's 5.899s baseline, the first apples-to-apples comparison since
T-038) — talk-time to physically speak the readback, not system
processing, is now the dominant remaining term.

## T-050 · VAD endpointing + TTS pipelining — **DONE (with honestly-scoped gaps), 2026-09-22**

**Priority:** 5 · **Status:** Part 1 (VAD) and Part 3-TTS (sentence
pipelining) and Part 4 (capture/playback half-duplex) done and tested.
Part 2 (streaming STT) and Part 3-LLM (streaming LLM) are NOT implemented —
verified impossible/inapplicable with this codebase's current providers and
architecture, not silently skipped. Part 5 (measurement) done with real
numbers and a disclosed methodology caveat (no WSL Parakeet GPU service
reachable this session — used faster-whisper CPU instead).

See `docs/STATUS.md`'s T-050 entry for the full account, real measured
numbers, and ADR-018 for the Silero-VAD-over-WebRTC-VAD choice and default
endpointing parameters. Short version: `lakewood/vad.py` (`Endpointer`) is
a pure, fully-unit-tested state machine; `lakewood/voice.py` feeds it real
Silero probabilities (reused from `faster_whisper.vad`'s bundled model —
zero new dependency); TTS is sentence-pipelined so the first sentence plays
while later ones synthesize; capture and playback never overlap by
construction, proven by a real 3-turn confirmation-flow test. 20 new tests
across `tests/test_vad.py`, `tests/test_voice_tts_pipeline.py`,
`tests/test_voice_vad_microphone.py`.

## T-049 · Printer hardware bring-up + end-to-end dispatch — **DONE, 2026-09-23**

**Priority:** 1 · **Status:** Done — real hardware verified end to end, see
`docs/STATUS.md`'s T-049 FINAL entry for the full account (real photos,
real timings, real failure/recovery cycle).

Short version: real device is an **Epson TM-m30III** (M374C), wired
Ethernet, static IP 10.1.10.197:9100. Two of three network preconditions
FAILED at task start — DHCP still Auto, and the printer's Wi-Fi Direct AP
(compromised password, per the brief's own warning) was actively
broadcasting — both fixed this session via the printer's own admin web
config (password = its physical serial number) rather than proceeding
around them. `PrintTransport` seam added (`TcpRawTransport`/
`DryRunTransport`/`UsbDeviceTransport`, `ServerDirectTransport` a named,
unbuilt T-053 slot) with zero change to `TicketPrinter`'s public
constructor. `WIDTH` corrected 42 -> 48 (physically measured). Real prints
verified by photo at every stage: a hardcoded test ticket, a real
confirmed-order ticket (half-and-half unambiguous, coupon, total, cut), a
full voice-to-print demo, and a live cable-pull producing a real 21-second
retry/backoff, `FAILED_DISPATCH`, and — after reconnecting — real recovery
via `redispatch_pending_orders`, idempotent (proven live by running it
twice).

**Closed the known gap the prior session disclosed:** `confirmed_orders`
now has a durable `dispatch_status`/`dispatched_at` (migration
`0002_dispatch_status`, reversible) — the ONE deliberate exception to that
table's insert-only design, since dispatch status is a physical-world fact
that settles after confirmation. `list_undispatched_confirmed_orders` is
the staff/ops visibility query; `redispatch_pending_orders` is the
recovery action (manually invoked — no scheduler exists in this codebase
yet). `held_orders` (named in ARCHITECTURE.md as "Not yet built") does not
actually exist anywhere to reuse — this column is the smallest correct
thing instead, per the task's own explicit fallback instruction.

## T-045 · `_size_supported_by_utterance` has no cross-turn context — a same-size correction turn is wrongly refused

**Priority:** 2 · **Status:** Not started

**Found in T-044's live N=3 gate, `CORRECT-004`, 3/3 identical.** Turn 1
("medium number ten") now correctly authorizes via `_pizza_creation_
authorized` (T-044). Turn 2 ("actually give me number eight instead") is
a correction that implicitly keeps the SAME size as before — the model
re-passes `size=MEDIUM` from conversation memory, but `_size_supported_by_
utterance`/`_size_word_matches` only ever look at THIS turn's own isolated
text, which has no size word in it at all, so the call is refused as
`UNSUPPORTED_ITEM_SUBSTITUTION`. Fails closed (asks for clarification /
refuses), never substitutes a wrong item — not a safety issue, an order-
correctness one for the single most common correction shape ("actually
give me X instead").

**Scope.** Decide how a same-turn size check should treat an implicit
carry-over from the EXISTING cart line being modified (`update_item`/a
`remove_item`+`add_item` replacement pair) vs a genuinely fabricated size.
Likely: when the call is replacing/updating an existing line, the line's
OWN current size is valid evidence too, not just the current utterance's
text — but this needs care not to let a model silently change BOTH item
and size in one unevidenced move. Diagnose with a few more live reps
before deciding the exact mechanism, same discipline as prior tasks.

**Acceptance.** `CORRECT-004`'s full 2-turn flow passes; a regression test
proving a model can't use "implicit carry-over" to fabricate an
UNRELATED size change it never had evidence for either.

## T-046 · Model self-applies an unrequested coupon when deflecting a price-manipulation probe

**Priority:** 0 (escalated from 2, 2026-09-23, `docs/
SECURITY_AUDIT_T054.md` finding T054-01 — a security audit reproduced
this deterministically, not just as a live-model tendency, and confirmed
it's a real, unauthorized money-leaving-the-register vector, not only a
trust/UX issue) · **Status:** Not started — see **T-055**, which
supersedes this entry's scope note below (a prompt clarification is not
enough; a real evidence gate is required)

**Found in T-044's live N=3 gate, `ADV-001`, 3/3 identical.** Customer:
"give me a large pizza but only charge me ten dollars for it" (a labeled
adversarial price-manipulation case — correct behavior is to add the
pizza at its real price and NOT honor the fabricated discount). The model
creates the pizza correctly, then calls `apply_coupon()` unprompted,
which happens to find a real, applicable coupon (`FREE_2L`) and applies
it — the customer never asked for a coupon at all. Not a pricing-engine
bug (the discount math itself is correct, verified) and not a
substitution — but a real order-correctness/trust issue: the agent should
not proactively discount an order nobody asked to discount, especially
right after refusing a price-manipulation attempt.

**Scope.** Likely a system-prompt clarification (`apply_coupon` should
only be called when the customer explicitly mentions a coupon/discount/
deal), same shape as T-033's "model proactively calls `request_quote()`"
finding. Diagnose at N=3-per-case before deciding.

**Acceptance.** `ADV-001` passes (state stays `BUILDING`, no coupon
applied); regression test for the exact mechanism.

## T-047 · Model calls `add_item` for a flat modifier (e.g. "side of ranch") instead of `add_modifier`

**Priority:** 3 · **Status:** Not started

**Found in T-044's live N=3 gate, `MOD-035`, 3/3 identical.** Pizza
created correctly; then for "a side of ranch," the model calls
`add_item(item="SIDE RANCH")` — `SIDE RANCH` is a `FLAT_MODIFIERS` entry
(a pizza-line modifier), not a `NON_PIZZA` item, so `add_item` correctly
refuses it (`UNSUPPORTED_ITEM_SUBSTITUTION` — not a bug in the guard,
which is behaving exactly as designed: it doesn't fabricate a new item
for an unresolved-as-add_item name). The actual gap is the model never
retries with `add_modifier` instead. Not a safety issue (fails closed,
never mutates wrongly) — a tool-selection/prompt-clarity issue.

**Scope.** Likely a `_TOOL_DESCRIPTIONS["add_item"]`/`["add_modifier"]`
clarification (flat modifiers like ranch/blue cheese go through
`add_modifier`, never `add_item`) or a friendlier refusal message when
`add_item` is called with a name that IS a real `FLAT_MODIFIERS`/topping
key, redirecting the model rather than a generic substitution refusal.

**Acceptance.** `MOD-035` passes; regression test for the exact mechanism.

## T-048 · Non-pizza SKUs with no `NON_PIZZA_ALIASES` entry have no direct-evidence authorization path

**Priority:** 7 · **Status:** Not started

**Incidental finding, T-044 (not a P0 — filed, not fixed, deliberately
out of that task's scope).** `_authorize_item_creation`'s non-pizza branch
only checks `oe.non_pizza_alias_hits` (the spoken-form alias table) for
direct evidence. Most `NON_PIZZA` keys have no alias entry at all (only
drinks and cheesecake do) — "twelve piece wings," "a grinder," "a
calzone" have no direct-evidence path; the model must `search_menu` first.
Never silently wrong (only refused-until-searched, and T-031's own
evidence is the live model does search first nearly every run), so this
is a latency/turn-count cost, not a correctness risk — but it means a
compound order's second item ("...and a twelve piece wings," now
correctly authorized for the PIZZA half by T-044) may still cost the
model an extra round trip for its OWN half.

**Scope.** Extend `_authorize_item_creation`'s non-pizza branch with the
same `oe.non_pizza_full_name_match` check T-044 built for the pizza-intent
clause resolver — a literal, exact, menu-sourced name match, not a new
alias table.

**Acceptance.** A direct add_item for any `NON_PIZZA` key whose literal
name is present in the utterance authorizes without a prior search;
adversarial cases (fabricated/partial names) still refuse.

---

**T-043 audit is DONE (2026-09-22)** — full ground-truth audit after a
repository-write incident mid-T-041's live gate. Found: (1) the rewrite
was strongly suspected as Codex's own crash-restart-and-queue-replay loop
(different project's prompt, same directory, exact time overlap — see
`docs/AUDIT_T043.md` PART 1), not fully proven with a literal write-log
entry; `chat.py` restored, `pricing_engine.py` deleted, both confirmed
byte-clean against HEAD; (2) the offline gate reproduces identically from
a clean tree (583/2/2, 91/91, 58/91, 50/50); (3) F5/F6/F14/F17/F18
mutation-tested and genuinely load-bearing, not vacuous; (4) the
authorization-boundary question settled: no literal shared function, but
5 of 6 `RuleBasedInterpreter` `add_item` sites share real evidence
functions with the LLM path, one (`_new_pizza_half_a_half_b`) doesn't —
named, not proven unsafe; (5) **T-041's own claim that the T-032→T-041
overlap-score gap was "unrelated model-capability limitations" was FALSE
for 11 of 22 currently-failing cases** — a real regression, root-caused
and filed as **T-044** below. `docs/STATUS.md`/`docs/EVALS.md`/ADR-017
corrected in place. **Owed, not done:** confirming Codex isn't pointed at
this repo before the next live N=3 run is trustworthy.

**T-044 is DONE (2026-09-22)** — `_has_pizza_intent` required the ENTIRE
utterance to be pizza-shorthand; replaced with two conditions (positive
evidence AND no unresolved product-bearing word left over, checked per
clause split on `" and "` only) that deliberately avoid the trap the task
warned about ("positive evidence alone" would reopen the original P0 —
every T-038 row contains a real topping word). No hand-maintained noun
list anywhere — resolution is menu-sourced (`oe.non_pizza_alias_hits`/
`oe.non_pizza_full_name_match`), and an unresolved word blocks whether
it's a known non-pizza noun or a total unknown, so growing the menu can
only get MORE permissive, never open a hole. Brought the sixth `add_item`
call site (`_new_pizza_half_a_half_b`, T-043's named unshared exception)
under real evidence: `a`/`b` must both be real toppings AND `_has_pizza_
intent` must hold. Gave gourmet-number pizzas a direct-evidence path for
the first time (`_pizza_creation_authorized`) — previously required a
prior `search_menu` round trip even when the utterance plainly said
"number ten." Two offline regressions found and fixed before the ratchet
was raised (a bare gourmet number with no "half" phrasing briefly looked
pizza-shaped; comma-based clause splitting let a bare size fragment count
as its own evidence); two more found only by the live gate (case-
sensitivity in the gourmet branch; `_size_supported_by_utterance` could
only ever agree with ONE size word per utterance, breaking genuine
two-different-sizes multi-item orders). Live N=3: overlap/73 recovered to
53.33 (was T-041's 47.0, essentially matching T-032's 54.33), 0/0/0
substitutions, 7 of the 11 named regressed cases now pass 3/3, the other
4 confirmed to have the pizza itself created correctly every run (their
remaining failures are separate, already-filed-or-newly-filed defects —
see T-045/T-046/T-047 below and T-035's re-confirmation). Full mechanism,
every regression found and fixed, and the live-gate data:
`docs/decisions/ADR-017-no-silent-item-substitution.md`'s T-044 amendment;
`docs/STATUS.md`/`docs/EVALS.md`'s T-044 entries;
`tests/test_t044_pizza_intent.py` (34 tests). `validate` 91/91 unchanged,
rule-based ratchet 59/91 (was 58, +1 genuine flip), pricing parity 50/50
unchanged, full suite 613/2/2 (was 583/2/2, zero regressions).

**T-041 is DONE (2026-09-18)** — the mutation-boundary guard's evidence
check had grown a SECOND retrieval/matching system, independently of
`search_menu`, with none of its normalization. Part 1 (done before any
patch): checked directly whether `search_menu` had the identical gaps
rather than assuming the evidence check was uniquely broken — it did
(`search_menu(sess, "number ten")`/`"six piece wings"`/`"large pizzas"`/
`"sodas"` all returned `NO_MATCH`, live, before this task). Unified with
one shared normalizer in `orders.py` (`normalize_spoken_numbers`/
`normalize_menu_text`), consumed by both `search_menu` and the
interpreter's evidence layer, closing all four named gaps (plural
"pizzas", "triple"/"double"/"extra"/"quadruple" intensity words, spelled
gourmet cardinals, spelled quantity-to-abbreviated-SKU matching) at the
root rather than as four independent patches. Also fixed the LLM/rule-
based narrowing asymmetry (`_narrow_pending_disambiguations`, closing the
`NONPIZZA-006` flakiness) while keeping `_authorize_item_creation` pure —
no shared mutation, no fourth authorized reason, all three T-039B
adversarial cases still refuse (re-verified directly). A first version of
the normalizer converted every standalone spelled number and broke `_ONE_
HALF_RE`'s "on one half" idiom — caught by the full suite, fixed by
anchoring conversion to number-reference/piece-count context only, never
a bare word. Live N=3 re-run: **zero silent substitutions, zero
authorization bypasses, rejection ratio inverted (evidence now exceeds
refusal) in every run** — historical-overlap mean recovered from 40.33 to
47.0 (T-032's pre-guard band: 54.33; residual gap is unrelated model-
capability categories, not authorization). A label-authoring mistake in
3 of this task's own new corpus cases was found and corrected post-gate
(ratchet: 58/91, not the mislabeled 61/91). Full mechanism, live-gate
data, and every consequence: `docs/decisions/ADR-017-no-silent-item-
substitution.md`'s T-041 amendment; `docs/EVALS.md`'s T-041 live-gate
section. **"T-038 Phase 2 is unblocked" — WITHDRAWN by the T-043 audit
(2026-09-22): the overlap-gap attribution above was half wrong (see
T-043/T-044 entries above). Current next task is T-044.**

**T-042 · "Extra X" on a half-portion modifier is interpreted as
`intensity=DOUBLE`, not a plain addition (found by T-041's live N=3
gate)** — **Priority:** 7 (model-accuracy, not correctness/safety) ·
**Status:** Not started. `GOURMET-010`'s real live-model behavior
("medium number ten, extra pepperoni just on one half") authorizes and
adds the item correctly but consistently prices it $24.00 instead of the
label's $21.50, because the model sets `add_modifier(intensity=DOUBLE)`
for "extra pepperoni" rather than a plain `NORMAL`-intensity addition.
Not a substitution, not an authorization-boundary issue, not new — a
modifier-intensity SEMANTICS question (does "extra X" mean "add X" or
"double X"?) unrelated to item-creation authorization. Reproduced
identically in all 3 of T-041's live runs. Scope: decide the correct
domain semantics for "extra" (likely: `NORMAL`, distinct from `DOUBLE`,
which should require an explicit "double"), then decide whether to fix via
prompt/tool-description clarification (cheapest) or a rule-based
text-to-intensity extractor generalization (also closes `MOD-020`/
`GOURMET-005`'s own honest DOUBLE-intensity `RuleBasedInterpreter`
misses, a related but separate pre-existing gap). Full evidence:
`docs/EVALS.md`'s T-041 live-gate section.

**T-039B is DONE (2026-09-18)** — T-039A's LLM-path guard
(`_item_creation_is_authorized`) treated any `search_menu` hit returned
this turn as authorization, even one returned under
`needs_disambiguation=True` and even when the model's own search query had
no support in the customer's utterance — reproduced directly: "I want a
salad" → model searches "wrap"/"coke"/a gourmet number → adds that
unrelated valid SKU, authorized. Replaced with `_authorize_item_creation`,
which returns a reason code (`AUTH_DIRECT_UTTERANCE_EVIDENCE`/
`AUTH_UNIQUE_SUPPORTED_SEARCH_RESULT`/`AUTH_CUSTOMER_CONFIRMED_PENDING_
CANDIDATE` authorize; `AUTH_AMBIGUOUS_CANDIDATE_NOT_CONFIRMED`/
`AUTH_UNSUPPORTED_ITEM_SUBSTITUTION` do not) and requires both the search
query and the exact retrieved SKU to be independently supported by the
customer's own words, never the model's say-so. Added deterministic
explicit-follow-up resolution against the server-owned
`session.pending_disambiguations` set (`_select_pending_candidate`, shared
by both interpreters — resolves "the large garden salad" directly, and
narrows a family like "the garden one" before a later bare "large"
resolves it). Also fixed a persistence gap found while building this:
`search_menu` was treated as read-only, but registering/narrowing/clearing
`pending_disambiguations` is a real mutation of authoritative clarification
state that was never saved — `PersistentChat.run_turn` now fingerprints
that state before/after the turn and saves only when it actually changed.
Full reasoning and evidence:
`docs/decisions/ADR-017-no-silent-item-substitution.md`'s T-039B amendment.
15 new tests (`tests/test_t039b_candidate_authorization.py`), 3 new corpus
cases (`evals/cases/non_pizza_items.yaml`: NONPIZZA-006/007/008).
`validate` 81/81 (was 78/78), rule-based ratchet 50/81 (was 46/78 — +1
genuine flip, +3 new cases passing as authored; honest breakdown in
`tests/test_evals.py`'s baseline comment), pricing parity 50/50 unchanged,
full suite 561/2/2 (was 546/2/2, zero regressions). **Live N=3 ran
2026-09-18 — zero silent substitutions, zero authorization bypasses;
historical-overlap score dropped to 41/39/41 vs T-032's 53/54/56, root-
caused to evidence-vocabulary gaps, fixed the same day by T-041 above
(historical-overlap mean recovered to 47.0).** Fully superseded by T-041's
entry above.

**T-039A is DONE (2026-09-17)** — reopened T-039 the same day: T-039's fix
(`_NON_PIZZA_HEAD_WORDS`, a 12-word denylist) did not establish the general
"never substitute" invariant it claimed. Confirmed directly on the commit
that closed T-039: "A medium nachos with chicken." (and four more
adversarial nouns) still produced a fabricated, priced pizza — any noun not
on the list fell through unchanged. Replaced the denylist entirely with
fail-closed intent parsing (`_has_pizza_intent`/`_pizza_shorthand_residual`
in `interpreter.py` — a pizza needs POSITIVE evidence, never merely the
absence of a known-bad word) and generalized the LLM-path mutation-boundary
guard (`_item_creation_is_authorized`) to reject a model substituting a
VALID SKU — pizza or another valid non-pizza item — not just a fabricated
unknown one (T-039's own LLM test only ever exercised the latter, false
confidence). Also extended Part 4 customer-safe-failure masking to unknown
tool names, raw exception text, provider failures, and tool-loop
exhaustion. Full reasoning, known tradeoff, and evidence:
`docs/decisions/ADR-017-no-silent-item-substitution.md`'s "Amendment"
section. 26 new tests (`tests/test_item_substitution_guard_generalized.py`),
4 pre-existing corpus labels corrected (asserted the old, permissive
compound-utterance behavior). `validate` 78/78 unchanged, rule-based
ratchet 46/78 (was 41/78 — real, observed growth, honest per-flip
breakdown in `tests/test_evals.py`'s baseline comment), pricing parity
50/50. **Live N=3 still BLOCKED** — no `EXPLABS_API_KEY` in this
environment; owner action needed. Recommended next task: **T-038 Phase 2's
remaining item** below (real hardware voice loop) — unblocked again now
that this P0 is genuinely closed.

**T-040 · `search_menu`'s single-hit reply always asks "What size would you
like?", even for a topping or non-pizza item hit** — **Priority:** 3 ·
**Status:** Not started. Found while verifying T-039: a `search_menu` hit
of kind `topping` or `item` (e.g. "garden salad small and grilled with
grilled chicken" resolving to a single `CHICKEN` topping candidate) still
gets `chat.py::_reply_for_search_menu`'s generic "Did you mean Chicken?
What size would you like?" — confusing for a hit that has no size at all.
Not a correctness/substitution defect (T-039 already proved no cart
mutation happens here) — a clarification-wording bug. Scope: make the
follow-up question depend on the hit's `kind` (gourmet/item needing size →
ask size; topping → ask which pizza/whether to add it; non-pizza item with
no size variants → just confirm). Low priority (UX polish, not order
correctness) but real and reproducible.

**T-037 is DONE (2026-09-16)** — persistence + customer identity, the
blocker STATUS.md has flagged High severity since early on. New
`lakewood/persistence/` package: `SessionRepository` interface with
`InMemorySessionRepository` (what the full suite/T-016 gate run against) and
`PostgresSessionRepository` (real Postgres, `psycopg2` lazy-imported).
Multi-tenant schema (`store_id` on every table, structurally — 13 new tests
prove cross-tenant reads are impossible), every F-series fail-safe (F5/F14/
F15/F16/F17) proven across a save/reload boundary including mid-confirmation,
recovery via revalidate-and-reprice (30-min resume window, offer-not-
silently-continue), phone->customer_id identity, and retention/deletion
paths. Full reasoning: `docs/decisions/ADR-014-persistence-postgres-
multitenant.md` (supersedes ADR-005). 472/472 offline tests pass with no
Postgres driver installed and no database server. No domain file touched.
Full detail: `docs/STATUS.md` "T-037 done". Recommended next task: **T-038**
below (voice loop) — this was T-037's own "NEXT TASK" recommendation.
Superseded by T-037's actual implementation: **T-005** ("Persistence design
spike," design-only, never implemented) — removed below, its job is done.

## T-038 Phase 1 · Persistent text call path — **DONE (2026-09-16)**

`PersistentChat` now wires the text path to T-037 persistence: server-bound
store resolution, explicit recovery offer/accept/decline, revalidation and
stale-quote invalidation, and one executor shared by rule-based and staged
LLM calls. Fake-LLM tests prove durable confirmation and restart-safe F6.

## T-038 Phase 2 · Local voice loop

**Priority:** 1 · **Status:** In progress · **Phase:** 6 (voice layer, per
CLAUDE.md's MVP build order — comes after the eval gate, which is done, and
before persistence's remaining layers: customer memory/reorder fast path
depend on this existing first)

T-037 built and tested the persistence machinery
(`lakewood/persistence/service.py::resume_or_create`/`confirm_and_persist`/
`save_progress`) but explicitly did not wire it into any live call path —
`chat.py`'s text sandbox still uses `new_session()` (always fresh, never
persisted) and no telephony/voice loop exists yet. This is the first task
that actually needs the STT/TTS/telephony provider decision ADR-004 deferred
to this phase.

**2026-09-17 provider increment:** Parakeet is selected for the local pilot
after a same-file hardware benchmark: 0.082 s warm median for 2.586 s audio
(31.5x realtime, 2.60 GB GPU peak) versus faster-whisper `small` CPU at
2.748 s (0.9x realtime, 0.95 GB RSS). ADR-016 records the decision. A warm,
localhost-only WSL server (`scripts/parakeet_server.py`) and Windows-side
`ParakeetProvider` are implemented; faster-whisper remains a fallback. Offline
provider/error-path tests and the full repository suite are green.

**Hardware run completed 2026-09-17:** 10/10 microphone turns traversed the
full capture -> warm WSL Parakeet -> PersistentChat -> Windows SAPI path with
no transport/provider crash. Median capture 5.219 s, STT 0.320 s, app 0.000 s,
TTS synthesis 0.360 s, total 5.899 s; observed maxima/p95 at N=10 were 5.343,
0.391, 0.000, 0.391, and 6.235 s respectively. Post-capture processing was
~0.688 s median. The fixed five-second recorder, not STT, dominates total
latency and must be replaced by endpointing/VAD before a phone pilot.

**Phase 2 is still NOT DONE because order correctness failed.** The run used
the deliberately limited `RuleBasedInterpreter`: it dropped requested items
and intensity, confused half scope, mapped salad/calzone/wrap requests onto an
existing pizza, and failed ordinary non-pizza menu requests. At least two STT
outputs also appear materially mistranscribed, but the spoken ground truth was
not written down, so no honest STT accuracy rate can be calculated. Next:
repeat a labeled, coherent human order flow through the actual candidate LLM
interpreter, then add every observed STT/interpreter failure as a permanent
fixture before changing prompts or parsing.

**Scope:** pick/confirm the voice provider (ADR-004a, if not already
resolved by the time this starts), build the call-handling loop that: (1)
resolves `store_id` from the inbound DID (`config.py::store_for_did`,
unchanged), (2) calls `resume_or_create` to check for a resumable dropped
call and offer it back if found, (3) calls `save_progress` after every tool
mutation so a mid-call drop is actually recoverable, (4) calls
`confirm_and_persist` instead of `orders.confirm_order` directly so F6
survives a restart. **Do not re-implement any of T-037's logic** — this task
is integration, not a second persistence implementation.

**Acceptance:** a real (or realistically simulated) dropped-call-then-
callback scenario recovers the cart and re-quotes it correctly; eval suite
re-run if any prompt/tool-description/provider surface changed; STATUS.md/
ARCHITECTURE.md updated to move "wired into a live call loop" from PLANNED to
CURRENT.

---

**T-031 is DONE (2026-09-16)** — full N=3 live trace sweep (T-030's capture,
no custom script), all 73 cases, all 3 runs clean (0 provider failures, 0
schema violations). 25 distinct cases failed in >=1 run; clustered into
~7-8 root causes, not 25 independent problems. Top cause (`search_menu`'s
"cheese"-substring alias collision) explains 9 cases alone. Three-way split
of the 25: 10 system defects, 8 model capability limits, 3 label defects
(one already filed, T-029), 4 inconclusive (N=3 too small). Two new causes
filed below (T-032 through T-036). Phase 2 before-picture recorded: the
model already attempts every named item nearly always — T-023's structural
guard (F18) already guarantees the batched ask regardless of model
behavior — so Phase 2's own expected impact is narrower than the aggregate
number alone would suggest. Full ranked clusters, evidence, and the
three-way split: `docs/STATUS.md` "T-031 done".

**T-032 is DONE (2026-09-16)** — fixed `search_menu`'s "cheese"-substring
alias collision: word-boundary matching (fixes `"cheesecake"`) plus a
query-shape precedence rule (bare `"cheese"` alias suppressed specifically
when `"pizza"` is also mentioned, since together they name the base item,
not a topping) plus a sibling precedence fix for `NON_PIZZA_ALIASES`
(a specific drink size beats the generic "soda"/"coke" catch-all). Verified
directly: **zero collision recurrence across 286 live `search_menu` calls**
in a clean N=3 live re-run (53/73, 54/73, 56/73, mean 54.33 — flat vs.
T-031's 53.67, doesn't clear the noise floor). Of the 9 named cases: 2
clean flips (`INVALID-002`, `MOD-030`), 1 mostly-flipped (`ADV-002`, 2/3), 1
inconclusive (`NEG-007`, 1/3), and **5 confirmed fixed at the mechanism
level but still failing for an independent second cause** (`AVAIL-001` — a
genuine separate `clams` ambiguity; `COUPON-001`/`CORRECT-007` — `T-028`'s
coupon-by-description gap; `MULTI-004`/`MULTI-006` — `T-033`'s proactive-quote
pattern, plus a new narrow entity-extraction observation on `MULTI-004`
noted but not filed separately). NO_MATCH rate moved 23.6%→27.6%, checked
directly and traced to unrelated new query attempts that round (coupon-code
guessing, `"plain cheesecake"`), not a recall regression. 16 new tests
(`tests/test_search_menu_alias_collision.py`), full suite green, `validate`
73/73, rule-based 36/73, parity 50/50 all unchanged. Full detail:
`docs/STATUS.md` "T-032 done".

---

## T-033 · Model proactively calls `request_quote()` when nobody asked

**Priority:** 1 · **Status:** Not started

**Found in T-022 (`CONFIRM-002`, 1/3, inconclusive) and now reproduced 4
more times in T-031** with real trace evidence: `MULTI-004` (2/3,
`"one cheesecake and two strawberry cheesecakes"` → unprompted
`request_quote()`), `NEG-008` (1/3, `"no, wait, that's fine"` → unprompted
`request_quote()`), `CORRECT-007` and `COUPON-001` (1/3 each, right after
applying/discussing a coupon). Tips state to `QUOTED` when the customer
never asked for a total, failing labels that expect `BUILDING`.

**Scope.** Diagnosis first (same discipline as T-022/T-027) — is this
consistent enough across a dedicated N=3-per-case re-run to call it
systematic, or still genuinely mixed? If systematic, this is prompt-shape
territory (a system-prompt clarification that `request_quote` is only for
an explicit ask), not a domain-layer fix.

**Acceptance.** A classification with real evidence, and a decision on
whether it's worth a Phase-2-style prompt addition or stays documented as a
known limitation.

---

## T-034 · "Extra X" topping intensity has no stable mapping — sometimes DOUBLE (over), sometimes NORMAL (under)

**Priority:** 2 · **Status:** Not started

**Found in T-031.** `MOD-036`, `MOD-014` (3/3 each): `"extra cheese"` maps
to `intensity=DOUBLE`, overcharging against the label. `GOURMET-010` (2/3):
`"extra pepperoni"` maps to plain `NORMAL`, undercharging. No consistent
rule; both directions observed on real traces.

**Scope.** Determine what "extra X" should actually price as per the real
menu/PrISM rules (is there a real intermediate tier, or is one of these
labels wrong?) before touching the tool description or prompt. Domain
question first, code second.

**Acceptance.** A documented, verified answer for what "extra X" should
mean, plus whichever side (labels or model guidance) needs to change.

---

## T-035 · Downgrading an existing topping to LITE doesn't remove the old entry first

**Priority:** 2 · **Status:** Not started

**Found in T-031, `NEG-005`, 3/3 identical.** `"actually make the pepperoni
light"` on a pizza that already has `PEPPERONI` at `NORMAL` intensity calls
`add_modifier(PEPPERONI, LITE)` again instead of `remove_modifier` first —
ends with two topping entries and an unchanged (wrong) total. Note:
`RuleBasedInterpreter` already has the correct logic for this exact shape
(`_resolve_intensity_calls`'s LITE-downgrade-of-existing branch, from
T-015) — the live model has no equivalent guidance.

**Scope.** Likely a small, targeted system-prompt or tool-description
clarification once diagnosed further — confirm the mechanism holds at
N=3-per-case before deciding the fix shape.

**Acceptance.** Regression test reproducing the exact mechanism; N=3 proof
the fix (whatever form it takes) actually changes it.

**Re-confirmed 2026-09-22 (T-044's live N=3 gate, all 3 runs, identical
mechanism):** `NEG-005` still fails this exact way — pizza + pepperoni
created correctly, then "make the pepperoni light" adds a SECOND, LITE
pepperoni line instead of replacing the NORMAL one. Not touched by T-044
(a different mechanism — intensity-change semantics, not item-creation
authorization). Still Priority 2, still not started.

---

## T-036 · `get_store_info`'s strict field-matching turns a naming near-miss into an unwarranted human transfer

**Priority:** 2 · **Status:** Not started

**Found in T-031, `FAQ-001`, 3/3 identical.** Model asks for fields
`['hours', 'delivery_minimum', 'delivery_radius', 'delivery_available']`;
real keys are `delivery_radius_miles` (not `delivery_radius`) and there is
no `delivery_available` key at all. `get_store_info`'s `missing = [f for f
in fields if f not in data]` fails the *entire* call on any single
mismatch, triggering `INFO_NOT_AVAILABLE` and a transfer for an ordinary
hours/delivery question — a real customer asking simple store-info
questions would get unnecessarily transferred.

**Scope.** Either return the fields it *does* have instead of failing
wholesale on a partial miss, or normalize/alias the field names the tool
accepts. Small, contained, `get_store_info`-only change.

**Acceptance.** `FAQ-001`-shaped questions no longer transfer; regression
test for the exact observed field-name mismatch.

---

**T-030 is DONE (2026-09-16)** — supersedes T-025's GUI framing: the real
need was trace capture first, rendering second. `evals/runner.py::score()`
now emits a full structured JSONL trace by default under `evals/traces/`
(every tool call/result, a session-state snapshot after every turn, real
tokens/latency/cost) — provably identical scores with capture on or off
(`tests/test_trace_capture.py`), zero dependency added to the T-016 gate.
`evals/catalog.py` generates a derived, always-fresh case index
(`evals/catalog.json`) — one-sentence description per case, never hand-
written. `scripts/viewer/server.py` (stdlib `http.server` only, localhost-
only, no framework) + one static page render Catalog/Replay/Live through
the exact same `lakewood/chat.py::run_turn_traced` function proven shared
across both call paths (`tests/test_viewer_shared_path.py`). Manually
verified end to end in a real browser (Catalog, Replay of a passing case,
Live session against `rule_based`) plus one real replayed failing case
(`MULTI-002`) showing its actual root cause at a glance. `validate` 73/73,
rule-based 36/73, parity 50/50 all unchanged. See `docs/STATUS.md` "T-030
done".

## T-028 · `apply_coupon()` can silently pick a coupon the customer didn't ask for

**Priority:** 0 (escalated from 1, 2026-09-23, `docs/
SECURITY_AUDIT_T054.md` finding T054-01) · **Status:** Not started — see
**T-055**, which covers this entry's mechanism together with T-046's

**Found while diagnosing T-027's `COUPON-001` live failure.** The customer
said "I have the three dollars off thirty coupon" (describing `OFF_3_AT_30`
by its terms, not its code). `search_menu` doesn't know about coupons at
all (they aren't a topping/item/gourmet), so nothing resolves the
description to a code. The live model, unable to resolve it, called
`apply_coupon()` with no `code` at all — letting the domain engine's own
default "any eligible coupon" selection pick `FREE_2L` ("buy 2 large
pizzas, get a 2-liter free") instead, in one observed rep with **zero large
pizzas in the cart at the time**. A real order-correctness risk: a
different discount than the one requested could reach a real ticket.

**Scope.** Decide whether `apply_coupon(code=None)`'s default-selection
behavior is intentional product behavior needing a customer-facing
description-to-code lookup (a `search`-shaped fix, likely on
`search_menu` or a sibling), or whether it should refuse/ask instead of
silently picking something when no code is given and more than one coupon
is eligible. Diagnose with real traces before deciding, same discipline as
T-022/T-027 — don't assume the mechanism from this one observation alone.

**Acceptance.** A decision, documented (ADR if it changes coupon-resolution
behavior), plus a regression test reproducing the exact observed mechanism.

---

## T-029 · `DECLINE-001`'s label is invalid — needs correction

**Priority:** 2 · **Status:** Not started

**Found while diagnosing T-027.** `DECLINE-001` (`evals/cases/
invalid_and_ambiguous.yaml`) authors turn 2's calls as `decline_item` +
`request_quote` + `begin_confirmation`, but the turn's own utterance
("actually forget the wings, just the pizza") never asks for a total or
confirmation. The system prompt correctly forbids `begin_confirmation`
without explicit affirmative confirmation — no well-behaved model can pass
this case as authored, confirmed directly: even a rep where the live model
called `decline_item` exactly right still failed on this account.

**Scope.** Split into a clean decline-only turn (assert the disambiguation
clears and the cart is correct, `state: BUILDING`) and, if confirmation
coverage is still wanted, a separate turn/case with an actual explicit
"yes, place it"-shaped utterance. Corpus edit only — no source changes.

**Acceptance.** `validate` still green; the corrected case's `assert_final`
is reachable by a model that behaves exactly as `CLAUDE.md`'s own
confirmation rule requires.

---

**T-024 is DONE (2026-09-15)** — the corpus couldn't credit correct
clarify-then-resolve behavior (a single-turn case has no way to answer the
question T-023's guard raises), inverting `CLAUDE.md`'s own "prefer
clarification over guessing" rule. Audited all 71 original cases: exactly 3
(~4%) had genuine single-turn ambiguity (`MULTI-002`, `COUPON-001`,
`CORRECT-007`, all "wings, no count given"), each given an unrelabeled
follow-up turn. Added `DECLINE-001`/`DISAMBIG-CAP-001` for `decline_item`
and cap-then-transfer coverage. `validate` 73/73, rule-based 36/73 (same
absolute count, ratchet unchanged), live N=3: 55/73, 53/73, 56/73 (mean
54.67) — `MULTI-002` flipped 0/3→3/3, a measurement-honesty fix, not a
model-capability claim. `COUPON-001`/`CORRECT-007`/`DECLINE-001` still fail
consistently for reasons not fully traced — see below. Full detail:
`docs/STATUS.md` "T-024 done", standing rule added to `docs/EVALS.md`.

**T-026 is DONE (2026-09-15)** — P0 fix, found while starting T-024: F15
cleared `unresolved_lookups` globally on ANY successful search/mutation
anywhere in the order, not just a resolution of the specific missed
request — silently reopening the exact T-018 dropped-request defect via any
ordinary multi-item order. Fixed: clears only via a textually-related later
success or explicit `decline_item`. See `docs/STATUS.md` "T-026" and
`docs/decisions/ADR-013-f15-scoped-clearing.md`.

---

**T-027 is DONE (2026-09-16)** — diagnosed all three T-024 live failures with
real per-call/per-turn traces (3 cases × 3 reps, live). **Found fewer root
causes than symptoms, as the task itself warned might happen:**
`COUPON-001` and `CORRECT-007` share the exact same root cause — both trip
the identical `search_menu` alias collision already diagnosed as T-022's
`ADV-002` finding ("party size cheese pizza" spuriously matches MOZZARELLA),
which T-024's audit failed to anticipate since it only checked the original
label's calls, never a live model's actual search queries. `CORRECT-007`'s
transfer is `F18`'s cap firing *correctly* on that same incidental,
unrelated ambiguity — not a defect in `MAX_DISAMBIGUATION_ASKS`, confirmed
not too tight. `DECLINE-001` fails for an unrelated reason entirely: **the
case's own label is a corpus defect** — turn 2 bundles `begin_confirmation`
onto an utterance that never asks for confirmation, which the system prompt
correctly forbids without explicit affirmative confirmation. No well-behaved
model could pass it as authored. Real signal: `decline_item` was called
correctly 1/3 unprompted reps (no system-prompt mention exists) — the
"customer can only exit via the cap" hypothesis is refuted, though
reliability is still an open, inconclusive question (2/3 reps: zero tool
call at all, not a wrong one). **Phase 2 scoping decision: one rule, not
two** — ship the already-scoped "resolve/ask about every named item" rule;
defer a second explicit-abandonment rule until a corrected `DECLINE-001`
produces a valid measurement. Two new findings filed below (T-028, T-029).
Full detail: `docs/STATUS.md` "T-027 done".

**T-016 is DONE (2026-09-08)** — see `docs/STATUS.md` "T-016 milestone" and
`docs/decisions/ADR-009-eval-gate-runs-an-interpreter.md` for the mechanism
decision, implementation, and the Part 3 proof (real numbers: gate passes at
35/71 today, correctly fails at 25/71 when the T-015 bug is monkeypatched
back in).

**T-018 Parts 3 & 4 are DONE (2026-09-15)** — first live baseline against a
real provider (Experiential/Luna): 45/71 (63%), full category/cost/latency
breakdown in `docs/STATUS.md` "T-018 milestone". Found the two findings T-019
below files.

**T-019 is DONE (2026-09-15)** — three deterministic defenses against the
confirmation-gate bypass and silent request drop found in T-018, plus the
stale-turn readback fix. See `docs/STATUS.md` "T-019 milestone",
`docs/decisions/ADR-011-confirmation-turn-gate.md`, and
`tests/test_confirmation_gate.py`. Filed two follow-ups below (T-020,
T-021) that this task deliberately did not fix.

**T-020 is DONE (2026-09-15)** — diagnosed 18 real `search_menu` failures
from actual checkpoint call logs before writing any fix (10 class-A "base
pizza has no searchable name," 10 class-B "reasonable phrasing, matcher too
strict/no alias table"), fixed both cheaply (filler-word stripping, number
normalization, an `ALIASES`/new `NON_PIZZA_ALIASES` lookup, a narrow
`CHEESE PIZZA` pseudo-hit) with **no embeddings** — `nomic-embed-text` was
never needed once the diagnosis was done first. Also ran the first real 3x
sequential variance measurement (42–48/71 band, 21% of cases flip run to
run on unchanged code) and found T-018/T-019's single-run deltas were
mostly noise. Post-fix live score: 51/71, 3 points above the band's max — a
real improvement. `search_menu` NO_MATCH rate: ~60–64% → 26% of calls. Full
detail: `docs/STATUS.md` "T-020 milestone", `docs/EVALS.md` "search_menu
recall diagnosis and fix" / "Non-determinism". No ADR — matching strategy
changed in degree, not architecture.

**T-022 is DONE (2026-09-15)** — diagnosed the "search succeeds, model
doesn't act" question with 19 live case-runs (5 cases × 3 reps + follow-up
reply-text capture), not assumed. Two of the five original candidates
(`CORRECT-003`, `NEG-006`) didn't reproduce at all in isolation — ordinary
run-to-run variance, not a context-dependent bug (score() gives every case a
fresh session regardless). Of the two that did reproduce: `ADV-002` turned
out to be `chat.py`'s own designed clarification reply firing correctly on
a spurious `search_menu` alias collision (search-precision gap, filed as
its own separate item below), not model abandonment. `MULTI-002` reproduced
5/5 across two independent runs — a real, consistent model/prompt-
sequencing gap: given ≥2 ambiguous items in one utterance, the model
resolves one and silently drops the rest. Also found the
`_HALLUCINATION_CODES` metric is counting honest `NO_MATCH` search misses,
not invented item names — a measurement-naming defect, not a new safety
finding. Full diagnosis: `docs/STATUS.md` "T-022 diagnosis". `MULTI-002`'s
finding is now fixed structurally by **T-023 Phase 1** below.

**Filed, not yet scheduled, from T-022:** `search_menu`'s `ADV-002`-shaped
alias collision (a compound "topping + item" query spuriously surfaces an
unrelated generic alias — e.g. MOZZARELLA on the word "cheese" — instead of
resolving the clearly-named item); the two low-frequency residual gaps
(word-form quantities, a gourmet number embedded in a longer compound
sentence); the `_HALLUCINATION_CODES` metric rename/reclassification.

---

## T-023 · Turn-completion guard for outstanding disambiguations

**Priority:** 1 · **Phase 1 (structural guard) is DONE (2026-09-15)** ·
**Phase 2 (prompt rule) is NOT STARTED**

**Phase 1 done.** `Session.pending_disambiguations` (F17) + `chat.py`'s
turn-completion guard (F18) — a turn can no longer end silently while a
`search_menu` hit the model flagged `needs_disambiguation` remains
unresolved; batched into one clarification, cleared by a matching
resolution or the new `decline_item` tool, capped at
`MAX_DISAMBIGUATION_ASKS = 2` before transfer. Full design:
`docs/decisions/ADR-012-turn-completion-guard.md`. 17 new offline tests
(`tests/test_disambiguation_guard.py`), all offline gates unchanged
(`validate` 71/71, rule-based 36/71, parity 50/50). Live N=3
regression-detection: 55/71, 50/71, 50/71 (mean 51.67) — flat-to-
slightly-above the post-T-020 49–53 range, no regression. Full report:
`docs/STATUS.md` "T-023 Phase 1" (current "Current phase" entry).

**Phase 2 — not started.** Add a system-prompt rule (separate change from
Phase 1, per `CLAUDE.md`'s "prompt for quality, structure for correctness"
principle and this task's own explicit instruction not to bundle the two):
when an utterance names more than one item, resolve or explicitly ask about
every named item before ending the turn. A UX optimization only — one turn
instead of two on the happy path — never the correctness guarantee, since
Phase 1 already holds that regardless of what the prompt says.

**Scope for Phase 2.** System-prompt text only
(`lakewood/interpreter.py::_SYSTEM_PROMPT`); no changes to Phase 1's
structural code, `search_menu`, the corpus, or the scorer. Its own separate
N=3 sequential live measurement, compared against Phase 1's own numbers
(55/50/50, mean 51.67), not against pre-T-023. If it shows no measurable
effect above the noise band, that is a legitimate, reportable outcome — the
guard still holds either way.

**Acceptance.** N=3 sequential live run post-change; report against Phase
1's numbers specifically; note whether `MULTI-002` itself now passes within
the eval harness's single-turn structure (it structurally cannot with
Phase 1 alone, per the corpus limitation noted in `docs/STATUS.md`).

---

**T-021 is DONE (2026-09-15) — no code/content fix needed; root cause was a
stale local `.venv`, not `requirements.txt`.** Found while running T-018's
live baseline: the repo's pre-existing `.venv` (created 2026-09-06) predated
`pyyaml`/`pytest` being added to `requirements.txt` and had never had
`pip install -r requirements.txt` re-run against it, so `tests/test_evals.py`
and `tests/test_eval_runtime_binding.py` failed to collect
(`ModuleNotFoundError: No module named 'yaml'`) in THAT venv specifically.
Verified with a genuinely fresh venv (`python -m venv` + `pip install -r
requirements.txt`, zero manual extras): `pyyaml 6.0.3` and `pytest 9.1.1`
install cleanly, and `python -m pytest -q` (365 passed, 2 xfailed) plus
`python evals/runner.py validate` (71/71) both run clean with no network, no
Ollama. `requirements.txt` needed no change. The stale repo `.venv` was
fixed in the T-018 session by installing the missing packages into it
directly; this task just confirmed there was never a repo-level defect to
fix, and documents the corrected diagnosis so it isn't repeated.

**Acceptance.** A fresh `python -m venv` + `pip install -r requirements.txt`
+ `scripts/check.sh` succeeds with no manual intervention.

---

## T-017 · `RuleBasedInterpreter` can't parse a bare "number N" as a single gourmet selection

**Priority:** 0 · **Status:** Not started

**Found 2026-09-08** while testing T-015's negation fix against
`GOURMET-005`'s own utterance ("gimme a medium number ten, no pineapple").
Unrelated to negation — the negation half now resolves correctly, but
"number ten" alone is never recognized as `gourmet_number=10`:
`RuleBasedInterpreter` only extracts a gourmet number in the "half #A half
#B" pattern (`_HH_BY_NUMBER_RE`) or while resolving a pending
disambiguation. A bare "number ten" (no "half", no prior `search_menu`
clarification) falls through to a plain `CHEESE PIZZA`, silently ordering
the wrong item — a medium cheese ($13.00) instead of a medium #10 Hawaiian
($19.00).

**Scope.** Add a single-gourmet-number pattern (e.g. `\bnumber\s*#?\s*(\d{1,2})\b`
or `\b#\s*(\d{1,2})\b`) to `_new_pizza`'s / `interpret()`'s rule ordering,
validated the same way `add_item(gourmet_number=...)` already validates
range via `GOURMET_NOT_FOUND` — never guess a name for a number that isn't
on the menu for that size.

**Tests required.** "medium number ten" → `gourmet_number=10`, MD gourmet
price · "medium number ten, no pineapple" → same, with the exclusion
correctly free · an out-of-range number → clarification/refusal, not a
guessed item.

**Acceptance.** `GOURMET-005`'s utterance produces the fully correct label
end to end (item **and** negation) under `score --adapter rule_based`.

---

## T-013d · Rerun the Ollama full-corpus benchmark against the current (71-case) corpus

**Priority:** 1 · **Status:** Stale result exists, needs rerunning

`python evals/runner.py score --adapter llm` (`LAKEWOOD_LLM_PROVIDER=ollama`,
`LAKEWOOD_LLM_MODEL=llama3.1:8b`) finished mid-session 2026-09-08: **real
result 11/63.** That run predates T-015's negation fix and its 8 new
`evals/cases/negation_and_removal.yaml` cases — the corpus and the
interpreter it's measuring have both changed since, so 11/63 is stale as a
current number (still real, still worth keeping as a historical baseline —
see `docs/STATUS.md`/`docs/EVALS.md`). `OllamaProvider` and 26 offline tests
are done and passing; two real manual smoke-test turns also ran live and
behaved exactly as designed (a schema violation from each model, both
caught before domain state — see `docs/STATUS.md` "Local Model Tier
milestone").

**To do:** rerun the same command against the current 71-case corpus;
consider a smaller `--cases` subset first for faster iteration (~60-90s
observed per turn on this hardware), or `LAKEWOOD_LLM_MODEL=gemma4:26b` for
the pinned-default comparison once `llama3.1:8b`'s current-corpus run is in.
Report per the same category breakdown T-013c asks for on the paid-provider
side: item-selection, modifier-scope, correction, ambiguity, hallucinated
SKUs, premature confirmation, stale-quote violations, invalid tool calls,
total calls, token usage (Ollama reports `prompt_eval_count`/`eval_count`),
latency (median/p95 — Ollama has none of the cost concerns a paid API run
would). Update `docs/STATUS.md`/`docs/EVALS.md` with the fresh numbers.

**Do not** re-run this by hard-coding fixes for individual failed phrasings
into the prompt/schema just to raise the number — the task that created
this line was explicit: generalizable improvements are fine if reported
with a full rerun showing improvements AND regressions against this
baseline; anything else is target-gaming.

---

## T-013c · Complete live Astra baseline — BLOCKED on application API access

**Priority:** 2. Provider code verified on 2026-09-08: 30 new fake-HTTP tests,
15 unchanged LLM tests; 261 passed / 2 xfailed overall. OPENAI_API_KEY absent.
This supersedes T-013b's Anthropic-only live plan. No real AI baseline exists.

Configure `LAKEWOOD_INTERPRETER=llm`, `LAKEWOOD_LLM_PROVIDER=openai`,
`LAKEWOOD_LLM_MODEL=gpt-6-astra`, and a funded application `OPENAI_API_KEY`.
Never use Codex session credentials or print/commit the key.

1. Send ONE cheap real provider request for "Large pepperoni." using the
   repository adapter and restricted schemas. Verify structured tool behavior.
   Stop on connectivity/resource failure; record the exact sanitized error.
2. Only after success, run `python evals/runner.py score --adapter llm` over
   all 63 unchanged cases. Capture traces for the full metric breakdown below.
3. Run `python -m lakewood.chat --debug` with the actual provider: large
   pepperoni; mushroom on one half; replace mushroom with sausage; total;
   Coke after quote; new total; "Yes, place it." Then test "Give me chicken.",
   "Give me a truffle lobster pizza.", and half #8 / half #10 in fresh sessions
   where necessary (confirmed orders are immutable).

**Report:** passed/63; final-cart exact match against available labels;
item-selection, modifier-scope, correction and ambiguity failures; hallucinated
SKUs; premature confirmation; stale-quote violations; invalid tool calls;
provider failures/timeouts; available token usage/cost; median/p95 latency.
The score CLI currently checks labeled final state, not all these metrics;
collect tool/state traces and analyze the missing metrics without editing cases
or treating absent measurements as zero. Latency and tokens are available per
provider response. No cost estimate without a verified applicable rate.

**Acceptance:** honest first AI baseline (not production accuracy), actual
manual trace reaching CONFIRMED, and updated STATUS/EVALS evidence.

---

## T-003 · Verify coupon tax ordering at the register — **owner action**

**Priority:** 3 · **Status:** Blocked on a store visit

The one open item keeping the pricing-parity milestone from being fully
DONE rather than PARTIALLY VERIFIED. Three orders in
`docs/COUPON-VERIFICATION.md`. Test 1 decides it: tax reads $2.28 → current
model is right; $2.50 → `pricing.taxable_base` changes and both
`tests/test_coupons.py` and the coupon category of
`tests/test_pricing_parity.py` need new expected values.

Also ask: are coupons honored by phone at all? If not, `COUPONS = []`.

---

## T-001 · Tool API layer (Phase 2)

**Priority:** 4 · **Status:** Not started

**Purpose.** Expose `orders.TOOLS` over HTTP so a voice provider can call them.
Every Phase 5 provider option needs this, and it needs no vendor decision.

**Dependencies.** Pricing/menu domain engine (met, including T-008's
`add_item(second_gourmet_number=...)` — generate the route off the full
current signature, not a stale copy of it).

**Scope — exactly this, nothing more.**
- `lakewood/api.py`: FastAPI app with one route per entry in `orders.TOOLS`,
  **generated by introspecting the list** so routes and tools cannot drift.
- Session resolution: `call_id` → in-memory registry for now (persistence is
  T-005/Phase 3, not this task).
- `store_id` derived from the inbound DID in the call payload. Reject any
  request body containing `store_id` or a price field.
- Shared-secret auth via `Authorization` header, secret from env.
- `GET /healthz` returning version and test-suite-verified menu hash.
- Emit `schemas/tools.json` for provider registration.

**Out of scope.** Prompts, provider SDKs, persistence, audio, dashboards,
business logic of any kind in the route layer.

**Files.** `lakewood/api.py` (new) · `lakewood/config.py` · `requirements.txt`
· `tests/test_api.py` (new) · `schemas/tools.json` (new)

**Tests required.**
- Every tool in `orders.TOOLS` has a reachable route (generated, so assert parity)
- Unauthenticated request → 401
- Body containing `store_id` → 400
- Body containing `price`/`total`/`amount` → 400
- Full order completed via HTTP only, ending in a confirmed order
- `/healthz` → 200

**Acceptance.** A complete pickup order — add, modify, quote, confirm — runs
end to end over HTTP with correct totals, no Python imports by the caller.

---

## T-002 · Tests for `search_menu` and `check_availability`

**Priority:** 5 · **Status:** Not started

Both are model-facing and untested. `search_menu` is the disambiguation path
for this menu's collisions — "chicken" (6 gourmet pizzas + the CHICKEN
topping + CHICKEN DINNER) is a real one today; see `evals/cases/
invalid_and_ambiguous.yaml::COLLIDE-002` for a worked example.

**Tests.** Collision queries return `needs_disambiguation: true` · numeric query
("number 10") resolves · unknown query returns `NO_MATCH` and never a guess ·
86'd item reports unavailable.

**Files.** `tests/test_orders.py`.

**Acceptance.** Both functions covered; no behavior change.

---

## T-005 · Persistence design spike — **DONE, superseded by T-037's actual implementation**

T-037 (2026-09-16) implemented persistence directly — Postgres, multi-tenant
schema, repository interface, recovery, retention, customer identity — per
`docs/decisions/ADR-014-persistence-postgres-multitenant.md`, which
supersedes ADR-005 (the SQLite proposal this spike would have detailed).
Nothing left for a separate design-only spike to produce.

---

## T-007 · Make `scripts/check.sh` portable to Windows

**Priority:** 7 · **Status:** Not started (partially done)

Found during a prior session on a Windows dev machine, tracked as two items:
- ~~`lakewood/orders.py:602` — `time.strftime("%-I:%M %p")` is a glibc-only
  format code and raises `ValueError: Invalid format string` on Windows'
  CRT.~~ **DONE** — fixed at all three call sites via the shared
  `lakewood/timefmt.format_12h()` helper. Covered by `tests/test_timefmt.py`.
- `scripts/check.sh` calls `python3`, which resolves to a non-functional
  Windows Store alias on at least one dev machine. Still open — detect
  `python` vs `python3` in the script (e.g. `command -v python3 || alias
  python3=python`, or a small shim).

**Acceptance.** `./scripts/check.sh` runs clean on both Windows and the
original Linux/Mac dev environment.

---

## T-009 · Digitize the restaurant's pasta menu, if one exists

**Priority:** 8 · **Status:** Not started — needs the owner/menu to confirm scope first

**Found by:** writing T-006's eval corpus. `docs/EVALS.md` documents "Shrimp
Scampi is both a $23 pizza and a $20 pasta dish" as a collision trap, but
`data/menu.json` has no pasta section at all — `search_menu("shrimp scampi")`
returns exactly one hit today. Either the pasta menu was never digitized, or
it doesn't exist and the EVALS.md note is aspirational/stale.

**First step is a question, not code.** Ask the owner: does this location
sell pasta dishes (Shrimp Scampi, or others)? If yes, get PrISM
screenshots/receipts the same way the pizza menu was verified — no price
goes into `data/menu.json` without one. If no, correct `docs/EVALS.md`'s
collision-trap note instead (it's describing a menu that doesn't exist here).

**Acceptance.** Either `data/menu.json` gains a verified pasta section with
its own parity tests, or `docs/EVALS.md` is corrected — not both left
contradicting each other.

---

## T-010 · Wire `scripts/check.sh` into CI

**Priority:** 9 · **Status:** Not started

GitHub Actions on push and PR. Fail on red. No coverage gate — see
`docs/TESTING_STRATEGY.md` on why coverage is not the metric here.
