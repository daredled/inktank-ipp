"""Scan straight from the printer over USB (eSCL), without the network server."""

from .transport.usb_http import UsbHttp

# Scan regions in 1/300 inch.
REGIONS = {"letter": (2550, 3300), "a4": (2480, 3508)}
COLOR_MODES = {"color": "RGB24", "gray": "Grayscale8", "bw": "BlackAndWhite1"}


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
