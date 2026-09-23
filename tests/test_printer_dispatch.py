"""
T-049 Part 3 — dispatch_confirmed_order wiring and idempotency.

No hardware in this environment (see docs/STATUS.md's T-049 entry) — every
test here runs against TicketPrinter(dry_run=True) or a status-overriding
subclass, never a real socket/device. These prove the DISPATCH LOGIC (one
print per confirmed order, a failure never silently marks an order
dispatched, printer errors never reach the customer) — they do NOT prove
real hardware behaves this way. That remains UNVERIFIED until a real M347C
is reachable from a session that can run these same code paths against it.
"""

import pytest

from lakewood import orders as oe
from lakewood.chat import PersistentChat, run_turn
from lakewood.interpreter import RuleBasedInterpreter
from lakewood.persistence.memory_repository import InMemorySessionRepository
from lakewood.persistence.service import confirm_and_persist
from lakewood.printer import DispatchError, PrinterStatus, TicketPrinter, dispatch_confirmed_order


# ---------------------------------------------------------------------------
# PrinterStatus.ready — pure logic, every individual failure reason
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("online,cover_open,paper_out,expected", [
    (True, False, False, True),    # fully ready
    (False, False, False, False),  # offline alone blocks
    (True, True, False, False),    # cover open alone blocks
    (True, False, True, False),    # paper out alone blocks
    (False, True, True, False),    # everything wrong
])
def test_printer_status_ready_reflects_each_failure_reason(online, cover_open, paper_out, expected):
    st = PrinterStatus(online=online, cover_open=cover_open, paper_out=paper_out, paper_low=False)
    assert st.ready is expected


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

class _AlwaysOfflinePrinter(TicketPrinter):
    """Never ready — proves the retry-then-fail path without ever touching
    a socket or the dry-run byte buffer."""
    def status(self):
        return PrinterStatus(online=False, cover_open=False, paper_out=False, paper_low=False)


def _confirmed_session():
    sess = oe.Session(call_id="C1", store_id="STORE-001", from_number="+12035551234")
    oe.set_order_type(sess, "pickup")
    oe.add_item(sess, "CHEESE PIZZA", size="large")
    q = oe.request_quote(sess)
    oe.begin_confirmation(sess)
    sess.turn += 1
    oe.confirm_order(sess, q["quote_id"])
    assert sess.state == "CONFIRMED"
    return sess


# ---------------------------------------------------------------------------
# dispatch_confirmed_order — one ticket, real state, no silent success
# ---------------------------------------------------------------------------

class _RecordingPrinter(TicketPrinter):
    """`TicketPrinter.last_output` is overwritten by EVERY `_send` call,
    including the status-probe bytes `dispatch()` sends before/after the
    real print — by the time `dispatch()` returns, `last_output` holds the
    LAST status probe, not the ticket (a pre-existing quirk of the class,
    not a T-049 defect: nothing in production reads `last_output`). Record
    every payload instead of relying on it."""
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.sent: list[bytes] = []

    def _send(self, payload, expect_reply=0):
        self.sent.append(payload)
        return super()._send(payload, expect_reply)


def test_confirmed_order_dispatches_once_and_prints_real_content():
    sess = _confirmed_session()
    printer = _RecordingPrinter(dry_run=True)
    result = dispatch_confirmed_order(sess, printer)
    assert result == {"status": "ok", "printed": True, "paper_low": False}
    assert sess.state == "STORE_ACKED"
    tickets = [p for p in printer.sent if b"LARGE" in p or b"CHEESE" in p]
    assert len(tickets) == 1, printer.sent  # the real ticket body, sent exactly once
    assert any(e["event"] == "printed" for e in sess.events)


def test_dispatch_is_not_re_entrant_on_an_already_dispatched_session():
    """A confirmed order that already dispatched must refuse a second
    attempt through this same function — the state guard IS the one-ticket
    guarantee at this layer (confirm_and_persist's idempotency-key cache is
    the layer above it; see that module's docstring)."""
    sess = _confirmed_session()
    printer = TicketPrinter(dry_run=True)
    first = dispatch_confirmed_order(sess, printer)
    assert first["status"] == "ok"
    first_output = printer.last_output

    second = dispatch_confirmed_order(sess, printer)
    assert second == {"status": "error", "code": "BAD_STATE", "state": "STORE_ACKED"}
    # Nothing new was ever sent to the printer for the second call.
    assert printer.last_output == first_output


