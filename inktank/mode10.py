"""PCL "Mode 10" compression (Bert format) for 24-bit RGB rows.

Reference: HPLIP prnt/hpcups/Mode10.cpp. Each row is encoded relative to the
previous one (the "seed row"). Pixels are compared with the least significant
bit of blue dropped (mask 0xFFFFFE), just like hpcups.

Command byte:
    bit 7      type: 0 = literal, 1 = RLE
    bits 6-5   source of the first pixel: 0 new, 1 west, 2 northeast, 3 cache
    bits 4-3   pixels to copy from the seed row before the command (3 = VLI follows)
    bits 2-0   pixel count (literal: n-1, RLE: n-2; 7 = VLI follows)
"""

MASK = 0xFFFFFE
WHITE = 0xFFFFFE

LITERAL = 0x00
RLE = 0x80
SRC_NEW, SRC_WEST, SRC_NE, SRC_CACHE = 0, 1, 2, 3


def pack_row(rgb: bytes) -> list[int]:
    """Convert RGB bytes into a list of masked pixels."""
    return [((rgb[i] << 16) | (rgb[i + 1] << 8) | rgb[i + 2]) & MASK
            for i in range(0, len(rgb), 3)]


def unpack_row(pixels: list[int]) -> bytes:
    out = bytearray(len(pixels) * 3)
    for i, p in enumerate(pixels):
        out[3 * i] = (p >> 16) & 0xFF
        out[3 * i + 1] = (p >> 8) & 0xFF
        out[3 * i + 2] = p & 0xFF
    return bytes(out)


def _vli(n: int, out: bytearray) -> None:
    # Same as outputVLIBytesConsecutively: an exact 255 is followed by 0.
    while True:
        b = min(n, 255)
        out.append(b)
        if n == 255:
            out.append(0)
        n -= b
        if n == 0:
            break


def _short_delta(p: int, up: int) -> int:
    dr = ((p >> 16) & 0xFF) - ((up >> 16) & 0xFF)
    dg = ((p >> 8) & 0xFF) - ((up >> 8) & 0xFF)
    db = (p & 0xFF) - (up & 0xFF)
    if -16 <= dr <= 15 and -16 <= dg <= 15 and -32 <= db <= 30:
        return 0x8000 | ((dr << 10) & 0x7C00) | ((dg << 5) & 0x03E0) | ((db >> 1) & 0x1F)
    return 0


