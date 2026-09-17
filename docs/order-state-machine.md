# Order & Call State Machine — Corrected Spec

Replaces PRD §11.

## 0. Core correction

The PRD conflates two machines. `CALL_STARTED` is not an order state — a call can exist with no order, and an order can outlive the call (dispatch, store ack). Split them.

Also: `CONFIRMED` is **not** terminal. The PRD's biggest gap is that nothing tracks whether the restaurant actually received the order.

---

## 1. Call FSM

```
RINGING → ANSWERED → IN_PROGRESS → COMPLETED
                                  → TRANSFERRED
                                  → ABANDONED   (caller hung up)
                                  → FAILED      (system fault)
```

| Field | Notes |
|---|---|
| `call_id` | ULID |
| `store_id` | **Resolved server-side from the inbound DID. Never a model parameter.** |
| `from_number` | E.164 |
| `order_id` | nullable — many calls create no order |
| `disclosure_played_at` | recording-consent line, required before any audio is retained |

---

## 2. Order FSM

```
DRAFT
  → BUILDING
      → QUOTED
          → AWAITING_CONFIRMATION
              → CONFIRMED
                  → SENT_TO_STORE
                      → STORE_ACKED   (terminal, success)
                      → FAILED_DISPATCH
              (any cart mutation) ──┐
          (any cart mutation) ──────┤
                                    ↓
                                 BUILDING

Terminal failure states (reachable from any non-terminal state):
  CANCELLED · TRANSFERRED · EXPIRED · FAILED_DISPATCH
```

### 2.1 Transition table

| # | From | Event | Guard | To | Effect |
|---|---|---|---|---|---|
| T1 | — | `create_order` | valid store, idempotency key unused | DRAFT | order row created |
| T2 | DRAFT | `set_order_type` | type ∈ {pickup, delivery}; if delivery → address captured **and** in zone | BUILDING | — |
| T3 | BUILDING | `add/remove/update_item`, `add/remove_modifier` | item AVAILABLE, modifier valid for item | BUILDING | recompute `cart_hash` |
| T4 | BUILDING | `request_quote` | cart non-empty; delivery minimum met | QUOTED | pricing engine issues `quote_id` + `cart_hash` + `expires_at` |
| T5 | QUOTED | `readback_delivered` | quote not expired | AWAITING_CONFIRMATION | start 45s confirm timer |
| T6 | AWAITING_CONFIRMATION | `customer_confirms` | **`quote_id` valid AND `cart_hash` matches AND quote not expired AND explicit affirmative** | CONFIRMED | cart frozen; order becomes append-only |
| T7 | **QUOTED or AWAITING_CONFIRMATION** | **any cart mutation** | — | **BUILDING** | **`quote_id` = null; readback invalidated** |
| T8 | CONFIRMED | `dispatch` | — | SENT_TO_STORE | push to printer / tablet / SMS |
| T9 | SENT_TO_STORE | `store_ack` | ack within 90s | STORE_ACKED | — |
| T10 | SENT_TO_STORE | `dispatch_timeout` | 3 retries exhausted | FAILED_DISPATCH | page on-call + notify store by second channel |
| T11 | any non-terminal | `customer_cancels` | — | CANCELLED | — |
| T12 | any non-terminal | `transfer_triggered` | — | TRANSFERRED | log `transfer_reason` |
| T13 | DRAFT/BUILDING/QUOTED | `abandon_timeout` (15 min) | — | EXPIRED | retained for callback recovery |

**T7 is the transition the PRD is missing and the one that causes wrong totals.**

---

## 3. Invariants (enforce in code, not prompt)

- **INV-1** — `subtotal`, `tax`, `fees`, `total`, and all modifier prices are writable **only** by the pricing engine. No agent-facing tool accepts a price argument.
- **INV-2** — The only path into `CONFIRMED` is T6. There is no other write path, in any code branch.
- **INV-3** — Any cart mutation in `QUOTED` or `AWAITING_CONFIRMATION` forces `BUILDING` and nulls the quote. A quote never survives a cart change.
- **INV-4** — `CONFIRMED` orders are immutable. A change after confirmation = cancel + new order, linked by `supersedes_order_id`.
- **INV-5** — `store_id` is bound to the call session server-side. Every tool call is scoped to it. The model cannot read or write another store's data even if instructed to.
- **INV-6** — Every transition writes to `order_events`: `{order_id, from, to, event, actor (ai|system|staff), call_id, quote_id, cart_hash, ts}`.
- **INV-7** — `create_order` and `confirm` require an idempotency key. Replays return the original result, never a second order.
- **INV-8** — No tool accepts card data. Any card-number-shaped utterance → immediate `TRANSFERRED`, and that audio segment is not persisted.

---

## 4. Timers

| Timer | Duration | On expiry |
|---|---|---|
| Quote TTL | 5 min | quote invalid; re-quote required before confirm |
| Confirm wait | 45 s | one re-prompt → then `TRANSFERRED` |
| Ambiguous-response retries | 2 attempts | → `TRANSFERRED` (reason: `repeated_misunderstanding`) |
| Abandoned order | 15 min | → `EXPIRED`, recoverable |
| Dispatch ack | 90 s × 3 retries | → `FAILED_DISPATCH` |
| Max call duration | 8 min | → `TRANSFERRED` (cost + UX guard) |
| Dead air | 12 s × 2 | → hang up (prevents voicemail-loop burn) |

---

## 5. Dropped-call recovery

The PRD has no path for this and it will happen on ~3–5% of calls.

1. Call drops while order is in `BUILDING`/`QUOTED` → order stays live for 15 min.
2. Same `from_number` calls back within that window → agent opens with: *"Looks like we got cut off — I have a large pepperoni started. Want to keep going?"*
3. Accept → resume in `BUILDING`. Decline → `CANCELLED`, start fresh.
4. Never auto-resume into `AWAITING_CONFIRMATION`. Always re-quote and re-read back.

---

## 6. Confirmation grammar

`customer_confirms` fires **only** on an explicit affirmative:

- **Accept:** yes / yeah / yep / correct / that's right / go ahead / sounds good / that's it / perfect
- **Reject (treat as ambiguous, re-prompt once):** uh / hmm / I guess / sure I guess / okay... / maybe / silence / any utterance containing a change request
- **Reject (treat as mutation → T7):** "yes but make it large", "correct, and add a coke"

Second ambiguous response → `TRANSFERRED`. Do not attempt a third.

---

## 7. Agent tool surface

The model may call **only** these. Everything else is server-internal.

```
get_store_info(fields)
search_menu(query, category?)
check_availability(item_id)
set_order_type(type, address?)
add_item(item_id, size_id, quantity)
update_item(line_id, {size_id?, quantity?})
remove_item(line_id)
add_modifier(line_id, modifier_id, portion, intensity?)
remove_modifier(line_id, modifier_id)
request_quote()                  → returns quote_id, breakdown, total
confirm_order(quote_id)          → server re-checks cart_hash
cancel_order(reason)
transfer_to_human(reason)
```

`portion` ∈ `{WHOLE, LEFT, RIGHT}` · `intensity` ∈ `{NORMAL, EXTRA, LIGHT, NONE}`

No tool returns a writable price field. No tool takes `store_id`.