def test_print_failure_moves_to_failed_dispatch_never_store_acked(monkeypatch):
    sess = _confirmed_session()
    printer = _AlwaysOfflinePrinter(dry_run=True)
    monkeypatch.setattr("lakewood.printer.time.sleep", lambda s: None)  # keep the test fast

    result = dispatch_confirmed_order(sess, printer)

    assert result["status"] == "error"
    assert result["code"] == "FAILED_DISPATCH"
    assert result["action"] == "alert_staff_by_second_channel"
    assert sess.state == "FAILED_DISPATCH"
    assert sess.state != "STORE_ACKED"
    assert any(e["event"] == "dispatch_failed" for e in sess.events)


def test_after_hours_confirmed_order_is_held_not_printed(monkeypatch):
    sess = _confirmed_session()
    printer = TicketPrinter(dry_run=True)
    monkeypatch.setattr(
        "lakewood.orders.store_status",
        lambda: {"open": False, "next_open": "10 AM", "next_open_iso": "2026-09-23T10:00:00"})

    result = dispatch_confirmed_order(sess, printer)

    assert result["status"] == "ok"
    assert result["held"] is True
    assert sess.state == "HELD_FOR_OPEN"
    assert printer.last_output == b""  # nothing was ever sent to the printer


# ---------------------------------------------------------------------------
# confirm_and_persist — the real PersistentChat funnel, replay safety
# ---------------------------------------------------------------------------

def test_confirm_and_persist_dispatches_exactly_once_across_a_replay():
    """T-049 acceptance: 'one confirmed order, one ticket, proven by test.'
    Simulates the crash-and-retry case F6/confirm_and_persist already
    guarantee at the persistence layer: the SAME idempotency_key is
    confirmed twice (e.g. a dropped connection before the customer heard
    the confirmation). The dispatch must fire on the first call only — the
    replay must hit the idempotency-cache branch and never reach dispatch
    again."""
    repo = InMemorySessionRepository()
    sess = oe.Session(call_id="C1", store_id="STORE-001", from_number="+12035551234")
    oe.set_order_type(sess, "pickup")
    oe.add_item(sess, "CHEESE PIZZA", size="large")
    q = oe.request_quote(sess)
    oe.begin_confirmation(sess)
    sess.turn += 1

    dispatch_calls = []

    class _CountingPrinter(TicketPrinter):
        def dispatch(self, payload, retries=3, backoff=2.0):
            dispatch_calls.append(payload)
            return super().dispatch(payload, retries=retries, backoff=backoff)

    printer = _CountingPrinter(dry_run=True)
    key = "confirm:C1:" + q["quote_id"]

    first = confirm_and_persist(repo, sess, q["quote_id"], key, printer=printer)
    assert first["status"] == "ok"
    assert len(dispatch_calls) == 1
    assert sess.state == "STORE_ACKED"

    # Replay: same idempotency_key, same session object (as a retried RPC
    # against the same in-memory session would look) — must be a cache hit,
    # not a second confirm, not a second dispatch.
    replay = confirm_and_persist(repo, sess, q["quote_id"], key, printer=printer)
    assert replay["order_id"] == first["order_id"]
    assert len(dispatch_calls) == 1  # still exactly one


def test_dispatch_failure_does_not_leak_into_the_customer_facing_confirm_result(monkeypatch):
    """The customer's confirmation is real regardless of what happens in the
    kitchen — a dispatch failure must not turn confirm_order's result into
    an error the customer hears (that would contradict CLAUDE.md's 'printer
    errors never reach the customer as raw text')."""
    monkeypatch.setattr("lakewood.printer.time.sleep", lambda s: None)
    repo = InMemorySessionRepository()
    sess = oe.Session(call_id="C1", store_id="STORE-001", from_number="+12035551234")
    oe.set_order_type(sess, "pickup")
    oe.add_item(sess, "CHEESE PIZZA", size="large")
    q = oe.request_quote(sess)
    oe.begin_confirmation(sess)
    sess.turn += 1

    result = confirm_and_persist(repo, sess, q["quote_id"], "k1",
                                 printer=_AlwaysOfflinePrinter(dry_run=True))

    assert result["status"] == "ok"
    assert "order_id" in result and "total" in result
    assert sess.state == "FAILED_DISPATCH"  # the real, staff-facing state


