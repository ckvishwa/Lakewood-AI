# ADR-016 — Parakeet is the local-pilot STT provider

**Status:** Accepted · 2026-09-17

**Supersedes ADR-008's provider choice for the local pilot.** ADR-008 remains
the record of the original faster-whisper decision and its adapter remains a
supported fallback.

## Context

ADR-008 selected faster-whisper before either candidate had been measured on
the actual development hardware. Both providers have now transcribed the same
2.586125-second human-recorded order on that machine:

> Two large pepperoni pizzas with no onions.

Hardware/runtime: NVIDIA GeForce RTX 3050 Ti Laptop GPU (4 GB), WSL Ubuntu
26.04, CUDA-capable PyTorch and NVIDIA NeMo. Both transcripts were semantically
correct; they differed only in terminal punctuation.

| Measurement | faster-whisper `small`, CPU int8 | Parakeet unified English 0.6B, GPU |
|---|---:|---:|
| Warm median | 2.748 s | 0.082 s |
| Audio duration | 2.586 s | 2.586 s |
| Realtime factor | 1.063 | 0.032 |
| Speed | 0.9x realtime | 31.5x realtime |
| Cold model load | 12.340 s | 11.073 s |
| Measured memory | 0.95 GB RSS | 2.60 GB GPU peak |

This is a latency/provider-selection measurement, not an accuracy claim. One
clean human clip cannot establish restaurant-call accuracy; the existing
synthetic fixture corpus cannot establish it either.

## Decision

Use `nvidia/parakeet-unified-en-0.6b` as the active local-pilot STT provider.
Keep faster-whisper available as a CPU fallback.

Run NeMo/Parakeet as one warm, localhost-only service inside WSL. The Windows
Lakewood process uses `ParakeetProvider`, which sends a PCM WAV turn to that
service and returns the existing provider-neutral `STTResult`. The model loads
once, stays resident, and serializes inference on the single 4 GB GPU.

The server binds only to `127.0.0.1`. Exposing it to another host would require
TLS, authentication, request limits, and a separate deployment decision; this
ADR does not authorize that.

## Why this boundary

- Importing NeMo into the Windows Lakewood environment duplicates a large,
  fragile CUDA dependency stack.
- Starting WSL/NeMo for every turn pays the 11-second cold load repeatedly.
- A warm localhost process preserves the measured 82 ms inference path while
  keeping NeMo types out of the application and domain layers.
- The existing STT contract and voice loop do not change when the provider
  changes.

## Consequences

- Local development uses two processes: the WSL Parakeet service, then the
  Windows voice loop.
- Service startup/health is explicit; the client fails closed if it is absent,
  times out, rejects audio, or returns malformed/empty output.
- Parakeet is English-only. Live translation is not provided by this model and
  would require a separate multilingual STT/translation adapter.
- This does not choose the production telephony vendor or prove cloud unit
  economics. ADR-004 remains open for the production voice/telephony stack.
- Promotion still requires real human speech across accents, kitchen noise,
  phone compression, negation, quantities, and modifier scope.

## Verification

Offline contract tests cover WAV validation, service outage/timeout behavior,
HTTP rejection, malformed JSON, empty transcripts, provider selection, and the
server's NeMo-output normalization. The real GPU benchmark above was run on the
owner's machine. A full microphone -> Parakeet -> interpreter -> order engine ->
SAPI loop remains the next hardware verification step.
