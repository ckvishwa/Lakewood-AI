"""
Owner-confirmed store rules. These are policy, not reverse engineering — if one
of these changes, the owner told us, and this file is where it gets recorded.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from lakewood import orders as oe
from lakewood.menu import PREMIUM_TOPPINGS, REGULAR_TOPPINGS, UNKNOWN_TIER
from lakewood.orders import (
    add_item, add_modifier, get_store_info, request_quote, set_order_type,
    store_status,
)
from lakewood.pricing import Order, PizzaLine, Topping, money


@pytest.fixture
def sess():
    return oe.Session(call_id="C", store_id="STORE-001", from_number="+12035551234")


# --- topping tiers ---------------------------------------------------------

def test_no_unpriced_toppings_remain():
    assert UNKNOWN_TIER == set()


@pytest.mark.parametrize("name", ["FRESH MOZZARELLA", "PINEAPPLE",
                                  "ARTICHOKE", "GARLIC"])
def test_owner_confirmed_regular_tier(name):
    assert name in REGULAR_TOPPINGS
    line = PizzaLine(size="SMALL", toppings=[Topping(name)])
    assert money(line.topping_cost()) == "2.00"


def test_shrimp_is_premium():
    assert "SHRIMP" in PREMIUM_TOPPINGS
    line = PizzaLine(size="SMALL", toppings=[Topping("SHRIMP")])
    assert money(line.topping_cost()) == "2.50"


def test_five_toppings_are_five_charges(sess):
    """Owner: five toppings means five button presses. No bundle discount."""
    set_order_type(sess, "pickup")
    lid = add_item(sess, "CHEESE PIZZA", size="large")["line_id"]
    for t in ["pepperoni", "sausage", "mushroom", "ham", "olives"]:
        add_modifier(sess, lid, t)
    assert request_quote(sess)["subtotal"] == "30.00"      # 15 + 5x3.00


# --- hours -----------------------------------------------------------------

@pytest.mark.parametrize("when,is_open", [
    ("2026-09-08 11:00", True),    # Tue open
    ("2026-09-08 10:59", False),   # Tue before open
    ("2026-09-08 22:00", False),   # Tue at close
    ("2026-09-12 21:00", True),    # Sat
    ("2026-09-13 12:00", True),    # Sun open
    ("2026-09-13 21:00", False),   # Sun at close
    ("2026-09-07 19:00", False),   # Monday — closed all day
])
def test_hours(when, is_open):
    assert store_status(datetime.fromisoformat(when))["open"] is is_open


def test_monday_rolls_to_tuesday():
    st = store_status(datetime.fromisoformat("2026-09-07 19:00"))
    assert st["next_open"] == "Tuesday at 11 AM"


# ---------------------------------------------------------------------------
# T-059 (docs/STATUS.md's own entry has the full diagnosis): store_status()
# used to compare raw server-local wall-clock time against HOURS with no
# timezone at all. ADR-019 puts the orchestrator on a UTC cloud server, so
# in production this would have held every real Eastern dinner-rush order
# as "closed" — proven concretely by T-058's CI run landing on Wednesday
# 22:00 UTC (= 6:08 PM Eastern, well within hours) and reading it closed.
# ---------------------------------------------------------------------------

def test_dinner_rush_on_a_utc_server_clock_is_open():
    """The exact bug, reproduced directly: a UTC-clock server evaluating
    Wednesday 22:00 UTC (6:08 PM Eastern, mid dinner rush) must read the
    store as OPEN, not closed."""
    utc_instant = datetime(2026, 9, 23, 22, 0, tzinfo=timezone.utc)
    st = store_status(utc_instant)
    assert st["open"] is True


@pytest.mark.parametrize("server_tz", [
    "UTC", "America/Los_Angeles", "Europe/London", "Asia/Tokyo", "Pacific/Auckland",
])
def test_same_real_instant_same_answer_regardless_of_server_timezone(server_tz):
    """The open/closed answer must depend ONLY on the store's own local
    time, never on what timezone the calling server/process happens to be
    in. One real instant (Wednesday 6 PM Eastern, mid dinner rush),
    represented as datetimes carrying every one of these server
    timezones' own tzinfo — all must convert to the identical store-local
    moment and agree."""
    # 2026-09-23 18:00 America/New_York, expressed once, then re-expressed
    # in each server timezone's own local clock for the SAME real instant.
    anchor = datetime(2026, 9, 23, 18, 0, tzinfo=ZoneInfo("America/New_York"))
    as_server_tz = anchor.astimezone(ZoneInfo(server_tz))
    assert store_status(as_server_tz)["open"] is True


def test_naive_now_is_still_treated_as_store_local_time():
    """Backward-compat: every existing caller/test passes a NAIVE datetime
    meaning 'store-local wall time' — that contract must not silently
    change just because aware-datetime support was added."""
    naive_in_hours = datetime(2026, 9, 23, 18, 0)  # no tzinfo at all
    assert store_status(naive_in_hours)["open"] is True


def test_dst_transition_is_handled_by_a_real_tz_database_not_a_fixed_offset():
    """The clearest possible proof this uses real DST rules, not a fixed
    UTC offset: the SAME UTC clock hour (02:00), on the SAME day of week
    (Tuesday), on either side of the 2026-11-01 US fall-back transition,
    must produce DIFFERENT open/closed answers — because the store's own
    local hour shifts by exactly one (EDT UTC-4 -> EST UTC-5). A fixed-
    offset implementation would give the same wrong answer both times."""
    before_fallback = datetime(2026, 9, 30, 2, 0, tzinfo=timezone.utc)   # EDT era
    after_fallback = datetime(2026, 11, 4, 2, 0, tzinfo=timezone.utc)    # EST era

    before = store_status(before_fallback)
    after = store_status(after_fallback)

    # 02:00 UTC = 22:00 EDT (Tue, at the exact close boundary) -> closed
    assert before["open"] is False
    # 02:00 UTC = 21:00 EST (Tue, one hour before close) -> open
    assert after["open"] is True


def test_after_hours_order_is_disclosed_in_readback(sess, monkeypatch):
    monkeypatch.setattr(oe, "store_status",
                        lambda now=None: {"open": False,
                                          "next_open": "Tuesday at 11 AM"})
    set_order_type(sess, "pickup")
    add_item(sess, "CHEESE PIZZA", size="large")
    q = request_quote(sess)
    assert q["scheduled_for"] == "Tuesday at 11 AM"
    assert "not tonight" in q["readback"]


# --- delivery --------------------------------------------------------------

def test_delivery_minimum_blocks_small_order(sess):
    set_order_type(sess, "delivery", address="3 Vine St")
    add_item(sess, "CHEESE PIZZA", size="small")          # $11
    r = request_quote(sess)
    assert r["code"] == "BELOW_DELIVERY_MINIMUM"
    assert r["short_by"] == "7.00"


def test_delivery_minimum_met(sess):
    """An XL cheese is $17 — still short. It takes a topping to clear $18."""
    set_order_type(sess, "delivery", address="3 Vine St")
    lid = add_item(sess, "CHEESE PIZZA", size="xl")["line_id"]
    assert request_quote(sess)["code"] == "BELOW_DELIVERY_MINIMUM"
    add_modifier(sess, lid, "pepperoni")
    q = request_quote(sess)
    assert q["status"] == "ok" and q["subtotal"] == "20.50"


def test_pickup_has_no_minimum(sess):
    set_order_type(sess, "pickup")
    add_item(sess, "CHEESE PIZZA", size="small")
    assert request_quote(sess)["status"] == "ok"


def test_store_info_now_has_hours_and_delivery_rules(sess):
    info = get_store_info(sess)["info"]
    assert "closed Mondays" in info["hours"]
    assert info["delivery_minimum"] == "18.00"
    assert info["delivery_radius_miles"] == "7"


def test_store_info_still_refuses_unknown_fields(sess):
    """Coupons are still unanswered. Never invent an answer."""
    assert get_store_info(sess, fields=["coupons"])["code"] == "INFO_NOT_AVAILABLE"


# --- stale Sicilian buttons ------------------------------------------------

def test_sicilian_uses_the_standard_specialty_list(sess):
    from lakewood.menu import GOURMET_SICILIAN_OVERRIDES
    assert GOURMET_SICILIAN_OVERRIDES == {}
    set_order_type(sess, "pickup")
    r = add_item(sess, "PIZZA", size="sicilian", gourmet_number=3)
    assert "CHICKEN & BROCCOLI ALFREDO" in r["description"].upper()
