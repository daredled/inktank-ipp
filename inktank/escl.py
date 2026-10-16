"""eSCL (AirScan) scanning through the printer's USB HTTP channel.

USB interface 2 of the Ink Tank (class ff/04/01, bound to WinUSB on Windows)
carries plain HTTP/1.1 to the printer's built-in web server, which already
speaks eSCL at /eSCL/*. Network scanning is therefore a proxy: forward each
/eSCL request over USB and fix up what refers to the printer itself
(Location headers, make and model, UUID).
"""

import asyncio
import logging
import re
import threading
import time
from xml.sax.saxutils import escape

from aiohttp import web

log = logging.getLogger("inktank")

VID, PID = 0x03F0, 0x1053
HTTP_INTERFACE = 2

# Scan regions in 1/300 inch.
REGIONS = {"letter": (2550, 3300), "a4": (2480, 3508)}
COLOR_MODES = {"color": "RGB24", "gray": "Grayscale8", "bw": "BlackAndWhite1"}


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


# --- Direct scan (CLI) -------------------------------------------------------

def scan_settings(dpi: int = 300, mode: str = "color", fmt: str = "image/jpeg", paper: str = "letter") -> bytes:
    w, h = REGIONS[paper]
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.63</pwg:Version>
  <pwg:ScanRegions>
    <pwg:ScanRegion>
      <pwg:Height>{h}</pwg:Height>
      <pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
      <pwg:Width>{w}</pwg:Width>
      <pwg:XOffset>0</pwg:XOffset>
      <pwg:YOffset>0</pwg:YOffset>
    </pwg:ScanRegion>
  </pwg:ScanRegions>
  <pwg:InputSource>Platen</pwg:InputSource>
  <scan:ColorMode>{COLOR_MODES[mode]}</scan:ColorMode>
  <scan:XResolution>{dpi}</scan:XResolution>
  <scan:YResolution>{dpi}</scan:YResolution>
  <pwg:DocumentFormat>{fmt}</pwg:DocumentFormat>
</scan:ScanSettings>
""".encode()


def scan(channel: UsbHttp, settings: bytes) -> bytes:
    """Scan one page from the platen and return the document."""
    status, headers, body = channel.request("POST", "/eSCL/ScanJobs", settings,
                                            {"Content-Type": "text/xml"})
    location = dict((k.lower(), v) for k, v in headers).get("location")
    if status != 201 or not location:
        raise RuntimeError(f"ScanJobs failed: HTTP {status} {body[:200]!r}")
    job = location[location.index("/eSCL"):]
    status, _, doc = channel.request("GET", f"{job}/NextDocument", timeout=300)
    if status != 200:
        raise RuntimeError(f"NextDocument failed: HTTP {status} {doc[:200]!r}")
    return doc


# --- Network proxy -----------------------------------------------------------

SCANNER = web.AppKey("scanner", UsbHttp)
SCANNER_INFO = web.AppKey("scanner_info", dict)

# Response headers worth passing on; the rest describe the USB link.
_PASS_HEADERS = {"content-type", "location", "retry-after", "cache-control", "pragma", "etag"}


def rewrite_capabilities(xml: bytes, make_model: str, uuid: str) -> bytes:
    # The firmware reports "xxxx" as make and model and its own UUID; use ours
    # so clients show the same device as the printer.
    text = xml.decode("utf-8")
    text = re.sub(r"<pwg:MakeAndModel>.*?</pwg:MakeAndModel>",
                  f"<pwg:MakeAndModel>{escape(make_model)}</pwg:MakeAndModel>", text, count=1)
    text = re.sub(r"<scan:UUID>.*?</scan:UUID>", f"<scan:UUID>{uuid}</scan:UUID>", text, count=1)
    return text.encode("utf-8")


async def handle_escl(request: web.Request) -> web.Response:
    scanner = request.app[SCANNER]
    body = await request.read()
    fwd = {"Content-Type": request.headers["Content-Type"]} if body and "Content-Type" in request.headers else {}
    # NextDocument blocks while the page is being scanned.
    timeout = 300 if request.path.endswith("/NextDocument") else 30
    try:
        status, headers, data = await asyncio.to_thread(
            scanner.request, request.method, request.path_qs, body, fwd, timeout)
    except Exception as e:     # noqa: BLE001 - reported to the client
        log.warning("eSCL %s %s failed: %s", request.method, request.path, e)
        return web.Response(status=503, text=f"scanner unavailable: {e}")

    out = {}
    for k, v in headers:
        if k.lower() not in _PASS_HEADERS:
            continue
        if k.lower() == "location" and "/eSCL" in v:
            v = f"{request.scheme}://{request.host}{v[v.index('/eSCL'):]}"
        out[k] = v
    if request.path == "/eSCL/ScannerCapabilities" and status == 200:
        info = request.app[SCANNER_INFO]
        data = rewrite_capabilities(data, info["make_model"], info["uuid"])
    if request.method == "POST" and request.path == "/eSCL/ScanJobs":
        log.info("scan job %s -> HTTP %d", out.get("Location", "-"), status)
    elif request.path.endswith("/NextDocument") and status == 200:
        log.info("scan: %s, %d bytes", out.get("Content-Type", "?"), len(data))
    return web.Response(status=status, body=data, headers=out)


def add_routes(app: web.Application, scanner: UsbHttp, make_model: str, uuid: str):
    app[SCANNER] = scanner
    app[SCANNER_INFO] = {"make_model": make_model, "uuid": uuid}
    app.router.add_route("*", "/eSCL/{tail:.*}", handle_escl)


def mdns_txt(make_model: str, uuid: str, ip: str, port: int, note: str = "") -> dict:
    return {
        "txtvers": "1", "vers": "2.63", "rs": "eSCL",
        "ty": make_model, "note": note, "UUID": uuid,
        "adminurl": f"http://{ip}:{port}/",
        "pdl": "application/pdf,image/jpeg",
        "cs": "color,grayscale,binary", "is": "platen", "duplex": "F",
    }
