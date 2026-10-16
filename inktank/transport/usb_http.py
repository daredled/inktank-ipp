"""HTTP over the printer's USB channel.

USB interface 2 of the Ink Tank (class ff/04/01, bound to WinUSB on Windows)
carries plain HTTP/1.1 to the printer's built-in web server: eSCL at /eSCL/*
and LEDM at /DevMgmt/*.
"""

import logging
import threading
import time

log = logging.getLogger("inktank")

VID, PID = 0x03F0, 0x1053
HTTP_INTERFACE = 2


# --- HTTP/1.1 response parsing ----------------------------------------------

class _Reader:
    def __init__(self, recv):
        self.recv = recv      # returns non-empty bytes or raises
        self.buf = b""

    def until(self, sep: bytes) -> bytes:
        while sep not in self.buf:
            self.buf += self.recv()
        i = self.buf.index(sep) + len(sep)
        out, self.buf = self.buf[:i], self.buf[i:]
        return out

    def exactly(self, n: int) -> bytes:
        while len(self.buf) < n:
            self.buf += self.recv()
        out, self.buf = self.buf[:n], self.buf[n:]
        return out


def read_response(recv) -> tuple[int, list[tuple[str, str]], bytes]:
    """Read one HTTP response: (status, headers, body). Chunked bodies are decoded."""
    r = _Reader(recv)
    lines = r.until(b"\r\n\r\n").decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers = [(k.strip(), v.strip()) for k, _, v in (l.partition(":") for l in lines[1:] if l)]
    h = {k.lower(): v for k, v in headers}
    if h.get("transfer-encoding", "").lower() == "chunked":
        body = b""
        while size := int(r.until(b"\r\n").split(b";")[0], 16):
            body += r.exactly(size)
            r.exactly(2)
        r.until(b"\r\n")     # no trailers
    else:
        body = r.exactly(int(h.get("content-length", 0)))
    if r.buf:
        log.debug("discarding %d stray bytes after the response", len(r.buf))
    return status, headers, body


# --- USB transport -----------------------------------------------------------

class UsbHttp:
    """HTTP over the printer's USB channel, one request at a time (thread-safe).

    Uses libusb (pyusb + libusb-package), so it works on Windows (WinUSB) and
    Linux alike. The device is opened lazily and reopened after any error,
    since the printer can power itself off or drop off the bus.
    """

    def __init__(self, vid: int = VID, pid: int = PID, interface: int = HTTP_INTERFACE):
        self.vid, self.pid, self.interface = vid, pid, interface
        self._lock = threading.Lock()
        self._dev = None

    def _open(self):
        import libusb_package
        import usb.core
        import usb.util
        dev = libusb_package.find(idVendor=self.vid, idProduct=self.pid)
        if dev is None:
            raise FileNotFoundError(f"USB device {self.vid:04x}:{self.pid:04x} not found")
        try:
            if dev.is_kernel_driver_active(self.interface):
                dev.detach_kernel_driver(self.interface)
        except (NotImplementedError, usb.core.USBError):
            pass      # Windows has no kernel driver to detach
        intf = usb.util.find_descriptor(dev[0], bInterfaceNumber=self.interface)

        def bulk(direction):
            return usb.util.find_descriptor(intf, custom_match=lambda e: (
                usb.util.endpoint_direction(e.bEndpointAddress) == direction
                and usb.util.endpoint_type(e.bmAttributes) == usb.util.ENDPOINT_TYPE_BULK))

        usb.util.claim_interface(dev, self.interface)
        self._dev, self._out, self._in = dev, bulk(usb.util.ENDPOINT_OUT), bulk(usb.util.ENDPOINT_IN)
        self._drain()

    def _drain(self):
        """Throw away leftovers from an interrupted response."""
        import usb.core
        for _ in range(100):
            try:
                self._in.read(65536, timeout=100)
            except usb.core.USBTimeoutError:
                return

    def close(self):
        if self._dev is not None:
            import usb.util
            try:
                usb.util.release_interface(self._dev, self.interface)
                usb.util.dispose_resources(self._dev)
            except Exception:     # noqa: BLE001 - the device may already be gone
                pass
            self._dev = None

    def _recv_until(self, deadline: float):
        import usb.core

        def recv() -> bytes:
            # The device sends zero-length packets now and then; keep reading
            # until there is data or the deadline passes.
            while (left := deadline - time.monotonic()) > 0:
                try:
                    data = bytes(self._in.read(65536, timeout=max(1, int(min(left, 5) * 1000))))
                except usb.core.USBTimeoutError:
                    continue
                if data:
                    return data
            raise TimeoutError("timed out waiting for the printer's HTTP response")
        return recv

    def request(self, method: str, path: str, body: bytes = b"", headers: dict | None = None,
                timeout: float = 120) -> tuple[int, list[tuple[str, str]], bytes]:
        head = f"{method} {path} HTTP/1.1\r\nHost: localhost\r\n"
        for k, v in (headers or {}).items():
            head += f"{k}: {v}\r\n"
        if body or method in ("POST", "PUT"):
            head += f"Content-Length: {len(body)}\r\n"
        with self._lock:
            try:
                if self._dev is None:
                    self._open()
                self._out.write(head.encode("latin-1") + b"\r\n" + body, timeout=10000)
                return read_response(self._recv_until(time.monotonic() + timeout))
            except Exception:
                self.close()
                raise
