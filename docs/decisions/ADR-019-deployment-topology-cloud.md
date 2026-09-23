# ADR-019 — Deployment topology: cloud, not on-prem

**Status:** Proposed · 2026-09-23. T-053 Phase 0 — reported and stopped for
approval per that task's own instruction; nothing past this decision has
been implemented.

## Context

The current stack cannot deploy as-is:

- **SAPI TTS is Windows-only.** Parakeet runs on Linux with a GPU. A
  deployed server is one box, and SAPI cannot run on it (Phase 1 of T-053
  replaces it regardless of which topology this ADR picks).
- **The printer is at `10.1.10.197`**, a restaurant-LAN address. Whatever
  answers the phone must reach it somehow.
- **Telephony needs a public endpoint** the phone provider can reach.

All three hinge on one decision: does the box that answers the phone live
at the restaurant, or in the cloud?

## Decision

**Cloud.** Orchestrator, Parakeet (GPU), and Postgres all run in the
cloud. The printer is reached by a **self-built lightweight polling
relay** at the restaurant — not Epson's own Cloud Services registration
(see "Printer: relay, not Epson Connect" below, a refinement on the
brief's suggested Server Direct Print approach).

## Why, against each factor the task asked to weigh

### Security — this is the deciding factor

**On-prem requires inbound connectivity into the restaurant's own
network** for the phone provider to reach the orchestrator — a tunnel or
a port-forward into a small-business/residential-grade router, kept
running indefinitely. `docs/SECURITY_AUDIT_T054.md` (T054's own Part 6)
already named "inbound ports into a restaurant LAN" as a real exposure
class before this task existed. Cloud keeps the ONLY inbound-facing
surface at a professionally-hosted endpoint behind the phone provider's
own infrastructure (Twilio/Telnyx terminate the PSTN side; this
project's own public endpoint is HTTPS/WSS behind normal cloud
firewalling, not a residential router). The restaurant's own network
needs **zero inbound ports** under the cloud option — the printer relay
(below) is outbound-poll-only, the same posture normal payment
terminals and POS-cloud integrations already use.

### Fit with the multi-tenant decision (ADR-014)

ADR-014 built the Postgres schema tenant-shaped from day one,
specifically to avoid a retrofit once a second store exists — "one
Postgres instance serving one store's traffic through a schema that
happens to already be tenant-shaped." **On-prem, one box per restaurant,
makes that schema decision pointless**: N separate Postgres instances,
one per site, is exactly the SQLite-single-store shape ADR-014 rejected,
just relocated. Cloud is the only topology where ADR-014's own stated
reason for existing (a second store eventually sharing the same
instance) is even reachable. `lakewood/config.py::store_for_did` is
still a single-store stub today ("becomes a lookup when a second store
onboards") — real multi-store config resolution is explicitly out of
T-053's scope either way, but only cloud keeps that door open without a
second migration later.

### Cost at ~30 calls/day

Neither option is cheap at this volume in isolation — a GPU sitting
mostly idle either way, on-prem or cloud. The difference is what idle
capacity can be shared: an on-prem GPU is dedicated to ONE restaurant's
~30 calls/day, full stop. A cloud GPU behind the same multi-tenant
schema can, once a second store exists, serve two restaurants' call
volume off the same idle capacity — the only path to the economics
ADR-004 already required ("COGS must stay ≤25% of ARPU... model choice
is the business model"). On-prem also carries a cost ADR-004's table
never counted: **hardware shipped and supported per store** — a real,
recurring operational burden this project has no infrastructure for yet
(no fleet management, no remote monitoring of restaurant-owned
hardware).

### What happens when the restaurant's internet drops

This is the sharpest, most concrete test, and it favors cloud clearly:

- **Both options already depend on the internet for the LLM call itself**
  — `llm_provider.py` only ever calls a remote API (Anthropic/OpenAI/
  Experiential); there is no local model in this design. `internet
  drops` degrades order-TAKING identically either way, and
  `RuleBasedInterpreter` (fully offline, zero network calls, the exact
  thing the ratcheted baseline gate already proves works standalone) is
  the real fallback in both topologies — this is not a point in
  on-prem's favor the way it might first look.
- **The difference is whether the PHONE ITSELF can be answered.**
  On-prem ties the inbound tunnel/port-forward to the restaurant's own
  internet — if it drops, the phone provider cannot reach the
  orchestrator at all, and the call doesn't even ring through. That is a
  strictly worse failure mode than a dropped ordinary phone line.
  Cloud's phone endpoint stays reachable regardless of the restaurant's
  own connectivity — a call is answered and can be taken, confirmed, and
  persisted in cloud Postgres even while the restaurant is offline.
- **The printer is the one piece that's unavoidably restaurant-bound
  either way.** Under cloud, a confirmed order during a restaurant-side
  outage can't reach the printer until connectivity returns — this
  needs a held/retry state, not a new mechanism: `printer.py::dispatch`
  already retries with backoff and raises `DispatchError` →
  `FAILED_DISPATCH` → "alert staff by second channel" on exhaustion,
  the same shape `HELD_FOR_OPEN` already uses for after-hours orders.
  **The order is never lost either way** — F12's confirmed-order
  immutability and the existing dispatch-retry path already cover this;
  what changes under cloud is that the CALL still gets answered while
  the ticket waits.

## Printer: a self-built relay, not Epson Connect

The brief's Option B suggested Server Direct Print "if T-049 confirmed
the TM-m30III supports it." T-049 FINAL confirmed the **Cloud Services
tab is present** in the real device's web config, but explicitly did
**not** enable it — registering the printer with Epson's own cloud
service is "a real, external, consequential action," left deliberately
untaken. That means Server Direct Print via Epson's own cloud is
**unvalidated**, not just unbuilt: no print job has ever been proven to
flow through it end-to-end, and choosing it would add a third-party
cloud dependency (Epson's infrastructure, outside this project's
control) as a new, untested trust boundary between a confirmed order
and the kitchen.

**Recommended instead:** a small, self-built polling relay at the
restaurant — a lightweight process that polls the cloud orchestrator
(`GET /pending-tickets?store_id=...` over HTTPS, outbound only, no
inbound port) and forwards any pending ticket to the printer over the
LAN via the **existing, already-tested** `TcpRawTransport`/`TicketPrinter
.build`/`.dispatch` code, unchanged. This is a few dozen lines, adds no
new external trust boundary, requires zero inbound restaurant-side
ports, and reuses 100% of `printer.py` as verified by T-049 FINAL
against the real hardware. `ServerDirectTransport`'s current
`NotImplementedError` stub becomes this relay's server-side counterpart
(receiving the poll, handing back the next pending payload) — the named
slot T-049 FINAL left for exactly this task, filled with the safer of
the two ways to fill it.

## Consequences

- Phase 1 (TTS replacement) targets a Linux-deployable provider — no
  change to that evaluation from this decision.
- Parakeet moves from the current WSL-localhost-only service (ADR-016,
  which explicitly did NOT authorize exposing it beyond `127.0.0.1`) to
  a cloud GPU instance reachable only from the orchestrator's own
  private network — a new, narrow authorization this ADR grants,
  replacing ADR-016's "not this ADR" boundary for the deployed target
  only; the local `127.0.0.1`-only dev path is unchanged.
- `TicketPrinter`/`TcpRawTransport`/ESC/POS formatting: **unchanged**,
  reused as-is by the new relay.
- `ServerDirectTransport` gets a real implementation this task, as the
  relay's server-side counterpart — not Epson Cloud Services.
- `docs/ARCHITECTURE.md`'s telephony/deployment rows move from CURRENT:
  none to this topology, once Phase 1–3 land.
- Multi-store config resolution (`store_for_did` becoming a real lookup)
  stays explicitly out of scope, per T-053's own OUT OF SCOPE list —
  this decision keeps that door open without requiring it now.

## Alternatives considered

**On-prem (Option A).** Rejected — see Security and Multi-tenant-fit
above; the deciding factors, not a close call.

**Server Direct Print via Epson Cloud Services.** Rejected in favor of
the self-built relay — see "Printer" section above. Revisit only if the
relay proves harder to operate in practice than expected; Epson's
service remains available if so, now that its presence is confirmed.

**Hybrid (cloud orchestrator, on-prem STT).** Not seriously considered —
reintroduces the same inbound-connectivity problem this ADR rejects for
Option A, just for the audio stream instead of the whole call.
