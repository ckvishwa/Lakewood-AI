"""
`customers.py` — phone normalization and the tenant-scoped customer_id it
feeds. Never the raw phone number as a key (T-037 acceptance criterion).
"""

import pytest

from lakewood.persistence.customers import mask_phone, normalize_phone
from lakewood.persistence.memory_repository import InMemorySessionRepository


@pytest.mark.parametrize("raw,expected", [
    ("+12035551234", "+12035551234"),
    ("12035551234", "+12035551234"),
    ("2035551234", "+12035551234"),
    ("(203) 555-1234", "+12035551234"),
    ("+1 203 555 1234", "+12035551234"),
    ("+10000000000", "+10000000000"),      # chat.py's own default test number
])
def test_normalize_phone_variants_converge(raw, expected):
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("bad", ["", "123", "not a phone", "999999999999999"])
def test_normalize_phone_rejects_unparseable_input(bad):
    with pytest.raises(ValueError):
        normalize_phone(bad)


def test_mask_phone_never_reveals_more_than_last_four():
    masked = mask_phone("+12035551234")
    assert masked.endswith("1234")
    assert "203" not in masked
    assert "555" not in masked


def test_customer_id_is_never_the_raw_phone_number():
    repo = InMemorySessionRepository()
    customer_id = repo.get_or_create_customer("STORE-001", "+12035551234")
    assert customer_id != "+12035551234"
    assert not customer_id.startswith("+")


def test_same_phone_different_formatting_resolves_to_one_customer():
    repo = InMemorySessionRepository()
    cid1 = repo.get_or_create_customer("STORE-001", normalize_phone("(203) 555-1234"))
    cid2 = repo.get_or_create_customer("STORE-001", normalize_phone("2035551234"))
    assert cid1 == cid2
