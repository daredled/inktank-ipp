"""eSCL (AirScan) on the network: a proxy to the printer's own eSCL server.

The printer already speaks eSCL over its USB HTTP channel, so each /eSCL
request is forwarded as is; only what refers to the printer itself is fixed
up (Location headers, make and model, UUID).
"""

import asyncio
import logging
import re
from xml.sax.saxutils import escape

from aiohttp import web

from .transport.usb_http import UsbHttp

log = logging.getLogger("inktank")


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
