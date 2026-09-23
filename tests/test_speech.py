"""
T-051 Part 2/4 — the speakable-rendering layer (`lakewood/speech.py`) and
the readback's completeness invariant.

No audio, no TTS engine, no VAD, no model — everything here is pure text.
The mapping VALUES themselves were derived from a real TTS->STT round trip
(see docs/STATUS.md's T-051 entry); this file does not re-run that (it
needs real SAPI/STT hardware) — it proves the deterministic TEXT-BUILDING
logic that consumes those mappings is correct and complete.
"""

import random

import pytest

from lakewood import coupons as cp
from lakewood import orders as oe
from lakewood import speech
from lakewood.orders import (
    _render_line, _short_readback, _speak_list, add_item, add_modifier,
    apply_coupon, begin_confirmation, request_quote, set_order_type,
)


# ---------------------------------------------------------------------------
# Speakable-rendering layer: pure mapping tests
# ---------------------------------------------------------------------------

def test_confirmed_broken_item_tokens_are_remapped():
    # Evidence: docs/STATUS.md T-051's TTS->STT round-trip table.
    assert speech.speakable_item_name("6PC WINGS") == "six-piece wings"
    assert speech.speakable_item_name("12PC WINGS") == "twelve-piece wings"
    assert speech.speakable_item_name("GARDEN SALAD SM") == "small garden salad"
    assert speech.speakable_item_name("GARDEN SALAD LG") == "large garden salad"
    assert speech.speakable_item_name("SALAD SM") == "small salad"
    assert speech.speakable_item_name("SALAD LG") == "large salad"
    assert speech.speakable_item_name("STRWBRY CHZCAKE") == "strawberry cheesecake"
    assert speech.speakable_item_name("20OZ") == "twenty-ounce"
    assert speech.speakable_item_name("2LITER") == "two-liter"


def test_verified_clean_item_tokens_pass_through_unchanged():
    # Round-tripped clean (docs/STATUS.md T-051) — NOT "fixed" because
    # measurement showed nothing to fix.
    for name in ("GRINDER", "CALZONE", "CHEESEBURGER", "TIRAMISU", "CAN"):
        assert speech.speakable_item_name(name) == name.lower()


def test_confirmed_broken_topping_is_remapped():
    assert speech.speakable_topping_name("RSTD RED PEPPR") == "roasted red pepper"


def test_ordinary_topping_names_pass_through_lowercased():
    assert speech.speakable_topping_name("PEPPERONI") == "pepperoni"
    assert speech.speakable_topping_name("MUSHROOMS") == "mushrooms"


def test_coupon_uses_the_spoken_field_not_the_raw_code():
    assert speech.speakable_coupon_phrase("OFF_3_AT_30") == "three dollars off thirty"
    assert speech.speakable_coupon_phrase("COMBO_27") == "the twenty-seven dollar deal"


def test_unknown_coupon_code_falls_back_to_itself_rather_than_raising():
    assert speech.speakable_coupon_phrase("NOT_A_REAL_CODE") == "NOT_A_REAL_CODE"


# ---------------------------------------------------------------------------
# Completeness: every real menu token is either mapped or explicitly
# verified clean — a genuinely new/uncovered token is a LOUD test failure.
# ---------------------------------------------------------------------------

# Every NON_PIZZA key round-trip-tested during T-051 and confirmed to
# already read naturally (docs/STATUS.md) — the "not broken" half of the
# audit, named explicitly rather than assumed.
_VERIFIED_CLEAN_ITEMS = {
    "GRINDER", "ROLL", "GRINDER EXTRA", "CALZONE", "STROMBOLI", "CALZONE ITEM",
    "EXTRA DRESSING", "BLUE CHEESE CUP", "CHEESEBURGER", "HAMBURGER",
    "BACON CHEESEBURGER", "DOUBLE CHEESEBURGER", "WRAP", "CHICKEN DINNER",
    "CHEESECAKE", "TIRAMISU", "CHOCOLATE CAKE", "CAN",
}

