"""Check the Mode 10 decoder against HPLIP's original encoder.

Usage: python3 tests/check_against_hplip.py /path/to/harness
(the harness is HPLIP's Mode10.cpp compiled with a main() that reads RGB rows)
"""
import random
import struct
import subprocess
import sys

from PIL import Image, ImageDraw

from inktank import mode10
from inktank.__main__ import make_test_page
from inktank.pcl3gui import JobOptions


def synthetic(w, h):
    rng = random.Random(7)
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    for _ in range(300):
        x, y = rng.randrange(w), rng.randrange(h)
        d.rectangle([x, y, x + rng.randrange(200), y + rng.randrange(60)],
                    fill=tuple(rng.randrange(256) for _ in range(3)))
    px = img.load()
    for y in range(h // 2, h):          # photo-like area: noisy gradients
        for x in range(0, w, 1):
            if rng.random() < 0.6:
                px[x, y] = ((x + rng.randrange(6)) % 256, (y * 3) % 256, (x ^ y) % 256)
    return img


def check(harness, img):
    w, h = img.size
    raw = img.tobytes()
    out = subprocess.run([harness, str(w), str(h)], input=raw, capture_output=True, check=True).stdout
    pos = 0
    seed = [mode10.WHITE] * w
    total = 0
    for y in range(h):
        (n,) = struct.unpack_from("<I", out, pos)
        pos += 4
        data = out[pos:pos + n]
        pos += n
        total += n
        expected = mode10.pack_row(raw[y * w * 3:(y + 1) * w * 3])
        got = mode10.decode_row(data, seed)
        if got != expected:
            bad = next(i for i in range(w) if got[i] != expected[i])
            sys.exit(f"row {y}: differs at x={bad}: {got[bad]:06x} != {expected[bad]:06x}")
        # Also check our own encoder.
        ours = mode10.encode_row(expected, seed)
        assert mode10.decode_row(ours, seed) == expected
        seed = expected
    print(f"ok {w}x{h}: hpcups produced {total} bytes, all rows match")


if __name__ == "__main__":
    harness = sys.argv[1]
    check(harness, synthetic(600, 300))
    check(harness, make_test_page(JobOptions()))
