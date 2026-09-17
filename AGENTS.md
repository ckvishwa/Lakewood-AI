# Agent instructions

Canonical operating instructions live in **[`CLAUDE.md`](./CLAUDE.md)** at the
repository root. They apply to every coding agent, not just Claude Code.

Reading order for a new agent:
`CLAUDE.md` → `docs/STATUS.md` → `docs/NEXT_TASKS.md` → `docs/PRD.md` →
`docs/ARCHITECTURE.md` → relevant ADRs → the source and tests you are changing.

Baseline at last verification: **323 passed, 0 failed, 2 xfailed** (includes
the T-016 ratcheted interpreter-regression gate, ADR-009), **71/71** eval
labels valid (label correctness only — `validate` never runs an
interpreter, see ADR-009) — clean on Windows and Linux/Mac alike.
Verify with `pip install -r requirements.txt && python -m pytest -q && python
evals/runner.py validate` (or `./scripts/check.sh` where `python3` resolves)
before changing anything.