def test_run_turn_confirm_order_reply_unaffected_by_a_failed_dispatch(monkeypatch):
    """End-to-end through the real text turn path: the spoken reply stays
    the normal confirmation, never a printer error string."""
    monkeypatch.setattr("lakewood.printer.time.sleep", lambda s: None)
    repo = InMemorySessionRepository()
    call = PersistentChat.start(repo, "+12037588880", "CALL-1", "+12035551234",
                                printer=_AlwaysOfflinePrinter(dry_run=True))
    i = RuleBasedInterpreter()
    call.run_turn(i, "large pepperoni")
    call.run_turn(i, "what's my total?")
    call.run_turn(i, "yes place it")
    result = call.run_turn(i, "go ahead")

    assert "all set" in result.reply.lower()
    assert "L5" not in result.reply and "FAILED_DISPATCH" not in result.reply
    assert call.chat.session.state == "FAILED_DISPATCH"


def test_no_printer_configured_leaves_confirm_untouched():
    """Every existing caller that never passes a printer (tests, evals,
    any store without one wired up yet) must behave exactly as before —
    printer=None is the default for a reason."""
    repo = InMemorySessionRepository()
    sess = oe.Session(call_id="C1", store_id="STORE-001", from_number="+12035551234")
    oe.set_order_type(sess, "pickup")
    oe.add_item(sess, "CHEESE PIZZA", size="large")
    q = oe.request_quote(sess)
    oe.begin_confirmation(sess)
    sess.turn += 1

    result = confirm_and_persist(repo, sess, q["quote_id"], "k1")

    assert result["status"] == "ok"
    assert sess.state == "CONFIRMED"  # never moved by a dispatch that never ran


# ---------------------------------------------------------------------------
# T-056 (docs/SECURITY_AUDIT_T054.md, finding T054-06) — ESC/POS
# command-injection guard: control bytes in customer-supplied free text
# (name/phone/address/note) must never reach the printer payload.
# ---------------------------------------------------------------------------

def _payload_text(payload: bytes) -> str:
    """Best-effort readable view of a built payload for substring checks —
    real ESC/POS byte constants are excluded by nature of not decoding
    cleanly as printable text; this is only used to assert ABSENCE of
    injected control bytes, never used to assert exact formatting."""
    return payload.decode("latin-1")


def test_build_strips_escape_byte_from_note():
    p = TicketPrinter(dry_run=True)
    injected = "extra cheese\x1b!\x38please"  # \x1b = ESC — real ESC/POS syntax
    payload = p.build("", "carry-out", "AI-000001", note=injected)
    assert b"\x1b!\x38" not in payload


def test_build_strips_gs_byte_from_address():
    p = TicketPrinter(dry_run=True)
    injected = "123 Main St\x1dV\x00"  # \x1d = GS — e.g. a fabricated cut command
    payload = p.build("", "delivery", "AI-000002", address=injected)
    assert b"\x1dV\x00" not in payload


def test_build_strips_dle_byte_from_name():
    p = TicketPrinter(dry_run=True)
    injected = "John\x10\x04\x01Doe"  # \x10\x04 = DLE EOT — a real status query
    payload = p.build("", "carry-out", "AI-000003", name=injected)
    assert b"\x10\x04" not in payload


def test_build_strips_control_bytes_from_phone():
    p = TicketPrinter(dry_run=True)
    injected = "555\x1b@1234"  # \x1b@ = ESC @ — printer INIT/reset
    payload = p.build("", "carry-out", "AI-000004", name="A Customer", phone=injected)
    assert b"\x1b@1234" not in payload


def test_build_preserves_ordinary_text_content():
    """The fix must not eat legitimate content — only real control bytes."""
    p = TicketPrinter(dry_run=True)
    payload = p.build("", "delivery", "AI-000005",
                      name="O'Brien", address="123 Main St, Apt 4B",
                      note="ring the bell, leave at door")
    text = _payload_text(payload)
    assert "O'Brien" in text
    assert "123 Main St" in text
    assert "ring the bell" in text


def test_sanitize_keeps_newline_only_when_requested():
    assert TicketPrinter._sanitize("line1\nline2", keep_newlines=True) == "line1\nline2"
    assert TicketPrinter._sanitize("line1\nline2", keep_newlines=False) == "line1 line2"


def test_sanitize_strips_every_ascii_control_byte_except_newline():
    every_control = "".join(chr(c) for c in list(range(0x20)) + [0x7f])
    cleaned = TicketPrinter._sanitize(every_control, keep_newlines=True)
    assert cleaned == "\n"  # only \n (0x0a) survives
