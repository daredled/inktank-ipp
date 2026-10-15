"""PWG Raster (PWG 5102.4) and Apple URF decoders.

Both use the same line compression:
    a line-repeat byte (n -> the line repeats n+1 times), then per pixel:
        c < 128 -> the next pixel repeats c+1 times;
        c > 128 -> 257-c literal pixels follow;
        c = 128 -> the rest of the line is white.
"""

import struct
from dataclasses import dataclass

import numpy as np

PWG_SYNC = b"RaS2"
URF_SYNC = b"UNIRAST\x00"

# cupsColorSpace values in PWG
PWG_SGRAY, PWG_SRGB, PWG_ADOBE_RGB, PWG_BLACK = 18, 19, 20, 3
# URF color spaces
URF_SGRAY, URF_SRGB, URF_GRAY, URF_RGB = 0, 1, 4, 5


@dataclass
class RasterPage:
    width: int
    height: int
    dpi: tuple[int, int]
    rgb: np.ndarray             # (height, width, 3) uint8
    page_size_pt: tuple[int, int] | None = None
    copies: int = 1


def _decode_lines(buf: memoryview, pos: int, width: int, height: int, bpp: int, fill: int):
    """Decompress `height` lines; returns (height x width*bpp array, new position)."""
    line_bytes = width * bpp
    out = np.empty((height, line_bytes), np.uint8)
    y = 0
    while y < height:
        repeat = buf[pos] + 1
        pos += 1
        line = bytearray()
        while len(line) < line_bytes:
            c = buf[pos]
            pos += 1
            if c == 128:
                line += bytes((fill,)) * (line_bytes - len(line))
            elif c < 128:
                line += bytes(buf[pos:pos + bpp]) * (c + 1)
                pos += bpp
            else:
                n = (257 - c) * bpp
                line += buf[pos:pos + n]
                pos += n
        row = np.frombuffer(bytes(line[:line_bytes]), np.uint8)
        end = min(height, y + repeat)
        out[y:end] = row
        y = end
    return out, pos


def _to_rgb(data: np.ndarray, width: int, channels: int, invert: bool = False) -> np.ndarray:
    img = data.reshape(data.shape[0], width, channels)
    if invert:
        img = 255 - img
    if channels == 1:
        img = np.repeat(img, 3, axis=2)
    return img


def decode_pwg(buf: bytes):
    if not buf.startswith(PWG_SYNC):
        raise ValueError("not PWG Raster")
    mv = memoryview(buf)
    pos = 4
    while pos + 1796 <= len(buf):
        h = mv[pos:pos + 1796]
        u32 = lambda off: struct.unpack_from(">I", h, off)[0]
        hres, vres = u32(276), u32(280)
        copies = u32(340) or 1
        page_size = (u32(352), u32(356))
        width, height = u32(372), u32(376)
        bpc, bpp_bits = u32(384), u32(388)
        color_space = u32(400)
        pos += 1796
        if bpc != 8:
            raise ValueError(f"PWG with {bpc} bits per color is not supported")
        bpp = bpp_bits // 8
        fill = 0x00 if color_space == PWG_BLACK else 0xFF
        data, pos = _decode_lines(mv, pos, width, height, bpp, fill)
        if color_space in (PWG_SRGB, PWG_ADOBE_RGB, 1) and bpp == 3:
            rgb = _to_rgb(data, width, 3)
        elif color_space in (PWG_SGRAY, 0) and bpp == 1:
            rgb = _to_rgb(data, width, 1)
        elif color_space == PWG_BLACK and bpp == 1:
            rgb = _to_rgb(data, width, 1, invert=True)
        else:
            raise ValueError(f"PWG: color space {color_space} with {bpp} bytes/pixel is not supported")
        yield RasterPage(width, height, (hres, vres), rgb, page_size, copies)


def decode_urf(buf: bytes):
    if not buf.startswith(URF_SYNC):
        raise ValueError("not URF")
    mv = memoryview(buf)
    pages = struct.unpack_from(">I", buf, 8)[0]
    pos = 12
    for _ in range(pages):
        bpp_bits, color_space, _duplex, _quality = struct.unpack_from(">BBBB", buf, pos)
        width, height, dpi = struct.unpack_from(">III", buf, pos + 12)
        pos += 32
        bpp = bpp_bits // 8
        data, pos = _decode_lines(mv, pos, width, height, bpp, 0xFF)
        if bpp == 3:
            rgb = _to_rgb(data, width, 3)
        elif bpp == 1:
            rgb = _to_rgb(data, width, 1)
        else:
            raise ValueError(f"URF with {bpp_bits} bits per pixel is not supported")
        yield RasterPage(width, height, (dpi, dpi), rgb)


def sniff(buf: bytes) -> str | None:
    """Detect the format from the first bytes."""
    if buf.startswith(PWG_SYNC):
        return "image/pwg-raster"
    if buf.startswith(URF_SYNC):
        return "image/urf"
    if buf.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if buf.startswith(b"\x89PNG"):
        return "image/png"
    if buf.startswith(b"%PDF"):
        return "application/pdf"
    return None


# --- Encoders (tests only) ---------------------------------------------------

def _encode_lines(rgb: np.ndarray) -> bytes:
    """Simple valid encoding: each line as literals in blocks of 128."""
    out = bytearray()
    h, w, c = rgb.shape
    for y in range(h):
        out.append(0)
        row = rgb[y].tobytes()
        for x in range(0, w, 128):
            n = min(128, w - x)
            out.append(257 - n if n > 1 else 0)
            out += row[x * c:(x + n) * c]
    return bytes(out)


def encode_pwg(pages: list[np.ndarray], dpi: int = 600, page_size_pt=(612, 792)) -> bytes:
    out = bytearray(PWG_SYNC)
    for rgb in pages:
        h, w, _ = rgb.shape
        hdr = bytearray(1796)
        hdr[0:9] = b"PwgRaster"
        for off, val in ((276, dpi), (280, dpi), (340, 1), (352, page_size_pt[0]), (356, page_size_pt[1]),
                         (372, w), (376, h), (384, 8), (388, 24), (392, w * 3), (400, PWG_SRGB)):
            struct.pack_into(">I", hdr, off, val)
        out += hdr + _encode_lines(rgb)
    return bytes(out)