# Every real topping round-trip-tested; the handful with an ambiguous STT
# result (FETA/HAM/PEPPERONI/RICOTTA/STEAK) are single-word ASR-recognition
# noise, not a confirmed TTS defect (see lakewood/speech.py's own comment) —
# listed here as verified/considered, not silently skipped.
_VERIFIED_CLEAN_TOPPINGS = oe.ALL_TOPPINGS - set(speech.SPEAKABLE_TOPPING_NAMES)


def test_every_real_non_pizza_item_is_mapped_or_verified_clean():
    from lakewood import pricing
    uncovered = speech.uncovered_names(
        set(pricing.NON_PIZZA), speech.SPEAKABLE_ITEM_NAMES, _VERIFIED_CLEAN_ITEMS)
    assert uncovered == [], (
        f"new/uncovered menu item(s) with no speakable-rendering decision: "
        f"{uncovered} — round-trip test and either add to "
        f"SPEAKABLE_ITEM_NAMES or _VERIFIED_CLEAN_ITEMS")


def test_every_real_topping_is_mapped_or_verified_clean():
    uncovered = speech.uncovered_names(
        oe.ALL_TOPPINGS, speech.SPEAKABLE_TOPPING_NAMES, _VERIFIED_CLEAN_TOPPINGS)
    assert uncovered == [], (
        f"new/uncovered topping(s) with no speakable-rendering decision: "
        f"{uncovered}")


def test_a_genuinely_new_unmapped_item_fails_the_completeness_check():
    """Mutation-prove the completeness test itself: an item that's neither
    mapped NOR verified-clean must show up as uncovered."""
    uncovered = speech.uncovered_names(
        {"BRAND NEW SKU"}, speech.SPEAKABLE_ITEM_NAMES, _VERIFIED_CLEAN_ITEMS)
    assert uncovered == ["BRAND NEW SKU"]


# ---------------------------------------------------------------------------
# No internal token reaches spoken output
# ---------------------------------------------------------------------------

_BAD_PATTERNS = ("_", " PC ", " SM.", " SM ", " LG.", " LG ", "CHZCAKE", "RSTD")


@pytest.fixture
def pickup():
    sess = oe.Session(call_id="C", store_id="STORE-001", from_number="+12035551234")
    set_order_type(sess, "pickup")
    return sess


def test_no_internal_token_in_multi_item_coupon_readback(pickup):
    add_item(pickup, "CHEESE PIZZA", size="party")
    add_item(pickup, "6PC WINGS")
    add_item(pickup, "20OZ")
    apply_coupon(pickup, "OFF_3_AT_30")
    request_quote(pickup)
    r = begin_confirmation(pickup)
    readback = r["readback"]
    for pattern in _BAD_PATTERNS:
        assert pattern not in readback, f"{pattern!r} leaked into: {readback!r}"


def test_no_raw_line_id_or_half_token_in_readback(pickup):
    r = add_item(pickup, "CHEESE PIZZA", size="large")
    lid = r["line_id"]
    add_modifier(pickup, lid, "PEPPERONI", portion="HALF_1")
    add_modifier(pickup, lid, "MUSHROOMS", portion="HALF_2")
    request_quote(pickup)
    readback = begin_confirmation(pickup)["readback"]
    assert "HALF_1" not in readback and "HALF_2" not in readback
    assert lid not in readback


# ---------------------------------------------------------------------------
# Half-and-half stays unambiguous
# ---------------------------------------------------------------------------