def _put_pixel(p: int, up: int, out: bytearray) -> None:
    d = _short_delta(p, up)
    if d:
        out += bytes((d >> 8, d & 0xFF))
    else:
        v = p >> 1
        out += bytes(((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF))


def encode_row(cur: list[int], seed: list[int]) -> bytes:
    """Encode one row. Only uses "new" pixels (no cache/west/northeast), so it
    compresses slightly less than hpcups, but the output is valid Bert."""
    n = len(cur)
    out = bytearray()
    x = 0
    while x < n:
        start = x
        while x < n and cur[x] == seed[x]:
            x += 1
        if x == n:
            break
        offset = x - start

        run = 1
        while x + run < n and cur[x + run] == cur[x]:
            run += 1

        if run >= 2:
            out.append(RLE | (min(3, offset) << 3) | min(7, run - 2))
            if offset >= 3:
                _vli(offset - 3, out)
            _put_pixel(cur[x], seed[x], out)
            if run - 2 >= 7:
                _vli(run - 9, out)
            x += run
            continue

        # Literal: extend until a run starts or a pixel matches the seed row.
        length = 1
        while x + length < n:
            i = x + length
            if cur[i] == seed[i]:
                break
            if i + 1 < n and cur[i] == cur[i + 1]:
                break
            length += 1
        out.append(LITERAL | (min(3, offset) << 3) | min(7, length - 1))
        if offset >= 3:
            _vli(offset - 3, out)
        for k in range(1, length + 1):
            i = x + k - 1
            _put_pixel(cur[i], seed[i], out)
            if (k - 8) % 255 == 0 and k >= 8:
                out.append(min(255, length - k))
        x += length
    return bytes(out)


def decode_row(data: bytes, seed: list[int]) -> list[int]:
    """Decode one Mode 10 row. Handles the full format (cache, west, northeast)."""
    row = list(seed)
    n = len(row)
    pos = 0
    x = 0
    cache = WHITE

    def read_vli() -> int:
        nonlocal pos
        total = 0
        while True:
            b = data[pos]
            pos += 1
            total += b
            if b != 255:
                return total

    def read_pixel(at: int) -> int:
        nonlocal pos
        b0 = data[pos]
        if b0 & 0x80:
            v = (b0 << 8) | data[pos + 1]
            pos += 2
            up = seed[at] if at < n else WHITE
            dr = (v >> 10) & 0x1F
            dg = (v >> 5) & 0x1F
            db = v & 0x1F
            dr -= 32 if dr & 0x10 else 0
            dg -= 32 if dg & 0x10 else 0
            db -= 32 if db & 0x10 else 0
            r = (((up >> 16) & 0xFF) + dr) & 0xFF
            g = (((up >> 8) & 0xFF) + dg) & 0xFF
            b = ((up & 0xFF) + db * 2) & 0xFF
            return ((r << 16) | (g << 8) | b) & MASK
        v = (b0 << 16) | (data[pos + 1] << 8) | data[pos + 2]
        pos += 3
        return (v << 1) & MASK

    def alt_pixel(src: int, at: int) -> int:
        if src == SRC_WEST:
            return row[at - 1] if at > 0 else WHITE
        if src == SRC_NE:
            return seed[at + 1] if at + 1 < n else WHITE
        return cache

    while pos < len(data):
        cmd = data[pos]
        pos += 1
        src = (cmd >> 5) & 3
        offset = (cmd >> 3) & 3
        count = cmd & 7
        if offset == 3:
            offset += read_vli()
        x += offset

        if cmd & RLE:
            total = count + 2
            if src == SRC_NEW:
                color = read_pixel(x)
                cache = color
            else:
                color = alt_pixel(src, x)
            if count == 7:
                total += read_vli()
            for i in range(x, min(n, x + total)):
                row[i] = color
            x += total
        else:
            total = count + 1
            k = 1
            while k <= total:
                if k == 1 and src != SRC_NEW:
                    p = alt_pixel(src, x)
                else:
                    p = read_pixel(x)
                    if k == 1:
                        cache = p
                if x < n:
                    row[x] = p
                x += 1
                if count == 7 and k >= 8 and (k - 8) % 255 == 0:
                    total = k + data[pos]
                    pos += 1
                k += 1
    return row


# --- numpy-vectorized version ------------------------------------------------
# Same algorithm and byte-for-byte the same output as encode_row, but it steps
# through commands instead of pixels: document rows have few commands.

import numpy as np


def pack_rows(rgb: np.ndarray) -> np.ndarray:
    """(height, width, 3) uint8 -> masked (height, width) uint32."""
    rgb = rgb.astype(np.uint32)
    return ((rgb[..., 0] << 16) | (rgb[..., 1] << 8) | rgb[..., 2]) & MASK


def _pixel_bytes(cur: np.ndarray, seed: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Encode every pixel: returns (per-pixel bytes as an Nx3 array, length 2 or 3)."""
    c = cur.astype(np.int32)
    s = seed.astype(np.int32)
    dr = ((c >> 16) & 0xFF) - ((s >> 16) & 0xFF)
    dg = ((c >> 8) & 0xFF) - ((s >> 8) & 0xFF)
    db = (c & 0xFF) - (s & 0xFF)
    ok = (dr >= -16) & (dr <= 15) & (dg >= -16) & (dg <= 15) & (db >= -32) & (db <= 30)
    short = 0x8000 | ((dr << 10) & 0x7C00) | ((dg << 5) & 0x03E0) | ((db >> 1) & 0x1F)
    raw = c >> 1
    out = np.empty((len(c), 3), np.uint8)
    out[:, 0] = np.where(ok, short >> 8, raw >> 16) & 0xFF
    out[:, 1] = np.where(ok, short, raw >> 8) & 0xFF
    out[:, 2] = raw & 0xFF
    return out, np.where(ok, 2, 3)


def _literal_bytes(enc: np.ndarray, lens: np.ndarray) -> bytearray:
    if (lens == 2).all():
        return bytearray(enc[:, :2].tobytes())
    if (lens == 3).all():
        return bytearray(enc.tobytes())
    keep = np.ones(enc.shape, bool)
    keep[:, 2] = lens == 3
    return bytearray(enc[keep].tobytes())


def encode_row_np(cur: np.ndarray, seed: np.ndarray) -> bytes:
    n = len(cur)
    diff = cur != seed
    changed = np.flatnonzero(diff)
    if len(changed) == 0:
        return b""
    eq_next = np.zeros(n, bool)
    eq_next[:-1] = cur[:-1] == cur[1:]
    # End of each run of equal consecutive pixels.
    breaks = np.flatnonzero(~eq_next)          # last index of each run
    # A literal stops at i if cur[i] == seed[i] or a run starts at i.
    stops = np.flatnonzero(~diff | eq_next)
    enc, lens = _pixel_bytes(cur, seed)

    out = bytearray()
    x = 0
    ci = 0
    while True:
        ci = np.searchsorted(changed, x)
        if ci >= len(changed):
            break
        nx = int(changed[ci])
        offset = nx - x
        x = nx
        run_end = int(breaks[np.searchsorted(breaks, x)]) + 1
        run = run_end - x
        if run >= 2:
            out.append(RLE | (min(3, offset) << 3) | min(7, run - 2))
            if offset >= 3:
                _vli(offset - 3, out)
            out += enc[x, :lens[x]].tobytes()
            if run - 2 >= 7:
                _vli(run - 9, out)
            x = run_end
            continue
        si = np.searchsorted(stops, x + 1)
        end = int(stops[si]) if si < len(stops) else n
        length = end - x
        out.append(LITERAL | (min(3, offset) << 3) | min(7, length - 1))
        if offset >= 3:
            _vli(offset - 3, out)
        if length < 8:
            out += _literal_bytes(enc[x:end], lens[x:end])
        else:
            # After pixel 8, 263, 518... comes a byte with the remaining pixel count.
            k = 0
            while k < length:
                upto = 8 if k == 0 else min(length, k + 255)
                upto = min(upto, length)
                out += _literal_bytes(enc[x + k:x + upto], lens[x + k:x + upto])
                if upto == 8 or (upto - 8) % 255 == 0:
                    out.append(min(255, length - upto))
                k = upto
        x = end
    return bytes(out)
