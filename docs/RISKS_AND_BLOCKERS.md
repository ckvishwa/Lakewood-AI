# Risks and blockers

Store-specific factual questions live in `docs/OPEN-QUESTIONS.md`. This file is
project risk. Severity × likelihood, with a trigger that forces action.

## Blocking now

| # | Risk | Sev | Likelihood | Mitigation | Trigger |
|---|---|---|---|---|---|
| B1 | **No persistence.** `Session` is in-memory; a restart loses a live call and its order. | High | Certain once live | Phase 3 before Phase 5 | Any real call — do not route traffic before this |
| B2 | **Printer untested.** `printer.py` has never driven hardware. Status-bit parsing is from the ESC/POS spec, not observation. | High | Likely to need fixes | Phase 4 on the dedicated unit | Hardware arrives |

## Not blocking, must not be forgotten

| # | Risk | Sev | Likelihood | Mitigation | Trigger |
|---|---|---|---|---|---|
| R1 | **Unit economics.** A flagship speech-to-speech model puts COGS at ~$284/mo against $299 ARPU — 5% margin, non-viable. | Critical | Certain if chosen carelessly | ADR-004 constrains choice; cost logged per call from day one | Any provider decision |
| R2 | **Quote-vs-register mismatch.** Documented POS variance means some split pizzas ring $6–7 below our quote. | Med | Certain, bounded | Owner will not change; reconciliation treats it as expected variance | Reconciliation goes live |
| R3 | **Coupon tax ordering unverified.** We tax after discount based on screen layout, not observation. | Med | 50/50 | Three test orders, `docs/COUPON-VERIFICATION.md` | Before any coupon reaches a customer |
| R4 | **Coupons may not be honored by phone.** Printed terms say "must present to redeem." | Med | Unknown | One question to the owner; if no, `COUPONS = []` | Before pilot |
| R5 | **Eval corpus is 10 cases.** Any accuracy claim today is unfounded. | High | Certain | Phase 6 to ~250 | Before quoting a number to anyone |
| R6 | **Provider lock-in** creeping into the domain core. | Med | Likely without discipline | Interfaces only; no vendor types in `pricing/orders/menu`; enforced by review | Every provider integration |
| R7 | **ASR collisions on this menu** — five Buffalo Chickens, Shrimp Scampi as both pizza and pasta. | High | Certain | Dedicated eval bucket; category resolved before item name | Corpus build |
| R8 | **Adoption.** Owner may not pay $299 even with proven ROI. | High | Unknown | Overflow-only pilot produces the ROI number before the ask | Pilot week 2 |
| R9 | **Competition.** Voice-AI-for-restaurants is crowded; Slice already sells phone AI *to this store*. | High | Certain | Compete on measured accuracy + legacy-POS pragmatism, not on being first | Ongoing |
| R10 | **Single design partner.** Everything is tuned to one menu. | Med | Certain | Adapter boundaries already separate menu data from logic | Second store |
| R11 | **Recording consent** if expanding beyond one-party-consent states. | Med | Certain on expansion | Disclosure line at call start | First out-of-state store |
| R12 | **MicroWorks may never open an interface.** | Low | Likely | V1 does not depend on it; staff re-key by design | N/A |
| R13 | **Founder bandwidth.** Several other substantial projects are open. This one needs to be the primary. | High | Unknown | Phased plan with hard exit criteria so partial progress is still usable | Any phase slipping twice |

## Architectural uncertainty (not blockers)

- Voice provider choice — deferred to ADR-004, does not block Phases 2–4.
- SQLite vs Postgres — SQLite decided (ADR-005); revisit at store #5.
- Whether to eventually build a PrISM adapter — deliberately out of V1.
