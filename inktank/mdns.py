"""mDNS / DNS-SD advertisement of the printer and the scanner."""

import logging
import socket

log = logging.getLogger("inktank")


def lan_ip() -> str:
    """IP of the default-route interface (sends no packets)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]


async def advertise(ip: str, port: int, services: list[tuple[str, str, dict]]) -> list:
    """Register each (service type, instance name, TXT record); returns the instances to close."""
    from zeroconf import IPVersion
    from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

    server = f"{socket.gethostname().lower()}-inktank.local."
    zcs = []
    # zeroconf indexes services by name, so each registration sharing the
    # name gets its own instance, in cooperating mode.
    for type_, name, txt in services:
        info = AsyncServiceInfo(type_, name, port=port, properties=txt, server=server, parsed_addresses=[ip])
        zc = AsyncZeroconf(interfaces=[ip], ip_version=IPVersion.V4Only)
        await zc.async_register_service(info, cooperating_responders=True)
        zcs.append(zc)
    return zcs
