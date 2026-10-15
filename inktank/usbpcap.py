"""Extract bulk data from a USBPcap capture (.pcap or .pcapng).

This rebuilds the stream the official HP driver sent to the printer (bulk OUT)
and what the printer answered (bulk IN, e.g. status).
"""

import struct
from collections import defaultdict

LINKTYPE_USBPCAP = 249
TRANSFER_BULK = 3


def _packets_pcap(buf: bytes):
    magic = buf[:4]
    endian = "<" if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1") else ">"
    linktype = struct.unpack(endian + "I", buf[20:24])[0]
    pos = 24
    while pos + 16 <= len(buf):
        _, _, incl, _ = struct.unpack(endian + "IIII", buf[pos:pos + 16])
        pos += 16
        yield linktype, buf[pos:pos + incl]
        pos += incl


def _packets_pcapng(buf: bytes):
    pos = 0
    endian = "<"
    linktypes = []
    while pos + 12 <= len(buf):
        btype = struct.unpack(endian + "I", buf[pos:pos + 4])[0]
        if btype == 0x0A0D0D0A:
            endian = "<" if buf[pos + 8:pos + 12] == b"\x4d\x3c\x2b\x1a" else ">"
            linktypes = []
        blen = struct.unpack(endian + "I", buf[pos + 4:pos + 8])[0]
        body = buf[pos + 8:pos + blen - 4]
        if btype == 1:      # Interface Description Block
            linktypes.append(struct.unpack(endian + "H", body[:2])[0])
        elif btype == 6:    # Enhanced Packet Block
            iface, _, _, caplen, _ = struct.unpack(endian + "IIIII", body[:20])
            yield linktypes[iface], body[20:20 + caplen]
        elif btype == 3:    # Simple Packet Block
            caplen = struct.unpack(endian + "I", body[:4])[0]
            yield linktypes[0], body[4:4 + caplen]
        if blen < 12:
            break
        pos += blen


def packets(buf: bytes):
    if buf[:4] == b"\x0a\x0d\x0d\x0a":
        yield from _packets_pcapng(buf)
    else:
        yield from _packets_pcap(buf)


def bulk_streams(buf: bytes) -> dict:
    """Return {(bus, device, endpoint): bytes} with the bulk data.
    Bit 0x80 of the endpoint means IN (printer -> host)."""
    streams = defaultdict(bytearray)
    for linktype, pkt in packets(buf):
        if linktype != LINKTYPE_USBPCAP or len(pkt) < 27:
            continue
        hlen, _irp, _status, _func, info, bus, dev, ep, transfer, dlen = \
            struct.unpack("<HQIHBHHBBI", pkt[:27])
        if transfer != TRANSFER_BULK or dlen == 0:
            continue
        to_host = bool(info & 1)
        # OUT: data travels in the request (host -> device).
        # IN: data travels in the completion (device -> host).
        if bool(ep & 0x80) != to_host:
            continue
        streams[(bus, dev, ep)] += pkt[hlen:hlen + dlen]
    return {k: bytes(v) for k, v in streams.items()}
