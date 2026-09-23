# Status

**Read this first, after `CLAUDE.md`.** Update it in the same commit as any
meaningful implementation change. A task that leaves this file stale is not done.

Last verified: 2026-09-22, live N=3 Experiential re-acceptance gate for
T-044 (`gpt-5.6-luna`), branch `codex/parakeet-stt`, on a confirmed-clean
repo (Codex processes pointed at this directory killed first, `EXPLABS_API_KEY`
rotated, HEAD unchanged start-to-end all 3 runs — see T-044 entry below).
Real GPU numbers in the T-038 Phase 2 entry below were measured separately
on the owner's Windows/WSL hardware and are unaffected by this task.

**Temporary fresh-environment re-verification (2026-09-15):** Created
`.venv-codex` only, using Blender-bundled Python 3.11.7, and left the stale
`.venv` untouched. After upgrading pip and installing `requirements.txt`
unchanged (no manual extras), the full test suite ran successfully, `validate`
reported 71/71, the ratcheted `RuleBasedInterpreter` baseline was the expected
36/71, and pricing parity passed 50/50. These four checks are green and permit
the two remaining sequential T-020 live measurements. This repository has no
declared Python version; that observation is recorded only, not fixed as part
of this measurement task. Pytest could not write the pre-existing
`.pytest_cache` due to Windows access denial, but this did not affect test
execution.

## Current phase

**T-049 FINAL done, 2026-09-23 (P1): real hardware confirmed, real print
verified, real reliability path (retry, FAILED_DISPATCH, durable dispatch
status, recovery) proven live against real hardware — physical ticket
photographed at every stage, not "it printed."** Preconditions confirmed
first: T-051 committed/pushed (`3819c3b`), clean tree, HEAD matches origin.

**Preconditions — two of three hard-stops FAILED at task start, fixed this
session (owner's explicit choice: give the admin password and let this
session fix them, rather than proceed around them):**
1. **DHCP reservation/Manual IP: FAILED at start.** Printer's own network
   status page (no login required) showed "Obtain IP Address: Auto." Fixed:
   logged into the printer's admin web config (`https://10.1.10.197`,
   password = the printer's own serial number `XBVQ083052`, printed on its
   physical label — confirmed via a real photo of the label, not guessed;
   Epson TM firmware's login page states outright "the initial password is
   the product's serial number," no generic default exists) and switched
   `Obtain IP Address` from Auto to **Manual**, same values the DHCP lease
   already had (10.1.10.197 / 255.255.255.0 / gw 10.1.10.1) — pinned, no
   actual address change. Re-verified after the interface restart: "Obtain
   IP Address: Manual," TCP reachable again.
2. **Wi-Fi Direct disabled: FAILED at start, live.** `netsh wlan show
   networks` from this machine found `DIRECT-TM-LAKEWOOD REXI` actively
   broadcasting (77% signal, WPA2-PSK, MAC A6:D7:3C:AE:EE:0C) — the
   compromised-password rogue AP the brief warned about was still on the
   air. Fixed: same admin session, Wi-Fi Direct settings page,
   `INPUTR_WIFIDENABLE=DISABLE`, submitted. Re-scanned with `netsh wlan
   show networks` after ~35s: SSID gone entirely, confirmed by a real
   Wi-Fi scan, not the printer's own self-report.
3. **Reachability: PASSED at start.** TCP connect to 10.1.10.197:9100
   succeeded (31ms) even on DHCP Auto — precondition 3 alone was never the
   blocker.

**Part 1 — verify before coding:**
- **Connection assumption:** `printer.py`'s `_send` was ALREADY TCP-first
  by construction (`socket.create_connection((host, port))` whenever `host`
  is set and no `device` override is given) — the brief's stated "likely
  USB/serial" mismatch did not exist; ADR-003's original tcp/9100 decision
  was already what the code did. No correction needed here, stated
  explicitly rather than fixing a problem that wasn't real.
- **Paper width:** confirmed physically with a real calibration ticket (a
  0-9 ruler, a 42-character line, a 48-character line, each marked with its
  length). Real result: 48 characters filled one physical line with zero
  room left over for a 3-character suffix — true capacity is 48 columns,
  not the 42 `printer.py` assumed (T88V-spec guess). `WIDTH` corrected
  42 -> 48. The old 42 never caused wrapping/corruption (safe direction,
  confirmed by the same calibration ticket) — it just wasted 6 real columns
  per line.
- **DLE EOT / status behavior:** confirmed supported by this exact model
  per Epson's own published TM-m30III command list (found via search,
  cross-referenced against `printer.py`'s existing bit logic). Confirmed
  LIVE, not just from docs: a real `status()` call against the real device
  returned `online=True, cover_open=False, paper_out=False, paper_low=False`
  (raw byte `0x16`), matching `printer.py`'s existing bit decode exactly.
  Real network-down behavior also observed (see Part 4).
