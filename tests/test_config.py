"""
T-053 Phase 2 Part 4 — the transfer-number/forwarded-line loop guard.

The pilot uses overflow forwarding: the restaurant's own public line
(`inbound_did`) forwards unanswered calls to Rexi. If a failure transfer
target ever pointed back at that same number, a failed call would bounce
between the two forever, never reaching a human — fail this at config
load, not mid-call.
"""

import pytest

from lakewood.config import ConfigError, StoreConfig


def test_real_config_loads_with_distinct_numbers():
    cfg = StoreConfig()
    assert cfg.transfer_number != cfg.inbound_did


def test_transfer_number_equal_to_inbound_did_is_rejected():
    with pytest.raises(ConfigError, match="loop"):
        StoreConfig(inbound_did="+12037588880", transfer_number="+12037588880")


def test_transfer_number_distinct_from_inbound_did_is_accepted():
    cfg = StoreConfig(inbound_did="+12037588880", transfer_number="+12038060537")
    assert cfg.transfer_number == "+12038060537"
