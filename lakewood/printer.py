"""
Ticket delivery — ESC/POS, written against the TM-T88V command reference.

Why this file matters: PRD §12 said the restaurant sees orders on a dashboard.
Nobody watches a dashboard during a Friday rush. This is how a confirmed order
physically reaches the kitchen, and — critically — how we learn whether it did.

T-049 FINAL (2026-09-23): the real hardware is confirmed **Epson TM-m30III**
(model label M374C, self-identified via its own web config), wired Ethernet
at 10.1.10.197:9100, real-print-verified (see docs/STATUS.md's T-049 FINAL
entry for the physical ticket, real DLE EOT status behavior, and real
paper-out/cover-open/network-down observations — not inferred from spec).
`DLE EOT` is confirmed supported by this model per Epson's own published
TM-m30III command list; exact status-bit MEANINGS were cross-checked against
real device behavior this session, not assumed from the TM-T88V reference
this module was originally written against. Status-bit semantics for any
model NOT yet tested this way remain exactly what the name says: TM-T88V's
documented values, unconfirmed elsewhere.

The status-query design (DLE EOT, answered even mid-job) is real for genuine
TM-series printers in general, so SENT_TO_STORE -> STORE_ACKED is meant to be
a real acknowledgement, not an assumption — and is now confirmed true for the
real TM-m30III this task verified. A ticket that didn't print becomes a
FAILED_DISPATCH that pages someone, instead of a lost order nobody knows about.
"""

from __future__ import annotations

import re
import socket
import time
from dataclasses import dataclass
from typing import Optional, Protocol

from .timefmt import format_12h


# ---------------------------------------------------------------------------
# Transport seam (T-049 FINAL) — same pattern as STT/TTS/LLM providers.
# Formatting and dispatch policy (below) never know which transport is
# active; no transport type leaks past this module. Deliberately NOT a
# broader protocol/formatting/dispatch refactor — only what the seam needs.
# ---------------------------------------------------------------------------

class PrintTransport(Protocol):
    def send(self, payload: bytes, expect_reply: int = 0) -> bytes: ...


class DryRunTransport:
    """Tests' default — never touches a socket or device."""
    def send(self, payload: bytes, expect_reply: int = 0) -> bytes:
        return b"\x12" * expect_reply


class TcpRawTransport:
    """LAN, port 9100 — the real, verified connection to the real hardware
    (ADR-003's original decision; confirmed live against a real TM-m30III
    this task, see docs/STATUS.md)."""
    def __init__(self, host: str, port: int = 9100, timeout: float = 3.0):
        self.host, self.port, self.timeout = host, port, timeout

    def send(self, payload: bytes, expect_reply: int = 0) -> bytes:
        with socket.create_connection((self.host, self.port), self.timeout) as s:
            s.sendall(payload)
            if expect_reply:
                s.settimeout(self.timeout)
                return s.recv(expect_reply)
        return b""


class UsbDeviceTransport:
    """The pre-existing `device=` path (e.g. /dev/usb/lp0) — unchanged
    behavior, not this task's real hardware (which is Ethernet)."""
    def __init__(self, device: str):
        self.device = device

    def send(self, payload: bytes, expect_reply: int = 0) -> bytes:
        with open(self.device, "wb") as f:
            f.write(payload)
        return b""


class ServerDirectTransport:
    """NOT built — a named slot for T-053's topology decision, not an
    implementation. The real TM-m30III's web config has a Cloud Services
    tab (Epson Connect-style cloud registration) confirmed present but
    DISABLED this task (docs/STATUS.md's T-049 FINAL entry has exactly what
    it offers and why it was left off — enabling it registers the device
    with an external Epson cloud service, a real, consequential action out
    of this task's scope). If a future cloud deployment needs the printer
    to poll for jobs instead of accepting inbound connections, T-053
    decides whether to implement this class for real."""
    def __init__(self, *a, **kw):
        raise NotImplementedError(
            "ServerDirectTransport is a named slot for T-053 — not built. "
            "See docs/STATUS.md's T-049 FINAL entry.")

