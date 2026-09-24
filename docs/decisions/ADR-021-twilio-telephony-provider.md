# ADR-021 — Twilio is the telephony provider; closes ADR-004

**Status:** Accepted · 2026-09-24 (T-053 Phase 2 Part 0)

## Context

ADR-004 deferred the voice-provider decision to "Phase 5," priced against a
buy-vs-build cascade table, and required a vendor be recorded as ADR-004a
when chosen. Two things have changed since ADR-004 was written:

1. The STT/TTS legs of that original cost table are **already closed and
   self-hosted** — ADR-016 (Parakeet, local GPU service) and ADR-020
   (Piper, CPU/ONNX) — not bought per-minute from a vendor. ADR-004's own
   $/min figures assumed a bought cascade; the real cascade this system
   runs today has near-zero marginal STT/TTS cost. ADR-004 is retroactively
   right about the *decision* ("buy, don't build" the parts that aren't the
   moat) but wrong about *which* parts get bought — telephony transport
   (PSTN + media WebSocket), not STT/TTS.
2. T-053 Phase 2 needs the one piece that's still unbought: a phone number,
   PSTN termination, and a bidirectional audio WebSocket to carry caller
   audio to/from this system's existing `PersistentChat`/`run_turn_traced`
   path (`lakewood/chat.py`).

This ADR picks that provider and closes ADR-004 for good — no ADR-004a is
needed; this document is the record ADR-004 asked for.

## Candidates evaluated

**Twilio (Programmable Voice + Media Streams)** and **Telnyx (Call Control
+ bidirectional media streaming)**. Both real US CPaaS carriers, both
support the exact shape this system needs: answer a PSTN call, open a
bidirectional WebSocket carrying 8kHz call audio, run our own logic, speak
back. Evaluated on the dimensions T-053 Phase 2's own brief asked for, each
with a source — no figure below is asserted without one.

### 1. Bidirectional audio over WebSocket, and its format

Both stream 8kHz narrowband audio over a plain WebSocket, matching this
system's existing 8kHz phone-line assumption (ADR-015's amendment already
capped TTS rate for "real compressed telephony audio").

