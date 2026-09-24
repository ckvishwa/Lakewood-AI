"""
Portable 12-hour time formatting.

`%-I` (no leading zero on the hour) is a glibc/BSD strftime extension. It is
not supported by Windows' CRT, which raises `ValueError: Invalid format
string` on any strftime call containing it. `%I` is the portable POSIX code,
but it always zero-pads to two digits (e.g. "08:05 PM"). This module strips
that leading zero in pure Python so every platform produces the same
ticket-facing string, e.g. "8:05 PM" (and "12:05 PM" unchanged).

T-059: this module is deliberately generic/context-free — it formats
whatever `struct_time` it's given and has no notion of "the store." Its
own DEFAULT (no argument -> `time.localtime()`) reads the calling
PROCESS's own OS timezone, which is correct for a purely mechanical
formatting test but is the wrong choice for anything customer/kitchen-
facing (a ticket timestamp, a spoken time). Every real call site that
needs "the current store-local time" must pass one explicitly —
`lakewood.orders.store_now().timetuple()` — never call `format_12h()`
bare and assume it means store time. See `docs/STATUS.md`'s T-059 entry:
the bare-call default is exactly the bug class that made every printed
ticket wrong on a UTC cloud server (ADR-019), fixed at the three real
call sites, not by changing this module's own generic default.
"""

import time


def format_12h(struct_time=None) -> str:
    """'%-I:%M %p' without the Unix-only flag — e.g. 8:05 PM, 12:05 PM."""
    t = struct_time if struct_time is not None else time.localtime()
    return time.strftime("%I:%M %p", t).lstrip("0")
