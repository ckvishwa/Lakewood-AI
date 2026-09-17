"""
Portable 12-hour time formatting.

`%-I` (no leading zero on the hour) is a glibc/BSD strftime extension. It is
not supported by Windows' CRT, which raises `ValueError: Invalid format
string` on any strftime call containing it. `%I` is the portable POSIX code,
but it always zero-pads to two digits (e.g. "08:05 PM"). This module strips
that leading zero in pure Python so every platform produces the same
ticket-facing string, e.g. "8:05 PM" (and "12:05 PM" unchanged).
"""

import time


def format_12h(struct_time=None) -> str:
    """'%-I:%M %p' without the Unix-only flag — e.g. 8:05 PM, 12:05 PM."""
    t = struct_time if struct_time is not None else time.localtime()
    return time.strftime("%I:%M %p", t).lstrip("0")