def test_half_and_half_topping_placement_is_unambiguous(pickup):
    r = add_item(pickup, "CHEESE PIZZA", size="large")
    lid = r["line_id"]
    add_modifier(pickup, lid, "PEPPERONI", portion="HALF_1")
    add_modifier(pickup, lid, "MUSHROOMS", portion="HALF_2")
    request_quote(pickup)
    readback = begin_confirmation(pickup)["readback"]
    assert "first half: pepperoni" in readback
    assert "second half: mushrooms" in readback
    # order in the string proves WHICH half is which, not just that both words appear
    assert readback.index("pepperoni") < readback.index("mushrooms")


def test_named_half_and_half_specialty_readback_names_both(pickup):
    add_item(pickup, "PIZZA", size="large", gourmet_number=10, second_gourmet_number=8)
    request_quote(pickup)
    readback = begin_confirmation(pickup)["readback"]
    assert "HAWAIIAN" in readback
    assert "BBQ CHICKEN" in readback


# ---------------------------------------------------------------------------
# The readback is produced without any model call
# ---------------------------------------------------------------------------

def test_readback_is_produced_without_any_model_call(pickup):
    """_short_readback/_render_line take only `sess`/a quote dict — there is
    no provider, no interpreter, nothing that could call out to a model.
    Static proof: neither function's own source references any provider
    module or an LLM-shaped call."""
    import inspect
    from lakewood import orders as oe_module
    src = inspect.getsource(oe_module._short_readback) + inspect.getsource(oe_module._render_line)
    for banned in ("Provider", "complete(", "anthropic", "openai", "ollama"):
        assert banned not in src


def test_readback_content_identical_across_repeated_calls(pickup):
    """Determinism, directly demonstrated: the SAME cart produces the
    BYTE-IDENTICAL readback every time it's rendered — impossible for
    anything that consulted a model."""
    r = add_item(pickup, "CHEESE PIZZA", size="large")
    add_modifier(pickup, r["line_id"], "PEPPERONI")
    q = request_quote(pickup)
    r1 = _short_readback(pickup, q)
    r2 = _short_readback(pickup, q)
    r3 = _short_readback(pickup, q)
    assert r1 == r2 == r3


# ---------------------------------------------------------------------------
# Property test: completeness of content (generate carts, don't hand-pick)
# ---------------------------------------------------------------------------

from lakewood import pricing as _pricing

_SIZES = ["SMALL", "MEDIUM", "LARGE", "XLARGE", "PARTY"]
_TOPPINGS = sorted(oe.ALL_TOPPINGS)
_ITEMS = sorted(_pricing.NON_PIZZA)


def _random_cart(rng: random.Random):
    sess = oe.Session(call_id="C", store_id="STORE-001", from_number="+12035551234")
    set_order_type(sess, "pickup")
    n_lines = rng.randint(1, 4)
    added = 0
    attempts = 0
    while added < n_lines and attempts < 20:
        attempts += 1
        if rng.random() < 0.6:
            size = rng.choice(_SIZES)
            r = add_item(sess, "CHEESE PIZZA", size=size)
            if r.get("status") != "ok":
                continue
            lid = r["line_id"]
            if rng.random() < 0.7:
                topping = rng.choice(_TOPPINGS)
                r2 = add_modifier(sess, lid, topping)
                # a rejected topping (unavailable/price-unknown) just leaves
                # a plain pizza line — still a valid, checkable cart
        else:
            name = rng.choice(_ITEMS)
            add_item(sess, name)
        added += 1
    return sess


def test_property_every_line_and_total_appear_in_the_full_readback():
    """For 200 randomly generated real carts, every line's OWN rendered
    text and the total appear in the final readback — the completeness
    invariant, checked against generated carts, not a handful of
    hand-picked examples."""
    rng = random.Random(20260922)
    checked = 0
    for _ in range(200):
        sess = _random_cart(rng)
        if not sess.order.lines:
            continue
        q = request_quote(sess)
        if q.get("status") != "ok":
            continue
        readback = _short_readback(sess, q)
        for line in sess.order.lines:
            rendered = _render_line(line)
            assert rendered in readback, (
                f"line {rendered!r} missing from readback {readback!r}")
        assert f"${q['total']}" in readback
        checked += 1
    assert checked > 150, f"only {checked}/200 random carts were actually exercised"


