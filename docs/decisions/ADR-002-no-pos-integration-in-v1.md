# ADR-002 — No PrISM integration in V1; staff re-key the ticket

**Status:** Accepted · 2026-09-06

## Context

The design partner runs MicroWorks PrISM 8.1.153. Directory listings indicate no
public API; every advertised integration (DoorDash, GrubHub, WebOrder) is
brokered by MicroWorks, whose commercial interest is selling their own online
ordering. **Observed on site: Slice and DoorDash orders already arrive on
separate tablets and are re-keyed into PrISM by hand.**

## Problem

Do we block V1 on obtaining a supported order-injection interface?

## Options

1. Wait for a MicroWorks partner API.
2. Local bridge agent on the PrISM Manager PC.
3. RPA / UI automation.
4. Print a ticket; staff re-key, matching the existing Slice workflow.

## Decision

Option 4 for V1. A read-only menu export request goes to MicroWorks separately,
sent by the owner, as a background thread that blocks nothing.

## Rationale

The store already tolerates an unintegrated channel twice daily. Matching a
workflow they run every day is a far smaller ask than a vendor negotiation, and
it removes the only dependency we cannot control. Options 2 and 3 risk breaking
a live POS — unacceptable in a design partner's store.

## Tradeoffs

V1 does not eliminate order entry, only phone answering. **Do not oversell this
to the owner.** The honest pitch is: nobody tied to the phone, no missed calls,
no forgotten upsells, plus after-hours capture.

## Consequences

- Ticket layout must mirror PrISM's entry sequence so re-keying is fast.
- The re-key produces a free accuracy oracle: our quote vs the keyed ticket,
  reconciled nightly.
- If MicroWorks ever opens an interface, it slots in behind the same boundary.
