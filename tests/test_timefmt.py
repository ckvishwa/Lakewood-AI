"""
Portable 12-hour time formatting. `%-I` (glibc/BSD only) raises
`ValueError: Invalid format string` on Windows; `format_12h` must produce the
same ticket-facing string on every platform without it.
"""

import time

from lakewood.timefmt import format_12h


def _t(hour, minute):
    return time.struct_time((2026, 9, 6, hour, minute, 0, 0, 0, -1))


def test_morning_hour_has_no_leading_zero():
    assert format_12h(_t(8, 5)) == "8:05 AM"


def test_evening_hour_has_no_leading_zero():
    assert format_12h(_t(20, 5)) == "8:05 PM"


def test_noon_stays_twelve():
    assert format_12h(_t(12, 5)) == "12:05 PM"


def test_midnight_stays_twelve():
    assert format_12h(_t(0, 5)) == "12:05 AM"


def test_ten_and_eleven_are_unaffected():
    assert format_12h(_t(10, 30)) == "10:30 AM"
    assert format_12h(_t(23, 30)) == "11:30 PM"


def test_defaults_to_current_time_without_raising():
    # Exercises the real call site's usage (no argument) end to end.
    result = format_12h()
    assert result.endswith(" AM") or result.endswith(" PM")