- **Twilio:** `audio/x-mulaw`, 8000 Hz, fixed — confirmed directly from the
  Media Streams WebSocket Messages reference: each `media` message carries
  a `track` field (`"inbound"`/`"outbound"`), so a bidirectional stream can
  tell caller audio from this system's own played-back audio on the same
  socket — directly answers T-053 Phase 2 Part 2's "consume the caller's
  audio track only" requirement, natively, no extra bookkeeping needed.
  [Media Streams WebSocket Messages](https://www.twilio.com/docs/voice/media-streams/websocket-messages)
- **Telnyx:** configurable — PCMU/PCMA/G722 at 8kHz, or Opus/AMR-WB/L16 at
  8 or 16kHz (`stream_bidirectional_codec` parameter); mismatched codec
  triggers on-the-fly transcoding, a real quality risk if misconfigured.
  Track separation for bidirectional streams is not detailed in the public
  media-streaming doc page — would need to be re-verified once inside a
  real account before trusting it the way Twilio's documented `track` field
  can be trusted today. [Media streaming docs](https://developers.telnyx.com/docs/voice/programmable-voice/media-streaming)

### 2. How each authenticates its requests to us

Neither provider signs the **media WebSocket** itself — both require the
app to build its own signed, short-lived, single-use stream-URL token (the
Part 1 requirement in T-053 Phase 2's brief applies to either vendor
equally; this is not a Twilio-specific gap).

- **Twilio webhook (HTTP, call-start):** `X-Twilio-Signature` header,
  HMAC-SHA1 over the full request URL + sorted POST params, keyed by the
  account Auth Token. Validated with Twilio's own maintained
  `RequestValidator` (official SDK, not hand-rolled crypto).
  [Webhooks security](https://www.twilio.com/docs/usage/webhooks/webhooks-security)
- **Telnyx webhook (HTTP, call-start):** `telnyx-signature-ed25519` +
  `telnyx-timestamp` headers, Ed25519 asymmetric signature over
  `{timestamp}|{raw body}`, verified against Telnyx's published account
  public key. **Stronger primitive than Twilio's HMAC-SHA1** (asymmetric,
  no shared secret to leak on our side, and the timestamp is a first-class
  part of the signed message rather than something we have to separately
  invent replay protection around).
  [Telnyx webhook receiving docs](https://developers.telnyx.com/docs/development/api-fundamentals/webhooks/receiving-webhooks)

Real, documented security edge to Telnyx here — noted honestly, not
minimized just because Twilio wins on balance below.

### 3. Fallback / forward-on-failure — the Part 4 safety net

Both support a **provider-hosted, zero-app-server-dependency** static
fallback that can `<Dial>` the restaurant's real line even if this
system's entire server is offline — the exact mechanism Part 4 needs
("your server is down entirely → the call rings the restaurant's own
line"):

- **Twilio:** number's Voice Fallback URL can point at a **TwiML Bin**
  (Twilio-hosted static XML, no server involved at all) containing a bare
  `<Dial>+1XXXXXXXXXX</Dial>` — fires automatically on connection failure
  or a non-2xx/timeout from the primary webhook.
  [Programmable Voice failover best practices](https://www.twilio.com/docs/voice/twilio-voice-failover-best-practices)
- **Telnyx:** equivalent via a **TeXML Bin** (same shape — Telnyx-hosted
  static markup, `<Dial><Number>` forwarding, no app server needed), plus
  a separate webhook-level Failover URL for retryable HTTP failures.
  [TeXML Bin voicemail/forwarding](https://support.telnyx.com/en/articles/13386198-texml-bin-simple-voicemail-and-call-forwarding)

**Parity.** Neither vendor forces us to rely on our own infrastructure
being up for the one failure mode that matters most for a pilot.

### 4. Latency and cost at ~30 calls/day

ADR-004's own volume estimate (~3,150 min/month) reused here for a direct
comparison; every rate below is sourced, not estimated.

| | Twilio | Telnyx |
|---|---|---|
| Inbound local voice | $0.0085/min ([US voice pricing](https://www.twilio.com/en-us/voice/pricing/us)) | $0.0032/min SIP + $0.002/min Voice API ≈ $0.0052/min ([Voice API pricing](https://telnyx.com/pricing/voice-api)) |
| Media stream | $0.004/min ([Media Streams overview](https://www.twilio.com/docs/voice/media-streams)) | $0.0035/min ([media streaming pricing update](https://telnyx.com/release-notes/media-streaming-and-decrypted-forking-service-pricing-updates)) |
| **Combined /min** | **$0.0125** | **$0.0087** |
| Local number, monthly | $1.15/mo | $1.00/mo |
| **~3,150 min/month total** | **≈ $40.5/mo** | **≈ $28.4/mo** |

Telnyx is ~30% cheaper (~$12/month at this volume). At P7 (cost
optimization ranks below reliability/operational simplicity per
`CLAUDE.md`'s own priority order), a $12/month delta is not the deciding
factor either way — recorded for completeness, not weighted heavily.

Neither vendor publishes a directly comparable "audio-path latency" figure
independent of our own network/transcoding stage; **Part 6 of this
task measures the real, combined number on whichever vendor is chosen**,
which is the only trustworthy latency figure for this system regardless of
vendor marketing claims.

### 5. US local number provisioning (203 area code)

Neither vendor's pricing page confirms live inventory for a specific area
code (that's only checkable inside a real account at provisioning time).
Both are large enough US carriers that Northeast/Connecticut inventory is
realistically available; this is **not independently verified** and is
called out here rather than asserted.

### 6. Documentation quality

Twilio: the deeper, more mature reference set for this exact use case —
Media Streams (the underlying primitive for voice-AI-over-phone) is
Twilio's most heavily documented and most widely used-in-production
pattern for this category, with an official, actively maintained Python
`RequestValidator`. Telnyx's docs are real and usable but thinner on this
specific bidirectional-track question (see §1) — had to be treated as
unverified rather than trusted from the docs alone.

## Decision

**Twilio.** Primarily on **reliability and integration risk** (`CLAUDE.md`
priority #2, above cost efficiency at #7): this is the first
internet-exposed endpoint this system has ever had, and Twilio's Media
Streams path is the most battle-tested version of exactly this integration
shape, with one concretely verified requirement (native inbound/outbound
`track` separation, §1) that Telnyx's public docs left unconfirmed. The
~30% cost gap (§4) is real but immaterial at this call volume and this
priority order. Telnyx's stronger webhook-signing primitive (§2) is noted
as a genuine point in its favor and should weigh more heavily if this
provider choice is revisited post-pilot.

This is a technical choice made with evidence, not a product one — no
owner decision required for the vendor itself (the "OWNER DECISIONS" list
in this task's brief is about consent/transfer-target/forwarding-mode, not
which carrier).

## Consequences

- `lakewood/telephony/` (new) isolates Twilio-specific request/response
  shapes behind an interface — no vendor type may appear in `pricing.py`,
  `orders.py`, or `menu.py`, per ADR-004's original constraint (unchanged,
  now enforced against a real vendor instead of a deferred one).
- Webhook signature validation uses Twilio's own `RequestValidator`
  (official SDK), not a hand-rolled HMAC comparison — per its own explicit
  recommendation, and per this project's own "don't hand-roll security
  primitives you can buy" instinct.
- The media WebSocket still needs our own signed/short-lived/single-use
  token scheme (Part 1) — Twilio provides no native WS-level signature,
  confirmed directly by its own `<Stream>` docs saying nothing about it.
- Fallback wiring: the number's Voice Fallback URL points at a Twilio-
  hosted TwiML Bin with a bare `<Dial>` to the restaurant's real line —
  built and proven by literally stopping the app server and calling
  (Part 4/Part 5's own acceptance criteria).
- Cost per call must still be logged from the first real call, per ADR-004's
  original consequence — unchanged by which vendor was chosen.
- If Telnyx's bidirectional track-separation gap (§1) turns out to matter
  later (e.g., a second store wants a cheaper line, or Twilio's WS auth
  story proves awkward in practice), this ADR should be revisited with a
  real Telnyx account's docs/dashboard, not just its public pages.
