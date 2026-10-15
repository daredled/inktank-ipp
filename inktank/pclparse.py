"""PCL3GUI stream reader: splits PJL, ESC commands and binary data, and
rebuilds the raster pages so they can be compared visually."""

from dataclasses import dataclass, field

from PIL import Image

from . import mode10

ESC = 0x1B


@dataclass
class Token:
    offset: int
    kind: str              # "pjl", "esc", "byte"
    text: str              # readable form, e.g. "ESC*b123W"
    data: bytes = b""      # binary payload of ...W / ...V commands


def tokenize(buf: bytes):
    i = 0
    n = len(buf)
    while i < n:
        b = buf[i]
        if b == ESC and i + 1 < n:
            start = i
            c = buf[i + 1]
            if 0x21 <= c <= 0x2F and i + 2 < n:
                # Parameterized command: ESC <group> <char> (value letter)+
                group = chr(c)
                j = i + 2
                pchar = ""
                if not (buf[j] in b"0123456789+-."):
                    pchar = chr(buf[j])
                    j += 1
                while j < n:
                    k = j
                    while k < n and buf[k] in b"0123456789+-.":
                        k += 1
                    value = buf[j:k].decode("ascii")
                    if k >= n:
                        j = k
                        break
                    term = chr(buf[k])
                    j = k + 1
                    text = f"ESC{group}{pchar}{value}{term.upper()}"
                    data = b""
                    if term in "WVwv" and group != "%":
                        count = int(float(value or 0))
                        data = buf[j:j + count]
                        j += count
                    yield Token(start, "esc", text, data)
                    start = j
                    if not ("`" <= term <= "~"):
                        break
                i = j
            else:
                yield Token(i, "esc", f"ESC{chr(c)}")
                i += 2
        elif buf.startswith(b"@PJL", i):
            j = buf.find(b"\n", i)
            j = n if j < 0 else j + 1
            yield Token(i, "pjl", buf[i:j].decode("latin-1").rstrip("\r\n"))
            i = j
        else:
            yield Token(i, "byte", f"0x{b:02X}")
            i += 1


@dataclass
class Page:
    width: int = 0
    rows: list = field(default_factory=list)   # RGB bytes per row
    compression: int | None = None
    notes: list = field(default_factory=list)

    def image(self) -> Image.Image:
        if not self.width or not self.rows:
            return Image.new("RGB", (max(self.width, 1), 1), "white")
        return Image.frombytes("RGB", (self.width, len(self.rows)), b"".join(self.rows))


def parse_crd(data: bytes) -> dict:
    """ESC*g#W format 6: components with (hres, vres, compression, orientation, bits, planes)."""
    info = {"format": data[0], "components": []}
    if data[0] == 6 and len(data) >= 4:
        count = data[3]
        for c in range(count):
            p = 4 + c * 8
            info["components"].append({
                "hres": int.from_bytes(data[p:p + 2], "big"),
                "vres": int.from_bytes(data[p + 2:p + 4], "big"),
                "compression": data[p + 4],
                "orientation": data[p + 5],
                "bits": data[p + 6],
                "planes": data[p + 7],
            })
    return info


def render(buf: bytes) -> list[Page]:
    pages: list[Page] = []
    page = Page()
    seed: list[int] = []
    width = 0
    compression = None

    def white():
        return [mode10.WHITE] * width

    for tok in tokenize(buf):
        t = tok.text
        if t.startswith("ESC*g") and t.endswith("W"):
            crd = parse_crd(tok.data)
            comps = crd["components"]
            if comps:
                compression = comps[-1]["compression"]
                page.compression = compression
            page.notes.append(f"CRD {crd}")
        elif t.startswith("ESC*r") and t.endswith("S"):
            width = int(t[5:-1])
            page.width = width
            seed = white()
        elif t.startswith("ESC*b") and t.endswith("M"):
            compression = int(t[5:-1])
            page.compression = compression
        elif t.startswith("ESC*b") and t.endswith("Y"):
            rows = int(t[5:-1])
            page.rows.extend([b"\xff" * (width * 3)] * rows)
            seed = white()
        elif t.startswith("ESC*b") and t.endswith("W"):
            if compression == 10:
                seed = mode10.decode_row(tok.data, seed)
                page.rows.append(mode10.unpack_row(seed))
            else:
                page.notes.append(f"row with unsupported compression {compression}")
                page.rows.append(b"\x80" * (width * 3))
        elif t.startswith("ESC*b") and t.endswith("V"):
            page.notes.append("K plane (ESC*b#V) ignored")
        elif tok.kind == "byte" and tok.text == "0x0C":
            pages.append(page)
            page = Page(width=width, compression=compression)
            seed = white()
    if page.rows:
        pages.append(page)
    return pages