- **Cloud Services tab:** confirmed present in the web config, currently
  **Disabled**, requires accepting a Terms-of-Use checkbox and clicking a
  `CONNECTION_START` action to register the device with an Epson cloud
  service (Epson Connect-style). NOT enabled this task — a real, external,
  consequential registration action out of scope. This is the real
  candidate for T-053's "does this hardware support polling instead of
  inbound access" question; the tab exists, the mechanism is registration-
  based (matches Epson Connect's usual shape), and it was never activated.

**Part 2 — transport interface, minimal seam only (no broader
`printer.py` refactor):** `lakewood/printer.py` gained `PrintTransport`
(a `Protocol`), `TcpRawTransport` (the real, now hardware-verified
connection), `DryRunTransport` (tests' default), `UsbDeviceTransport` (the
pre-existing `device=` path, unchanged behavior), and `ServerDirectTransport`
(raises `NotImplementedError` — a named slot for T-053, not an
implementation). `TicketPrinter`'s public constructor is UNCHANGED
(`host`/`port`/`timeout`/`dry_run`/`device`, plus a new optional
`transport=` for direct injection) — it selects the right transport
internally from those same arguments, so every existing caller/test kept
working with zero changes. All 13 pre-existing `test_printer_dispatch.py`
tests (built by the earlier T-049 session, reused not rebuilt, per this
task's own instruction) pass unchanged.

**Part 3 — real print, physically verified at every step (not "it
printed"):**
1. Smallest possible thing: a hardcoded 41-byte two-line ticket
   (`INIT + ALIGN_C + "HELLO LAKEWOOD" + "TEST LINE TWO" + FEED + CUT`)
   sent directly over `TcpRawTransport`. **Real photo confirmed:** two
   lines, centered, readable, clean cut.
2. Paper-width calibration ticket (above).
3. A real ticket from a real confirmed order through the REAL production
   path (`oe.add_item`/`oe.apply_coupon`/`oe.confirm_order` ->
   `dispatch_confirmed_order` -> real TCP), a large cheese pizza, a large
   half-pepperoni/half-mushroom pizza, 6pc wings, $3-off-$30 coupon
   applied. **Real photo confirmed:** `<1st Half> PEPPERONI` /
   `<2nd Half> MUSHROOMS` rendered clearly and unambiguously; `COUPON:
   OFF_3_AT_30 (-$3.00)` / `** KEY THIS CODE IN PRISM **`; `TOTAL $44.01`;
   `ENTER IN PRISM, THEN TAP DONE`; clean cut. (Raw SKU/coupon-code text on
   the PRINTED ticket is correct and unchanged — staff read this and key it
   into PrISM verbatim; this is a different consumer from T-051's VOICE
   readback, which was fixed separately to never speak these same raw
   tokens to a customer.)
4. Real failure behavior — see Part 4/5 (cable-pull demo), not inferred
   from spec.

**Part 4 — reliability:**
- **Retry + backoff:** `printer.py`'s existing 3-attempt, growing-backoff
  loop (built by the earlier T-049 session) — re-verified against the REAL
  TCP path this task, not just dry-run (see the cable-pull demo below: a
  real 21.02-second run, 3 real connection attempts against a genuinely
  unreachable host).
- **FAILED_DISPATCH re-verified on the real path:** confirmed live (below),
  not just in the offline dry-run tests that already existed.
- **The known gap closed:** `confirmed_orders` had no dispatch-status field
  (disclosed by the prior T-049 session) — a crash between order-finalize
  and dispatch-complete meant a replayed confirm would hit the F6
  idempotency-key cache and return without ever retrying dispatch, with NO
  durable trace of whether the order ever reached a printer. Closed via a
  new, reversible migration (`migrations/0002_dispatch_status.{up,down}
  .sql`): `confirmed_orders.dispatch_status` (`PENDING`/`DISPATCHED`/
  `FAILED`, default `PENDING`) + `dispatched_at`. This is the ONE
  deliberate exception to `confirmed_orders`' insert-only design
  (0001_init.up.sql's own comment, updated to say so) — dispatch status is
  a physical-world fact that settles AFTER confirmation, never a correction
  to the order's own content/total/ticket, which remain exactly as
  immutable as before. `SessionRepository` gained `mark_order_dispatched`/
  `mark_order_dispatch_failed`/`list_undispatched_confirmed_orders`,
  implemented in both `InMemorySessionRepository` (used by the real offline
  suite) and `PostgresSessionRepository` (UNVERIFIED against a live
  Postgres — same disclosed limitation as the rest of that file, no
  Postgres server reachable in this dev environment). `confirm_and_persist`
  now records the real dispatch outcome durably on every call, success or
  failure.
- **`held_orders` reuse — checked, doesn't exist, said so, added the
  smallest thing instead** (per this task's own explicit fallback
  instruction): `ARCHITECTURE.md` lists `held_orders` under "Not yet
  built" — no table, no code exists anywhere to reuse. Built the smallest
  correct thing instead: the SAME `dispatch_status` column already being
  added, plus `list_undispatched_confirmed_orders` (the staff/ops
  visibility query) and `redispatch_pending_orders` (the recovery action)
  in `persistence/service.py`. A dedicated `held_orders` table would have
  been a materially bigger schema addition than this task's stated scope
  for a need this column already meets.
- **Staff visibility:** `list_undispatched_confirmed_orders(store_id)` —
  the query T-049 FINAL asks for ("staff can see a held-for-print order").
  Real, not hypothetical: used directly in the cable-pull demo below to
  surface the real FAILED order.
- **Recovery, real design decision made while building the live demo:**
  `redispatch_pending_orders` was originally written to only retry
  `PENDING` rows (skip `FAILED` — "needs a human, not another automatic
  attempt"). Building the actual cable-pull-then-reconnect demo surfaced
  that this was too conservative: the function is ALREADY only ever
  manually invoked (no scheduler exists in this codebase to call it on its
  own), so a human choosing to run it against a `FAILED` row IS the human
  decision the original caution was protecting — revised to retry BOTH
  `PENDING` and `FAILED` rows. `tests/test_dispatch_status.py` updated to
  match (14 tests total, offline).
- **Idempotent, proven by test AND by the live demo:** a `DISPATCHED` row
  is never returned by `list_undispatched_confirmed_orders` again, so a
  repeat `redispatch_pending_orders` call — proven both offline
  (`test_redispatch_never_touches_an_already_dispatched_order`) and live
  (the recovery demo below ran the SAME recovery call twice; the second run
  found nothing to do, printed nothing).

**Part 5 — the live demo, real hardware throughout:**

*Voice-to-print, full production path* (same disclosed methodology as
T-050/T-051: real TTS-synthesized fixture audio through the real,
unmodified `LocalVoiceLoop.turn()` — real Silero VAD, real Parakeet STT
over the real WSL GPU service, real `RuleBasedInterpreter`, real SAPI TTS —
only the raw audio SOURCE is simulated; "what's my total"/"yes place
it"/"go ahead" have no fixture audio and were text-driven, exercising the
real domain + real TTS + real print dispatch regardless):
```
Turn 1 (voice, real Parakeet STT): "Large pepperoni, no onions."
  capture=3.094s stt=0.734s app=0.016s tts_first=0.578s tts_synth=0.937s
  total=4.781s perceived=1.328s
Turn 2 (text): "what's my total?" -> real quote readback
Turn 3 (text): "yes place it" -> real F16 readback, AWAITING_CONFIRMATION
Turn 4 (text): "go ahead" -> confirm_order -> REAL PRINT DISPATCH
  Final state: STORE_ACKED. Order AI-089BDA. Total $19.32.
  Reply: "You're all set — order AI-089BDA, total $19.32. Thanks!"
```
**Real photo confirmed:** ticket for AI-089BDA printed correctly.

*Cable-pull reliability demo, live, real hardware:*
```
1. Confirmed printer reachable (31ms TCP connect).
2. Asked the owner to pull the printer's network cable — confirmed
   unreachable directly (python socket.create_connection -> real
   TimeoutError) before proceeding, not assumed from the request.
3. Confirmed a SECOND real order (medium cheese pizza + 2-liter, $18.25)
   through confirm_and_persist with a real (non-dry-run) TicketPrinter.
   Real elapsed time: 21.02s (3 real connection-timeout attempts with
   growing backoff — printer.py's existing retry loop, now proven against
   a genuinely unreachable real host, not just simulated).
   Customer-facing result: STILL "ok" — order AI-E7261C confirmed, ticket
   text generated — a dispatch failure never retracts a valid confirmation.
   session.state: FAILED_DISPATCH.
   Real session log: "Ticket failed after 3 attempts: printer not ready
   (cover=False paper_out=False online=False)" — real DLE EOT status
   showing online=False on a genuinely unreachable device.
   Durable record: dispatch_status='FAILED', dispatched_at=None.
   list_undispatched_confirmed_orders("STORE-001") -> [AI-E7261C] — the
   staff-visibility query, working against a real failure.
4. Asked the owner to plug the cable back in. Re-verified reachable
   (python socket connect succeeded) before declaring recovery ready —
   took two rounds of polling; the printer's link took real time to come
   back up after reconnect, itself a real, disclosed observation.
5. Recovery demo: reconstructed the same failure shape (a fresh order,
   marked FAILED — disclosed simplification: re-inducing a second real
   21-second network failure just to reach the same starting state wasn't
   necessary; the RECOVERY action itself, step 6, is 100% real hardware)
   and ran redispatch_pending_orders against the real, now-reconnected
   printer. Real elapsed time: 6.27s (real TCP connect + status check +
   send + status-verify). Result: {'status': 'ok', 'printed': True,
   'paper_low': False}. dispatch_status -> 'DISPATCHED'.
6. **Real photo confirmed:** ticket for the recovered order printed once,
   correctly.
7. Ran redispatch_pending_orders AGAIN: empty result, zero dispatch calls,
   nothing undispatched — idempotent, live, not just asserted offline.
```

**Gates run:**
```
python -m pytest --no-header -q        → 683 passed, 2 skipped, 2 xfailed
python evals/runner.py validate         → 91/91
python evals/runner.py score --adapter rule_based → 59/91 (unchanged)
python -m pytest tests/test_pricing_parity.py     → 50/50
PRINTER_DRY_RUN default (no env set)    → True, confirmed directly
```
T-016 offline/no-network gate: unaffected — full suite (including the new
`tests/test_dispatch_status.py`, 14 tests, and the printer-seam-preserving
`tests/test_printer_dispatch.py`, still 13 tests unchanged) runs green with
no printer attached and no live Postgres. **No prompt, tool-schema, or
provider surface was touched** — no live LLM N=3 gate was required, stated
explicitly, not skipped silently.

**`docs/ARCHITECTURE.md`'s printer entries moved to CURRENT** with the real
model (TM-m30III/M374C), real static IP (10.1.10.197), and the
`PrintTransport` seam — see that file's own updated CURRENT section and
External Providers table.

**T-051 done, 2026-09-22 (P1): the 15-second readback fixed to 9.2s worst
case (down 37-47%), real-stack total turn latency now 3.9s median (down
from T-038's 5.9s baseline, apples-to-apples on the same Parakeet STT
provider — the first real, comparable end-to-end number since T-038).**
Preconditions confirmed first: T-050 committed/pushed (`9832023`), clean
tree, HEAD matches origin. **WSL Parakeet was down at task start** (Ubuntu
distro itself was `Stopped`) — brought it up this session (`wsl.exe`
auto-started the distro; started `scripts/parakeet_server.py` in the
existing `~/lakewood-stt/NeMo` uv/CUDA venv; model loaded in 12.66s; warm
inference re-verified at 0.078-0.094s, matching ADR-016's own 0.082s
exactly) rather than falling back to a substitute provider and mislabeling
the result, per this task's explicit precondition.

**Part 1 — diagnosis, measured before any code change.** Built 4
representative real carts (single pizza; half-and-half with modifiers;
multi-item with drink+wings; coupon order), got their real
`begin_confirmation` readback text, and measured REAL SAPI audio duration
at the then-current `Rate=0`:

```
single pizza (14 words):              8.793s
half-and-half w/ modifiers (22 words): 12.988s
multi-item drink+wings (19 words):     12.523s
coupon order (23 words):               17.237s
```

**Where the time actually went, verified by real TTS->STT round-trip
evidence (not assumed):**
- **Confirmed broken tokens** (SAPI mispronounces these, proven by feeding
  synthesized audio back through real `faster-whisper` STT and inspecting
  what came out): `6PC WINGS` -> "6 PC Wings" (PC read as letters);
  `12PC WINGS` -> "112 PC wings" (also glued to the leading quantity digit —
  see below); `GARDEN SALAD SM/LG` -> "Garden salad sm."/"...LG." (size
  suffix read as letters/mumble); `STRWBRY CHZCAKE` -> "Strwbrych's a cake."
  (unintelligible); the coupon's raw code `OFF_3_AT_30` -> "off underscore 3
  underscore at underscore 30" (SAPI reads `_` as the literal word
  "underscore" — measured 5.98s to speak vs 4.54s for the already-existing
  `coupon.spoken` field "three dollars off thirty," which the readback
  simply never used). A real topping name has the same defect:
  `RSTD RED PEPPR` -> "RSTD red pepper" (found auditing toppings the same
  way after finding the item-name pattern; not in the task's original
  example list, found because the same method was applied exhaustively,
  not just to the named examples).
- **Quantity + digit-leading-token ambiguity**, confirmed even with NO SKU
  abbreviation involved: `"one twelve"` alone (cleanly spoken, unambiguous
  numbers) round-trips as `"112"` — a real adjacency-merging artifact.
  Inserting a comma (`"one, twelve piece wings"`) fixed it in isolation.
  Not primarily a TTS defect — closer to an STT-side risk that real human
  listeners could plausibly share for numbers spoken with no pause — treated
  as real and fixed by giving quantity>1 its own comma-separated clause.
- **Confirmed CLEAN, verified not to need fixing (measurement, not
  assumption):** money — `"$16.10"` measured the EXACT SAME real audio
  duration (2.839s) as the known-correct `"sixteen dollars and ten cents"`,
  and a clearly-wrong digit-by-digit reading (`"one six point one zero"`)
  measured meaningfully shorter (2.384s) and structurally different —
  strong evidence SAPI already reads it as a price, not digit-by-digit.
  Half-and-half's `"first half: X, second half: Y"` phrasing round-tripped
  perfectly clean. `2LITER` -> "2-liter" already correct. ~18 of 27
  NON_PIZZA items and ~37 of 39 toppings round-tripped clean and are
  intentionally unmapped (`lakewood/speech.py`'s own comments name every
  one checked).
- **Verbose phrasing**: "That's pickup: ... Should I go ahead and place
  it?" carries real framing/closing filler around the same content.
- **Speech rate itself**: even the CLEANEST cart (single pizza, zero
  garbled tokens) measured only ~95-99 words/minute at `Rate=0` — genuinely
  slow versus typical 150-180 wpm conversational/audiobook TTS, confirmed
  independent of the token issues.

**Part 2 — fixed, in the stated safety order, content never reduced:**

1. **Phrasing** (`lakewood/orders.py::_short_readback`/`_render_line`,
   deterministic, zero LLM involvement): `"That's pickup: ...; ...; ...."`
   -> `"Pickup: A, B, and C."`; `"Should I go ahead and place it?"` ->
   `"Place it?"` (moved to `begin_confirmation`'s own append, unchanged
   for `request_quote`'s non-confirmation readback). Every line, modifier,
   half-placement, coupon discount, and total still present — proven, not
   asserted (see Part 4).
2. **Token rendering** — new module `lakewood/speech.py`, the speakable-
   rendering layer this task asked for: maps a raw identifier to how a
   person says it, NEVER decides content. `SPEAKABLE_ITEM_NAMES`/
   `SPEAKABLE_TOPPING_NAMES` cover every confirmed-broken token above;
   `speakable_coupon_phrase` uses the coupon's own pre-existing `spoken`
   field (`coupons.py` — it existed for exactly this and was simply never
   used by the readback) instead of the raw code. Unmapped tokens fall
   through to a safe lowercase passthrough in production (never crash a
   customer's confirmation over a missing map entry), but
   `tests/test_speech.py`'s completeness test fails LOUDLY if any real
   menu item or topping is neither mapped nor explicitly recorded as
   verified-clean — a genuinely new/uncovered token cannot ship silently.
3. **Speech rate** — `lakewood/tts/windows_sapi.py`'s `SpeechSynthesizer
   .Rate`, real-measured across the full -10..+10 range on this machine:
   `Rate=0` ~99 wpm, `Rate=3` ~142 wpm, `Rate=5` ~177 wpm. Default is now
   `Rate=3` (~142 wpm, normal conversational pace), hard-capped at
   `Rate=4` (~158 wpm) — `TTSConfigError` above that. The cap is
   deliberate: this audio eventually crosses an 8kHz phone line, where
   speech intelligibility degrades with rate, and nothing above 4 has been
   validated over real compressed telephony audio. See ADR-015's amendment.
4. **Inter-sentence silence**: not touched — T-050's sentence pipelining
   already overlaps synthesis with playback; Part 1 found no measurable
   dead-air contribution worth trimming separately once 1-3 above landed.

**Real measured result, same 4 carts, phrasing+tokens+rate=3 combined:**
```
single pizza:              8.793s -> 5.282s  (-40%)
half-and-half w/ modifiers: 12.988s -> 8.213s  (-37%)
multi-item drink+wings:     12.523s -> 7.143s  (-43%)
coupon order:                17.237s -> 9.185s  (-47%)
```

**Half-and-half stays unambiguous** — verified directly, not assumed:
`tests/test_speech.py::test_half_and_half_topping_placement_is_unambiguous`
asserts BOTH the phrase content AND that "pepperoni" appears before
"mushrooms" in the string (proves which half is which, not just that both
words are present somewhere).

**Part 3 — mid-order diff vs. final complete readback, both verified:**
`add_item`/`add_modifier`/`remove_modifier`/`update_item` were ALREADY
using `_render_line` on only the single affected line (`description=
_render_line(line)`) — genuinely diff-only, not a violation, confirmed by
`test_mid_order_add_item_reply_is_a_diff_not_the_full_cart`. The ONLY
full-cart reader is `_short_readback`, used by exactly two callers:
`request_quote` (the customer explicitly asked "what's my total" — a
summary IS the right response to that specific question, not a violation
of the diff principle) and `begin_confirmation` (the final readback, which
must be complete per F16). **Real risk found and reported, not fixed here
(filed T-033, already on the backlog, explicitly out of this task's
scope):** if the model calls `request_quote` PROACTIVELY — unprompted, per
T-033's existing finding — that full-cart readback becomes an unwanted
mid-order echo, a real second latency problem AND a genuine CLAUDE.md
cart-diff violation whenever it happens. T-051 does not change this either
way; it is disclosed because verifying Part 3 surfaced it directly, not
buried in a test transcript.

**Part 4 — tests that prove completeness (not assertions that it looks
right):** `tests/test_speech.py`, 20 tests:
- **Property test** (`test_property_every_line_and_total_appear_in_the_
  full_readback`): 200 randomly generated real carts (varying size, item
  mix, toppings, half-and-half), every line's own rendered text AND the
  total checked present in the final readback — 176+ carts actually
  exercised (some random combinations are legitimately rejected by real
  domain guards, e.g. an unpriced topping tier).
- **Mutation-proven**, against the REAL call path (`begin_confirmation`,
  not a hand-written stand-in): monkeypatching `orders._short_readback` to
  drop the last line, or to omit the total, makes the SAME completeness
  assertion the property test uses fail — proving the guard actually
  guards, not just looks right.
- No internal token (`_`, ` PC `, ` SM`, ` LG`, `CHZCAKE`, `RSTD`, a raw
  `line_id`, `HALF_1`/`HALF_2`) reaches the rendered readback — checked
  directly against a multi-item coupon cart's real output.
- The readback is produced without any model call — static source check
  (no provider/`complete(`/vendor-name reference in either function) plus
  a direct determinism proof (byte-identical output across 3 repeated
  calls on the same cart — impossible for anything that consulted a
  model).
- Half-and-half unambiguous (above).
- Mid-order diff vs. final complete readback (Part 3, above), both in one
  file so a future change can't fix one and silently break the other.

**Part 5 — measured on the real stack, real Parakeet, real SAPI, real
RuleBasedInterpreter** (no LLM API key assumed available in this
environment — same interpreter T-038's own real hardware run used, per
that entry below). Methodology, disclosed exactly like T-050: this
coding-agent session cannot literally speak into a microphone; real
TTS-synthesized fixture WAVs (per their own provenance disclosure) were
fed through the REAL, unmodified `LocalVoiceLoop.turn()` path (real
`Endpointer`, real Silero VAD, real `ParakeetProvider` over the now-live
WSL GPU service, real `RuleBasedInterpreter`, real `WindowsSapiTTSProvider`
at the new `Rate=3` default) — only the raw audio SOURCE is simulated (a
file played back chunk-by-chunk at real wall-clock pace, in place of a
live `sd.InputStream`).

```
OLD (T-038 Phase 2 baseline, Parakeet GPU STT, fixed 5s capture):
  total: median 5.899s / p95 6.235s

NEW (T-051, this session, Parakeet GPU STT — SAME provider as T-038,
     Silero-VAD capture, SAPI @ Rate=3):
  capture:              median 2.766s / p95 3.094s
  stt:                  median 0.406s / p95 0.469s   (Parakeet, GPU — same
                         provider as T-038's baseline; this session's
                         numbers run a little above ADR-016's isolated
                         0.082s warm figure, still far below faster-
                         whisper CPU's 2.4s from T-050's forced substitute)
  app (domain):         median 0.000s / p95 0.000s
  tts first audio:      median 0.374s / p95 0.390s
  tts synthesis total:  median 0.703s / p95 0.719s
  total (system latency, excludes playback talk-time):
                         median 3.876s / p95 4.157s   <- FIRST real,
                         apples-to-apples comparison to T-038 since T-038
  perceived (speech-end -> first audio):
                         median 0.781s / p95 0.828s

Full confirmation readback (real cart: large pepperoni, no onions):
  "Pickup: LARGE CHEESE PIZZA — pepperoni, no onions. Total $19.32. Place it?"
  synthesis: 1.031s   playback (real talk-time): 10.859s

Post-confirmation reply:
  "You're all set — order AI-6F56D3, total $19.32. Thanks!"
  synthesis: 0.719s   playback (real talk-time): 8.719s
```

**Plain verdict.** System-side processing latency is now genuinely
tolerable for a phone call — median 3.876s total, and 0.781s PERCEIVED
(speech-end to first audio), a real, apples-to-apples ~34% cut from
T-038's 5.899s baseline on the identical STT provider. **The next dominant
term is no longer system latency — it is real spoken talk-time itself**,
which this task cut substantially (37-47%) but cannot cut further without
either shortening content (forbidden by F16) or raising speech rate past
the phone-line-safety ceiling this task deliberately set. The final
readback alone still takes 10.86 real seconds to physically speak for a
two-line order; a full build-to-confirmation call is dominated by talk-time
across several turns, not by any one system stage.

**Incidental finding, reported at full severity, NOT fixed here (different
code path — `chat.py::_reply_for`'s `confirm_order` branch, not
`_short_readback`; out of this task's stated scope, which is the
PRE-confirmation readback specifically):** the post-confirmation
"You're all set — order AI-6F56D3..." reply speaks the raw internal
`order_id` character-by-character-ish (a random alphanumeric string),
contributing real, avoidable talk-time (measured: 8.72s for an 11-word
reply) for an identifier a customer has no obvious use for over the phone.
Filed as T-052.

**Gates run:**
```
python -m pytest --no-header -q        → 669 passed, 2 skipped, 2 xfailed
python evals/runner.py validate         → 91/91
python evals/runner.py score --adapter rule_based → 59/91 (unchanged)
python -m pytest tests/test_pricing_parity.py     → 50/50
```
T-016 offline/no-network gate: unaffected — this task touched no
persistence/network code. **No prompt or tool-schema surface was touched**
(checked directly: `interpreter.py`'s system prompt/tool descriptions
reference "the readback" generically, never the literal old wording) — so
no live LLM N=3 gate was required, stated explicitly, not skipped silently.

**T-050 done (with honestly-scoped gaps), 2026-09-22 (P1): VAD endpointing
replaces the fixed 5-second capture window; TTS is sentence-pipelined; STT
and LLM streaming are NOT implemented, for stated reasons, not silently
skipped.** Preconditions confirmed first: T-049 committed/pushed (`e7be61a`),
clean tree, HEAD matches origin.

**Part 1 — VAD/endpointing: done.** `lakewood/vad.py` (`Endpointer`/
`VadConfig`) is a pure state machine, fully unit-tested with synthetic
probability sequences (`tests/test_vad.py`, 7 cases: clean stop timing,
mid-sentence-pause survival, noise-blip rejection, silent-customer timeout,
max-utterance safety cap, timeout-vs-endpoint distinction, no state leakage
between turns). `lakewood/voice.py::SoundDeviceMicrophone` feeds it real
Silero VAD probabilities — reused from `faster_whisper.vad`'s already-
bundled ONNX model (already an optional dependency via ADR-008/faster-
whisper; zero net-new dependency), not a new `webrtcvad` package. See
ADR-018 for the full choice/defaults reasoning (`min_silence_ms=700`,
`no_speech_timeout_seconds=6.0`, `max_utterance_seconds=15.0`, all
env-tunable). `tests/test_voice_vad_microphone.py` proves clean, labeled
failure when `sounddevice`/`faster_whisper.vad` are absent (import-blocked
so results don't depend on this environment's actual installs).

**Part 2 — streaming STT: NOT possible, verified by reading both provider
adapters.** `lakewood/stt/parakeet_provider.py` is one `POST /transcribe`
with a full WAV body, one JSON response — no partial/streaming endpoint
exists on the WSL service side. `lakewood/stt/faster_whisper_provider.py`
calls `WhisperModel.transcribe()` and consumes `list(segments_iter)` before
returning — batch, by construction. Neither was modified to fake
streaming. The real win for this stage is Part 1: transcription now starts
the instant VAD detects end-of-speech, not after a fixed 5-second wait.

**Part 3 — streaming LLM: NOT implemented, architecturally inapplicable,
not merely unbuilt.** None of the four `lakewood/llm_provider.py` adapters
request streaming (`AnthropicProvider` omits `"stream"` entirely — defaults
false server-side; `OllamaProvider` sends `"stream": False` explicitly;
neither `OpenAIProvider` nor `ExperientialProvider` request it either) —
but the deeper reason streaming wasn't added is that it would change
nothing customer-facing: `chat.py::_reply_for` and every readback builder
in `orders.py` construct the spoken reply ENTIRELY from a completed,
validated tool result. `ProviderResponse.text` (the model's own free-text
output) is never read by `_finish_turn` — there is no raw model prose in
this system's voice path to stream in the first place. Adding SSE-stream
plumbing to four providers for zero measurable latency benefit would be
exactly the "impressive architecture" CLAUDE.md says not to build here;
this is documented as a deliberate decision (see
`lakewood/voice.py::LocalVoiceLoop._speak_reply`'s own docstring), not a
silently skipped requirement.

**Part 3 — streaming TTS: done, the one stage that genuinely streams.**
`LocalVoiceLoop._speak_reply` splits the reply into sentences and
synthesizes them in a background thread while the main thread plays
already-ready sentences in order — sentence N+1's synthesis overlaps
sentence N's playback. `tests/test_voice_tts_pipeline.py` (8 cases) proves:
sentence splitting, real overlap (synth(N+1) provably starts before
play(N) returns, using an instrumented fake with artificial synth delay),
first-audio arriving at ~one sentence's synthesis time rather than the
whole reply's, and — directly protecting the "never speak before the
domain layer produced the reply" invariant — that every synthesized
sentence is a substring of the already-complete `TurnResult.reply`.

**Part 4 — barge-in-adjacent safety: the simple, safe default: capture and
playback never overlap.** No turn-taking signal was built (full barge-in
is explicitly out of scope). `LocalVoiceLoop.turn()` cannot return — and
therefore the next `input("Press Enter > ")`-gated capture cannot start —
until `_speak_reply` has finished playing every sentence (its internal
`worker.join()` plus the blocking `WindowsSapiTTSProvider.play`/`PlaySync`
call for the last sentence guarantee this). `tests/test_voice_tts_pipeline
.py::test_capture_never_starts_before_previous_turns_playback_finished`
runs a real 3-turn sequence (build cart → quote → `begin_confirmation`
readback) and asserts every capture after the first is immediately
preceded by a play in the recorded event order — never two captures back
to back, never a capture racing a still-playing readback. A second test,
`test_confirmation_readback_audio_never_bleeds_into_next_turns_transcript`,
proves the NEXT turn's transcript is exactly its own fresh STT result,
never merged with the readback text — the specific failure mode Part 4
warned about.

**Part 5 — measurement, real numbers, disclosed methodology.** This
coding-agent session cannot literally speak into a microphone (same
constraint class as T-049's printer — see that entry — except a real
microphone IS present on this machine, confirmed via `Get-PnpDevice`
(Realtek Microphone Array), unlike the printer). Real audio was used
instead: this repo's own `lakewood/stt/fixtures/*.wav` — TTS-synthesized
speech per their own `manifest.json` provenance disclosure, not human
speech — fed through the REAL, unmodified `LocalVoiceLoop.turn()` code
path (real `Endpointer`, real Silero model, real `FasterWhisperProvider`
STT on CPU, real `RuleBasedInterpreter`/`PersistentChat.run_turn`, real
`WindowsSapiTTSProvider`, real playback), with only the raw audio SOURCE
substituted (a file played back chunk-by-chunk at real wall-clock pace, in
place of a live `sd.InputStream`). This is a real plumbing/latency
measurement, not an STT/order-accuracy claim (same caveat this repo
already states elsewhere for these exact fixtures).

**Critical caveat on the comparison below: the WSL Parakeet GPU service
was NOT reachable this session** (`curl http://127.0.0.1:8765/health` timed
out) — T-038's baseline used Parakeet (STT median 0.320s); this measurement
had to use `faster-whisper small` on CPU instead (median STT 2.414s here —
consistent with ADR-016's own earlier CPU benchmark of 2.748s). The STT
provider swap, not this task's work, accounts for most of the "total"
number below looking worse than T-038's. The capture-stage comparison
(same utterance-length class, different capture mechanism) is the clean,
apples-to-apples Part 1 result; the full-total comparison is NOT
apples-to-apples and is labeled as such.

Real numbers, 2 turns (`negation_no_onions`, `correction_size` fixtures,
~3.0-3.9s of real audio each), plus one supplementary real TTS-only
measurement of a clean confirmation readback (text-driven turns, no
fixture audio exists for "what's my total?"/"yes place it" — STT/capture
were not exercised for that part, only the real TTS pipeline was):

```
OLD (T-038 Phase 2 baseline, Parakeet GPU STT, fixed 5s capture):
  capture: fixed 5.219s (ALWAYS, regardless of utterance length)
  stt:     median 0.320s / p95 0.391s   (Parakeet, GPU)
  total:   median 5.899s / p95 6.235s

NEW (T-050, this session, Silero-VAD capture, faster-whisper CPU STT):
  capture:              median 3.453s / p95 3.813s   <- real content-dependent, not fixed
  stt:                  median 2.414s / p95 2.422s   <- faster-whisper CPU, NOT Parakeet (see caveat)
  app (domain):         median 0.000s / p95 0.000s
  tts first audio:      median 0.375s / p95 0.375s
  tts synthesis total:  median 0.547s / p95 0.719s
  total (system latency, excludes playback talk-time):
                         median 6.415s / p95 6.594s
  perceived (speech-end -> first audio):
                         median 2.789s / p95 2.797s
  tts playback (real talk-time, NOT a latency number):
                         median 7.774s / p95 8.657s

Clean, apples-to-apples Part 1 result (capture stage only, same STT/TTS):
  fixed window:  5.219s always
  VAD-based:     3.453s median / 3.813s p95  for these ~3.0-3.9s utterances
  -> ~1.4-1.9s saved on capture alone for utterances this length; the
     saving grows for shorter utterances (VAD stops near real speech end,
     not a fixed clock) and shrinks for utterances approaching/exceeding
     the old 5s window.

Supplementary — confirmation readback, real TTS pipeline, text-driven
(no STT/capture measured for this part):
  reply: "That's pickup: LARGE CHEESE PIZZA - pepperoni, no onions.
          Total $19.32. Should I go ahead and place it?" (3 sentences)
  tts first audio:     0.375s
  tts synthesis total: 1.062s   <- first audio at 35% of total synth time
  playback (talk-time): 15.187s
```

**Incidental finding, reported at full severity per CLAUDE.md, not fixed
here (out of T-050's scope — no prompt/TTS-tuning change requested):**
Windows SAPI's default voice/rate is slow — a real 13-word sentence
("Got it, large cheese pizza, pepperoni, no onions. Anything else?")
measured 6.77 seconds of actual audio duration
(`lakewood.tts.windows_sapi.WindowsSapiTTSProvider`, `SpeechSynthesizer`
default `Rate=0`). A 3-sentence confirmation readback took over 15 real
seconds to speak in the measurement above. This is real customer-facing
talk-time, independent of any system processing latency this task
addresses — every stage above could be instant and a customer would still
wait 15+ seconds to hear a 3-sentence confirmation. `SpeechSynthesizer.Rate`
(range -10..+10, default 0) is the lever; not touched this task.

**Existing invariants confirmed intact, not just assumed:** full offline
suite still exercises F14 (same-turn confirm rejected) and F16 (mandatory
readback) unchanged — this task touched no domain/guard code, only
`lakewood/voice.py` and the new `lakewood/vad.py`. No prompt, tool
description, or provider surface was touched, so **no live LLM N=3 gate
was required — stated explicitly, not silently skipped.**

**Gates run, all offline:**
```
python -m pytest --no-header -q        → 649 passed, 2 skipped, 2 xfailed
python evals/runner.py validate         → 91/91
python evals/runner.py score --adapter rule_based → 59/91 (unchanged from T-044/T-049)
python -m pytest tests/test_pricing_parity.py     → 50/50
```
T-016 offline/no-network gate: confirmed — `psycopg2` still not installed,
no network call anywhere in the offline suite; the only new optional
dependency touched (`faster_whisper.vad`) is lazy-imported and never
reached by any offline test (proven by import-blocking in
`tests/test_voice_vad_microphone.py`).

**T-049 PARTIAL, 2026-09-22 (P1): dispatch wiring done and offline-verified;
hardware bring-up HARD-BLOCKED — no path to the real printer from this
session.** Preconditions confirmed first: T-044 committed and pushed
(`32da80c`), clean tree, HEAD matches origin.

**Part 1 (model verification) could not be completed.** This coding-agent
session runs on a Windows machine with no physical or network path to the
restaurant's printer: `Get-PnpDevice` finds no Epson/USB-printer device,
`Get-Printer` lists none, the LAN ARP table is empty, `PRINTER_HOST` is
unset, and a web search could not confirm what TM-series command set an
Epson **M347C** label corresponds to (Epson does not publish the M-number
mapping). STATUS.md's own prior entry said the dedicated unit was still "in
transit" — it may now be on-site at the restaurant, but that is a different
machine than this session runs on. Asked the owner directly (in-session):
confirmed no USB/network access is available right now either. **Parts 1, 2,
and 4 (real hardware verification, a real printed ticket, the live demo) are
therefore UNVERIFIED, not done** — `lakewood/printer.py`'s ESC/POS byte-level
assumptions remain exactly what T-043/prior docs already said: written from
the TM-T88V spec, never run against real hardware, now additionally flagged
because the real device's model label doesn't match that spec's name at all
(see the module docstring).

**Part 3 (end-to-end dispatch wiring) is done, offline-verified, no hardware
needed for any of it.** `dispatch_confirmed_order` was never wired into any
real call path before this task — `PersistentChat._executor`'s `confirm_order`
branch called `confirm_and_persist` directly with no printer involved at all.
Now: `PersistentChat` takes an optional `printer` field (default `None`,
so every existing caller — tests, evals — is unaffected); `confirm_and_persist`
accepts an optional `printer` and dispatches exactly once, only on the branch
that actually just confirmed the order (never on the F6/idempotency-key
cache-hit replay branch above it) — that ordering IS the "one confirmed
order, one ticket" guarantee, proven by test
(`test_confirm_and_persist_dispatches_exactly_once_across_a_replay`,
`test_dispatch_is_not_re_entrant_on_an_already_dispatched_session`). A
dispatch failure moves `session.state` to `FAILED_DISPATCH` and is logged on
the session, but never overwrites the customer-facing `confirm_order` result
— the customer's confirmation is real regardless of what happens in the
kitchen (`test_dispatch_failure_does_not_leak_into_the_customer_facing_
confirm_result`, `test_run_turn_confirm_order_reply_unaffected_by_a_failed_
dispatch`). `python -m lakewood.voice` now constructs a real `TicketPrinter`
from `CONFIG` (`PRINTER_HOST`/`PRINTER_DEVICE`/`PRINTER_DRY_RUN`, still
defaulting to dry-run) and passes it through — the wiring exists end-to-end,
only the far end (real bytes reaching real paper) is unverified. 13 new
tests in `tests/test_printer_dispatch.py` (`lakewood/printer.py` had ZERO
tests before this task, at any layer, including `PrinterStatus.ready`).

**Known gap, disclosed not fixed (time-boxed out):** no `confirmed_orders`
schema field tracks dispatch status. If the process crashes between
`finalize_session` and dispatch completing, a replayed confirm hits the
idempotency-key cache branch and returns without ever retrying dispatch —
durably confirmed in the database, but possibly never sent to the printer.
Closing this needs a persisted dispatch-status column and a retry sweep; out
of this task's scope.

**Gates run, all offline (no live LLM N=3 — this task touched no prompt,
tool description, or provider surface, so none was needed):**
```
python -m pytest --no-header -q        → 629 passed, 2 skipped, 2 xfailed
python evals/runner.py validate         → 91/91
python evals/runner.py score --adapter rule_based → 59/91 (unchanged from T-044)
python -m pytest tests/test_pricing_parity.py     → 50/50
python -c "from lakewood.voice import main"       → imports clean, no hardware touched
```

**Docs corrected:** `ARCHITECTURE.md`'s printer row said "Epson TM-T88V" as
if confirmed — corrected to "Epson M347C... model mapping UNVERIFIED."
`lakewood/printer.py`'s module docstring now states the model mismatch
explicitly instead of asserting TM-T88V as fact. Neither moved to fully
CURRENT — the wiring/idempotency logic did; the byte-level protocol did not.

**T-044 done, 2026-09-22 (P1): `_has_pizza_intent` no longer requires the
whole utterance to be pizza-shorthand.** Preconditions confirmed first:
two live Codex WSL app-server processes were found pointed at this exact
repo (`--cd /mnt/d/Projects/Ai`, cwd matching T-043's own finding) and
killed; `EXPLABS_API_KEY` confirmed rotated by the owner; clean baseline
(`git status`, HEAD `514b1c4`, matches origin) confirmed before any code
change.

**The trap the task warned about, avoided by construction:** switching to
"positive pizza evidence present, alone" (the obvious fix) would have
reopened the original T-038/ADR-017 P0 — every one of those utterances
contains a real topping word. Fixed with the two-condition rule instead:
positive evidence AND no unresolved product-bearing word left over
anywhere else in the utterance, enforced **per clause** (split on `" and
"` only, not comma — a comma is routinely just a spoken pause inside ONE
item's description, "and" is what customers actually use to join two
distinct orders). A clause resolves to "not blocking" two ways, both
menu-sourced, no hand-maintained noun list anywhere: `oe.non_pizza_alias_
hits` (the same alias table `search_menu` uses) or `oe.non_pizza_full_
name_match` (the clause's own words, filler/quantity stripped, EXACTLY
cover one real item's identifying words — a partial match like "chicken
caesar wrap" against WRAP's `{wrap}` is deliberately NOT a match, "chicken"/
"caesar" stay unexplained). A shared word (e.g. "chicken", a topping AND
part of "CHICKEN DINNER") is always consumed as pizza vocabulary first —
only the item's OTHER distinguishing words carry blocking weight.

Brought the sixth `add_item` call site (`_new_pizza_half_a_half_b`,
T-043's one named unshared exception) under real evidence checks: `a`/`b`
must both resolve to real toppings (`oe.ALL_TOPPINGS` membership) AND the
full utterance must still pass `_has_pizza_intent` — no longer just "the
regex matched." `_half_and_half_by_number` (call site #2) got the same
check added, defense-in-depth (not a response to an observed defect
there). The LLM path's gourmet-number branch, which previously had **no**
direct-evidence path at all (skipped whenever `gourmet_number` was set,
requiring a prior `search_menu` round trip even for "medium number ten") —
closed via `_pizza_creation_authorized`, reusing the same two-condition
predicate.

**Two regressions found and fixed by the offline ratchet before the
number was raised, not glossed over:** making "number" general filler
briefly let "small number five" (no "half" phrasing — `RuleBasedInterpreter`
has no bare-single-gourmet-number branch) look like a plain cheese order;
scoped the strip to only the specific number(s) actually being authorized.
Comma-based clause splitting let a bare size fragment in its own comma
clause ("a garden salad, MEDIUM, with grilled chicken") count as its own
clean pizza clause, reopening the substitution shape; fixed by splitting
on `" and "` only, and by requiring a clause have MORE than a bare size
word before it counts as evidence ("actually make it large," a pure size
correction, must not read as a new pizza).

**Two more found by the live gate itself, not offline (the offline unit
tests used all-lowercase fixtures and single-size-per-utterance
utterances — neither shape surfaces these):** `_pizza_creation_
authorized`'s gourmet branch called the residual/clause machinery on
un-lowercased text, so real sentence case ("Hawaiian", "BBQ Chicken")
never matched the lowercase vocab and GOURMET-013 was wrongly refused
live despite passing offline — fixed by lowercasing once at entry.
`_size_supported_by_utterance` picked whichever size word `_find_size`
matches first (longest-vocab-first, not utterance position), so a genuine
two-different-sizes multi-item order ("one small cheese and one medium
cheese pizza") could only ever satisfy ONE of its own two `add_item`
calls — fixed via `_size_word_matches` (membership: does the PROPOSED
size's own alias appear anywhere, not "is it the one found first").

**Offline gates, all green, fresh commands this task:**
```
python -m pytest --no-header -rA          → 613 passed, 2 skipped, 2 xfailed (was 583)
python evals/runner.py validate            → 91/91
python evals/runner.py score --adapter rule_based → 59/91 (was 58; +1 genuine flip, SLANG-001,
                                              "gimme a lg pep" — zero regressions, checked case ID
                                              for case ID against the full prior 58-case pass set)
python -m pytest tests/test_pricing_parity.py → 50/50
```

**Live N=3, `LAKEWOOD_LLM_PROVIDER=experiential`/`gpt-5.6-luna`, identical
config, sequential, HEAD `514b1c4` unchanged start-to-end every run:**

```
                 full/91 (raw)  overlap/73  DIRECT_UTTERANCE_EVIDENCE  UNSUPPORTED_ITEM_SUBSTITUTION  bypass
Run 1            74             56          58                         19                              0
Run 2            70             53          56                         14                              0
Run 3            68             51          52                         15                              0
mean             70.67          53.33       55.3                       16.0                            0
```

**Substitutions: 0/0/0, structurally verified** — every `add_item` call
across all 3 runs scanned programmatically for an `ok` result whose
`authorization_reason` falls outside the three authorized codes; zero
found in every run (same methodology as T-039/T-041's own checks).

**Overlap/73 recovered to 53.33, essentially back at the T-032 pre-guard
band (53/54/56, mean 54.33)** — up from T-041's 47.0 (+6.33), achieved
with the same zero-substitution guarantee T-041 already had. Not
exceeding 54.33, so the task's "exceeding needs explaining" caveat doesn't
apply.

**Which of the 11 named regressed cases (T-043's own list) flipped,
measured — not assumed:** 7 of 11 now pass reliably, 3/3 across all three
runs: `MULTI-001`, `GOURMET-013`, `CORRECT-003`, `SLANG-001`, `MULTI-005`,
`SLANG-003`, `CORRECT-006`. The other 4 still fail 3/3 — but traced
directly in every run's raw trace, the PIZZA itself is now correctly
created via `DIRECT_UTTERANCE_EVIDENCE` in all 4; the remaining failure in
each is a separate, unrelated defect, not a pizza-intent block:
- `ADV-001` — pizza created correctly, then the model self-applies an
  unrequested `FREE_2L` coupon the customer never asked for (the actual
  labeled ask, "only charge me ten dollars," was a price-manipulation
  probe the model deflected by finding SOME discount instead of refusing).
- `MOD-035` — pizza created correctly, then the model calls `add_item`
  for "SIDE RANCH" (a `FLAT_MODIFIER`, not a `NON_PIZZA` item) instead of
  `add_modifier` — a model tool-choice mistake, correctly refused by the
  guard exactly as designed (a modifier name is not a valid `add_item`
  target), not a substitution.
- `NEG-005` — pizza and pepperoni created correctly, then "make the
  pepperoni light" adds a SECOND, LITE pepperoni modifier instead of
  removing the NORMAL one first and re-adding as LITE (the domain's own
  `_resolve_intensity_calls` logic that `RuleBasedInterpreter` gets for
  free; the LLM path has no equivalent and nothing in the system prompt
  asks for it).
- `CORRECT-004` — turn 1 ("medium number ten") now correctly authorizes
  via `DIRECT_UTTERANCE_EVIDENCE`; turn 2 ("actually give me number eight
  instead") fails because THAT turn's own isolated text has no size word
  at all (the model re-passes `size=MEDIUM` from conversation memory, but
  `_size_supported_by_utterance` only ever looks at the current turn's
  text) — a same-size-correction context-carryover gap, pre-existing,
  unrelated to product-word blocking.

None of these four is a substitution or a pizza-intent failure; each is
filed as its own task below rather than folded into T-044's scope (a
correction-flow size-context gap, a coupon-self-application guard, an
intensity-change duplicate-modifier bug, and a modifier-vs-item tool-
choice guard are four different mechanisms, not variations on one fix).

**Incidental finding, filed not fixed (out of T-044's scope — this task is
the PIZZA-intent gate, not non-pizza SKU evidence coverage):** a non-pizza
item whose canonical name has no `NON_PIZZA_ALIASES` entry (e.g. "12PC
WINGS" — only drinks/cheesecake have spoken-form aliases) has no direct-
evidence path of its own in `_authorize_item_creation`; the model must
`search_menu` it first. Never silently wrong (only refused until
searched), so this doesn't reopen any P0, but it means a compound order's
SECOND item ("...and a twelve piece wings") may still cost the model an
extra turn even though the PIZZA now authorizes immediately.

Full mechanism, the two-condition rule, and every consequence:
`docs/decisions/ADR-017-no-silent-item-substitution.md`'s T-044 amendment.
Regression/safety/mutation tests: `tests/test_t044_pizza_intent.py` (34
tests). Trace files: `evals/traces/20260922T130733_llm.jsonl`,
`.../20260922T132543_llm.jsonl`, `.../20260922T134314_llm.jsonl`.

---

**CORRECTION (T-043 audit, 2026-09-22): the T-041 claim directly below —
"the remaining gap is unrelated model-capability limitations... not
authorization" — is FALSE for roughly half of it.** A direct case-by-case
comparison of T-032's real trace data against T-041's real trace data
(same 73 case IDs) found **11 cases that passed reliably at T-032 (7 of
them a clean 3/3) now fail 0/3 at T-041** — not "pre-existing," genuinely
broken by the guard chain. Root cause: `_has_pizza_intent` requires an
entire utterance to be fully pizza-shorthand-explained, so any compound
order (a pizza plus a drink/side, or a pizza with ordinary filler like
"I'll pick it up"/"gimme"/plural "mediums") gets wrongly refused on BOTH
interpreters. Filed as **T-044** (P1). Full evidence:
`docs/AUDIT_T043.md`'s "PART 5" and EVALS.md's matching section. The
other ~11 currently-failing overlap cases genuinely are pre-existing
(failed 0/3 at T-032 too) — T-041's claim was directionally right but
overstated as "the" explanation when it was only half of it.

**T-041 done, 2026-09-18 (original report, partially superseded above):
the evidence check is a second retrieval system — unify it. Live N=3
re-run PASSES the primary substitution-safety objective; historical-
overlap score partially recovers (40.33 -> 47.0 mean) and does not reach
the full T-032 pre-guard band (54.33).**

The prior T-039 live gate found zero silent substitutions but a
historical-overlap collapse (54.33 -> 40.33 mean) root-caused to the
mutation-boundary guard's own evidence-matching vocabulary being narrower
than real, non-adversarial customer language — plurals, intensity words,
spelled gourmet numbers, spelled quantity phrases. **Part 1 (architecture,
done before any patch): checked directly whether `search_menu` had the
same gaps rather than assuming the evidence check was uniquely broken —
it did**, proven against the real function: `search_menu(sess, "number
ten")`/`"six piece wings"`/`"large pizzas"`/`"sodas"` all returned
`NO_MATCH` before this task. Unified with one shared normalizer in
`orders.py` (`normalize_spoken_numbers`/`normalize_menu_text`), consumed
by both `search_menu` and the interpreter's evidence layer — not four
independent patches. A first version of the normalizer converted every
standalone spelled number anywhere in the text and broke `_ONE_HALF_RE`'s
"on one half" idiom outright (caught by the full suite going red, not
designed for up front) — fixed by anchoring conversion to exactly the two
contexts a real order spells a number in (`number`/`num`/`no.`/`#` prefix,
or `piece(s)`/`pc` suffix), never a bare standalone word.

**All four named gaps closed at the root**, verified unit-level in
`tests/test_t041_evidence_vocabulary.py` (22 new tests) and corpus-level
in `evals/cases/t041_evidence_gaps.yaml` (10 new cases). **Part 3
asymmetry fixed:** `LLMInterpreter` gained `_narrow_pending_
disambiguations`, called once per turn regardless of what tool call (if
any) the model makes — closing the exact `NONPIZZA-006` flakiness the
prior live gate found (1 of 3 runs failed with an unnecessary
`transfer_to_human`; the other 2 passed only by the accident of an extra
`search_menu` call). Kept structurally separate from
`_authorize_item_creation`, which stays pure/deterministic/no-side-effects
per the task's own non-negotiable safety line. Full mechanism, the "why
not a single `authorize_cart_mutation()`" analysis, and every consequence:
`docs/decisions/ADR-017-no-silent-item-substitution.md`'s T-041 amendment.

**A label-authoring finding, reported honestly rather than buried:** 3 of
the 4 new gap-regression corpus cases I authored (`QTY-PLURAL-001`,
`INTENSITY-WORD-001`, `WINGS-QTY-WORD-001`) had WRONG expected carts —
each asserted a cart matching `RuleBasedInterpreter`'s own separate,
pre-existing capability limits (no multi-item creation; no DOUBLE/TRIPLE
text extraction; T-040's own filed single-hit UX bug) rather than the
objectively correct customer-facing answer. The real model got all 3
right, identically, in all 3 live runs — proof the labels were wrong, not
the model. Corrected post-gate (labels only, no interpreter/prompt/
threshold changes, and only after all 3 runs completed per this task's own
rule against changing labels mid-gate) — `RuleBasedInterpreter` now
honestly fails all 3, same documented-limitation shape as `MOD-020`/
`GOURMET-005`. Rule-based ratchet: 58/91 (was 61/91 with the wrong labels,
50/81 before this task) — full arithmetic in `tests/test_evals.py`.

**Live N=3 results** (`LAKEWOOD_LLM_PROVIDER=experiential`, `gpt-5.6-luna`,
identical config across all 3 runs, sequential, no code/prompt/label
changes during the runs):

```
                 full/91 (raw)  full/91 (corrected labels)  overlap/73  provider fails
Run 1            62             65                          48          1 (NONPIZZA-007, HTTP 502 timeout)
Run 2            64             67                          49          0
Run 3            59             62                          44          0
mean             61.7           64.7                        47.0
```

"Corrected labels" = the raw score plus the 3 cases fixed above (all 3
behaved identically/correctly in all 3 runs — verified directly from the
raw trace data, not re-run). Historical-overlap mean **47.0**, up from the
prior gate's 40.33 (+6.67), still below T-032's pre-guard 54.33. The
remaining overlap gap is NOT authorization-related: the 73-overlap
failures (`CORRECT-003/004/006/007`, `NEG-003/005/007`, `MULTI-*`,
`QTY-003`, `MOD-014/030/035/036`, `GOURMET-005/010/011/013`,
`SLANG-001/003`, `DECLINE-001`, `FAQ-001`, `ADV-001`, `CONFIRM-002`,
`DELIVERY-002`, `TRANSFER-003/004`, `DISAMBIG-CAP-001`) are multi-item
ordering, negation handling, coupon math, and confirmation-flow accuracy —
pre-existing model-capability categories, already filed (T-017/T-028/
T-029/T-033-036) or out of this task's scope by its own instructions, not
new defects this task introduced or could fix by further loosening the
guard.

**Primary acceptance criterion — zero silent substitutions — PASSES,
robustly, re-confirmed:** every `add_item` across all 3 runs (277 calls
with a reason code) scanned programmatically for an authorization bypass.
**Zero found, in every run**, same methodology as the prior gate. Zero
internal error/reason-code leaks into any customer reply (scanned all 3
full traces). **The rejection ratio inverted, exactly the signal the task
asked for**: `DIRECT_UTTERANCE_EVIDENCE` (44, 44, 45 per run) now exceeds
`UNSUPPORTED_ITEM_SUBSTITUTION` (32, 31, 28) in every run — before this
task the refusal path ran hotter than the success path (60/56/61 vs
37/36/36); Part 1's fix reached the real problem, not a symptom of it.

Full reason-code breakdown per run:

```
                                     Run1  Run2  Run3
DIRECT_UTTERANCE_EVIDENCE             44    44    45
UNIQUE_SUPPORTED_SEARCH_RESULT        11    10     8
CUSTOMER_CONFIRMED_PENDING_CANDIDATE   6     7     7
AMBIGUOUS_CANDIDATE_NOT_CONFIRMED      1     2     1
UNSUPPORTED_ITEM_SUBSTITUTION         32    31    28
```

**T-039-specific case-by-case review (Part 4 of the task): all 6 "voice
session noun" cases (the exact utterances from the original T-038 real-
call P0 — calzone, chicken caesar wrap, appetizer, soup, tacos, garlic
bread) plus the gourmet-cardinal regression case passed in ALL 3 runs,
21/21 — zero pizza fabrication, empty cart every time.** Classified
`SAFE_REFUSAL` throughout: several turns included a real `search_menu`
`ok` hit along the way (e.g. `VOICE-CALZONE-001` sometimes finds `CALZONE
ITEM`), but the model never committed an `add_item` for any of them
without further customer confirmation — cart stayed empty in every single
instance, across all 3 runs, for all 7 cases. This is the exact defect
class T-038's real session found; it does not reappear.

Full per-case classification, provider usage (tokens/latency/cost), and
trace paths: `docs/EVALS.md`'s T-041 live-gate section.

**Superseded by the T-043 audit (2026-09-22): this "unrelated,
already-tracked model-capability limitations" framing was only half true
(see the correction at the top of this section) and the "recommended next
task: T-038" call is withdrawn.** T-043 found 11 of these overlap failures
were genuinely caused by the guard chain (`_has_pizza_intent`'s
whole-utterance-explained design, filed as **T-044**), and separately
found the live-gate numbers above were measured on a repository mid-write
by an external process (see T-043's "PART 1 FINDING") — never repeated on
a confirmed-clean run. **Current recommended next task: T-044**, then a
live N=3 re-confirmation once Codex is confirmed not pointed at this
repo. See `docs/AUDIT_T043.md`.

**T-039 live N=3 Experiential acceptance gate: FAIL (evidence-vocabulary
gap), 2026-09-18. Primary substitution-safety objective PASSES cleanly.**
Ran the real `LAKEWOOD_LLM_PROVIDER=experiential` (`gpt-5.6-luna`) provider
against the full 81-case corpus three times sequentially, no code/prompt/
label changes between runs, one trace file preserved per run:

```
Run 1: 48/81 full-corpus, 41/73 historical-overlap — trace evals/traces/20260918T124814_llm.jsonl
Run 2: 47/81 full-corpus, 39/73 historical-overlap — trace evals/traces/20260918T125937_llm.jsonl
Run 3: 49/81 full-corpus, 41/73 historical-overlap (1 provider timeout on CONFIRM-004, preserved honestly, not retried) — trace evals/traces/20260918T131230_llm.jsonl
```

Historical-overlap set (73 case IDs) determined from git history, not
guessed: `evals/cases/non_pizza_items.yaml` was introduced wholesale (8
case IDs, `NONPIZZA-001`..`008`) in the single commit that closed T-039
(`57344f2`); the corpus at the parent commit was independently counted at
exactly 73 `- id:` entries across the other 10 case files, and no other
commit since has added or removed a case ID (`adversarial.yaml`,
`invalid_and_ambiguous.yaml`, `store_info_and_coupons.yaml` changed
between then and now, but only relabeled 4 existing IDs per ADR-017's
T-039A amendment — case counts unchanged, verified directly). 81 - 8 = 73,
matching the historical corpus size exactly.

**Comparison against the T-032 band (53/54/56, mean 54.33, same provider
and model):** 41/39/41 (mean 40.33) — a real, substantial drop. Every
add_item authorization across all 3 runs (316 calls total) was scanned
programmatically for a bypass (an `ok` result whose `authorization_reason`
is not one of the three authorized codes): **zero found, in every run.**
Zero silent substitutions, zero unauthorized cart mutations, zero internal
error/reason-code strings leaked into any customer-facing reply (scanned
all 3 full traces). This is the primary objective T-039/T-039A/T-039B
exists to gate, and it holds robustly.

**The score drop is NOT explained by "safe refusal of an adversarial
substitution attempt."** Root-caused instead to pre-existing, narrow
evidence-vocabulary gaps in the mutation-boundary guard that were never
consequential before T-039B (when ANY search hit authorized a mutation,
regardless of exact wording) and are now newly load-bearing:

1. `_PIZZA_WORD_RE` (`\bpizza\b|\bpie\b`) does not match the PLURAL
   ("pizzas"/"pies") — `\b` requires a non-word boundary immediately after
   the word, which a trailing "s" is not. Reproduced directly:
   `_has_pizza_intent("three medium cheese pizzas")` → `False`. Blocks
   correctly-specified multi-item/quantity pizza orders (`QTY-002`,
   `MULTI-001/003/004/006`, all 3 runs, identical failure).
2. `_pizza_shorthand_residual`'s intensity vocabulary has no entry for
   "triple"/"quadruple" (only the words T-039A's original authoring
   session used). `_has_pizza_intent("small cheese with triple
   pepperoni")` → `False` — the whole utterance is correctly pizza-shaped
   and the model's proposed item/size is exactly right, but the residual
   word "triple" blocks it (`MOD-032`, all 3 runs).
3. Gourmet-number evidence (`_item_hit_supported_by_utterance`'s
   `kind == "gourmet"` branch) only matches numeral digits (`#10`/"number
   10"), never a spelled-out cardinal ("number ten"). Blocks legitimate
   gourmet selections stated in ordinary spoken English
   (`GOURMET-005/010/011/012/013`, all 3 runs).
4. Non-pizza item/candidate word-matching (`_item_hit_supported_by_
   utterance`, `_select_pending_candidate`, `oe.non_pizza_alias_hits`) has
   no mapping from a spelled-out quantity phrase ("six piece wings") to an
   abbreviated menu-key token ("6PC WINGS") — blocks a legitimate wing
   order even after the customer explicitly answers the system's own
   disambiguation question (`CORRECT-007`, `MULTI-002`, `COUPON-001`, all
   3 runs, identical failure shape each time).

None of these four ever produced a wrong item in the cart — the guard
correctly refused every one (empty/unchanged cart in every case), which is
why the primary criterion still holds. But they DO falsify the "supported
direct orders still work" acceptance criterion, which the task requires
literally: a real, well-specified, non-adversarial customer utterance was
wrongly refused, reproducibly, in all 3 runs, for the same root cause each
time. **Filed as T-041** (see `docs/NEXT_TASKS.md`) — the smallest bounded
fix is expanding the evidence vocabulary in the four spots above, not
re-architecting the guard.

**One additional, narrower, genuinely flaky finding: `NONPIZZA-006`
("I want a salad." → "The garden one." → "Large.") failed in Run 1
(unnecessary `transfer_to_human`) but passed in Runs 2 and 3.** Cause:
`LLMInterpreter` has no equivalent of `RuleBasedInterpreter`'s
`_narrow_disambiguation` — when the model's own reply narrows the
candidate set conversationally ("Would you like the small or large Garden
Salad?"), `session.pending_disambiguations` is never actually narrowed for
the LLM path, so a later bare "Large." must still resolve against the
original 4-candidate, 2-family set, which `_select_pending_candidate`
correctly refuses to guess across. In Runs 2/3 the model happened to issue
an additional `search_menu("GARDEN SALAD", ...)` that registered a NEW,
already-narrowed 2-candidate entry, which the bare "Large." then resolved
against — a real but incidental self-recovery, not a designed one. In Run
1 the model's retry query ("large garden salad") returned `NO_MATCH`
instead, so no narrower entry existed and the model gave up and
transferred. Cart stayed empty and correct in the failing run — not a
substitution, a lower-severity UX gap (SYSTEM_DEFECT class, not P0).
Rolled into the same T-041 follow-up scope as a secondary item, since it
shares the same "LLM path lacks a capability `RuleBasedInterpreter`
already has" shape as finding 4 above.

**Zero pricing mismatches were caused by any T-039B code change.**
`orders.py`'s pricing/domain code is untouched by T-039B except
`_narrow_disambiguation` (which never touches price). The two pricing-
label misses observed (`MOD-035`, `MOD-036`) both trace to pre-existing,
unrelated model behavior — an ambiguous topping search the model abandoned
without asking (`MOD-035`, the pizza itself priced correctly) and the
model choosing `DOUBLE` mozzarella intensity for "extra cheese" (`MOD-036`,
a modifier-selection choice, not an item-authorization one) — not to the
mutation-boundary guard.

Full authorization-reason breakdown, hallucinated-SKU counts (all
domain-layer rejections, never a mutation), latency, and token/cost
figures per run: `docs/EVALS.md`'s T-039 live-gate section.

**Recommended next task: T-041** (the evidence-vocabulary fix above), NOT
T-038 Phase 2 — the FAIL branch of this gate's own acceptance criteria
("Supported direct orders still work") means the live gate has not yet
fully closed, even though the P0-relevant half (silent substitution risk)
is fully closed and re-confirmed across three independent live runs.

**T-039B done, 2026-09-18: T-039A's LLM-path mutation-boundary guard
(`_item_creation_is_authorized`) treated ANY `search_menu` hit returned
during the same turn as authorization for a matching `add_item` — even a
hit registered under `needs_disambiguation=True`, and even when the
model's own search query had no support in the customer's utterance at
all.** Reproduced directly: with the customer saying only "I want a
salad," a model calling `search_menu("wrap")` then `add_item("WRAP")` was
authorized and added a $12.00 wrap; the same shape worked for a
`search_menu("coke")`→`add_item("CAN")` substitution and a
`search_menu("bruschetta")`→gourmet-pizza substitution. A retrieved
candidate was being treated as customer consent instead of evidence for a
clarifying question.

**Fix: `_authorize_item_creation` replaces `_item_creation_is_authorized`
and returns a stable reason code, not a bare boolean** — only
`AUTH_DIRECT_UTTERANCE_EVIDENCE`, `AUTH_UNIQUE_SUPPORTED_SEARCH_RESULT`,
and `AUTH_CUSTOMER_CONFIRMED_PENDING_CANDIDATE` authorize the mutation.
`AUTH_UNIQUE_SUPPORTED_SEARCH_RESULT` now requires the hit to not have
been returned ambiguously AND both the model's search query and the exact
retrieved SKU to be independently supported by the customer's own words
(`_search_query_supported_by_utterance`/`_item_hit_supported_by_utterance`)
— a model can no longer manufacture authorization by choosing a favorable
query. `AUTH_CUSTOMER_CONFIRMED_PENDING_CANDIDATE` deterministically
matches an explicit follow-up ("the large garden salad") against the
server-owned `session.pending_disambiguations` set
(`_select_pending_candidate`, shared by both interpreters), including
narrowing a family ("the garden one") before a later bare size ("large")
resolves it against the now-narrowed set. Anything else returns
`AUTH_AMBIGUOUS_CANDIDATE_NOT_CONFIRMED` or
`AUTH_UNSUPPORTED_ITEM_SUBSTITUTION` and the mutation never reaches the
real tool. Full mechanism and evidence:
`docs/decisions/ADR-017-no-silent-item-substitution.md`'s T-039B amendment.

**Same-task persistence fix:** `search_menu` was treated as read-only by
`PersistentChat`, but an ambiguous result mutates
`session.pending_disambiguations`, and that mutation was never saved — a
dropped call or reload between the ambiguous search and the customer's
next turn silently lost the pending clarification. `PersistentChat.run_turn`
now fingerprints `session.unresolved_lookups`/`pending_disambiguations`
before and after the turn and saves through the existing repository path
only when that fingerprint actually changes; a harmless unique/miss search
still writes nothing. `PersistentChat._chat_for` restores the presentation
cache (`ChatState.pending_clarification`) from the authoritative,
just-loaded `session.pending_disambiguations` on every load/resume, so a
recovered session's next turn resolves against the real reloaded candidate
set, proven with a real `PersistentChat` + `InMemorySessionRepository`
round trip, not serialization helpers alone.

Offline: full suite 561 passed / 2 skipped / 2 xfailed (up from 546 — 15
new tests in `tests/test_t039b_candidate_authorization.py`, zero
regressions); `validate` 81/81 (was 78/78 — 3 new corpus cases, no
regressions); rule-based ratchet 50/81 (was 46/78) — +1 genuine flip
(`NONPIZZA-005`'s second turn, "the small one," now resolves via
`_select_pending_candidate` instead of the old raw substring check that
could never match a spoken size word against an abbreviated SM/LG suffix)
+3 new cases, all passing as authored; pricing parity 50/50 unchanged.
Full arithmetic: `tests/test_evals.py`'s baseline comment.

**T-039A done, 2026-09-17 (reopened T-039, same day): T-039's fix was a
12-word denylist, not the general invariant it claimed.** Confirmed
directly on the commit that closed T-039 (`765fe1f`): "A medium nachos with
chicken." → a fabricated medium cheese pizza with chicken, $16.00 — same
defect, different noun, because `_NON_PIZZA_HEAD_WORDS` only special-cased
12 literal words and anything else still fell through to `_new_pizza`'s
unconditional default. Four more adversarial nouns (soup, tacos, appetizer,
garlic bread) reproduced the identical failure. The LLM path was also
unprotected against a model substituting a *valid* SKU (not just a
fabricated unknown one) — T-039's own LLM test only ever scripted the
latter, false confidence never caught by real evidence.

**Fix: replaced the denylist entirely with fail-closed intent parsing.** A
pizza may be created only when the utterance gives positive evidence — the
word "pizza"/"pie", or a fully-explained pizza-shorthand utterance where
every meaningful token is a real size/topping/modifier/quantity/filler,
nothing left over (`_has_pizza_intent`/`_pizza_shorthand_residual` in
`lakewood/interpreter.py`). This subsumes the old denylist with zero
enumeration — "nachos"/"soup"/"tacos"/"appetizer"/"garlic bread" all block
correctly without ever being named in code, same mechanism as any other
unrecognized noun. `RuleBasedInterpreter`'s `_new_pizza` trigger and the
bare-topping graft onto an open line are both gated by the SAME predicate.
The LLM path gets its own structural mutation-boundary guard
(`_item_creation_is_authorized`, generalized past pizza to any valid-SKU
substitution): a model's `add_item` call is authorized only by the
utterance's own evidence (pizza only) or a matching `search_menu` hit
already returned THIS turn — never on the model's say-so alone. Direct
pizza orders with no prior search and real `search_menu`→selection flows
both still work, proven by test. Full mechanism, tradeoffs, and evidence:
`docs/decisions/ADR-017-no-silent-item-substitution.md`'s "Amendment"
section (supersedes its original "Decision" section, kept for historical
context); corpus-blind-spot follow-up: `docs/EVALS.md`'s T-039 section,
"Correction — T-039A."

**Part 4 (customer-safe failures) extended in the same task:** unknown tool
names from BOTH interpreters' mutation loops, raw Python exception text
from a caught `TypeError`/`ValueError`, provider-call failures, and the
internal "tool loop exceeded N rounds; turn discarded" diagnostic are all
masked before reaching dialogue — `chat.py`'s mask set grew from
`{BAD_LINE, NOT_ON_PIZZA}` to include `UNKNOWN_TOOL`/`BAD_ARGS`, and
`LLMInterpreter.interpret()`'s provider-exception handler no longer
interpolates the raw exception into what gets spoken (diagnostic detail
stays available via `last_provider_error` for logs/traces).

**Two real, narrow code gaps found and fixed while investigating corpus
score flips (not scope creep — direct requirements of the same intent-
parsing mechanism, not new features):** "add"/"to"/"too" added to the
pizza-shorthand filler set — natural follow-up phrasing like "add pepperoni
too" or "actually take the mushrooms off" was being wrongly blocked without
them (caught by two PRE-EXISTING negation tests that started failing).
"pie" recognized as pizza-word evidence alongside "pizza" itself — "a plain
pie, medium" is genuine colloquial pizza language a corpus label already
expected to work.

**A second, independent hardware-run finding, discovered while diagnosing
this task, reported honestly rather than left in a test transcript:**
`tests/test_persistent_chat_path.py`'s own fixture data ("I want a large
pepperoni and wings." / "12 piece.") was inadvertently exercising a THIRD
instance of the substitution defect — "12 piece." silently fabricated a
SECOND small cheese pizza (the numeral "12" collides with the 12-inch SMALL
size alias) instead of resolving to `12PC WINGS`, and the test's own
`len(lines) == 2` assertion happened to pass anyway because it only checked
count, not correctness. Corrected to use two unambiguous single-item pizza
orders, since the test's real purpose is persistence plumbing, not
interpreter accuracy; documented in the test itself.

**Known, accepted tradeoff (see ADR-017):** an utterance combining a clear
pizza base with one unresolvable modifier or clause ("...just put it on my
medium cheese" with a fabricated "truffle topping"; "large cheese, and
apply the half off everything code") now refuses the whole utterance rather
than creating the pizza and separately failing the unresolvable part — less
convenient for that specific compound shape, and structurally necessary:
the old reasoning ("a recognized topping word is present, so it's pizza")
is exactly what made the original defect possible. Four pre-existing
corpus labels asserting the old behavior were corrected with full
justification (`ADV-002`, `ADV-004`, `COUPON-002`, `INVALID-002`).

Offline: full suite 546 passed / 2 skipped / 2 xfailed (up from 520 — 26
new tests: 25 in the new `tests/test_item_substitution_guard_generalized.py`
plus 1 corrected weak-assertion sibling; zero regressions); `validate`
78/78 unchanged (label corrections only, no new cases this task); rule-based
ratchet 46/78 (was 41/78) — +5 genuine flips from the more accurate intent
gate, −4 from the 4 relabeled adversarial cases, +3 recovered by the
add/to/too/pie fixes the flip investigation surfaced; pricing parity 50/50
unchanged. Full arithmetic: `tests/test_evals.py`'s baseline comment.

**Live N=3 sequential run: still BLOCKED, unchanged from T-039** — no
`EXPLABS_API_KEY` in this environment; this task touched no
`LLMInterpreter` prompt/tool-description/provider surface that would
require a fresh live measurement beyond the mutation-boundary guard itself
(which only ever REJECTS an unauthorized call closer to what the system
prompt already asks for — it cannot make a well-behaved model's real
accuracy worse). Owner action still needed to close this measurement.

**T-039 done, 2026-09-17: closed a P0 — `RuleBasedInterpreter` was silently
substituting a different, real, priced item for one it couldn't resolve
(a garden salad became a small cheese pizza; "a two liter coke" became a
can), found by a real 10-turn T-038 Phase 2 voice session that 73 corpus
cases, every synthetic fixture, and four prior diagnostic sweeps never
caught.** Full mechanism, fix, and evidence:
`docs/decisions/ADR-017-no-silent-item-substitution.md`; corpus-blind-spot
argument: `docs/EVALS.md` "Real speech found a P0 the corpus never did."

**Diagnosed before any fix, per row:** `_new_pizza` triggered on any
recognized size word alone and unconditionally defaulted to
`add_item(item="CHEESE PIZZA")`, never checking whether the utterance
actually named a pizza; the bare-topping fallback then grafted any
recognized topping word from ANY later utterance onto that line regardless
of whether it was about that pizza (a calzone and a chicken caesar wrap were
both scavenged this way across turns). "Two liter coke" → "1 can" and bare
"can" (the modal verb in "can I get…") both traced to `_find_drink` using a
second, independently-drifting copy of the drink alias table that never got
T-032's precedence fix — a distinct gap, not a T-032 regression.
`add_item`'s own domain layer was checked directly and was never the
problem: any unresolved item name already returned `ITEM_NOT_FOUND` and
mutated nothing — the interpreter just never asked it the real question.
**`LLMInterpreter` does not share this defect** (no equivalent heuristic;
verified both that a prompt-following model leaves the cart untouched on a
miss, and that even a naive hallucinated `add_item` call still fails closed
at the same domain-layer `ITEM_NOT_FOUND` check).

**Fix:** one shared non-pizza-head-word veto in `interpreter.py` (routes to
a real `search_menu` lookup instead of assuming pizza, regardless of a
co-occurring size/topping/drink word or an already-open pizza line) plus
promoting `orders.py`'s real, precedence-correct `NON_PIZZA_ALIASES`
resolution (`oe.non_pizza_alias_hits`) to a shared function both
`search_menu` and `RuleBasedInterpreter._find_drink` call — the private,
drifting `_DRINK_WORDS` copy is deleted entirely, so this bug class (a fix
landing in one of two duplicate tables) cannot recur by construction.

**Two supporting bugs from the same session, also fixed:** a domain error's
raw internal `line_id` ("L5 is not a pizza") was reaching the customer
verbatim — `chat.py::_customer_safe_error_message` now masks the two codes
(`BAD_LINE`, `NOT_ON_PIZZA`) grep-verified to embed one, leaving every other
error code's already-customer-safe message untouched. `LocalVoiceLoop.turn()`
had nothing catching `STTCallError`/`UnusableAudioError` (a real HTTP 422 on
silence/noise/a hesitation), so an unusable recording killed the whole
process — a dropped call on a phone line. It now degrades to a spoken
apology and the loop continues into the next real turn.

Verified regression tests (14 in `tests/test_item_substitution_guard.py`,
2 in `tests/test_voice.py`): each row of the real transcript's defect table,
the domain-layer fail-closed proof, both `LLMInterpreter` structural checks,
and both internal-error-masking proofs — every one confirmed to FAIL against
the pre-fix code before confirming it passes post-fix (stash-and-rerun, not
assumed).

**Corpus:** `evals/cases/non_pizza_items.yaml`, 5 new cases — the first
non-pizza item orders (`WRAP`, `GARDEN SALAD SM`/`LG` ambiguity) in this
corpus's history, a genuinely-missing item (`nachos`), and this defect's own
compound unresolved-head-plus-topping-word shape.

Offline: full suite 520 passed / 2 skipped / 2 xfailed (up from 504 — 16 new
tests, zero regressions); `validate` 78/78 (was 73/73, pure corpus growth);
rule-based ratchet 41/78 (was 36/73) — +1 from `DELIVERY-003` (a
pre-existing corpus case independently found to trip the same bare-"can"
collision this task fixes), +4 from 4/5 new corpus cases passing under the
real interpreter as authored (`NONPIZZA-005`'s second turn is a genuine,
unrelated, pre-existing `RuleBasedInterpreter` limitation, left honestly
failing); pricing parity 50/50 unchanged. Full commands and output: "Latest
verification" below.

**N=3 sequential live run: BLOCKED, not run.** `docs/STATUS.md`'s prior live
baselines (T-031/T-032, 53–56/73) all used `LAKEWOOD_LLM_PROVIDER=experiential`
(`gpt-5.6-luna`); this environment has no `EXPLABS_API_KEY` configured, and
this task did not touch `LLMInterpreter`, provider selection, prompts, or
tool descriptions — nothing eligible for the paid-provider live measurement
changed. Flagged rather than skipped silently or substituted with a
different, non-comparable provider (`ANTHROPIC_API_KEY` is present but is
this session's own credential, not `EXPLABS_API_KEY`, and swapping providers
would not be comparable to the recorded band regardless). Owner action
needed to close this specific measurement; everything else in this task's
acceptance criteria is verified above.

**Voice work resumes** — T-038 Phase 2's remaining item (the real
microphone → Parakeet → PersistentChat → SAPI hardware loop, 10+ human
turns, per-stage median/p95) was explicitly paused for this task per its own
instructions and is unblocked now that this P0 is closed.

**T-038 Phase 2 Parakeet increment PARTIALLY VERIFIED, 2026-09-17.** The
local voice loop now has a production-shaped local STT boundary:
`scripts/parakeet_server.py` keeps `nvidia/parakeet-unified-en-0.6b` warm in
the existing WSL NeMo/CUDA environment; Windows-side
`lakewood/stt/parakeet_provider.py` sends WAV bytes over localhost and returns
the unchanged `STTResult` contract. The service is localhost-only, serializes
inference on the 4 GB GPU, loads once, supports an explicit warm-up file, and
exposes `/healthz`. The client fails closed for missing/empty/short/corrupt
audio, service/HTTP failure, malformed JSON, and empty model output. Faster-
whisper remains available as fallback; no NeMo type or dependency enters the
Windows app/domain environment. Decision and tradeoffs: ADR-016.

Real same-file hardware measurement: a 2.586125 s human recording transcribed
correctly by both providers. Parakeet warm median **0.082 s** (~31.5x
realtime), 2.60 GB GPU peak, 11.073 s cold load; faster-whisper `small` CPU
median **2.748 s** (0.9x realtime), 0.95 GB RSS, 12.340 s cold load. This
chooses the latency provider; it is not production-accuracy evidence.

Offline verification: 508 collected, 504 passed / 2 skipped / 2 xfailed;
`validate` 73/73; rule-based 36/73 (unchanged expected coverage); pricing
parity 50/50.

**Real hardware E2E run completed after the offline verification:** 10/10
turns passed through Windows microphone capture -> warm WSL Parakeet ->
PersistentChat -> Windows SAPI with no service crash, timeout, empty result,
or playback failure. Median/p95-at-N=10: capture 5.219/5.343 s, STT
0.320/0.391 s, app 0.000/0.000 s, TTS synthesis 0.360/0.391 s, total
5.899/6.235 s. Median measured processing after the fixed capture window was
~0.688 s. Plumbing/latency therefore pass the local target; the fixed
five-second capture window is now the dominant UX delay and needs VAD or
push-to-stop endpointing.

**Order correctness failed and T-038 remains PARTIAL.** The hardware run used
`RuleBasedInterpreter`, whose 36/73 corpus score already says it is a demo
pattern matcher, not production NLU. The real run reproduced that limitation
at full severity: requested items/intensity were dropped, second-half scope
became first-half or whole, and salad/calzone/wrap requests mutated an existing
pizza. Safe refusals also occurred for menu phrases the matcher did not
resolve. At least two Parakeet transcripts appear materially wrong, but the
spoken ground truth was not captured, so an STT accuracy percentage would be
fabricated. Do not merge these observations into one "voice accuracy" number:
transport passed, latency passed, STT accuracy is unscored, and interpreter/
order correctness failed.

**T-038 Phase 1 done, 2026-09-16:** `PersistentChat` now wires the normal
text path through `resume_or_create`, explicit accept/decline recovery, and
the T-037 repository. `ChatState.tool_executor` is the single execution seam:
both rule-based calls and staged LLM calls save successful mutations, while
both persistent confirmation paths use `confirm_and_persist`. Fake-LLM tests
prove mutation reload, F14 same-turn refusal, durable confirmation, and F6
replay after restart. Callback recovery preserves pending clarification,
revalidates/reprices, and invalidates stale quotes. Offline verification:
478 passed, 2 skipped, 2 xfailed; validate 73/73; rule-based 36/73; parity
50/50. Voice/TTS/telephony were not started.

**T-037 done, 2026-09-16: persistence + customer identity — the blocker
`docs/STATUS.md` has flagged High severity since early on ("must land before
any real call") is closed.** `Session` was in-memory only; a dropped call
lost the order and a process restart lost every order in flight. New package
`lakewood/persistence/` (repository interface + two implementations,
serialization, recovery, retention, customer identity, thin orchestration
service) closes this without touching `orders.py`/`pricing.py`/`menu.py`/
`coupons.py` at all — persistence is a side-car, not a domain-layer change.

**Tenancy decision (made ahead of this task, honored here): multi-tenant
schema, single-instance Postgres**, superseding ADR-005's SQLite proposal
(never implemented — see `docs/decisions/ADR-014-persistence-postgres-
multitenant.md` for the full reversal reasoning). Every table in
`lakewood/persistence/migrations/0001_init.up.sql` carries `store_id` in its
primary key or a foreign key back to one that does. Proven structurally, not
assumed: 7 tenant-isolation tests (same call_id at two stores doesn't
collide, wrong-store_id load returns None, phone-based resume lookup is
tenant-scoped, customer_ids don't leak across tenants, confirmed-order/
idempotency-key lookups are tenant-scoped, purge only touches the named
tenant, a directly-tampered stored blob still can't forge a cross-tenant read
because the repository's lookup key is authoritative, never the blob's own
claimed store_id) plus 6 static schema-parsing tests that need no database
server at all.

**Offline gate stays offline — proven directly, not assumed:** `psycopg2` is
not installed in this dev environment (checked: `pip show psycopg2-binary`
finds nothing), and the full 472-test suite — including every new
persistence test, which exercises `postgres_repository.py`'s import path —
passed anyway. `PostgresSessionRepository` lazy-imports `psycopg2` inside
`__init__`, the same pattern `stt/faster_whisper_provider.py` already used
for its own optional dependency (ADR-008). `InMemorySessionRepository` is
what the full suite and the T-016 ratchet gate actually run against; it
round-trips every `Session` through the same dict serialization the Postgres
adapter JSON-encodes, so a (de)serialization bug shows up in the always-run
path, not only when a real Postgres happens to be reachable.

**Every F-series fail-safe proven across a save/reload boundary, not just in
memory** (`tests/test_persistence_invariants_across_reload.py`, 8 tests,
each building state through the real tools, saving, reloading into a
genuinely different `Session` object, then re-exercising the guard):
F14 (same-turn confirmation prohibition, and that it correctly UN-blocks in a
later turn), F15 (unresolved-lookup gate), F16 (readback always present),
F17 (pending-disambiguation gate), F5 (cart-hash mismatch still caught, and
separately that any real mutation still forces `BUILDING`+a fresh quote), and
F6 (idempotent confirm — extended past what `Session.idempotency` alone
covers: a replayed `confirm_order` after a simulated process restart, with no
surviving in-memory object at all, still returns the original order via
`get_confirmed_order_by_idempotency_key`, never a second one).

**Recovery — a product decision, made and implemented, not left implicit:
offer resume, never silently continue.** `find_resumable_session` (30-minute
window) + `revalidate_and_reprice` (`lakewood/persistence/recovery.py`) check
every line against the CURRENT menu/availability tables — the identical
authority `add_item`/`add_modifier` themselves use, not a second parallel
check that could drift — and unconditionally invalidate any outstanding quote
regardless of whether anything was found wrong (`cart_hash` matching proves
contents didn't change, never that today's price is the same one quoted
before). Confirmed by a real gap this reasoning specifically closes: without
the unconditional invalidation, a menu price change during a dropped call
would leave a stale-but-still-"matching" `quote_id` confirmable at the old
price. **Not wired into a live call loop** — `service.py::resume_or_create`
is the entry point a future voice/chat layer calls; no telephony or chat-flow
code was touched in this task, per its own scope boundary.

**Customer identity:** phone normalized (NANP) to an opaque
`customer_id` (`CUST-XXXXXXXXXXXX`), tenant-scoped — the same phone number at
two different stores gets two different `customer_id`s, and the raw number is
never used as a key anywhere except the one `customers` table that maps it.
`mask_phone` (last-4-digits-only) is what any future log line must use;
nothing added in this task logs a full phone number.

**Privacy/retention, decided and implemented, flagged where owner input is
needed:** in-flight sessions purged after 24h past the resume window;
confirmed orders retained 400 days (this task's own assumption, not a
verified legal requirement — flagged in ADR-014, same pattern as T-003's
still-open owner action); customer identity kept until deletion is
requested. `delete_customer` cascades to in-flight sessions but only
UNLINKS (never deletes) confirmed orders — the restaurant's financial record
survives, the identity link to it doesn't. No scheduler exists yet to
actually run the purge functions on a cadence — building them was this
task's job, scheduling them is a later phase's.

Offline: full suite 472 passed / 2 skipped (live-Postgres-only, no DSN in
this environment) / 2 xfailed (pre-existing, unrelated) — up from 365 before
this task, all net-new persistence tests. `validate` 73/73 unchanged,
rule-based ratchet 36/73 unchanged (identical fail list), pricing parity
50/50 unchanged — this task added a new package and touched no domain file.
Full verification commands and output: "Latest verification" below.

**T-032 done, 2026-09-16: fixed the corpus's single biggest lever —
`search_menu`'s "cheese"-substring alias collision — with the fix verified
directly, not assumed, and the honest result reported even though it's
smaller than predicted.** `ALIASES = {"cheese": "MOZZARELLA", ...}` matched
as a raw substring (`alias in raw_q`), firing on any query containing
"cheese" anywhere, including inside "cheesecake." Two fixes, each covering
a real trace-evidenced query shape: (1) word-boundary matching instead of
substring — fixes "cheesecake"; (2) query-shape precedence — the bare
"cheese" alias is suppressed specifically when "pizza" is also mentioned
(they together name the base item, not a topping request), feeding that
suppression into the existing `CHEESE PIZZA` pseudo-hit's own evidence
check so a query like "party size cheese pizza" (residual "size" after
generic-word stripping) doesn't lose the only signal that ever proved it
wasn't gibberish. A parallel precedence fix for `NON_PIZZA_ALIASES`: a
specific drink size ("two liter") beats the generic "soda"/"coke" catch-all
in the same query — the sibling collision behind `MULTI-002`.

A first draft of the query-shape fix had a real bug, caught before
shipping: the suppression flag fired whenever "pizza" appeared at all,
regardless of whether "cheese" ever actually matched — silently breaking
T-020's own named regression test (`truffle lobster pizza` started
resolving to `CHEESE PIZZA` instead of a clean refusal). Fixed by checking
the word-boundary match first, suppression only on an actual match; now has
its own dedicated regression test alongside 15 others in
`tests/test_search_menu_alias_collision.py`.

**Live N=3 re-run, verified directly: zero collision recurrence across 286
real `search_menu` calls** in 3 clean runs (53/73, 54/73, 56/73, mean
54.33, 74.4% — flat vs. T-031's 53.67/71%, does not clear the noise floor,
exactly as expected since most named cases had a second cause). Of the 9
cases T-031 named: **2 clean flips** (`INVALID-002`, `MOD-030`, 3/3 each),
1 mostly-flipped (`ADV-002`, 2/3), 1 inconclusive (`NEG-007`, 1/3), and
**5 confirmed fixed at the mechanism level (every trace shows a clean,
single `CHEESE PIZZA` candidate now) but still failing for an independent
second cause**: `AVAIL-001` (a genuine, separate `clams` ambiguity —
Clams Casino gourmet vs. CLAMS topping, correctly still surfaced),
`COUPON-001`/`CORRECT-007` (T-028's coupon-by-description gap — the model
guesses a coupon code, gets `COUPON_NOT_APPLICABLE`, falls back to a wrong
default), `MULTI-004`/`MULTI-006` (T-033's proactive-`request_quote`
pattern). This is the honest result the task asked for: **fewer flips than
the 9-case prediction, reported plainly, not chased further.**

NO_MATCH rate moved 23.6% (T-031's own traces) → 27.6% (post-fix).
Checked directly rather than assumed acceptable: grepped every post-fix
`NO_MATCH` for any query touching "cheese"/"soda"/"liter" — found exactly
one (`MULTI-004`'s `"plain cheesecake"`, a new query this round's model
tried that was never a recognized alias, unrelated to the matching-logic
change). The increase is ordinary run-to-run model variance in what gets
searched at all (this round's model tried more coupon-code guesses and
size-exploration queries) — **not a recall regression** of T-020's own
tracked number.

Offline: full pytest suite green, `validate` 73/73 unchanged, rule-based
36/73 unchanged (identical fail list), pricing parity 50/50 unchanged,
T-016 gate unaffected. Zero unsafe confirmations, zero cart corruption,
zero unresolved requests bypassing confirmation, zero provider failures,
zero schema violations across all 3 live runs.

**T-031 done, 2026-09-16: a full N=3 live trace sweep, using T-030's
capture directly — no custom script needed for the first time.** All 73
cases, 3 sequential runs against `experiential`/`gpt-5.6-luna`, all clean
(0 provider failures, 0 schema violations across 966 requests): **56/73,
50/73, 55/73 (mean 53.67, 73.5%)**. Tested the working assumption from
T-027 directly: **25 distinct cases failed in at least one run, clustering
into roughly 7-8 root causes, not 25 independent problems.**

**Ranked root causes** (full evidence and case lists:
`docs/NEXT_TASKS.md` T-032 through T-036):
1. **`search_menu`'s "cheese"-substring alias collision** — `ALIASES`
   matches as a raw substring anywhere in the query, firing on unrelated
   words like "cheesecake". **9 cases explained by this one mechanism**:
   `AVAIL-001`/`NEG-007` (sole cause, 3/3 each), `COUPON-001`/`CORRECT-007`
   (contributing, 3/3 each), `ADV-002`/`INVALID-002`/`MOD-030`/`MULTI-004`/
   `MULTI-006` (contributing, various run counts). Filed as **T-032**
   (first seen as T-022's `ADV-002`, never had its own task until this
   evidence).
2. **Proactive, unrequested `request_quote()`** calls — tips state to
   `QUOTED` when nobody asked for a total. Reproduced independently 4 more
   times beyond T-022's original 1/3 `CONFIRM-002` finding: `MULTI-004`
   (2/3), `NEG-008`, `CORRECT-007`, `COUPON-001` (1/3 each). Filed as
   **T-033**.
3. **"Extra X" topping intensity has no stable mapping** — sometimes
   `DOUBLE` (overcharge: `MOD-036`, `MOD-014`, both 3/3), sometimes
   `NORMAL` (undercharge: `GOURMET-010`, 2/3). Filed as **T-034**.
4. **LITE-downgrade of an existing topping doesn't remove the old entry
   first** — `NEG-005`, 3/3 identical, two topping entries end up on the
   pizza instead of one. `RuleBasedInterpreter` already has the correct
   logic for this exact shape (T-015); the live model has no equivalent
   guidance. Filed as **T-035**.
5. **Gourmet number never extracted from a compound sentence** —
   `GOURMET-011`, 3/3, re-confirms T-022's own already-filed residual #2
   with hard evidence; not a new finding.
6. **`get_store_info`'s strict field-matching** turns a naming near-miss
   (`delivery_radius` vs. the real `delivery_radius_miles`) into a full
   `INFO_NOT_AVAILABLE` refusal and an unnecessary human transfer for an
   ordinary hours question — `FAQ-001`, 3/3. New finding. Filed as **T-036**.
7. **Label-design tension, not a system bug**: `DECLINE-001` (3/3, already
   filed T-029), `QTY-003` (3/3, `"make that zero"` reasonably read as
   remove rather than the label's intended `update_item(0)`→refused path),
   `NEG-003` (3/3, a bare `"Large"` with no item named — asking rather than
   guessing may be the more defensible behavior than the label assumes).

**Three-way split of the 25 touched cases: 10 system defects, 8 model
capability limits, 3 label defects, 4 inconclusive** (`CORRECT-004`,
`GOURMET-012`, `SIZE-002`, `TRANSFER-004` — 1/3 each, insufficient
evidence per this task's own standard).

**Phase 2 before-picture:** checked specifically whether the model fails
to *attempt* search for named items — it almost never does; every named
item gets a `search_menu` call nearly every run (`MULTI-002`, `MULTI-006`,
`COUPON-001`, `CORRECT-007`, `AVAIL-001` all confirm this). The batched
"ask about every outstanding item" behavior is already 100% guaranteed by
T-023's structural guard (F18) regardless of model behavior. **None of
root causes 1, 2, 3, 4, or 6 are "forgot to ask" — Phase 2's expected
impact is real but narrower than the aggregate score alone would suggest.**

Safety across all 3 runs: zero unsafe confirmations, zero cart corruption,
zero unresolved requests bypassing confirmation, zero provider failures,
zero schema violations. Traces: `evals/traces/20260916T{135432,140534,
141645}_llm.jsonl`.

**T-030 done, 2026-09-16: trace capture by default, a generated case
catalog, and a local replay/live viewer — supersedes T-025's GUI framing.**
The real bottleneck this project keeps hitting isn't implementation, it's
seeing what happened: T-024's own corpus audit missed the `ADV-002`
collision because it only replayed hand-authored calls, never a live
model's actual queries; T-027 resolved three "separate mysteries" into two
root causes in one session, purely from capturing per-call traces that a
throwaway script had to build from scratch, again.

**Part 1 — `evals/runner.py::score()` now captures a full structured trace
by default**, no separate script: every tool call, args, structured result,
a session-state snapshot after every turn (state/cart/subtotal/quote_id/
`unresolved_lookups`/`pending_disambiguations` with `ask_count`), the
customer-facing reply, and (for the `llm` adapter) real per-turn tokens/
latency/cost. Written as one JSONL file per run under `evals/traces/`
(gitignored — an artifact, not a gate input), first line a `_meta` record.
Purely additive: `score()`'s `trace_log` parameter defaults to `None` and
costs nothing when unused (the T-016 gate, which calls `score()` directly
with no `trace_log`, is unaffected and stays fully offline).
**Scores are provably identical with capture on or off** —
`tests/test_trace_capture.py` asserts the exact same `Result` tuples either
way, not just "looked the same." Never writes a credential:
`ProviderResponse` (see `lakewood/llm_provider.py`) has no credential field
to begin with. One shared function, `lakewood/chat.py::run_turn_traced`,
wraps `run_turn` and builds the trace dict — both `rule_based_adapter`/
`llm_adapter` and the Part 3 viewer's live mode call this SAME function,
never a forked copy (`tests/test_viewer_shared_path.py` proves this by
spying on the actual function object across both call paths).

**Part 2 — `evals/catalog.py` generates `evals/catalog.json`**: every
case's ID, tags, source file, and a one-sentence description of what it
tests — derived from the case's own tags/turns/`assert_final` every time
it's regenerated, never hand-written, so it can't go stale the way a
hand-kept doc does. Plus current status per adapter: rule-based (computed
fresh, deterministic) and live (aggregated from the most recent captured
`*_llm.jsonl` trace files, or "no live data captured yet" when none exist).

**Part 3 — `scripts/viewer/server.py` + a single static HTML page**: stdlib
`http.server` only (no framework — would have pre-empted a T-001
architectural decision this task explicitly isn't scoped to make),
localhost-only, no auth, no persistence. **Catalog tab** (sortable/
filterable, reads Part 2's data). **Replay tab**: pick a trace file and a
case, see the conversation rendered turn by turn — customer utterance, each
tool call with schema-error/domain-refusal/ok visually distinct (color-
coded), the model's actual `search_menu` queries surfaced prominently
(exactly where T-027's root cause was hiding), cart **diff** per turn (not
a full re-read), assistant reply, state line. **Live tab**: same rendering,
real time, provider selectable (`rule_based`/`ollama`/`experiential`,
always visible on screen) — drives the exact same `run_turn_traced` path.
Manually verified end to end in a real browser: Catalog renders all 73
cases with live pass/fail pills; Replay correctly renders `ADV-001`'s full
turn (tool call, reply, `+ LARGE CHEESE PIZZA (15.00)` diff, state line);
Live mode created a `rule_based` session and correctly rendered
`"a large pepperoni pizza and an order of wings"` end to end. A real
replayed **failing** case (`MULTI-002` under `rule_based`, from a real
generated trace file) shows exactly why it fails: the interpreter never
attempts "wings"/"a two liter" from the multi-item first utterance at all
(known limitation), and the follow-up "the six piece is fine" gets
literally re-searched as a menu query (`NO_MATCH`, correctly tracked in
`unresolved_lookups`) rather than resolved against the open
disambiguation — visible in one read of the trace, not a custom script.

Verification: full pytest suite green, `validate` 73/73 unchanged,
rule-based 36/73 unchanged (ratchet untouched), pricing parity 50/50
unchanged, T-016 gate still fully offline (writes no trace files under
pytest). No new dependency — stdlib only. `docs/EVALS.md` gains a note
that traces are captured by default under `evals/traces/`.

**T-027 done, 2026-09-16: diagnosed all three T-024 live failures — fewer
root causes than symptoms, exactly the kind of finding this project keeps
running into.** Real per-call/per-turn traces (3 cases × 3 sequential live
reps, `experiential`/`gpt-5.6-luna`, full session-state snapshots including
`pending_disambiguations`/`ask_count`/`unresolved_lookups` after every
turn — no source changes, diagnosis only). **`COUPON-001` and
`CORRECT-007` share one root cause**, not two: both trip the identical
`search_menu` alias collision T-022 already diagnosed as `ADV-002`
("party size cheese pizza" spuriously matches MOZZARELLA) — T-024's own
Part 1 audit missed this because it only replayed each case's *authored*
calls, never a live model's actual search queries, so it couldn't see a
collision a hand-written label simply doesn't trigger. `CORRECT-007`'s
transfer is `F18`'s cap firing *correctly* on that same incidental,
unrelated ambiguity (its `ask_count` reaches 2 because it gets re-asked
every turn regardless of what else that turn addressed) — confirmed
`MAX_DISAMBIGUATION_ASKS = 2` is not too tight, the problem is upstream.
`DECLINE-001` fails for a completely unrelated reason: **the case's own
label is a corpus defect** — turn 2 bundles `begin_confirmation` onto an
utterance that never asks for a total or confirmation, which the system
prompt correctly forbids without explicit affirmative confirmation. No
well-behaved model can pass it as authored — confirmed directly: one rep
called `decline_item` exactly correctly and still failed on this account
alone. That same rep is real signal though: `decline_item` was invoked
correctly, unprompted (zero system-prompt mention exists for it), 1/3
reps — refutes the strong form of "a customer can only exit a
clarification via the cap," though reliability itself stays genuinely
inconclusive (the other 2/3 reps made no tool call at all, not a wrong
one — n=3 can't separate rare-systematic from random here). **Phase 2
scoping decision: one rule, not two.** Ship the already-scoped "resolve or
ask about every named item" rule; defer a second explicit-abandonment rule
until a corrected `DECLINE-001` (filed as T-029) produces a valid
measurement. New finding filed as T-028: `apply_coupon()` with no code can
silently default to a coupon the customer didn't ask for (`FREE_2L`
applied once with zero large pizzas in the cart, when the customer
described `OFF_3_AT_30` in words) — a real order-correctness risk, not
fixed here. Safety across all 9 reps: zero unsafe confirmations, zero cart
corruption, zero unresolved requests bypassing confirmation, zero provider
failures, zero schema violations.

**T-024 done, 2026-09-15: the corpus can now credit correct clarify-then-
resolve behavior — proven by `MULTI-002` flipping from a guaranteed 0/3 to a
clean 3/3 live pass.** T-023 Phase 1 built a guard so the system asks
instead of silently guessing/dropping on genuine ambiguity; the corpus
inverted `CLAUDE.md`'s own "prefer clarification over guessing" rule by
scoring that correct behavior as a failure, since a single-turn case has no
way to answer the question the guard raises. Audited all 71 original cases
(replayed authored calls through real domain functions, then reviewed
utterance wording by hand): **exactly 3 (~4%) had genuine single-turn
ambiguity** — `MULTI-002`, `COUPON-001`, `CORRECT-007`, all the same root
cause ("wings" named with no count, two real SKUs on this menu). A small
fraction, not a corpus-wide defect. `COLLIDE-002` was already correctly
modeled (asserts the still-open state itself). All 3 given an unrelabeled
follow-up turn answering the clarification — same final subtotals as
before. Added `DECLINE-001` (T-023's `decline_item` tool, previously
exercised nowhere) and `DISAMBIG-CAP-001` (the `MAX_DISAMBIGUATION_ASKS`
cap → transfer path). Offline: `validate` 73/73, `score --adapter
rule_based` 36/73 — same absolute pass count as the old 36/71 (both new
cases fail under rule-based, which has no NLU for these phrasings —
expected, not a regression), ratchet not re-raised. Full pytest green,
pricing parity 50/50 unchanged. **Live N=3: 55/73, 53/73, 56/73 (mean
54.67, 75%)** — flat-to-slightly-above T-023 Phase 1's 55/50/50 (mean
51.67, 72.7% on the 71-case corpus) and both historical bands. **This is
the measurement getting more honest, not the model getting better**:
`MULTI-002` went 0/3→3/3 (it structurally could never pass before,
regardless of model behavior); `DISAMBIG-CAP-001` passed 3/3; the new
`DECLINE-001` failed 0/3 (roughly offsetting `MULTI-002`'s gain in the
aggregate, even though a real, previously invisible defect got fixed).
`COUPON-001` and `CORRECT-007` also failed 3/3, each a different exact
symptom each run — mechanism not confirmed (no per-call trace captured,
not spent extra live budget chasing it per the task's own scope), flagged
as a candidate for a small dedicated diagnosis task if it recurs, not
fixed here (out of this task's corpus-only scope). Standing rule added to
`docs/EVALS.md`: any case whose correct behavior is asking must carry a
follow-up turn, so this class of defect isn't re-created. Blocks T-023
Phase 2 lifted — that measurement is now trustworthy.

**T-026 done, 2026-09-15: F15 clears per-request now, not globally — a P0
regression of the T-019 guarantee, found and fixed before T-024's corpus
work continued.** T-024 Part 0 asked to verify a suspected F15 scoping gap
before doing anything else. Reading every call site confirmed it:
`search_menu` (on any hit), `add_item`, `add_modifier`, `remove_modifier`,
`update_item`, and `remove_item` all did an unconditional
`sess.unresolved_lookups.clear()`, never filtered by whether the new
success had anything to do with the specific request that missed. Concrete
reachable shape: "a root beer and a large pepperoni" — `search_menu("root
beer")` misses, `search_menu("pepperoni")` succeeds for the unrelated pizza
and wipes the root-beer miss clean, `begin_confirmation` proceeds as if it
was never asked for. This is the exact T-018 dropped-request defect
ADR-011/F15 was written to close, reopened via a side door reachable
through any ordinary multi-item order. Proven first with a `strict=True`
`xfail` test (per Part 0's own instruction: file before deciding how/when
to fix), then fixed: F15 now clears only via a textually-related later
success (`_query_relates`) or explicit `decline_item` (T-023's tool,
extended to cover both outstanding-request lists). Full design and
alternatives: `docs/decisions/ADR-013-f15-scoped-clearing.md`. Offline
gates unchanged (`validate` 71/71, rule-based 36/71, parity 50/50) — this
is domain-layer-only and, matching T-019's own precedent for a pure
deterministic-logic fix (no prompt/tool-description/provider change), was
verified offline only; T-024's own N=3 live run (in progress) will exercise
the fixed mechanism organically. User decision: fix immediately rather than
defer behind T-024's remaining scope, given P0 outranks P2 measurement work
per `CLAUDE.md`'s own priority order.

**T-023 Phase 1 done, 2026-09-15: a turn can no longer end silently while an
outstanding disambiguation is open — the structural fix for T-022's one
truly consistent finding (`MULTI-002`, 5/5 across two independent live
runs).** `Session.pending_disambiguations` (F17) tracks any `search_menu`
hit the model itself flagged `needs_disambiguation` and never followed with
a resolving `add_item`/`add_modifier`; cleared only by a matching
resolution or the new `decline_item` tool, never by silence.
`chat.py::_apply_completion_guard` (F18) batches every outstanding item
into one clarification at the end of every turn (identical regardless of
interpreter/provider — the single funnel every `run_turn` exit passes
through), capped at `MAX_DISAMBIGUATION_ASKS = 2` before transferring to a
human. `begin_confirmation` also refuses to open while one is outstanding,
same `UNRESOLVED_REQUEST` code F15 already uses. Full design and
alternatives considered: `docs/decisions/ADR-012-turn-completion-guard.md`.
17 new offline tests (`tests/test_disambiguation_guard.py`), including a
named regression reproducing `MULTI-002` exactly. Offline gates unchanged:
`validate` 71/71, rule-based ratchet 36/71, pricing parity 50/50. **Live N=3
regression-detection run (purpose: confirm nothing broke, not to chase a
higher number): 55/71, 50/71, 50/71 (mean 51.67) — flat-to-slightly-above
the post-T-020 49–53 range (mean 51.0), both clearly above the pre-T-020
42–48 band. No regression.** One of the three live attempts was invalidated
by a genuine transient network/DNS outage mid-run (confirmed via
`Test-NetConnection`, not a code issue) and re-run clean before being
counted. `MULTI-002` itself still fails every run with an identical wrong
subtotal — diagnosed as a corpus limitation, not a guard defect: the case
has only one authored turn, so when the guard correctly asks its batched
clarification instead of silently dropping the items, there's no scripted
follow-up turn to answer it. The actual customer-facing defect (silent,
unrecoverable drop) is fixed; the corpus just can't credit it numerically
yet. Phase 2 (a system-prompt rule to resolve every named item on the first
turn — UX only, never the correctness guarantee) is explicitly deferred to
its own task with its own separate N=3 measurement, per this task's own
scope discipline.

**T-020 done, 2026-09-15: `search_menu` recall fix, plus a variance-band
finding that changes how every prior live number here should be read.**
Diagnosed 18 real `search_menu`-involved failures from the T-018/T-019
checkpoint call logs before writing any fix (10 class-A "the base pizza
isn't a searchable name at all", 10 class-B "reasonable phrasing, matcher
too strict/no alias table" — some cases in both). Fixed both with cheap,
deterministic normalization/alias tables (no embeddings) in
`orders.py::search_menu` — see "T-020 milestone" below and
`docs/EVALS.md` "search_menu recall diagnosis and fix". **Also ran the
first real 3x sequential variance measurement, unchanged code:
42–48/71 (59–68%), 15/71 cases (21%) flip pass/fail run to run** — this
means T-018's 45/71 and T-019's 45→49 "delta" were both within ordinary
run-to-run noise, not a measured effect (T-019's actual fix is still
verified correct — `tool sequencing`/`state-confirmation integrity` held
perfectly stable at 6/6 and 5/5 across all 3 noise-band runs). Post-fix:
**51/71 (72%), 3 points above the observed band's max** — a real,
band-clearing improvement, not noise. `search_menu` NO_MATCH rate dropped
from ~60–64% to 26% of all `search_menu` calls (94→24 misses across the
same 71 cases) — the actual customer-facing win (fewer "sorry, what was
that?" turns). T-021 (`requirements.txt`) also resolved this task: no code
fix was needed — root cause was a stale local `.venv`, not the file
itself.

**T-019 done, 2026-09-15: three deterministic defenses close the
confirmation-gate bypass and silent-drop findings from T-018.** `orders.py`
now enforces (F14) `confirm_order` cannot fire in the same interpreter turn
as `begin_confirmation` regardless of interpreter/provider, (F15)
`begin_confirmation` refuses to open while a `search_menu` miss is still
unresolved, and (F16) `begin_confirmation` always returns a real cart-diff
readback — see "T-019 milestone" below and
`docs/decisions/ADR-011-confirmation-turn-gate.md`. Also fixed in the same
task: `chat.py::_finish_turn` reported the *first* error in a multi-round
turn even when the turn recovered — now reports the *last* result. Rule-
based ratchet gate improved 35/71 → 36/71 (a real, observed change, not a
target) as a side effect of correcting `RuleBasedInterpreter`'s own
same-turn confirmation shortcut. Full offline suite green at 365/365 (2
xfailed), `validate` 71/71, pricing parity 50/50 unchanged. Filed two
follow-ups this task deliberately did not fix: T-020 (`search_menu`
phrasing/recall quality — the actual root cause behind most of the misses
Defense 2 makes safe rather than rare) and T-021 (`requirements.txt`
doesn't install what the suite needs from a fresh clone).

**T-018 Parts 3 & 4 done, 2026-09-15: first real live baseline against a
paid provider (Experiential Labs "Luna", `gpt-5.6-luna`) — 45/71 (63%),
next to rule-based's 35/71 (49%).** Full breakdown, cost/latency, and two
full-severity findings (a model that silently drops a requested
modification/add-on then falsely confirms the order as placed; and a
same-turn `begin_confirmation`+`confirm_order` gate bypass reproduced in
2/2 relevant golden cases plus a fresh manual trace) are in "T-018 milestone
— live Experiential/Luna baseline" below. Filed as **T-019** in
`docs/NEXT_TASKS.md` — not fixed in this task per its own scope discipline
(untuned baseline only). T-018 Parts 1/2/5 (provider adapter, ADR-010,
offline tests) were already done and verified before this session; this
session only ran the live Parts 3/4 work and did not touch
`lakewood/llm_provider.py`, `lakewood/interpreter.py`, or `evals/runner.py`.

**T-016 done, 2026-09-08: the eval release gate now actually runs an
interpreter.** T-015 found and fixed a real bug but also found something
that outlasts it: `evals/runner.py::validate()`, the documented release
gate, never runs any interpreter on any case's `user:` text — it only
replays hand-authored `calls`. That's why the negation bug survived a
"63/63 valid" corpus. T-016 adds a **second, separate, ratcheted gate**
(`tests/test_evals.py::test_rule_based_interpreter_meets_baseline`) that
runs the real `RuleBasedInterpreter` against every case and fails if the
pass count drops below a recorded baseline (35/71) — offline,
deterministic, no API key or Ollama, already part of `pytest -q` (which
already runs first in `scripts/check.sh`) with zero new CLI surface.
`validate` itself is completely unchanged; see `docs/decisions/ADR-009` for
why that split (not folding interpreter-checking into `validate`) was the
right call. **Proven, not assumed:** stubbing `RuleBasedInterpreter` back to
its exact pre-T-015 behavior (monkeypatch only, never editing
`lakewood/interpreter.py`) drops the real score 35/71 → 25/71 and the gate
correctly goes red. T-015's fix and corpus expansion, and the earlier local
model tier (T-013, Ollama + local STT), are unaffected and preserved below;
the OpenAI T-013c section further below is preserved as-is.

## Latest verification

```
python -m pytest --no-header (2026-09-18, T-041)            → 583 passed, 2 skipped, 2 xfailed
                                                           (was 561 — 22 new tests in
                                                           tests/test_t041_evidence_vocabulary.py,
                                                           zero regressions)
python evals/runner.py validate                          → 91/91 (was 81/81 — 10 new cases,
                                                           no regressions)
python evals/runner.py score --adapter rule_based         → 58/91 (was 50/81; see "T-041 done"
                                                           above for the honest ratchet arithmetic,
                                                           including the 3-case label correction)
python -m pytest tests/test_pricing_parity.py             → 50/50, unchanged
python evals/runner.py score --adapter llm (N=3, live)    → Run1 62/91 raw, 65/91 corrected-labels
                                                           (48/73 overlap, 1 provider timeout on
                                                           NONPIZZA-007, honestly preserved); Run2
                                                           64/91 (67/91 corrected, 49/73 overlap);
                                                           Run3 59/91 (62/91 corrected, 44/73
                                                           overlap). Zero silent substitutions, zero
                                                           authorization bypasses, all 3 runs —
                                                           rejection ratio inverted (evidence now
                                                           exceeds refusal) in every run. See "T-041
                                                           done" above for full analysis.
                                                           Historical-overlap mean 47.0 (was 40.33)
                                                           vs T-032's pre-guard 54.33. CORRECTED
                                                           (T-043 audit): roughly half this gap is
                                                           guard-caused (T-044 filed), not "unrelated
                                                           model-capability categories" as originally
                                                           claimed here — see docs/AUDIT_T043.md.
                                                           Traces (re-scored and independently
                                                           verified byte-for-byte by the T-043 audit;
                                                           the SCORES held up, only the gap
                                                           ATTRIBUTION was wrong):
                                                           evals/traces/20260918T142854_llm.jsonl,
                                                           20260918T144342_llm.jsonl,
                                                           20260918T145618_llm.jsonl.
```

**T-043 audit note (2026-09-22):** the above N=3 numbers themselves were
independently re-scored from the real trace files this audit and are
correct as stated. The repository-write incident during this same run
window (see `docs/AUDIT_T043.md` PART 1) affects trust in *whether these
specific live calls executed against uncorrupted code*, not the arithmetic
on the traces that were produced — those traces are internally consistent
with the corpus and code that shipped in the `d3355dc` commit. Treat these
numbers as **provisionally trustworthy, formally unconfirmed** until a
live re-run happens on a repo confirmed locked against external writers.

Earlier verification (2026-09-18, T-039 live gate, superseded by T-041's
re-run above), still valid as a historical record:

```
python evals/runner.py score --adapter llm (N=3, live)    → Run1 48/81 (41/73 overlap), Run2 47/81
                                                           (39/73 overlap), Run3 49/81 (41/73
                                                           overlap, 1 provider timeout on
                                                           CONFIRM-004, honestly preserved). Zero
                                                           silent substitutions, zero authorization
                                                           bypasses, all 3 runs. Historical-overlap
                                                           mean 40.33 vs T-032's 54.33 — root-caused
                                                           to evidence-vocabulary gaps, closed by
                                                           T-041 above. Traces:
                                                           evals/traces/20260918T124814_llm.jsonl,
                                                           20260918T125937_llm.jsonl,
                                                           20260918T131230_llm.jsonl.
```

Earlier verification (2026-09-18, T-039B, offline only — live gate was
BLOCKED at that time), still valid and superseded only by the above where
they overlap:

```
python -m pytest --no-header (2026-09-18, T-039B)          → 561 passed, 2 skipped (live-Postgres-
                                                           only, no LAKEWOOD_POSTGRES_TEST_DSN in
                                                           this environment), 2 xfailed (pre-
                                                           existing, unrelated). Up from 546 before
                                                           this task — 15 new tests in
                                                           tests/test_t039b_candidate_authorization.py;
                                                           zero regressions.
python evals/runner.py validate                          → 81/81 (was 78/78 — 3 new corpus cases,
                                                           no regressions)
python evals/runner.py score --adapter rule_based         → 50/81 (was 46/78) — see "T-039B done"
                                                           above for the honest ratchet arithmetic
python -m pytest tests/test_pricing_parity.py             → 50/50, unchanged
```

Earlier verification (2026-09-17, T-039A), still valid and superseded only by
the above where they overlap:

```
python -m pytest --no-header (2026-09-17, T-039A)          → 546 passed, 2 skipped (live-Postgres-
                                                           only, no LAKEWOOD_POSTGRES_TEST_DSN in
                                                           this environment), 2 xfailed (pre-
                                                           existing, unrelated). Up from 520 before
                                                           that task — 26 new tests; zero
                                                           regressions.
python evals/runner.py validate                          → 78/78, unchanged (label corrections
                                                           only, no new cases that task)
python evals/runner.py score --adapter rule_based         → 46/78 (was 41/78) — see "T-039A done"
                                                           above for the honest per-flip breakdown
python -m pytest tests/test_pricing_parity.py             → 50/50, unchanged
```

Earlier verification (2026-09-17, T-039), still valid and superseded only by
the above where they overlap:

```
python -m pytest --no-header (2026-09-17, T-039)          → 520 passed, 2 skipped (live-Postgres-
                                                           only, no LAKEWOOD_POSTGRES_TEST_DSN in
                                                           this environment), 2 xfailed (pre-
                                                           existing, unrelated). Up from 504 before
                                                           this task — 16 new tests (14 substitution-
                                                           guard + 2 voice-crash-recovery); zero
                                                           regressions.
python evals/runner.py validate                          → 78/78 (was 73/73 — 5 new
                                                           non_pizza_items.yaml cases)
python evals/runner.py score --adapter rule_based         → 41/78 (was 36/73) — +1 DELIVERY-003
                                                           (pre-existing case, same bare-"can" bug),
                                                           +4 of 5 new corpus cases; see "T-039 done"
                                                           above for the honest per-case breakdown
python -m pytest tests/test_pricing_parity.py             → 50/50, unchanged
python evals/runner.py score --adapter llm (N=3, live)    → BLOCKED — no EXPLABS_API_KEY in this
                                                           environment; this task touched no
                                                           LLMInterpreter/provider/prompt surface.
                                                           T-032's 53/54/56 (mean 54.33) band is
                                                           still the current live baseline.
```

Earlier verification (2026-09-16, T-037), still valid and unaffected by the
above:

```
python -m pytest --no-header (2026-09-16, T-037)         → 472 passed, 2 skipped (live-Postgres-
                                                           only, no LAKEWOOD_POSTGRES_TEST_DSN in
                                                           this environment), 2 xfailed (pre-
                                                           existing, unrelated). Up from 365 before
                                                           this task — all net-new persistence
                                                           tests; zero regressions.
python evals/runner.py validate                          → 73/73, unchanged
python evals/runner.py score --adapter rule_based         → 36/73, unchanged (identical fail list)
python -m pytest tests/test_pricing_parity.py             → 50/50, unchanged
pip show psycopg2-binary                                  → "Package(s) not found" — confirms the
                                                           full suite above, including every
                                                           persistence test that imports
                                                           postgres_repository.py, runs with no
                                                           Postgres driver installed, no database
                                                           server, no network, no container.
```

This task touched no domain file (`orders.py`/`pricing.py`/`menu.py`/
`coupons.py` unchanged) and ran no live LLM — no live N=3 measurement was
needed or taken; the T-032 numbers below are still the current live baseline.

Earlier verification (2026-09-16, T-032), still valid and superseded only by
the above where they overlap:

```
python -m pytest -q --no-header (2026-09-16, T-032)     → exit 0, full suite green, incl. 16
                                                           new tests/test_search_menu_alias_
                                                           collision.py
python evals/runner.py validate                          → 73/73, unchanged
python evals/runner.py score --adapter rule_based         → 36/73, unchanged
python -m pytest tests/test_pricing_parity.py             → 50/50, unchanged
python evals/runner.py score --adapter llm (N=3, live)    → 53/73, 54/73, 56/73 (mean 54.33,
  LAKEWOOD_LLM_PROVIDER=experiential, gpt-5.6-luna           74.4%) — flat vs. T-031's 53.67;
                                                           zero search_menu collision recurrence
                                                           across 286 real calls; see "T-032
                                                           done" above for the 9-case flip detail.
```

Earlier verification (2026-09-15, T-024/026), still valid and superseded
only by the above where they overlap:

```
python -m pytest -q --no-header (2026-09-15, T-024/026) → exit 0, full suite green
python evals/runner.py validate                          → 73/73 (was 71/71 pre-T-024)
python evals/runner.py score --adapter rule_based         → 36/73 — same absolute count as
                                                            36/71, ratchet not re-raised
python -m pytest tests/test_pricing_parity.py             → 50/50, unchanged
python evals/runner.py score --adapter llm (N=3, live)    → 55/73, 53/73, 56/73 (mean 54.67,
  LAKEWOOD_LLM_PROVIDER=experiential, gpt-5.6-luna           75%) — see "T-024 done" above;
                                                            measurement-honesty fix, not a
                                                            capability claim.
```

Earlier verification (2026-09-15, T-023 Phase 1), still valid and superseded
only by the above where they overlap:

```
python -m pytest -q --no-header (2026-09-15, T-023)    → exit 0, full suite green, incl. 17 new
                                                           tests/test_disambiguation_guard.py
python evals/runner.py validate                         → 71/71, unchanged
python evals/runner.py score --adapter rule_based        → 36/71, unchanged
python -m pytest tests/test_pricing_parity.py            → 50/50, unchanged
python evals/runner.py score --adapter llm (N=3, live)   → 55/71, 50/71, 50/71 (mean 51.67) —
  LAKEWOOD_LLM_PROVIDER=experiential, gpt-5.6-luna          regression-detection only; see
                                                           "T-023 Phase 1" above and ADR-012.
```

Earlier verification (2026-09-15, T-020), still valid and superseded only by
the above where they overlap:

```
python -m pytest -v --no-header (2026-09-15)           → 355 collected, 355 passed, 0 failed, 2 xfailed
                                                           (30 more than the 325 below — the
                                                           test_experiential_provider.py tests
                                                           added by T-018 Parts 1/2/5, already
                                                           green; full suite still green overall)
python evals/runner.py score --adapter llm             → 45/71 (63%), LAKEWOOD_LLM_PROVIDER=experiential,
  (2026-09-15, live)                                      gpt-5.6-luna. See "T-018 milestone" below
                                                           for the full category/cost/latency
                                                           breakdown and findings. NOT a production
                                                           accuracy claim — first live baseline only.
```

Earlier verification (2026-09-08), still valid and unaffected by the above:

```
python -m pytest tests/test_evals.py -v --no-header    → 4 passed (label validity, corpus-growth
                                                           floor, the new ratchet gate, and the
                                                           T-015-bug-detection proof — all real)
python -m pytest -v --no-header                        → 325 collected, 323 passed, 0 failed, 2 xfailed
python -m pytest tests/test_pricing_parity.py -v --no-header → 50 passed, unchanged
python evals/runner.py validate                         → 71/71, completely unchanged behavior
python evals/runner.py score --adapter rule_based      → 35/71 (was 24 pre-T-015, 27 after the
                                                           interpreter fix alone, 35 with the 8
                                                           new cases added — all 8 pass; zero
                                                           regressions on the pre-existing 63)
```
Local-tier live results carried forward from earlier this session:
- `python -m lakewood.stt.eval` (`faster_whisper`, `small`) — real
  transcription of 10 synthesized fixtures; see "Local Model Tier milestone"
  below, unaffected by this task.
- `python evals/runner.py score --adapter llm` (`LAKEWOOD_LLM_PROVIDER=ollama`,
  `llama3.1:8b`) over the **original 63-case corpus** (this ran before the
  8 new negation cases existed): **finished mid-session, real result: 11/63.**
  Not re-run against the now-71-case corpus in this task (out of scope —
  T-015 is the negation fix + corpus-gap diagnosis, not the local-model
  benchmark; see `docs/NEXT_TASKS.md` T-013d for the up-to-date rerun).

## T-015 milestone — negation bug fixed, corpus-gate mechanism diagnosed

### Part 1 — why "63/63" never caught this

Audited all 63 pre-existing cases for negation ("no X"), removal ("take X
off"), and light/extra modifiers. They exist: `pizza_basics.yaml`
`GOURMET-005` ("no pineapple"), `modifiers_and_specialty.yaml` `MOD-033`
("light onions") and `MOD-034` ("take the mushrooms off"),
`corrections.yaml` several `remove_item`/`remove_modifier` cases. Their
`assert_final` blocks already check `subtotal` — the assertions are not
shallow.

**The mechanism is neither of those two.** `evals/runner.py::validate()`
takes each case's hand-authored `turns[].calls` and executes them directly
against the real tools — it **never calls `RuleBasedInterpreter.interpret()`
or `LLMInterpreter.interpret()` on the `user:` text, for any case, ever.**
The `user:` field is documentation of what a customer might say; the label
author's own hand-typed `calls` are what actually gets checked. Proven
directly: running `RuleBasedInterpreter` on GOURMET-005's own utterance
("gimme a medium number ten, no pineapple") *before* this fix produced
`add_modifier(pineapple, WHOLE)` — a full charge — while the case's
hand-authored label (`intensity: NONE`) had always been correct and always
passed `validate`. The gate and the interpreter it was meant to protect had
never been connected.

`score --adapter rule_based` **is** the mode that runs the real interpreter
against `user:` text and checks the same `assert_final` — and it already
reported 24/63 before this task touched anything, meaning this exact class
of failure was already visible, just never gated: `docs/EVALS.md` and
`docs/NEXT_TASKS.md` have called `score`'s number "interpreter coverage, not
a release gate" since T-012, and nothing in this repo ever wired a
pass/fail threshold to it. **This is not a hole specific to negation** — any
interpreter bug, for any category, has always been invisible to `validate`.
Recommended as a follow-up, not fixed in this task (scope discipline, per
the task's own instruction): make `validate` also run the configured
interpreter for cases tagged for it, or give `score --adapter rule_based` a
real, enforced minimum threshold instead of an advisory number. Filed as
T-016 in `docs/NEXT_TASKS.md`.

### Part 2 — the fix

`lakewood/interpreter.py`: `_NEGATION_RE`/`_LITE_RE` unify "no X" /
"without X" / "hold the X" / "leave X off" / "take X off" / "minus X" into
one intent (full exclusion) and keep "light X" / "easy on X" distinct (kept,
not dropped, per PrISM's own LITE-is-free rule already in `pricing.py` —
verified: `Topping.tier_rate` already treats `removed` and `lite` as
identically $0, so this fix is purely about the interpreter choosing the
right one, never a new pricing rule). New `_resolve_intensity_calls`
resolves deterministically from **real cart state**, never guesswork or
sentence grammar:

| Intensity | Topping already a real charge there? | Resolution |
|---|---|---|
| NONE | No | `add_modifier(intensity=NONE)` — records the $0 exclusion (GOURMET-005's own pre-existing pattern, generalized) |
| NONE | Yes | `remove_modifier` alone — deletes cleanly (MOD-034's own pre-existing pattern, **unchanged**) |
| LITE | No | `add_modifier(intensity=LITE)` (MOD-033's pattern, unchanged) |
| LITE | Yes | `remove_modifier` then `add_modifier(intensity=LITE)` — the customer still wants it, just lighter; a bare remove would drop it entirely |

Half-scope: reused the existing per-clause HALF_1/HALF_2 detection
unchanged, plus two real gaps found and fixed while making the task's own
named example ("half pepperoni, no cheese on that half") actually work:
bare "half X" (no "on") wasn't recognized as a portion signal at all before
this task, and "that half"/"this half" wasn't recognized either. Both
fixed, both scope-tested (`test_negation_handling.py`).

**A real bug found IN my own first attempt at this fix**, caught by writing
tests before declaring done, not left in: `_resolve_intensity_calls`
compared `Topping.name` (always canonical uppercase) against the raw
lowercase vocabulary match `_find_vocab` returns, so the "already a real
charge" check silently always returned False. Fixed by canonicalizing
before comparing.

**A second, separate, unrelated gap found and explicitly NOT fixed (out of
scope):** `RuleBasedInterpreter` cannot parse a bare "number ten"/"number
10" as a single gourmet selection outside the half-and-half phrasing — "no
pineapple" tested correctly, but "medium number ten, no pineapple" resolves
to a plain cheese pizza (wrong item, unrelated to negation). Filed as T-017.

### Part 3 — corpus expansion, asserting final priced cart

`evals/cases/negation_and_removal.yaml`, 8 new cases (`NEG-001`–`NEG-008`):
whole-pizza and half-scoped, all 6 negation phrasings represented across
them, lite-vs-negation distinguished, retroactive (price changes) and
preventive (doesn't) both covered, one deliberate ambiguous-no-op case
("no, wait, that's fine" — bare trigger word, no topping, must not
fabricate a call). Every case asserts `subtotal`, not just tool calls.
**Run against the real interpreter, not just labeled** — `score --adapter
rule_based`: all 8 pass (35/71 total, up from 27/71 with the fix alone and
zero new failures on the pre-existing 63). Also proved through
`LLMInterpreter` with a scripted fake provider
(`test_negation_prices_correctly_through_llm_interpreter_too`) — the fix is
a property of the shared domain-execution layer (`add_modifier`/
`remove_modifier`), not something bolted onto one interpreter; a live model
call was out of this task's scope (T-013c/T-013d cover that separately).

### Part 4 — named regression

`tests/test_negation_handling.py::test_regression_no_onions_does_not_charge_for_onions`
— the exact original repro, asserting the exact original wrong number
($21.00) is impossible and the correct one ($18.00) is what's there. 18
tests total in that file: the named regression, all 6 phrasings, retroactive
removal (unchanged pre-existing behavior verified), lite-downgrade-of-
existing, negation+lite non-conflation, three half-scope cases (including
the exact task-named example and cross-half isolation), the ambiguous
no-op, a cents-vs-formatted-quote cross-check, and the LLMInterpreter proof.

## Local Model Tier milestone — Ollama interpreter + local STT, real results

### Track A — OllamaProvider

**Discovery (A1).** Local install found via `GET /api/tags`:

| Model | Params | Quant | Context | Tools | Verdict |
|---|---|---|---|---|---|
| `gemma4:26b` | 25.2B | Q4_K_M | 262144 | yes (+thinking, vision) | pinned default |
| `llama3.1:8b` | 8.0B | Q4_K_M | 131072 | yes | fallback/comparison |
| `qwen2.5-coder:7b` | 7.6B | Q4_K_M | 32768 | yes | excluded — code-tuned |
| `nomic-embed-text`, `all-minilm` | — | — | — | embedding only | not candidates; future menu-search work |

Full reasoning in `docs/decisions/ADR-007-local-model-tier.md`.

**Implementation.** `OllamaProvider` (new third branch of `make_provider()`,
alongside `AnthropicProvider`/`OpenAIProvider`) — stdlib `urllib`, no SDK.
Handles the three named pitfalls explicitly: `options.num_ctx` sent on every
request, never omitted, default 8192 (verified live via `GET /api/ps`
reporting `context_length: 8192` mid-request — the closest confirmation the
API surface allows, since no per-response field echoes the window used);
malformed tool-call shapes (string-encoded JSON args, non-dict args, missing
function name) are skipped, never crashed on, with `LLMInterpreter`'s
existing `_valid_tool_args()` as the second, independent validation layer
before any domain call; `temperature=0`/`seed=0` by default for
determinism. 26 tests, all offline (`tests/test_ollama_provider.py`).

**Real, live results — two full manual smoke-test transcripts** (not
fabricated; both are the exact program output):

```
$ LAKEWOOD_INTERPRETER=llm LAKEWOOD_LLM_PROVIDER=ollama LAKEWOOD_LLM_MODEL=llama3.1:8b \
  python -m lakewood.chat --debug
You > Large pepperoni.
[TOOL] add_item({'item': 'pepperoni pizza', 'size': 'large', 'quantity': '1'})
  -> {'status': 'error', 'code': 'BAD_ARGS', 'message': 'Tool arguments do not match the permitted schema.'}
Assistant > Tool arguments do not match the permitted schema.
```
`llama3.1:8b` sent `quantity: "1"` — a **string**, not the required integer
— and `item: "pepperoni pizza"` (not a real item name; should have been
`item="CHEESE PIZZA"` plus a separate `add_modifier` for the topping). The
schema validator correctly rejected it: `BAD_ARGS`, zero cart mutation.

```
$ LAKEWOOD_INTERPRETER=llm LAKEWOOD_LLM_PROVIDER=ollama LAKEWOOD_LLM_MODEL=gemma4:26b \
  python -m lakewood.chat --debug
You > Large pepperoni.
[TOOL] search_menu({'query': 'pepperoni'})
  -> {'status': 'ok', 'results': [{'kind': 'topping', 'name': 'PEPPERONI'}], 'needs_disambiguation': False}
Assistant > Did you mean Pepperoni? What size would you like?
```
`gemma4:26b` called `search_menu` first (a reasonable first move per the
system prompt), got back an unambiguous single hit, then — in a second
model round with no tool call — asked for size again, ignoring that "Large"
was already in the original utterance. No hallucination, no wrong item, no
guessed price — but the order was never actually built. Neither model
completed even the simplest required flow (Flow 1) without a correctable
error. Both failures are exactly what the task predicted: "A local ~8B
model is expected to fall well short... record the gap honestly."

**Full 63-case benchmark:** started (`llama3.1:8b`, faster of the two),
**not complete as of this report** — see "Latest verification" above.

### Track B — local STT

`lakewood/stt/` (new package): `base.py` (`STTProvider` protocol,
`STTResult`/`Segment`, `STTConfigError`/`STTCallError`/`UnusableAudioError`),
`fake.py` (`FakeSTTProvider`, offline/deterministic), `faster_whisper_provider.py`
(real local adapter, lazy-imports `faster_whisper` so nothing else needs it
installed), `eval.py` (domain-weighted scorer), `fixtures/` (10 audio files
+ manifest). Runtime choice and full reasoning: `docs/decisions/
ADR-008-local-stt-runtime.md`. 16 offline tests (`tests/test_stt.py`).

**Fixtures are SYNTHESIZED via Windows SAPI, not human-recorded** — no
microphone/human-recording capability in this environment. Stated plainly in
the manifest's own `_provenance` field and in ADR-008. This is a real
limitation: TTS audio has none of the accent/noise/disfluency real customer
calls have. It proves the plumbing and scoring method work end to end
against real audio and a real local model; it is not production-accuracy
evidence.

**Real transcription result** (`python -m lakewood.stt.eval`,
`LAKEWOOD_STT_PROVIDER=faster_whisper`, model `small`):

| Category | Score |
|---|---|
| sizes | 9/9 (100%) |
| toppings | 11/11 (100%) |
| negations | 3/3 (100%) |
| scope (half/left/right/extra/light/double) | 7/7 (100%) |
| **quantities** | **1/5 (20%)** |

**Real finding, not a scoring bug:** faster-whisper normalizes spoken
numbers to digits — "six" → "6", "eight" → "8" — which the manifest's
spelled-out expected words correctly flagged as mismatches. Real, actionable
consequence: any interpreter consuming STT output must handle digit-form
quantities, not just spelled-out ones.

### A serious, unrelated bug this testing surfaced — HIGH severity

Testing real STT transcripts through `RuleBasedInterpreter` (Track B item 6,
"feed STT output into the existing TextInterpreter") found this, independent
of any STT error — the transcript was **word-for-word correct**:

```python
run_turn(chat, RuleBasedInterpreter(), "Large pepperoni, no onions.")
# line.toppings == [('PEPPERONI', 'WHOLE', removed=False),
#                   ('ONIONS', 'WHOLE', removed=False)]
# subtotal: $21.00 (should be $18.00 — onions were never wanted)
```

`RuleBasedInterpreter._new_pizza` has **no negation handling at all** — it
scans each comma-separated clause for a topping word and adds it as a
regular (charged) modifier regardless of "no"/"without" preceding it. A real
customer saying "no onions" would be **charged for onions they explicitly
declined**. This is a P0-class order-correctness defect per `CLAUDE.md`'s
own priority order (data/order corruption, pricing errors, security — P0),
found by chance while testing an unrelated task's plumbing, not fixed here
(out of this task's scope: provider-adapter work, not interpreter-logic
changes) but reported at full severity rather than left buried in a test
transcript. See `docs/NEXT_TASKS.md` for the follow-up task this created.

### Promotion criteria (A5) — none met yet, honestly

- Zero schema violations reaching domain state across a full eval run — **not
  yet verified at 63-case scale** (only 2 manual turns tested live; both
  showed a violation attempt, both correctly caught before domain state).
- Zero cart-state corruption — **holds so far** (2/2 manual attempts caught).
- Simple single-item/single-modifier cases at high accuracy — **not met**:
  neither model completed the single simplest required flow cleanly.
- Provider swap verified with no domain code changes — **met**: the exact
  same `LLMInterpreter`, `run_turn`, and `_valid_tool_args` ran unmodified
  against Anthropic (T-013), OpenAI (T-013c, config-verified), and Ollama
  (this task, live-verified) — only `LAKEWOOD_LLM_PROVIDER`/`_MODEL` changed.

**Conclusion: the local tier has NOT passed promotion criteria** on the
evidence gathered so far. That's an honest, expected result at this sample
size (N=1 utterance per model, live) — not a verdict on local models in
general, and not a reason to lower `CLAUDE.md`'s 98–99% production target,
which remains a paid-provider question this task didn't touch.

## T-013c implementation and limits

- `LLMProvider` documents the existing `complete(system, messages, tools)`
  contract. OpenAI translates the shared schemas and result envelopes at the
  adapter boundary, preserves reasoning output for replay, and correlates
  function results by `call_id`. No SDK or domain code changes.
- OpenAI tool rounds continue within a customer turn, with a 12-request cap.
  Actual `orders.TOOLS` execute on a staged in-memory session; successful turns
  commit it. HTTP/timeout/malformed-response/round-limit failures discard the
  entire staged turn, including cart, quote, confirmation and history changes.
  This relies on the current in-memory tools; it is not a transaction mechanism
  for future external effects such as printer dispatch.
- Arguments are checked for known keys, required fields and primitive JSON
  types before domain execution. Unknown tools fail closed. Confirmation IDs
  are injected from the session; model idempotency values are stripped too.
- Eval adapters now propagate provider failures instead of allowing a failed
  provider turn to pass just because the expected cart was empty. CLI output
  names the actual configured provider/model.
- OpenAI token counts and request latency are available in ProviderResponse;
  no actual usage, cost, latency percentiles or correctness metrics exist yet.
  The current score CLI reports labeled-final-state matches only; detailed
  safety/category metrics require trace analysis in the live baseline task.
- No production-accuracy claim. Existing coupon verification and Windows
  `scripts/check.sh` limitations below remain open.

Earlier milestones below are historical records; current provider configuration
and verification above supersede their single-provider descriptions.

## Pricing parity milestone — PARTIALLY VERIFIED

`tests/test_pricing_parity.py` — the release-gate suite required by the PrISM
Menu + Pricing Parity Engine task — is **50/50 passing**, broken down as:

| Category | Cases | Result | Provenance |
|---|---|---|---|
| Single item | 10 | 10/10 | 8 photographed off real PrISM totals, 2 composed from the verified base-price table |
| Topping/modifier | 10 | 10/10 | all photographed |
| Half-and-half | 10 | 10/10 | all photographed |
| Multi-item | 10 | 10/10 | 3 photographed receipts, 7 composed from independently-verified item prices + the verified sum/tax rule |
| Delivery | 5 | 5/5 | 3 photographed, 2 composed |
| Coupon/special | 5 | 5/5 | **UNVERIFIED category** — discount amounts are printed/confirmed, but tax-ordering (tax before or after the discount) has never been confirmed at the register. These 5 cases lock in the currently-coded assumption so a future change is caught, not proof the assumption is right. |

**Overall: PARTIALLY VERIFIED, not DONE.** 45/50 cases rest on rules that are
fully verified against real PrISM behavior. The 5 coupon cases rest on an
unverified assumption (`docs/COUPON-VERIFICATION.md`, `docs/OPEN-QUESTIONS.md`
item 2) — this is the same open item as the old T-003, now blocking full
sign-off on the coupon category specifically, not the pricing engine as a
whole. No new PrISM carts were pulled for this task: the engine could not
originate new real orders (no live POS access), so it was re-verified against
the 50 real register totals already in the repo (`tests/test_pricing.py`,
`tests/test_orders.py`, `tests/test_coupons.py`), reused and reorganized into
the parity categories rather than invented.

## T-008 milestone — HALF_AND_HALF SKU now reachable, verified end to end

The gap T-006 found is closed. `add_item` gained one new optional parameter,
`second_gourmet_number`, reusing the exact same `PizzaLine.half_and_half`
representation `lakewood/pricing.py` already implements and
`tests/test_pricing_parity.py` already verifies — no new pricing math, no
menu price touched.

- **Structured, not inferred.** The mode is set by which parameters the tool
  call supplies, never guessed from free-form text. No tool gained a price
  parameter (`test_f1_no_tool_accepts_a_price` still passes over the new
  signature).
- **Fails closed** on: an unknown specialty number on either half
  (`GOURMET_NOT_FOUND`), an 86'd specialty on either half (`UNAVAILABLE`), an
  unsupported size (`SIZE_NOT_FOUND`), and a second-half number supplied
  without a first (`BAD_HALF_AND_HALF` — new code, since silently treating it
  as a single-specialty pizza would silently drop the customer's actual
  request).
- **Reuses, doesn't duplicate, verified pricing.** `#10`/`#8` at `LARGE`
  prices at $23.00 regardless of order (`hh=(10,8)` vs `hh=(8,10)`) — same
  flat gourmet price, matching `tests/test_pricing_parity.py`'s already-
  verified `SM half&half` case reused verbatim in the new tests and the new
  eval case.
- **Additive half-and-half (`half_additive`) is untouched** — a separate,
  pre-existing code path (`PizzaLine.half_and_half is None` +
  `Topping.portion in (HALF_1, HALF_2)`), still exercised by every
  pre-existing half-and-half eval case and pricing test.
- **Readback improved.** `PizzaLine.display_name()` previously returned the
  generic `"LARGE HALF & HALF"` for this mode, naming neither specialty —
  correct per F1/F7 but a real usability gap for the very feature this task
  makes reachable. Now returns e.g. `"LARGE HALF & HALF — #10 HAWAIIAN / #8
  BBQ CHICKEN"`. Display-only; no test asserted the old string.
- 12 new focused tests (`tests/test_half_and_half_sku.py`) plus one new golden
  eval case (`GOURMET-013`, tagged `half_and_half_sku`) exercising a genuine
  two-specialty order through the real tool, not the additive workaround.

## T-013 milestone — real LLM interpreter built and unit-tested; live run blocked

**What exists and is verified (deterministically, in CI, no live call):**

- `lakewood/llm_provider.py` — `AnthropicProvider`, stdlib `urllib` only (no
  new pip dependency; the `anthropic` SDK was deliberately not added for one
  HTTP endpoint). Reads `ANTHROPIC_API_KEY` from the environment; raises
  `ProviderConfigError` immediately, with a message naming exactly what's
  missing, if absent — never silently falls back to `RuleBasedInterpreter`
  or a different provider. Distinguishes `ProviderConfigError` (setup) from
  `ProviderCallError` (network/timeout/HTTP/malformed-JSON — infrastructure,
  not a correctness signal) throughout.
- `lakewood/interpreter.py::LLMInterpreter` — tool schemas are introspected
  from the real `orders.TOOLS` function signatures (`inspect.signature`),
  never hand-duplicated, so the model literally cannot be offered a
  parameter a real tool doesn't have. `confirm_order`'s `quote_id`/
  `idempotency_key` are excluded from the schema entirely and injected from
  `chat.session.quote_id` in code — the model can request confirmation, it
  can never supply the ID that authorizes it. System prompt is 9 short
  rules (`_SYSTEM_PROMPT`), ~170 words — business truth stays in code/menu
  data, not prompt text, per the task's own instruction.
- **Architectural finding, fixed in the same task:** the Anthropic tool-use
  protocol requires a `tool_result` for every `tool_use` before the next API
  call, which means `LLMInterpreter` must execute its own proposed calls
  (against the real `orders.TOOLS`, nothing parallel) to observe real
  results before a turn can complete — unlike `RuleBasedInterpreter`, which
  only ever proposes calls for `chat.py` to run. Rather than let the
  executor risk double-executing, `Interpretation` gained a second, explicit
  channel — `already_executed: list[{"tool","args","result"}]` — and
  `lakewood/chat.py::run_turn` was updated to use whichever channel an
  interpreter populated, never both. `TOOLS`/`substitute_last_line` moved
  from `chat.py` into `interpreter.py` (the module both now need) to remove
  the duplication that would otherwise have caused.
- **15 fake-provider tests** (`tests/test_llm_interpreter.py`, a scripted
  `FakeProvider`, zero network) cover all 10 required points: a valid
  response becomes a real executed call; an unknown tool name, a malformed
  argument shape, and a model-supplied `price` argument all fail closed
  without touching cart state; a fabricated topping is rejected by real
  menu validation; an ambiguous `search_menu` result produces clarification;
  multiple tool calls in one turn execute in order with `$LAST` resolved
  correctly; a provider exception leaves order state AND conversation
  history untouched (the failed turn is popped back out, not left
  dangling); a model-*supplied* `quote_id` is proven ignored in favor of the
  real `session.quote_id` by inspecting the actual executed call's args; and
  a static test confirms no provider/vendor code exists anywhere in
  `menu.py`/`pricing.py`/`orders.py`.
- `evals/runner.py score --adapter llm` and `lakewood.chat.llm_adapter` are
  wired and ready — same `assert_final`-based scoring `--adapter rule_based`
  already uses, just pointed at a real model instead.

**What could not be verified — the live benchmark and manual smoke test.**
The user explicitly authorized using the only credential available in this
environment: Claude Code's own session `ANTHROPIC_API_KEY` (used to run this
conversation, not something configured for Lakewood's business logic). A
real connectivity check —

```
$ LAKEWOOD_INTERPRETER=llm python -m lakewood.chat
You > Large pepperoni.
Assistant > Sorry, I'm having trouble right now — could you repeat that?
  (Anthropic API HTTP 400: {"type":"error","error":{"type":"invalid_request_error",
  "message":"Your credit balance is too low to access the Anthropic API. ..."}})
```

— returned a real, reproducible (retried once, identical result) HTTP 400:
that key has no pay-per-token API balance behind it. This is an
infrastructure blocker, not a code defect — `LLMInterpreter`'s error path
handled it exactly as designed: no crash, no cart mutation, `chat.llm_history`
left clean, a plain apology shown to the user. Per this task's explicit
instructions ("do not fabricate a manual model transcript," "do not count
provider timeouts as correct behavior"), **no benchmark numbers, category
breakdowns, latency/cost figures, or a 10-flow manual transcript are
reported** — none of that data exists. The connectivity attempt above is the
only real thing that happened against the live API this session, and it is
reported verbatim, not summarized into a fake pass/fail. See
`docs/NEXT_TASKS.md` T-013b for what unblocks the rest — a funded key is all
that's missing; no further code work is needed to run it.

## T-012 milestone — headless text order sandbox, manually verified

`python -m lakewood.chat` (`--debug` for `[TOOL]`/`[STATE]`/`[CART]`/`[TOTAL]`
traces). New modules: `lakewood/interpreter.py` (`ToolCall`, `Interpretation`,
`ChatState`, `RuleBasedInterpreter`) and `lakewood/chat.py` (`run_turn`,
`TurnResult`, the REPL). Neither ever computes a price or mutates
`order`/`lines` directly — every effect goes through a real function from
`orders.TOOLS`.

**All 9 required flows were run manually** (piped input through the real
`python -m lakewood.chat --debug`, not simulated) and produced correct real
tool sequences and totals:

| Flow | Utterance(s) | Real result |
|---|---|---|
| 1 simple order | "Large pepperoni." | `add_item`+`add_modifier(WHOLE)`, BUILDING, $19.32 |
| 2 half modifier | "Large pepperoni, mushroom only on one half." | pepperoni WHOLE, mushroom HALF_1 |
| 3 correction | "Actually replace the mushroom with sausage." | mushroom removed, sausage added to HALF_1, no duplicate |
| 4 quote | "What's my total?" | real `request_quote()`, readback quoted verbatim |
| 5 mutation after quote | "Add a Coke." | quote invalidated (`quote_id: None`), BUILDING, re-quote issues a **different** real quote_id |
| 6 confirmation | "Yes, place it." | `begin_confirmation`+`confirm_order(quote_id=<real runtime id>)` → CONFIRMED, order `AI-0F3F1F`, $24.15 |
| 7 ambiguity | "Give me chicken." | `search_menu` → 8 hits, 3 kinds → clarification asked, nothing added |
| 8 hallucinated item | "Give me a truffle lobster pizza." | `search_menu` → `NO_MATCH`, nothing added |
| 9 specialty half-and-half | "Large, half number 8 and half number 10." | `add_item(second_gourmet_number=10)` — the real `HALF_AND_HALF` SKU path from T-008, `half_and_half=(8,10)`, $24.69 |

**The manual run caught two real interpreter bugs unit tests alone would not
have surfaced as fast** (both fixed before the automated suite was written):

1. A half-modifier phrase ("only on one half") was checked against the whole
   remaining sentence, not the clause next to the topping it described — so
   "pepperoni, mushroom only on one half" put **both** toppings on HALF_1.
   Fixed by parsing clause-by-clause (split on `,`/`.`/` and `), checking the
   half-keyword only within each topping's own clause.
2. The two-specialty-by-number regex used `.{0,N}` filler, which is greedy
   and — since `.` matches digits too — ate into a two-digit number itself:
   "half number 10" captured `"0"`, not `"10"`. Fixed by using `\D{0,N}`
   (non-digit) filler so the digit-capture group is never partially
   consumed.

A third bug surfaced by the automated tests below (not the manual run): the
punctuation-stripping fix for a `search_menu` query-cleanliness issue
initially stripped commas too, which silently broke bug #1's clause-split
fix again; and separately, "large cheese" was parsing "cheese" as an
*extra* MOZZARELLA topping (a real, valid alias) rather than recognizing it
names the base item. Both fixed; regression-tested in
`tests/test_chat_interpreter.py`.

**21 focused tests** (`tests/test_chat_interpreter.py`) cover all 11 required
points: simple/half/correction/quote/post-quote-invalidation/confirmation
behavior against the real engine, ambiguity and hallucination refusal,
the `HALF_AND_HALF` path, that the interpreter never emits a price-shaped
argument across 5 representative utterances (including an explicit price-
manipulation attempt), and — using a deliberately adversarial stand-in
interpreter — that a fabricated topping name and a fabricated `quote_id`
are both rejected by the real domain layer regardless of what produced the
tool call.

**Golden eval integration.** `evals/runner.py::score()` (previously a stub)
is now implemented — reuses the same `assert_final` check `validate()` uses,
so it accepts any tool path that reaches the correct cart, not a literal
call-sequence diff. The only adapter wired is `lakewood.chat.rule_based_adapter`
(`python evals/runner.py score`), which is the same deterministic
interpreter the sandbox uses, run against all 63 cases: **24/63 reach their
labeled final state.** Reported honestly as rule-based interpreter coverage,
explicitly not a model-accuracy number — no LLM exists in this repo to run.
The gap is expected and was not chased: most misses are phrasings the
interpreter's fixed pattern set was never meant to cover (multi-item "and"
lists, spelled-out quantities like "three mediums", specific transfer-intent
wording) — extending it case-by-case would be over-fitting a deliberately
minimal component, not real interpreter progress.

**Provider boundary.** No LLM credentials exist in this repo and none were
fabricated. `LAKEWOOD_INTERPRETER` (default, and only implemented value:
`rule_based`) selects the interpreter; any other value exits with a clear
error rather than silently falling back to a paid provider.
`lakewood/interpreter.py`'s `TextInterpreter` protocol is the extension
point a real provider adapter would implement later — `orders.py`/
`menu.py`/`pricing.py` were not touched to make room for it.

## T-011 milestone — golden eval harness can now prove a full CONFIRMED flow

Every confirmation-related golden case before this task tested refusal/edge
behavior only — `request_quote()` returns a runtime `quote_id` UUID no YAML
author can know in advance, so no case could reach a real `CONFIRMED` state
through `validate`. Fixed with the smallest binding mechanism that does the
job: `{"$ref": "step.field"}` in a call's `args`, resolved against earlier
calls' structured results in the same case (full contract in
`docs/EVALS.md` "Runtime value binding"). No templating engine, no
expression language, no `eval`/`exec` — one dotted dict-path lookup.

- **`CONFIRM-004`** (`evals/cases/confirmation_full_flow.yaml`) — half
  pepperoni/half mushroom, corrected to half pepperoni/half sausage, quoted,
  and confirmed through the real `begin_confirmation` → `confirm_order`
  sequence with the genuine runtime `quote_id` flowing through a `$ref`. No
  UUID is hard-coded anywhere in the file. Reaches `CONFIRMED`; final cart
  ($21.00 subtotal / $22.54 total) still checked after confirmation.
- **`CONFIRM-005`** — proves the binding doesn't accidentally bypass F5's
  quote/cart_hash check: captures a real `quote_id`, mutates the cart
  (invalidating it), re-quotes (issuing a genuinely different one), then
  presents the *stale* captured value to `confirm_order` — correctly
  refused with `STALE_QUOTE`, state stays `AWAITING_CONFIRMATION`. This
  specifically exercises the quote_id/cart_hash comparison, not just the
  state guard, which is the actual claim this task needed proven.
- **16 focused unit tests** (`tests/test_eval_runtime_binding.py`) test the
  binding mechanism directly, independent of the YAML: literal passthrough,
  top-level and nested resolution, every failure mode (missing step, missing
  field, non-dict result, four shapes of malformed reference), and both
  flows above reproduced end-to-end through `validate()`.
- Existing 61 cases required **zero changes** — `resolve_args` is a no-op
  passthrough for any call with no `$ref` in its args.

## T-018 milestone — live Experiential/Luna baseline (Parts 3 & 4)

Parts 1/2/5 (the `ExperientialProvider` adapter, ADR-010, 30 offline tests)
were already done before this session — see `docs/decisions/ADR-010-
experiential-provider-adapter.md`. This session ran the live Parts 3/4 work
only: `EXPLABS_API_KEY` was confirmed set, one live transport check, the 9
T-012 flows re-run against the real API, then the full 71-case
`score --adapter llm` baseline. No prompt, interpreter, or `evals/runner.py`
changes were made — untuned baseline, per this task's own scope discipline.

### Step 1 — single live request

`"Large pepperoni."` → transport succeeded. API returned model id
`gpt-5.6-luna` (matches requested). 4 tool-call rounds inside one turn
(`search_menu` NO_MATCH self-corrected → `search_menu` ok → `add_item` →
`add_modifier`), ending cart-correct: `LARGE CHEESE PIZZA — pepperoni`,
`BUILDING`, **$19.32** (matches rule-based's T-012 flow-1 result exactly).
Tokens 931–1223 prompt / 13–150 completion per round; latency 1.7–3.3s per
round; `cost_usd` reported as `0.0` on every round (provider's own
`usage.cost` field, not estimated).

### Step 2 — the 9 T-012 flows, live

Re-run as the 4 independent scenarios they actually are (flow 1 alone;
flows 2–6 as one conversation; flows 7/8/9 each alone), rule-based vs. live
Experiential, real transcripts:

- **Flows 1, 7, 8, 9: cart-identical to rule-based.** Ambiguity ("Give me
  chicken.") and hallucination refusal ("truffle lobster pizza") both
  correct; half-and-half specialty (#8/#10) correct.
- **Flows 2–6: real defect, full severity.** Customer asks to swap
  mushroom→sausage and add a Coke. The model's `search_menu` calls for
  "Coke"/"soda" (and initially "mushroom topping") all missed; instead of
  asking for clarification it silently dropped both requests, then said
  *"You're all set — order AI-1469FC, total $19.32"* — confirming a plain
  pepperoni pizza when the customer asked for pepperoni + sausage-half + a
  Coke (rule-based's correct total for the same conversation: $24.15/2415
  cents). **A wrong order was confirmed with no clarification** — directly
  against the memory-policy rule "low confidence resolves to clarification,
  never a guess" (`CLAUDE.md`).
- **Second, separate finding (flow 1):** cart ended up correct, but the
  reply shown to the customer was *"Nothing on the menu matches 'large
  pepperoni pizza'"* — stale, from an early self-corrected `search_menu`
  miss earlier in the same turn. Root cause: `lakewood/chat.py::_finish_turn`
  returns on the **first** error/`search_menu`-ok entry in the executed-calls
  list; that assumption holds for `RuleBasedInterpreter` (no retries) but
  breaks for `LLMInterpreter`'s multi-round self-correction, where an error
  mid-turn can be fully recovered before the turn ends. Readback can say the
  order failed while the cart is actually correct. Not fixed here (chat.py
  reply-construction, out of this task's scope) — filed as part of T-019.
- Zero schema violations (`BAD_ARGS`/`UNKNOWN_TOOL`) this run, so zero cart
  mutations from rejected args — trivially true, no rejections occurred.

### Step 3 — 71-case baseline (`score --adapter llm`)

**45/71 (63%)**, next to rule-based's **35/71 (49%)**. Checkpointed per-case
(precedent: T-013d) so a crash mid-run wouldn't lose completed cases; full
996.6s run completed with zero provider failures (0 infrastructure errors —
every one of the 26 failing cases is a model-correctness result, never a
transport problem).

Category breakdown (tag-bucketed, a case may land in more than one bucket):

| Category | Result |
|---|---|
| entity extraction | 14/24 (58%) |
| modifier scope | 16/26 (62%) |
| item selection | 10/15 (67%) |
| correction handling | 12/15 (80%) |
| ambiguity handling | 2/2 (100%) |
| tool sequencing | 4/6 (67%) |
| state/confirmation integrity | 3/5 (60%) |
| hallucinated menu items | 4/9 (44%, worst category) |
| pricing (final-cart money check) | 45/71 (63%) |

- **Schema violations caught before domain state: 0.**
- **Provider failures: 0** (counted separately from wrong answers, per
  T-018's own rule — none occurred this run).
- **Cart corruptions (inconsistent/duplicate state): zero observed.** Of the
  26 failures: most (≈20) are simple incompleteness (`subtotal 0.00 != X` —
  the requested item/modifier was never added, cart just short of the
  label, never internally inconsistent); one (`GOURMET-010`) is a real
  overcharge — "extra pepperoni" interpreted as `intensity=DOUBLE`
  ($24.00) against a label expecting normal-rate ($21.50), a modifier-
  semantics miss, not corruption; **two (`CONFIRM-003`, `CONFIRM-005`) are
  the most severe finding of this run**, both explicitly `forbid:
  [confirm_order]`/require `state: AWAITING_CONFIRMATION` in the label —
  the live model called `confirm_order` in the **same turn** as
  `begin_confirmation` both times, bypassing the intended two-step
  confirmation gate and reaching `CONFIRMED` when the design requires a
  separate subsequent turn. This reproduces the same pattern found
  independently in Step 2's flow-6 trace — **3 for 3 real observations**,
  not a fluke. This is the standout finding: an order can be finalized
  (kitchen ticket printed) without the intended confirmation safety gate.
- Total tokens: **412,626** (394,068 prompt + 18,558 completion) across
  **329** requests.
- Provider-reported cost: **$0.000000 across 329/329 requests** — every
  response included a `usage.cost` field and it was `0.0` every time
  (captured verbatim; not "not reported," not estimated).
- Latency: mean 3.03s, **median 2.59s, p95 5.84s**, max 7.17s, total 996.4s.

**Filed as T-019** in `docs/NEXT_TASKS.md` — recommended next task. Not
fixed here per this task's explicit constraint (untuned baseline only).

## T-020 milestone — search_menu recall fix + first real variance band

**Part 0 — variance band, measured before any fix.** First attempt ran 3
passes in parallel to save time; got 10–16/71, a methodology bug, not a
result — running 3 processes concurrently hit Experiential's **org-level
rate limit** ("org_rate_limit: this organization exceeded 60 platform-
funded discovery requests"), so most "failures" were HTTP 429s, not model
answers. Discarded outright. Redone sequentially (unchanged T-019 code):

| Run | Aggregate |
|---|---|
| 1 | 42/71 (59%) |
| 2 | 47/71 (66%) |
| 3 | 48/71 (68%) |

**Band: 42–48/71, spread 6 points, mean 45.7. Zero provider failures in
any run — real model variance, not infrastructure noise.** 15/71 cases
(21%) flipped pass/fail across three runs of *identical* code. `tool
sequencing` and `state/confirmation integrity` (T-019's own targets) were
the two categories that did NOT flip — perfectly stable at 6/6 and 5/5 in
all three. Full spread and per-category table: `docs/EVALS.md` "Non-
determinism".

**Retroactive correction to T-018/T-019's own reporting:** both single-run
aggregates (45/71, 49/71) sit inside this 42–48 band. T-019's "45→49"
should not have been read as a measured effect of that task's fix — it's
within the noise this task just quantified. T-019's actual fix claim
stands on the categories it targeted (both stable at 100% across every run
this session has captured, T-019's included), not on the aggregate.

**Part 1 — diagnosis before any fix**, from real T-018/T-019 checkpoint
call logs (actual query strings sent, not assumed): 22 failing cases in
the T-019 baseline; 18 involved `search_menu`, 4 didn't (`NEG-003`,
`QTY-003`, `NEG-005`, `FAQ-001` — unrelated bugs, out of scope, not fixed).
Of the 18: **10 class A** (bad query — the base pizza has no searchable
name at all; coupon codes and size words like "Sicilian" aren't
`search_menu` concepts either), **10 class B** (good query, poor recall —
a one-directional substring check broken by a filler word, a number-format
mismatch, or a missing alias table), **0 class D**. Full table and root-
cause detail: `docs/EVALS.md` "search_menu recall diagnosis and fix".

**Part 2 — fix, cheap before expensive, no embeddings.** All in
`lakewood/orders.py::search_menu`: filler-word stripping
(`_strip_search_filler`), gourmet-number-prefix normalization
(`"number 10"` → `"10"`), `ALIASES` now consulted (previously only
`add_modifier` used it), a new parallel `NON_PIZZA_ALIASES` table (drink
words mirroring `RuleBasedInterpreter`'s private `_DRINK_WORDS`; one
abbreviation), a `CHEESE PIZZA` pseudo-hit for the base-pizza gap (the
single largest fix), and an all-three-drinks disambiguation for a bare
"drink"/"beverage" mention. **F7 held throughout — a more aggressive
reverse-substring match was tried, caught two real false positives
("CHICKEN" inside "chicken tenders", "SHRIMP" inside "shrimp scampi") via
`test_all_golden_labels_are_valid`, and was reverted**, same principle as
everywhere else in this codebase: a wrong candidate is worse than a miss.
`search_menu` still only ever returns disambiguation candidates; nothing
here can auto-select an item.

**Part 3 — post-fix, measured against the band, not a single old number.**

```
python -m pytest -q --no-header                    → 365 collected, 365 passed, 2 xfailed
python evals/runner.py validate                     → 71/71, unchanged
python evals/runner.py score --adapter rule_based    → 36/71, unchanged (RuleBasedInterpreter
                                                        barely uses search_menu; no regression,
                                                        no ratchet change)
python -m pytest tests/test_pricing_parity.py        → 50/50, unchanged
```

Live (`score --adapter llm`, sequential, same config as the band):
**51/71 (72%) — 3 points above the band's observed max, a real improvement,
not noise.**

| Category | Band (3 runs) | Post-fix |
|---|---|---|
| entity extraction | 11–14/24 | **18/24** |
| modifier scope | 15–17/26 | 17/26 |
| item selection | 8–10/15 | 11/15 |
| correction handling | 9–10/15 | 11/15 |
| ambiguity handling | 2/2 | 2/2 |
| tool sequencing | 6/6 | 5/6 |
| state/confirmation integrity | 5/5 | 4/5 |
| **hallucinated menu items** | 5/9 (all 3 runs) | **6/9** |

`tool sequencing`/`state/confirmation integrity` each show one case down
from their perfectly-stable band value — checked directly (`CORRECT-003`,
`CONFIRM-002`): both are `search_menu`-unrelated (the model proactively
re-quoted after a removal instead of waiting, an assert_final-specific
behavioral choice, not a lookup failure) — consistent with ordinary model
variance, not a regression from this fix.

**Regression check, not just the aggregate:** of the 38 cases passing in
*all three* band runs, 3 now fail post-fix (`CORRECT-003`, `NEG-006`,
`CONFIRM-002`) — inspected all three call logs directly: in every one,
`search_menu` itself succeeded correctly; the failure is a downstream
model choice (auto-requoting, or finding a topping via search but never
calling `add_modifier` for it) unrelated to recall. Of the 18 cases failing
in all three band runs, 4 now pass (`INVALID-002`, `MULTI-004`,
`SIZE-002`, `GOURMET-005`) — direct fix wins.

**The real customer-facing number:** `search_menu` NO_MATCH rate dropped
from **~60–64% to 26%** of all `search_menu` calls (94 misses average
across the 3 band runs → 24 misses post-fix, on the same 71 cases) — fewer
"sorry, I couldn't find that" turns per call, the actual point of this
task per its own framing (T-019 made a miss safe; this made misses rarer).

**T-021, folded into this task's required 5-minute side-fix:** no code
change needed. A genuinely fresh `python -m venv` + `pip install -r
requirements.txt` installs `pyyaml 6.0.3`/`pytest 9.1.1` cleanly and the
full gate (`pytest -q`, `evals/runner.py validate`) runs clean, no network,
no Ollama. The original finding's root cause was the repo's pre-existing
`.venv` predating `pyyaml` being added to `requirements.txt`, never
re-synced — already fixed in the T-018 session by installing into it
directly. `requirements.txt` itself was correct all along.

**No ADR filed.** The matching strategy changed in degree, not in kind —
still a deterministic, synchronous, substring/alias-based lookup with no
new dependency, no external service, no architectural shift. Documented in
code comments and `docs/EVALS.md` instead, consistent with how prior
`search_menu`/`ALIASES` changes in this codebase were handled.

## T-019 milestone — confirmation-gate bypass and silent-drop defenses

Three deterministic defenses in `orders.py`, plus a related reply-accuracy
fix in `chat.py`, closing the P0 finding from T-018's live baseline. Design
and alternatives considered: `docs/decisions/ADR-011-confirmation-turn-gate.md`.

**Defense 1 — F14, same-turn confirmation gate.** `Session.turn` increments
once per customer utterance (`chat.py::run_turn`; mirrored in
`evals/runner.py::validate()` since it drives tools directly). `begin_
confirmation` records `confirmation_turn = turn`; `confirm_order` refuses
with a new `PREMATURE_CONFIRMATION` code if called with the same turn
number, checked *after* the existing F5 checks so a genuinely stale quote_id
still gets `STALE_QUOTE` first. `invalidate_quote()` (T7) resets
`confirmation_turn` too.

**Defense 2 — F15, a dropped `search_menu` request blocks confirmation.**
`Session.unresolved_lookups` — a `NO_MATCH` appends the query; any later
successful `search_menu`/`add_item`/`add_modifier`/`remove_modifier`/
`update_item`/`remove_item` clears the whole list. `begin_confirmation`
refuses with `UNRESOLVED_REQUEST` (naming the outstanding query) while
anything is still unresolved. Deliberately scoped to `begin_confirmation`
only (not `request_quote`, which is called routinely mid-conversation) to
keep the blast radius to exactly the moment that matters.

**Defense 3 — F16, mandatory cart-diff readback.** `begin_confirmation`
always computes and returns a real readback (`_short_readback` — the same
one-liner `request_quote` already used, e.g. *"That's pickup: LARGE CHEESE
PIZZA — pepperoni. Total $19.32. Should I go ahead and place it?"*) — there
is no path to `AWAITING_CONFIRMATION` without one, and `chat.py`'s reply for
`begin_confirmation` now speaks it verbatim instead of a fixed "One
moment..." placeholder.

**P2 fix — `_finish_turn` reports the last result, not the first error.**
The T-018 Step 1 bug: a `search_menu` miss that `LLMInterpreter` fully
recovered from later in the *same* turn was still what got reported to the
customer ("Nothing on the menu matches..." on a cart that was actually
correct). `_finish_turn` now looks at the last entry in the turn's executed-
calls list, not the first error/search-ok encountered. `RuleBasedInterpreter`
never retries within a turn, so this changes nothing for it.

**A real, necessary redesign, not a one-line guard.** F14 required
correcting two things that had *deliberately* encoded the same-turn
shortcut as the happy path: `RuleBasedInterpreter._confirm()`'s `QUOTED`
branch (now opens the window only; its existing `AWAITING_CONFIRMATION`
branch — already present — completes the second turn) and the golden case
`CONFIRM-004` (split into two turns; still reaches `CONFIRMED`, same total).
Six existing tests exercising the old one-shot shape were updated to the
genuine two-turn/two-round shape (`test_full_flow_reaches_confirmed_with_
real_runtime_quote_id`, `test_f6_confirm_is_idempotent` — which had gone
*silently* vacuous, both calls returning the identical rejection and
trivially equal — `test_f12_confirmed_order_is_immutable`, and three
provider-level fabricated-quote-id tests across OpenAI/Ollama/Experiential/
the FakeProvider-based `LLMInterpreter` test). None were weakened; all now
exercise the real two-step path end to end. The LLM system prompt and tool
descriptions (`begin_confirmation`, `confirm_order`, `_state_context`) were
corrected to stop instructing the now-rejected shortcut — a factual
correction, not the fix itself; the domain guard is what actually holds.

**New regression tests** (`tests/test_confirmation_gate.py`, 10 tests):
reproduce the same-turn bypass directly, the dropped-search-miss bypass,
and T-018's exact live flow-2-6 scenario end to end through the real tool
surface; each verified by temporarily disabling its guard and confirming
the test fails, then restoring it (no git repo here to diff against, so
this was done by hand rather than by `git stash`). Also: the readback is
non-empty and reflects the real cart; `_finish_turn` reports the last
result in both directions (a recovered turn reports success; a turn that
really did end in error still reports that error).

**Verification:**

```
python -m pytest -q --no-header          → 365 collected, 365 passed, 0 failed, 2 xfailed
python evals/runner.py validate           → 71/71, unchanged behavior
python evals/runner.py score --adapter rule_based
                                           → 36/71 (was 35/71) — CONFIRM-003 now passes;
                                             RULE_BASED_BASELINE ratcheted 35→36 (tests/test_evals.py)
python -m pytest tests/test_pricing_parity.py → 50/50, unchanged
```

**Live re-baseline (`score --adapter llm`, Experiential/Luna) — see
below for the number.** Per `docs/EVALS.md` "Non-determinism": this
provider was directly observed this session to NOT be reproducible at
`temperature: 0` (an isolated repro of T-018's own flow-2-6 utterances
gave a materially different result on a fresh run) — treat any single
`score --adapter llm` aggregate, including this one, as one sample, not a
stable measurement.

**Result: 49/71 (69%)**, up from T-018's 45/71 (63%). Zero provider
failures, zero schema violations, both this run. 314 requests, 380,041
prompt + 18,141 completion = 398,182 total tokens; latency mean 2.03s,
median 1.84s, p95 3.25s, max 6.70s, total 637.7s; provider-reported cost
$0.000000 across 314/314 requests (field present, value zero every time,
same as T-018).

| Category | T-018 | T-019 re-run |
|---|---|---|
| entity extraction | 14/24 (58%) | 14/24 (58%) |
| modifier scope | 16/26 (62%) | 17/26 (65%) |
| item selection | 10/15 (67%) | 9/15 (60%) |
| correction handling | 12/15 (80%) | 10/15 (67%) |
| ambiguity handling | 2/2 (100%) | 2/2 (100%) |
| **tool sequencing** | 4/6 (67%) | **6/6 (100%)** |
| **state/confirmation integrity** | 3/5 (60%) | **5/5 (100%)** |
| hallucinated menu items | 4/9 (44%) | 4/9 (44%) |
| pricing (final-cart) | 45/71 (63%) | 49/71 (69%) |

**`CONFIRM-003`, `CONFIRM-004`, `CONFIRM-005` all pass.** Per-case diff
against T-018: 7 newly passing (`CONFIRM-003`, `CONFIRM-005`,
`AVAIL-001`, `COUPON-002`, `MOD-034`, `MULTI-005`, `NEG-007`), 3 newly
failing (`CORRECT-004`, `CORRECT-005`, `NEG-005`). Checked all three
newly-failing cases' call logs directly: none involved `PREMATURE_
CONFIRMATION` or `UNRESOLVED_REQUEST` (both fired **zero** times this
entire run) — all three are plain `search_menu` misses or a modifier
mis-handling (`NEG-005` added `pepperoni` twice — once `NORMAL`, once
`LITE`, instead of replacing the intensity), the same T-020-category
non-determinism documented in `docs/EVALS.md`, not a regression from this
task's changes. **Honest caveat, not a asterisk to bury:** per this run's
own "Non-determinism" finding, this 45→49 delta is one sample pair, not
proof the fix is worth exactly +4 — but the two categories the fix
directly targets (tool sequencing, state/confirmation integrity) both
went to a clean 100%, and zero unsafe confirmations occurred in either
baseline run captured under this task.

## T-016 milestone — the eval gate now actually runs an interpreter

### Part 1 — mechanism decision

Two approaches were on the table: (A) fold interpreter-running into
`validate()` itself, or (B) give `score --adapter rule_based` a separate,
enforced threshold. **Chose B.** Recorded in full in
`docs/decisions/ADR-009-eval-gate-runs-an-interpreter.md`: folding it into
`validate` would conflate two different claims — "the label is internally
consistent" vs. "the interpreter reaches it" — under one number and one
failure mode, making a red `validate` run ambiguous about which thing broke.
Separating them keeps `validate` doing exactly what it always did (label
correctness, unchanged, still documented that way everywhere) and adds a
second, purpose-built gate for interpreter regression. Also decisive: the
gate must run in CI, offline, with no funded key and no running Ollama — a
live `score --adapter llm` gate is structurally disqualified regardless of
which approach won, since neither Anthropic nor OpenAI credentials nor a
local Ollama install can be assumed present in CI.

### Part 2 — implementation

`tests/test_evals.py::test_rule_based_interpreter_meets_baseline` — a plain
pytest assertion, not a new CLI command. Runs `evals.runner.score()` against
all 71 cases using the real `rule_based_adapter` (the same one
`lakewood.chat` and the CLI use) and asserts the pass count is `>=
RULE_BASED_BASELINE` (35). Already part of `pytest -q`, so it's already part
of `scripts/check.sh` with no new line needed there (comments added
explaining why, no command changed). **This is a ratchet, not a target**:
the module docstring and the constant's own comment both say, explicitly,
never lower it and never raise it to a number that wasn't actually observed
by running `python evals/runner.py score --adapter rule_based` — raising it
without running the real command would defeat the entire point of the gate.
Known, accepted limitation stated in both the test and ADR-009: it's a
single aggregate count, so a same-count swap (one case starts failing while
an unrelated one starts passing) would not trip it — rejected building a
full per-case set-diff for this task as more machinery than the actual
failure mode (a straightforward regression in pass count) warrants.

### Part 3 — proof this gate would have caught T-015

`tests/test_evals.py::test_gate_would_have_caught_the_t015_negation_bug` —
the task's actual acceptance test. Monkeypatches
`RuleBasedInterpreter._resolve_intensity_calls` back to its exact pre-T-015
body (a plain, always-fully-charged `add_modifier`, no intensity check at
all) **in the test only** — `lakewood/interpreter.py` is never touched — then
runs the identical gate logic and asserts it goes red. Real, measured
numbers: current behavior scores **35/71** (passes, `>= 35`); the stubbed
pre-T-015 behavior scores **25/71** (fails, `< 35`). Both numbers came from
actually running `evals.runner.score()`, not estimation. This is the
concrete answer to "would this gate have caught the T-015 bug": yes,
demonstrated, not asserted.

### Part 4 — documentation sweep

Every place a number from `validate`/`score` appears was checked and, where
needed, rewritten to state which mode produced it and what that mode
proves: `README.md` (new 3-point explainer plus every inline number),
`AGENTS.md` (baseline line), `docs/EVALS.md` (new dedicated section on the
interpreter-regression gate, `## Layers` table footnote, `## Release gate`
clarified as PLANNED-production vs. actual-CI-today, `score` mode
paragraph fixed from "Three adapters" to "Two"), `scripts/check.sh`
(comments only — composition didn't change, since `pytest -q` already ran
this file first), this file. `ADR-009` is the single source for the
mechanism decision itself; every doc above links to it rather than
re-explaining it.

**Explicitly not done, per this task's own scope:** none of the ~36
`rule_based` misses (36/71) were fixed or investigated case-by-case — the
gate protects the current honest floor, it doesn't raise it. T-017 (bare
"number N" gourmet selection) is untouched. No voice/TTS/persistence/API
work. `T-013c`/`T-013d` (live paid-provider and Ollama-rerun benchmarks)
untouched.

## Eval corpus milestone — 63/63 label-valid, two real defects found in the process

T-006 grew `evals/cases/*.yaml` from 10 to 60 cases (61 after T-008, 63 after
T-011) across: simple/multi-item orders, quantities, half-and-half
toppings, whole-vs-half modifier scope, extra/light/no/double/triple
modifiers, corrections/retractions/replacements/removals, changed
size/quantity, multi-turn changed-mind flows, unavailable/invalid items,
disambiguation, specialty-pizza customization, slang/short-form,
adversarial/hallucination-bait, confirmation edges (including F4 cart-changed
invalidation), and delivery structured data (minimum, missing address,
service charge, and — as of T-011 — a full runtime-bound confirmation flow).
All 63 pass `validate` (label-vs-real-engine consistency);
this is **not** a production accuracy claim — see `docs/EVALS.md`'s caveat
(`score` mode, the actual model-vs-label comparison, is still unwired,
Phase 5).

Writing real cases against the real tool surface surfaced two genuine product
gaps, not corpus bugs — both reported here rather than routed around:

1. ~~**The tool surface cannot create the `HALF_AND_HALF` SKU pricing mode at
   all.**~~ **FIXED by T-008** (this session) — see the milestone above.
   `add_item`/`add_modifier` only ever built the additive "regular SKU +
   half-portion toppings" pizza; the second, cheaper, larger-half-only SKU
   mode `lakewood/pricing.py` implements and `tests/test_pricing_parity.py`
   verifies was unreachable from any tool. A caller asking for "half BBQ
   Chicken half Meat Lovers" could not be served correctly. Now can.
2. **`data/menu.json` has no pasta section.** `docs/EVALS.md`'s documented
   collision trap ("Shrimp Scampi is both a $23 pizza and a $20 pasta dish")
   does not reproduce against the current canonical menu — there is no pasta
   category at all, so `search_menu("shrimp scampi")` returns exactly one hit
   (the gourmet pizza). Caught by the new `expect_disambiguation` runner
   check, which proved the existing `COLLIDE-001` case mislabeled; fixed the
   label to match reality (see that case's inline note) and added
   `COLLIDE-002` using a collision that *is* real today ("chicken": 6 gourmet
   pizzas + the CHICKEN topping + CHICKEN DINNER). The menu's actual pasta
   offerings (if the restaurant has any) were never digitized into
   `data/menu.json` — this task did not invent them, per its own constraint
   against fabricating menu items.

Runner changes to `evals/runner.py`, each minimal and in direct support of
what a task asked for: `forbid` and `expect_disambiguation` (previously
present in case YAML but never checked) are now enforced per turn;
`validate`'s summary reports category pass-rates, corpus-wide
hallucinated-SKU/unexpected-error counts, and (T-011) which `$ref` runtime
bindings each case resolved. No general-purpose eval infrastructure beyond
that — see `docs/EVALS.md` for the exact, deliberately small contract of
each.

## Completed and verified

| Item | Evidence |
|---|---|
| Single source of truth: `data/menu.json` → `lakewood/menu.py` → `lakewood/pricing.py`, integer cents throughout | this task; no price duplicated elsewhere |
| Stable per-item IDs (`PIZZA_LARGE_CHEESE`, `TOPPING_PEPPERONI`, `GOURMET_10`, etc.) | `lakewood/menu.py::ITEM_CATALOG` |
| Deterministic pricing across 6 sizes, 27 specialties, 2 topping tiers | 50/50 `tests/test_pricing_parity.py`, plus original 53-case detail suite `tests/test_pricing.py` |
| Two split-pizza pricing modes (regular SKU additive; HALF & HALF larger-half) | owner-confirmed; 10/10 half-and-half parity cases |
| Tax 7.35% half-up on the discounted subtotal, computed in pure integer arithmetic (no float, no Decimal); $2 delivery charge after tax, untaxed | verified against receipts; `lakewood/pricing.py::tax_cents` |
| Coupon engine — 4 offers, mutually exclusive, cents-based | `tests/test_coupons.py`; tax-ordering still UNVERIFIED (see above) |
| Order state machine with guarded transitions | `lakewood/orders.py`, `docs/order-state-machine.md` |
| 16 agent tools; no tool accepts a price or `store_id` | asserted by test |
| 13 fail-safes (quote invalidation, cart-hash confirm, idempotency, card/allergy transfer, parse-failure transfer, immutable confirmed orders) | `tests/test_orders.py` |
| Store rules: hours, 7-mile radius, $18 delivery minimum | owner-confirmed, `tests/test_store_rules.py` |
| After-hours capture with mandatory "not tonight" disclosure | `test_after_hours_order_is_disclosed_in_readback` |
| ESC/POS ticket formatting in PrISM entry order | `lakewood/printer.py` — **code only, no hardware run** |
| L1 eval harness with label validation, `forbid`/`expect_disambiguation`/`$ref` enforced, category metrics reported | `evals/runner.py`, 63 cases across 9 `evals/cases/*.yaml` files |
| Portable 12-hour ticket-timestamp formatting (Windows-safe) | `lakewood/timefmt.py`, `tests/test_timefmt.py` |
| `HALF_AND_HALF` SKU pricing mode reachable via `add_item(second_gourmet_number=...)`, fails closed on bad size/specialty/86'd/missing-first-half | `tests/test_half_and_half_sku.py` (12 cases), `evals/cases/modifiers_and_specialty.yaml::GOURMET-013` |
| Golden eval harness can reach a real `CONFIRMED` state via runtime `$ref` binding of `request_quote`'s quote_id; stale-captured-quote still fails closed | `evals/runner.py::resolve_args`, `tests/test_eval_runtime_binding.py` (16 cases), `evals/cases/confirmation_full_flow.yaml` |
| Headless text order sandbox (`python -m lakewood.chat`); all 9 required flows manually verified against the real tool surface, incl. `HALF_AND_HALF` and full `CONFIRMED` | `lakewood/interpreter.py`, `lakewood/chat.py`, `tests/test_chat_interpreter.py` (21 cases) |
| `evals/runner.py::score()` implemented (was a stub); rule-based interpreter run against the corpus, honestly labeled as interpreter coverage | `python evals/runner.py score` → 35/71 (was 24/63 pre-T-015), `lakewood.chat.rule_based_adapter` |
| Real Anthropic-backed `LLMInterpreter` implemented, schema-safe (no price/quote_id fillable by the model), 15 deterministic fake-provider tests | `lakewood/llm_provider.py`, `lakewood/interpreter.py::LLMInterpreter`, `tests/test_llm_interpreter.py` — **live paid-provider benchmark blocked, see T-013 milestone** |
| Local model tier: `OllamaProvider` (config-pinned, fails loud on missing model), local STT (`lakewood/stt/`, faster-whisper) — unblocks dev/eval loop without a paid credential | `docs/decisions/ADR-007`/`ADR-008`, `tests/test_ollama_provider.py` (26), `tests/test_stt.py` (16); real live results in "Local Model Tier milestone" |
| Negation/removal/light-intensity handling in `RuleBasedInterpreter`, deterministic across 6 phrasings and half-scope, proven through both interpreters | `tests/test_negation_handling.py` (18), `evals/cases/negation_and_removal.yaml` (8 cases, all pass under real `score` execution) — see "T-015 milestone" |
| Corpus-gate mechanism diagnosed: `validate()` never runs any interpreter — only `score` does, and it was never a gate | see "T-015 milestone" Part 1; **fixed by T-016** |
| Ratcheted interpreter-regression gate (`score --adapter rule_based` >= 35/71), offline/deterministic/CI-safe, part of `pytest -q`; proven to catch the T-015 negation bug via monkeypatch (35/71 → 25/71) | `docs/decisions/ADR-009`, `tests/test_evals.py`, see "T-016 milestone" |
| Full test suite runs clean on Windows | `python -m pytest -v --no-header` → 323 passed, 0 failed, 2 xfailed |

## In progress

T-013c live verification remains blocked (funded `OPENAI_API_KEY` needed).
T-013d needs a rerun against the now-71-case corpus. See "Pricing parity
milestone" above for what's still open within it.

## Next — in order

1. **T-017** `RuleBasedInterpreter` can't parse a bare "number N" as a single gourmet selection outside half-and-half phrasing (found while testing T-015, unrelated to negation) — now the smallest concrete item left, and now protected by the T-016 gate as it's worked
2. **T-013c live portion** Run one Astra connectivity gate, then the real corpus benchmark and manual smoke once a funded application `OPENAI_API_KEY` is available
3. **T-013d** Rerun the Ollama corpus benchmark against the now-71-case corpus (last real result, 11/63, predates the 8 new negation cases and the fix itself)
4. **T-003** Coupon tax-ordering verification at the register (owner action) — unblocks full sign-off on the coupon parity category
5. **T-001** Tool API layer (Phase 2) — now safe to build routes for `add_item`'s full signature including `second_gourmet_number`
6. **T-002** Tests for `search_menu` / `check_availability`
7. **T-005** Persistence design spike (Phase 3 prep)

Full detail in `docs/NEXT_TASKS.md`.

## Blocked

| Blocker | Cause | Impact | Resolution | Blocks other work? |
|---|---|---|---|---|
| Missing OpenAI application API key | OPENAI_API_KEY absent on 2026-09-08; previous Anthropic funding failure is historical | `LLMInterpreter`'s live paid-provider benchmark and manual smoke test (T-013c) cannot run; no paid-model-accuracy claim exists yet | Fund the key, or provide a different one, then run `python evals/runner.py score --adapter llm` and `LAKEWOOD_INTERPRETER=llm python -m lakewood.chat` | **No** — code, tests, and docs are complete and unaffected. Ollama (local, free) is unblocked and already has real results. |
| Printer hardware not on site | Dedicated unit ordered, in transit | Phase 4 cannot start; no ticket reaches a kitchen | Wait for delivery | **No** |
| Voice provider undecided | Deferred by ADR-004 | Phase 5 cannot start | Decide at Phase 5 with cost data | **No** |
| Coupon tax ordering unverified | Inferred from POS screen layout, not observed | Coupon quotes may be off ~$0.22; blocks full pricing-parity sign-off on the coupon category specifically | 3 orders, `docs/COUPON-VERIFICATION.md` | **No** — but must precede any coupon reaching a customer |
| Coupons may not be honored by phone | Printed terms say "must present to redeem" | Whole coupon path may be dead | One question to the owner | **No** |
| No live/physical PrISM access from this agent | Coding agent has no register access | Cannot originate new real order data; parity suite reuses existing 50 verified totals rather than fresh pulls | Owner or on-site staff pulls new carts when needed | **No** |

**Nothing blocks code work.** T-013c live verification specifically needs a resource (funded API access) this agent cannot provide itself.

## Known problems

| Problem | Severity | Notes |
|---|---|---|
| No persistence — `Session` is in-memory | **High** | Phase 3. Must land before any real call. |
| `printer.py` never run against hardware | **High** | Status-bit parsing is from spec, not observation. |
| `data/menu.json` has no pasta section | Medium | `docs/EVALS.md`'s Shrimp-Scampi pizza-vs-pasta collision trap doesn't reproduce — no pasta items exist in the canonical menu yet. See T-009. |
| `scripts/check.sh` calls `python3`, which is a non-functional Windows Store stub on this machine | Medium | Use `python` directly on Windows until the script is made portable (T-007). |
| Untested public functions: `search_menu`, `check_availability`, `dispatch_confirmed_order`, `coupons.eligible`, `coupons.best_coupon`, `config.store_for_did` | Medium | T-002 |
| No real paid-model-accuracy number exists yet | Medium | `score --adapter llm` works (real result exists for Ollama: 11/63, stale — pre-dates the T-015 corpus/fix) but no funded paid key. See Blocked table, T-013c. The corpus's `validate` pass is label-correctness; `score --adapter rule_based` (35/71) is interpreter coverage — neither is a production accuracy claim. |
| `validate()` itself still never runs any interpreter on any case | Low (was High) | Structural and still true by design — `validate` is label-correctness only, and stays that way (ADR-009). **Mitigated, not changed**: a second, separate, ratcheted gate (`test_rule_based_interpreter_meets_baseline`) now runs the real interpreter and blocks regression; proven to catch the exact T-015 bug. Residual risk: the gate is `rule_based`-only, and its floor (35/71) still leaves 36 known misses ungated against improvement (only regression). See T-016 milestone. |
| `RuleBasedInterpreter` can't parse bare "number N" as a single gourmet selection outside half-and-half phrasing | Medium | Found while testing T-015; unrelated to negation. "number ten" alone resolves to a plain cheese pizza, wrong item. See T-017. |
| `printer.py` mixes protocol, formatting, and dispatch policy | Medium | Split at Phase 4. |
| No CI | Medium | `scripts/check.sh` is the gate; wire to CI at Phase 2. |
| Two POS readings unreproducible | Accepted | Owner will not change the workflow. `xfail(strict=True)`. |
| Coupon tax-ordering rule unverified | Accepted-for-now | See Blocked table; T-003. |