# --- ESC/POS ---------------------------------------------------------------
ESC, GS, DLE, EOT = b"\x1b", b"\x1d", b"\x10", b"\x04"

INIT        = ESC + b"@"
ALIGN_L     = ESC + b"a\x00"
ALIGN_C     = ESC + b"a\x01"
BOLD_ON     = ESC + b"E\x01"
BOLD_OFF    = ESC + b"E\x00"
SIZE_NORMAL = GS + b"!\x00"
SIZE_2H     = GS + b"!\x01"     # double height
SIZE_2W2H   = GS + b"!\x11"     # double both
CUT         = GS + b"V\x42\x00"  # partial cut, feed first
FEED        = lambda n: ESC + b"d" + bytes([n])
# Drawer-kick pulse. Wire a $10 piezo buzzer to the DK port and the ticket
# announces itself — a silent ticket in a loud kitchen gets missed.
BUZZ        = ESC + b"p\x00\x32\xfa"

WIDTH = 48          # Font A on 80mm paper — physically confirmed on the
                    # real TM-m30III (T-049 FINAL): a 48-char calibration
                    # line filled one physical line exactly (zero room left
                    # for a 3-char suffix); 42 (the old T88V-spec guess)
                    # printed safely but wasted 6 real columns per line.


@dataclass
class PrinterStatus:
    online: bool
    cover_open: bool
    paper_out: bool
    paper_low: bool
    raw: Optional[int] = None

    @property
    def ready(self) -> bool:
        return self.online and not self.cover_open and not self.paper_out


class DispatchError(Exception):
    pass


