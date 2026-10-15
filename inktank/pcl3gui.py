"""PCL3GUI job generator for the HP Ink Tank 310 family (P15_CISS).

Mirrors what HPLIP does (prnt/hpcups/Pcl3Gui2.cpp + Encapsulator.cpp) with
this family's settings: 8-bit-per-channel RGB, Mode 10, 600 dpi, 8.4 pt
margin on every side and HPMechOffset = 117.
"""

from dataclasses import dataclass

import numpy as np
from PIL import Image

from . import mode10

ESC = b"\x1b"
UEL = ESC + b"%-12345X"
RESET = ESC + b"E"

# cupsInteger0 (pcl_id) values from hpcups.drv.
PAPER_SIZES = {
    # name: (pcl_id, width_pt, height_pt)
    "letter": (2, 612.0, 792.0),
    "a4": (26, 595.44, 841.68),
}

# MediaType -> (PCL media type, subtype). The Windows driver uses 1004 for
# plain paper; HPLIP uses 1084. We follow Windows.
MEDIA_TYPES = {
    "plain": (0, 1004),
    "photo": (5, 1069),
}

QUALITY = {"normal": 0, "best": 1}

MARGIN_PT = 8.4
MECH_OFFSET = 117  # thousandths of an inch


@dataclass
class JobOptions:
    paper: str = "letter"
    media: str = "plain"
    quality: str = "normal"
    dpi: int = 600

    @property
    def page_px(self) -> tuple[int, int]:
        _, w, h = PAPER_SIZES[self.paper]
        return round(w * self.dpi / 72), round(h * self.dpi / 72)

    @property
    def printable_px(self) -> tuple[int, int]:
        _, w, h = PAPER_SIZES[self.paper]
        m = 2 * MARGIN_PT
        return int((w - m) * self.dpi / 72), int((h - m) * self.dpi / 72)

    @property
    def margin_px(self) -> int:
        return int(MARGIN_PT * self.dpi / 72)


def _crd_color(dpi: int) -> bytes:
    # ESC*g12W: format 6, 1 RGB component, compression 10, 32 bits, 1 plane
    return (ESC + b"*g12W" + bytes((0x06, 0x07, 0x00, 0x01))
            + dpi.to_bytes(2, "big") + dpi.to_bytes(2, "big")
            + bytes((0x0A, 0x01, 0x20, 0x01)))


def job_header() -> bytes:
    # The Windows driver prepends 11000 zero bytes; we copy that as-is.
    return (b"\x00" * 11000 + RESET + UEL
            + b"@PJL ENTER LANGUAGE=PCL3GUI\n" + RESET)


def page_header(opt: JobOptions) -> bytes:
    pcl_id = PAPER_SIZES[opt.paper][0]
    media_type, subtype = MEDIA_TYPES[opt.media]
    width, _ = opt.printable_px
    top = opt.margin_px - (MECH_OFFSET * opt.dpi) // 1000

    out = bytearray()
    # Same order as the Windows driver.
    out += b"\x1b&l0S\x1b&l1H"
    out += ESC + b"*o5W" + bytes((0x0D, 0x03, 0x00)) + subtype.to_bytes(2, "big")
    out += b"\x1b*o%dM\x1b&l%dA\x1b&l%dM" % (QUALITY[opt.quality], pcl_id, media_type)
    out += ESC + b"*o5W" + bytes((0x0B, 0x01, 0x00, 0x00, 0x00))   # color mode
    out += b"\x1b&u%dD\x1b*t%dR" % (opt.dpi, opt.dpi)
    out += _crd_color(opt.dpi)
    out += b"\x1b*r%dS" % width
    out += b"\x1b&l-2H"          # paper pre-load
    out += b"\x1b*r1A"           # start raster graphics
    out += b"\x1b*p%dY" % max(top, 0)
    return bytes(out)


def encode_page(img: Image.Image | np.ndarray, opt: JobOptions) -> bytes:
    """Encode an RGB image the size of the printable area (see JobOptions.printable_px).
    Accepts a PIL image or a numpy (height, width, 3) uint8 array."""
    width, height = opt.printable_px
    arr = img if isinstance(img, np.ndarray) else np.asarray(img.convert("RGB"))
    if arr.shape[:2] != (height, width):
        raise ValueError(f"image must be {width}x{height}, got {arr.shape[1]}x{arr.shape[0]}")
    rows = mode10.pack_rows(arr)
    blank = (rows == mode10.WHITE).all(axis=1)

    out = bytearray(page_header(opt))
    white = np.full(width, mode10.WHITE, np.uint32)
    seed = white
    skipped = 0
    for y in range(height):
        if blank[y]:
            skipped += 1
            continue
        if skipped:
            # Like hpcups: skip blank rows with ESC*b#Y and reset the seed row.
            out += b"\x1b*b%dY" % skipped
            seed = white
            skipped = 0
        cur = rows[y]
        data = mode10.encode_row_np(cur, seed)
        out += b"\x1b*b%dW" % len(data) + data
        seed = cur
    out += b"\x1b*rC\x0c"
    return bytes(out)


def job_trailer() -> bytes:
    return RESET + UEL


def encode_job(pages: list[Image.Image], opt: JobOptions) -> bytes:
    out = bytearray(job_header())
    for img in pages:
        out += encode_page(img, opt)
    out += job_trailer()
    return bytes(out)


def raster_to_printable(rgb: np.ndarray, dpi: tuple[int, int],
                        page_size_pt: tuple[int, int] | None = None) -> tuple[np.ndarray, JobOptions]:
    """Turn a full page (as sent by an IPP client) into the printable area at
    600 dpi: pick the paper size, scale, and crop the margins."""
    h, w, _ = rgb.shape
    if page_size_pt and page_size_pt[0] > 0:
        size_in = (page_size_pt[0] / 72, page_size_pt[1] / 72)
    else:
        size_in = (w / dpi[0], h / dpi[1])
    paper = min(PAPER_SIZES, key=lambda p: abs(PAPER_SIZES[p][1] / 72 - size_in[0])
                + abs(PAPER_SIZES[p][2] / 72 - size_in[1]))
    opt = JobOptions(paper=paper)
    page_w, page_h = opt.page_px
    if (w, h) != (page_w, page_h):
        if dpi == (300, 300) and abs(w * 2 - page_w) <= 8 and abs(h * 2 - page_h) <= 8:
            rgb = rgb.repeat(2, axis=0).repeat(2, axis=1)
            h, w = h * 2, w * 2
        if abs(w - page_w) <= 8 and abs(h - page_h) <= 8:
            # Rounding difference (e.g. A4 4960x7016 vs 4962x7014): crop or pad.
            canvas = np.full((page_h, page_w, 3), 255, np.uint8)
            canvas[:min(h, page_h), :min(w, page_w)] = rgb[:page_h, :page_w]
            rgb = canvas
        else:
            img = Image.fromarray(rgb).resize((page_w, page_h), Image.LANCZOS)
            rgb = np.asarray(img)
    m = opt.margin_px
    pw, ph = opt.printable_px
    return np.ascontiguousarray(rgb[m:m + ph, m:m + pw]), opt


def fit_to_printable(img: Image.Image, opt: JobOptions) -> Image.Image:
    """Scale the image to fit the printable area, centered on white."""
    width, height = opt.printable_px
    img = img.convert("RGB")
    img.thumbnail((width, height), Image.LANCZOS)
    canvas = Image.new("RGB", (width, height), "white")
    canvas.paste(img, ((width - img.width) // 2, (height - img.height) // 2))
    return canvas
