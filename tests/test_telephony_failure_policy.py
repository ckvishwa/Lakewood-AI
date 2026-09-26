"""T-053 Phase 2 Part 4 — repeated-failure escalation to a human."""

from lakewood.telephony.failure_policy import MAX_CONSECUTIVE_FAILURES, FailureEscalation


def test_single_failure_does_not_escalate():
    esc = FailureEscalation()
    assert esc.record(is_failure=True) is False


def test_two_consecutive_failures_escalate():
    esc = FailureEscalation()
    assert esc.record(is_failure=True) is False
    assert esc.record(is_failure=True) is True


def test_success_resets_the_counter():
    esc = FailureEscalation()
    esc.record(is_failure=True)
    assert esc.record(is_failure=False) is False
    assert esc.record(is_failure=True) is False   # back to 1, not 2 -- no escalation yet
    assert esc.record(is_failure=True) is True


def test_success_never_escalates():
    esc = FailureEscalation()
    for _ in range(10):
        assert esc.record(is_failure=False) is False


def test_threshold_constant_is_two():
    # A change to this constant is a real product decision (this module's
    # own docstring reasoning) -- pin it explicitly so a future accidental
    # edit shows up as a failing test, not a silent behavior change.
    assert MAX_CONSECUTIVE_FAILURES == 2
