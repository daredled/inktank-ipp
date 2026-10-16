"""Documents to PCL3GUI, and where the printer data goes (sinks).

Accepts PWG Raster (Windows, Android/Mopria), URF (AirPrint), JPEG and PNG.
"""

import io
import logging
from pathlib import Path

import numpy as np
from PIL import Image

from . import pcl3gui, raster

log = logging.getLogger("inktank")

FORMATS = ["image/pwg-raster", "image/urf", "image/jpeg", "image/png", "application/octet-stream"]


def detect_format(doc: bytes, fmt: str) -> str:
    if fmt == "application/octet-stream":
        return raster.sniff(doc) or fmt
    return fmt


def pages(doc: bytes, fmt: str):
    """Yield (rgb, JobOptions) for each page, already cut to the printable area."""
    if fmt == "image/pwg-raster":
        decoded = raster.decode_pwg(doc)
    elif fmt == "image/urf":
        decoded = raster.decode_urf(doc)
    elif fmt in ("image/jpeg", "image/png"):
        opt = pcl3gui.JobOptions()
        img = pcl3gui.fit_to_printable(Image.open(io.BytesIO(doc)), opt)
        yield np.asarray(img), opt
        return
    else:
        raise ValueError(f"unsupported format: {fmt}")
    for page in decoded:
        yield pcl3gui.raster_to_printable(page.rgb, page.dpi, page.page_size_pt)


def encode_job(doc: bytes, fmt: str, copies: int = 1, on_page=None):
    """Yield the PCL3GUI job in pieces, calling on_page() after each page."""
    yield pcl3gui.job_header()
    for page_rgb, opt in pages(doc, fmt):
        # Raster formats already carry their copies; images do not.
        for _ in range(copies if fmt in ("image/jpeg", "image/png") else 1):
            yield pcl3gui.encode_page(page_rgb, opt)
            if on_page:
                on_page()
    yield pcl3gui.job_trailer()


# --- Sinks -------------------------------------------------------------------

class FileSink:
    """Save each job as a .prn file (for development without a printer)."""

    def __init__(self, directory: str):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def send(self, job_id: int, chunks):
        path = self.dir / f"job-{job_id}.prn"
        with open(path, "wb") as f:
            for c in chunks:
                f.write(c)
        log.info("job %d saved to %s", job_id, path)


class UsbSink:
    """Send straight through usbprint.sys (Windows)."""

    def send(self, job_id: int, chunks):
        from .transport.usbprint import UsbPrinter
        with UsbPrinter() as printer:
            for c in chunks:
                printer.write(c)
