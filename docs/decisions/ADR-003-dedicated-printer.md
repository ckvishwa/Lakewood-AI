# ADR-003 — Dedicated printer, never shared with PrISM

**Status:** Accepted · 2026-09-06

## Context

The store has an Epson TM-T88V (M244A) **wired directly to the PrISM Manager PC
with no network interface**. PrISM owns that port.

## Options

1. Share the existing printer via the Manager PC.
2. Install an agent on the Manager PC to relay print jobs.
3. Buy a second, network-attached printer.

## Decision

Option 3. A dedicated unit on tcp/9100. **Ordered.**

## Rationale

Options 1 and 2 put our code on the machine running the store's POS during
service. A bug there stops the restaurant. The isolation is worth ~$150.

## Tradeoffs

One more device on the counter; the store must have a network drop or wifi
bridge nearby.

## Consequences

- `printer.py` targets tcp/9100 and carries a `dry_run` flag.
- Never send test jobs to the store's existing printer.
- Two tickets now exist for an AI order (ours, then PrISM's). Staff workflow
  must be observed once live to confirm this is not confusing.