class TicketPrinter:
    """
    Network (port 9100) by default; set dry_run=True to develop without hardware.

    NOTE for Lakewood: the store's TM-T88V is wired directly to the PrISM
    Manager PC (no network card), so PrISM owns that port. Do not print to it.
    Use a second, dedicated unit — a T88V with a UB-E04 ethernet board, or any
    network thermal printer — so a bug here can never disrupt the kitchen.

    Usage:
        p = TicketPrinter("192.168.1.50")
        p.dispatch(ticket_text)      # raises DispatchError after 3 failed tries
    """

    def __init__(self, host: str | None = None, port: int = 9100,
                 timeout: float = 3.0, dry_run: bool = False,
                 device: str | None = None,
                 transport: PrintTransport | None = None):
        self.host, self.port, self.timeout = host, port, timeout
        self.dry_run = dry_run
        self.device = device          # e.g. /dev/usb/lp0 for a USB unit
        self.last_output: bytes = b""
        # Explicit `transport` (tests, ServerDirectTransport once T-053
        # builds it) wins; otherwise selected from the same host/device/
        # dry_run args exactly as before — the public constructor is
        # unchanged, only what it builds internally is new.
        if transport is not None:
            self.transport: PrintTransport | None = transport
        elif dry_run:
            self.transport = DryRunTransport()
        elif device:
            self.transport = UsbDeviceTransport(device)
        elif host:
            self.transport = TcpRawTransport(host, port, timeout)
        else:
            self.transport = None

    # -- transport ---------------------------------------------------------

    def _send(self, payload: bytes, expect_reply: int = 0) -> bytes:
        self.last_output = payload
        if self.transport is None:
            raise DispatchError("No printer host or device configured.")
        return self.transport.send(payload, expect_reply)

    # -- status ------------------------------------------------------------

    def status(self) -> PrinterStatus:
        """
        Real-time status. DLE EOT is answered even mid-job, so it works when the
        printer is busy or offline — that's the whole point of using it.
        """
        try:
            printer = self._send(DLE + EOT + b"\x01", 1)
            offline = self._send(DLE + EOT + b"\x02", 1)
            paper   = self._send(DLE + EOT + b"\x04", 1)
        except Exception:
            return PrinterStatus(False, False, False, False)

        if self.dry_run:
            return PrinterStatus(True, False, False, False, raw=0x12)

        pb = printer[0] if printer else 0
        ob = offline[0] if offline else 0
        rb = paper[0] if paper else 0
        return PrinterStatus(
            online=not (pb & 0x08),
            cover_open=bool(ob & 0x04),
            paper_out=bool(rb & 0x60),      # roll paper end sensor
            paper_low=bool(rb & 0x0c),      # near-end
            raw=pb,
        )

    # -- formatting --------------------------------------------------------

    # T-056 (docs/SECURITY_AUDIT_T054.md, finding T054-06): every ASCII
    # control byte except \n — \n is legitimate (_wrap()'s own line-break
    # convention for address/note); every other control byte (ESC 0x1B, GS
    # 0x1D, DLE 0x10, and the rest of 0x00-0x1F/0x7F) is real ESC/POS
    # command syntax and must never reach the printer from customer text.
    _CONTROL_BYTES_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")

    @classmethod
    def _sanitize(cls, text: str, keep_newlines: bool = False) -> str:
        """Strip control bytes from a customer-supplied field before it
        enters the ESC/POS byte stream — the cart-line text three lines
        below this method's caller already gets this treatment via
        `.encode("ascii", "replace")`; name/phone/address/note (this
        class's only free-text customer input) previously did not."""
        if not text:
            return text
        cleaned = cls._CONTROL_BYTES_RE.sub("", text)
        return cleaned if keep_newlines else cleaned.replace("\n", " ")

    @staticmethod
    def _wrap(text: str, indent: int = 0) -> list[str]:
        out, width = [], WIDTH - indent
        for raw in text.split("\n"):
            if not raw.strip():
                out.append("")
                continue
            line = ""
            for word in raw.split():
                if len(line) + len(word) + (1 if line else 0) > width:
                    out.append(" " * indent + line)
                    line = word
                else:
                    line = f"{line} {word}".strip()
            out.append(" " * indent + line)
        return out

    def build(self, ticket_text: str, order_type: str, order_id: str,
              phone: str = "", name: str = "", address: str = "",
              note: str = "", total: str = "",
              coupon: str | None = None, discount: str = "0.00") -> bytes:
        """
        Layout mirrors the store's existing PrISM ticket so re-keying is fast:
        order type banner, contact block, then items in PrISM's own button order.
        """
        # T-056: sanitize every customer-supplied free-text field before any
        # of it is interpolated into the ESC/POS byte stream below.
        name = self._sanitize(name)
        phone = self._sanitize(phone)
        address = self._sanitize(address, keep_newlines=True)
        note = self._sanitize(note, keep_newlines=True)

        b = [INIT, ALIGN_C]
        b += [BOLD_ON, SIZE_2W2H,
              f"** {order_type.upper()} **\n".encode(), SIZE_NORMAL, BOLD_OFF]
        b += [b"** AI PHONE ORDER **\n", b"=" * WIDTH + b"\n"]

        b += [ALIGN_L]
        # T-059: store-local date/time on the physical ticket, never the
        # server's own clock — a cloud server (ADR-019) would otherwise
        # print the wrong date/time on every single ticket, not just near
        # a boundary. See lakewood.orders.store_now()'s docstring.
        from .orders import store_now
        ticket_now = store_now()
        head = [f"Ord# {order_id}", f"{format_12h(ticket_now.timetuple())}  {ticket_now.strftime('%m/%d')}"]
        b += [f"{head[0]:<22}{head[1]:>20}\n".encode()]
        if name or phone:
            b += [f"{name or 'Phone order':<22}{phone:>20}\n".encode()]
        if address:
            b += [BOLD_ON]
            for l in self._wrap(address):
                b += [l.encode() + b"\n"]
            b += [BOLD_OFF]
        if note:
            for l in self._wrap(f"NOTE: {note}"):
                b += [l.encode() + b"\n"]
        b += [b"-" * WIDTH + b"\n"]

        for line in ticket_text.split("\n"):
            if line.startswith("**") or line.startswith("Ord#") \
               or line.startswith("QUOTED"):
                continue
            b += [line.encode("ascii", "replace") + b"\n"]

        if coupon:
            b += [b"-" * WIDTH + b"\n", BOLD_ON]
            b += [f"COUPON: {coupon}  (-${discount})\n".encode()]
            b += [b"** KEY THIS CODE IN PRISM **\n", BOLD_OFF]
        b += [b"-" * WIDTH + b"\n", ALIGN_C, BOLD_ON, SIZE_2H]
        b += [f"TOTAL  ${total}\n".encode(), SIZE_NORMAL, BOLD_OFF]
        b += [b"\nENTER IN PRISM, THEN TAP DONE\n"]
        b += [FEED(3), CUT, BUZZ]
        return b"".join(b)

    # -- dispatch ----------------------------------------------------------

    def dispatch(self, payload: bytes, retries: int = 3,
                 backoff: float = 2.0) -> PrinterStatus:
        """
        Print, then verify. Returns the post-print status on success.
        Raises DispatchError after `retries` — caller moves the order to
        FAILED_DISPATCH and alerts a human. An order is never silently lost.
        """
        last = None
        for attempt in range(1, retries + 1):
            st = self.status()
            if not st.ready:
                last = f"printer not ready (cover={st.cover_open} paper_out={st.paper_out} online={st.online})"
                time.sleep(backoff * attempt)
                continue
            try:
                self._send(payload)
            except Exception as e:
                last = f"send failed: {e}"
                time.sleep(backoff * attempt)
                continue
            post = self.status()
            if post.online and not post.paper_out:
                return post
            last = "printer went offline during print"
            time.sleep(backoff * attempt)
        raise DispatchError(f"Ticket failed after {retries} attempts: {last}")


