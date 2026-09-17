"""
The recovery story (ADR-014): resume window, mandatory revalidate-and-reprice,
and the "menu changed while the call was dropped" case that makes
unconditional quote invalidation on resume a real requirement, not caution
for its own sake.
"""

import time

import pytest

from lakewood import orders as oe
from lakewood.orders import UNAVAILABLE, add_item, add_modifier, request_quote, set_order_type
from lakewood.persistence.memory_repository import InMemorySessionRepository
from lakewood.persistence.recovery import (
    RESUME_WINDOW_SECONDS, find_resumable_session, revalidate_and_reprice,
)
from lakewood.persistence.service import resume_or_create


@pytest.fixture
def repo():
    return InMemorySessionRepository()


def _fresh(call_id="C1"):
    s = oe.Session(call_id=call_id, store_id="STORE-001", from_number="+12035551234")
    set_order_type(s, "pickup")
    return s


def test_find_resumable_session_within_window(repo):
    sess = _fresh()
    add_item(sess, "PIZZA", size="LARGE")
    repo.save_session(sess)
    found = find_resumable_session(repo, "STORE-001", "+12035551234", now=time.time() + 60)
    assert found is not None
    assert found.call_id == "C1"


def test_find_resumable_session_outside_window_returns_none(repo):
    sess = _fresh()
    add_item(sess, "PIZZA", size="LARGE")
    repo.save_session(sess)
    later = time.time() + RESUME_WINDOW_SECONDS + 1
    assert find_resumable_session(repo, "STORE-001", "+12035551234", now=later) is None


def test_find_resumable_session_none_when_nothing_in_flight(repo):
    assert find_resumable_session(repo, "STORE-001", "+12035551234") is None


def test_revalidate_clean_cart_reports_no_issues(repo):
    sess = _fresh()
    add_item(sess, "PIZZA", size="LARGE")
    result = revalidate_and_reprice(sess)
    assert result.clean
    assert result.issues == []


def test_revalidate_flags_a_now_86d_topping(repo):
    sess = _fresh()
    r = add_item(sess, "PIZZA", size="LARGE")
    add_modifier(sess, r["line_id"], "pepperoni")
    UNAVAILABLE.add("PEPPERONI")
    try:
        result = revalidate_and_reprice(sess)
        assert not result.clean
        assert any("PEPPERONI" in issue for issue in result.issues)
    finally:
        UNAVAILABLE.discard("PEPPERONI")   # module-level set — never leak into other tests


def test_revalidate_flags_a_now_86d_gourmet_number(repo):
    sess = _fresh()
    add_item(sess, "PIZZA", size="LARGE", gourmet_number=1)
    UNAVAILABLE.add("#1")
    try:
        result = revalidate_and_reprice(sess)
        assert not result.clean
        assert any("#1" in issue for issue in result.issues)
    finally:
        UNAVAILABLE.discard("#1")


def test_revalidate_unconditionally_invalidates_an_outstanding_quote(repo):
    """The core resume-safety property: even a CLEAN cart's stale quote must
    not survive a resume — cart_hash matching proves contents didn't change,
    never that today's price is the same one quoted before."""
    sess = _fresh()
    add_item(sess, "PIZZA", size="LARGE")
    request_quote(sess)
    assert sess.state == "QUOTED"
    assert sess.quote_id is not None

    result = revalidate_and_reprice(sess)
    assert result.clean
    assert sess.quote_id is None
    assert sess.state == "BUILDING"


def test_resume_or_create_returns_fresh_session_when_nothing_to_resume(repo):
    sess, offer = resume_or_create(repo, "STORE-001", "NEW-CALL", "+12035551234")
    assert offer is None
    assert sess.call_id == "NEW-CALL"
    assert sess.order.lines == []


def test_resume_or_create_returns_revalidated_prior_cart(repo):
    prior = _fresh("DROPPED-CALL")
    add_item(prior, "PIZZA", size="LARGE")
    repo.save_session(prior)

    sess, offer = resume_or_create(repo, "STORE-001", "CALLBACK", "+12035551234")
    assert offer is not None
    assert offer.clean
    assert sess.call_id == "DROPPED-CALL"           # the resumed session, not a new one
    assert len(sess.order.lines) == 1
