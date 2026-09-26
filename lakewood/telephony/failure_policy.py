"""
Repeated-failure escalation (T-053 Phase 2 Part 4).

"Every failure degrades to something a caller can recover from — never
silence, never a dead line" (this phase's own brief). A single STT
hiccup, one silent turn, or one LLM provider blip already degrades to a
spoken apology (Part 2's `_STT_TROUBLE_REPLY` / the interpreter's own
`_PROVIDER_TROUBLE_REPLY`) — never a crash. This module is the ESCALATION
on top of that: a customer stuck failing repeatedly, for any reason, gets
rescued by a human instead of being asked to repeat themselves forever.

**One counter across every failure type, not three.** The brief lists
STT failure, silence, and LLM failure as separate scenarios, but a
customer doesn't care WHICH layer is struggling — two bad turns in a row
is the same experience regardless of cause, and maintaining three
separate thresholds would only make the escalation point less
predictable, not more correct.
"""

from __future__ import annotations

from dataclasses import dataclass

# Matches the phase brief's own scenario wording literally ("say nothing
# -> graceful prompt, THEN transfer" / "force an LLM failure -> apology,
# THEN transfer" both read as: one apology, then escalate on the very
# next failure) — not tuned against real call data, since none exists yet
# (same honest caveat VadConfig's own defaults carry).
MAX_CONSECUTIVE_FAILURES = 2

TRANSFER_ANNOUNCEMENT = (
    "I'm having trouble helping with that — let me transfer you to "
    "someone who can."
)


@dataclass
class FailureEscalation:
    consecutive_failures: int = 0

    def record(self, is_failure: bool) -> bool:
        """Call once per completed turn. Returns True the instant this
        turn's failure pushes the count to/past `MAX_CONSECUTIVE_FAILURES`
        — the caller must transfer, not just apologize again. A SUCCESSFUL
        turn resets the count to zero, even after a prior failure — a
        customer who recovers on their own is never penalized for an
        earlier bad turn."""
        if not is_failure:
            self.consecutive_failures = 0
            return False
        self.consecutive_failures += 1
        return self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES
