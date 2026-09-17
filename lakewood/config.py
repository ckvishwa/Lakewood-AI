"""Runtime config. Nothing secret lives in code."""

import os
from dataclasses import dataclass


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


CONFIG = StoreConfig()


def store_for_did(did: str) -> str:
    """Single store today. Becomes a lookup when a second store onboards."""
    return CONFIG.store_id