# ---------------------------------------------------------------------------
# Wiring into the order engine
# ---------------------------------------------------------------------------

def dispatch_confirmed_order(sess, printer: TicketPrinter):
    """
    Drives CONFIRMED -> SENT_TO_STORE -> STORE_ACKED, or -> FAILED_DISPATCH.
    Call immediately after confirm_order returns ok.
    """
    from .pricing import render_ticket

    if sess.state != "CONFIRMED":
        return {"status": "error", "code": "BAD_STATE", "state": sess.state}

    # After-hours orders are held, not printed. A ticket landing on a dark
    # kitchen's printer at 11pm is a ticket nobody sees. The scheduler prints
    # the queue at opening.
    from .orders import store_now, store_status
    st = store_status()
    if not st["open"]:
        sess.to("HELD_FOR_OPEN")
        sess.log("held_for_open", next_open=st.get("next_open"))
        return {"status": "ok", "held": True,
                "print_at": st.get("next_open_iso"),
                "message": f"Held until {st.get('next_open')}."}

    q = sess.order.quote()
    # T-059: store-local time on the printed ticket — see store_now().
    body = render_ticket(sess.order, sess.order_id, format_12h(store_now().timetuple()))
    payload = printer.build(
        body,
        order_type="delivery" if sess.order.order_type == "DELIVERY" else "carry-out",
        order_id=sess.order_id, phone=sess.from_number,
        name=sess.customer_name or "", address=sess.address or "",
        note=sess.delivery_note or "", total=q["total"],
        coupon=sess.order.coupon_code, discount=q["discount"],
    )

    sess.to("SENT_TO_STORE")
    try:
        st = printer.dispatch(payload)
    except DispatchError as e:
        sess.to("FAILED_DISPATCH")
        sess.log("dispatch_failed", error=str(e))
        return {"status": "error", "code": "FAILED_DISPATCH", "message": str(e),
                "action": "alert_staff_by_second_channel"}

    sess.to("STORE_ACKED")
    sess.log("printed", paper_low=st.paper_low)
    return {"status": "ok", "printed": True, "paper_low": st.paper_low}