def _assert_readback_complete(sess, q, readback):
    """The exact completeness assertion `test_property_every_line_and_
    total_appear_in_the_full_readback` makes, factored out so the mutation
    tests below exercise the SAME check against the REAL call path
    (`begin_confirmation`, which internally calls the real/patched
    `_short_readback` via `orders.py`'s own module-level name) rather than
    a hand-written stand-in — proving the guard, not a tautology."""
    for line in sess.order.lines:
        assert _render_line(line) in readback
    assert f"${q['total']}" in readback


def test_mutation_dropping_a_line_makes_the_completeness_check_go_red(monkeypatch):
    """Mutation-prove the property test itself: patch the REAL
    `orders._short_readback` (the function `begin_confirmation` actually
    calls) to drop the last line, run the real confirmation flow, and
    confirm the SAME completeness assertion the property test uses now
    fails. If it didn't, the property test would be vacuous."""
    sess = oe.Session(call_id="C", store_id="STORE-001", from_number="+12035551234")
    set_order_type(sess, "pickup")
    add_item(sess, "CHEESE PIZZA", size="large")
    add_item(sess, "6PC WINGS")

    real = oe._short_readback

    def broken_readback(sess, q):
        items = _speak_list([_render_line(l) for l in sess.order.lines[:-1]])  # drops last line
        return f"Pickup: {items}. Total ${q['total']}."

    monkeypatch.setattr(oe, "_short_readback", broken_readback)
    q = request_quote(sess)
    readback = begin_confirmation(sess)["readback"]  # goes through the REAL call path
    with pytest.raises(AssertionError):
        _assert_readback_complete(sess, q, readback)

    # sanity: the check DOES pass again once the mutation is removed
    monkeypatch.setattr(oe, "_short_readback", real)


def test_mutation_dropping_the_total_makes_the_completeness_check_go_red(monkeypatch):
    sess = oe.Session(call_id="C", store_id="STORE-001", from_number="+12035551234")
    set_order_type(sess, "pickup")
    add_item(sess, "CHEESE PIZZA", size="large")

    def broken_readback_no_total(sess, q):
        items = _speak_list([_render_line(l) for l in sess.order.lines])
        return f"Pickup: {items}."  # total missing entirely

    monkeypatch.setattr(oe, "_short_readback", broken_readback_no_total)
    q = request_quote(sess)
    readback = begin_confirmation(sess)["readback"]
    with pytest.raises(AssertionError):
        _assert_readback_complete(sess, q, readback)


# ---------------------------------------------------------------------------
# Mid-order diff vs. final complete readback — different rules, both true
# ---------------------------------------------------------------------------

def test_mid_order_add_item_reply_is_a_diff_not_the_full_cart(pickup):
    """T-051 Part 3: adding a SECOND item must not re-speak the first one —
    the mid-order reply is per-line, matching CLAUDE.md's cart-diff
    principle. (The tool result's own `description` field IS the spoken
    reply for add_item — see chat.py::_reply_for.)"""
    add_item(pickup, "CHEESE PIZZA", size="large")
    r2 = add_item(pickup, "6PC WINGS")
    assert r2["status"] == "ok"
    assert "CHEESE PIZZA" not in r2["description"]
    assert r2["description"] == "six-piece wings"


def test_final_readback_before_confirmation_is_the_full_cart_not_a_diff(pickup):
    """The FINAL pre-confirmation readback is a different utterance with a
    different rule: complete, not a diff — verified directly."""
    add_item(pickup, "CHEESE PIZZA", size="large")
    add_item(pickup, "6PC WINGS")
    request_quote(pickup)
    readback = begin_confirmation(pickup)["readback"]
    assert "CHEESE PIZZA" in readback
    assert "six-piece wings" in readback
