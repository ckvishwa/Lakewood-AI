"""Runtime config. Nothing secret lives in code."""

import os
from dataclasses import dataclass

from . import menu


class ConfigError(Exception):
    """Invalid configuration — fail at load time, never at call time with a
    customer on the line."""


@dataclass(frozen=True)
class StoreConfig:
    store_id: str = "STORE-001"
    name: str = "Lakewood Pizza"
    inbound_did: str = os.getenv("LAKEWOOD_DID", "+12037588880")

    # F2: store_id is resolved from the inbound DID server-side. The model
    # never supplies it, so a caller cannot reach another store's data.
    printer_host: str | None = os.getenv("PRINTER_HOST")
    printer_port: int = int(os.getenv("PRINTER_PORT", "9100"))
    printer_device: str | None = os.getenv("PRINTER_DEVICE")   # e.g. /dev/usb/lp0
    printer_dry_run: bool = os.getenv("PRINTER_DRY_RUN", "1") == "1"

    # T-053 Phase 2 Part 4: where a failed/unanswered/after-hours call gets
    # transferred to a human. Per-store (`data/menu.json`), never an env
    # var/global constant — same reasoning as `inbound_did` above.
    transfer_number: str = menu.TRANSFER_NUMBER

    def __post_init__(self) -> None:
        # Loop-guard: the pilot forwards the restaurant's own public line
        # (`inbound_did`) to Rexi on overflow. If `transfer_number` (or any
        # future Twilio fallback target) ever pointed back at that SAME
        # number, a failed call would transfer to the line that forwards
        # unanswered calls TO Rexi in the first place — bouncing between
        # the two forever, never reaching a human. Fail at config load,
        # not mid-call.
        if self.transfer_number == self.inbound_did:
            raise ConfigError(
                f"transfer_number ({self.transfer_number!r}) must not equal "
                f"inbound_did ({self.inbound_did!r}) — the restaurant's own "
                f"forwarded public line. Transferring a failed call back to "
                f"the number that forwards unanswered calls TO Rexi would "
                f"loop the call between the two forever.")


CONFIG = StoreConfig()


def store_for_did(did: str) -> str:
    """Single store today. Becomes a lookup when a second store onboards."""
    return CONFIG.store_id
