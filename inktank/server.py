"""The network service: IPP printer and eSCL scanner on one port, plus mDNS."""

import asyncio
import logging

from aiohttp import web

from . import escl_server, ipp_server, mdns
from .ipp_server import MAKE_MODEL
from .transport.usb_http import UsbHttp

log = logging.getLogger("inktank")


async def serve(port: int, sink, mdns_enabled: bool = True, ip: str | None = None, name: str = MAKE_MODEL,
                scanner: UsbHttp | None = None):
    printer = ipp_server.Printer(sink, name)
    app = web.Application(client_max_size=512 * 1024 * 1024)
    ipp_server.add_routes(app, printer)
    if scanner:
        escl_server.add_routes(app, scanner, MAKE_MODEL, str(printer.uuid))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    log.info("IPP listening on port %d", port)
    worker = asyncio.create_task(printer.worker())
    zcs = []
    if mdns_enabled:
        ip = ip or mdns.lan_ip()
        txt = ipp_server.mdns_txt(printer, ip, port)
        services = [
            ("_ipp._tcp.local.", f"{name}._ipp._tcp.local.", txt),
            # Subtype iOS/macOS browse for AirPrint.
            ("_universal._sub._ipp._tcp.local.", f"{name}._ipp._tcp.local.", txt),
        ]
        if scanner:
            services.append(("_uscan._tcp.local.", f"{name}._uscan._tcp.local.",
                             escl_server.mdns_txt(MAKE_MODEL, str(printer.uuid), ip, port, txt["note"])))
        zcs = await mdns.advertise(ip, port, services)
        log.info("advertised over mDNS as '%s' at %s:%d%s", name, ip, port,
                 " (printer and scanner)" if scanner else "")
    try:
        await asyncio.Event().wait()
    finally:
        worker.cancel()
        for zc in zcs:
            await zc.async_close()
        await runner.cleanup()
        if scanner:
            scanner.close()
