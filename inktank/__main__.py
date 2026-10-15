"""Command-line tools.

    inktank testpage out.prn
    inktank image photo.png out.prn
    inktank inspect job.prn [--png prefix]
    inktank usbpcap capture.pcapng [--out prefix]
    inktank print job.prn | photo.png     (Windows, straight over USB)
    inktank device-id                     (Windows)
    inktank serve                         (IPP Everywhere server)
"""

import argparse
import collections
import re
import sys

from PIL import Image, ImageDraw

from . import pclparse, pcl3gui, usbpcap


def make_test_page(opt: pcl3gui.JobOptions) -> Image.Image:
    width, height = opt.printable_px
    img = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(img)
    dpi = opt.dpi
    # 1 mm frame to show where the real margins land.
    d.rectangle([0, 0, width - 1, height - 1], outline="black", width=dpi // 25)
    colors = ["#000000", "#00FFFF", "#FF00FF", "#FFFF00",
              "#FF0000", "#00FF00", "#0000FF", "#808080"]
    sw = (width - 2 * dpi) // len(colors)
    for i, c in enumerate(colors):
        x0 = dpi + i * sw
        d.rectangle([x0, dpi, x0 + sw - dpi // 20, 2 * dpi], fill=c)
    # Gray ramp.
    for x in range(dpi, width - dpi):
        v = 255 * (x - dpi) // (width - 2 * dpi)
        d.line([x, int(2.3 * dpi), x, int(2.8 * dpi)], fill=(v, v, v))
    # A tick every inch along the left edge.
    for y in range(0, height, dpi):
        d.line([0, y, dpi // 2, y], fill="black", width=4)
    d.text((dpi, int(3.2 * dpi)), "inktank - test page", fill="black")
    return img


def cmd_testpage(args):
    opt = pcl3gui.JobOptions(paper=args.paper)
    data = pcl3gui.encode_job([make_test_page(opt)], opt)
    open(args.output, "wb").write(data)
    print(f"{args.output}: {len(data)} bytes")


def cmd_image(args):
    opt = pcl3gui.JobOptions(paper=args.paper)
    img = pcl3gui.fit_to_printable(Image.open(args.input), opt)
    data = pcl3gui.encode_job([img], opt)
    open(args.output, "wb").write(data)
    print(f"{args.output}: {len(data)} bytes")


def cmd_inspect(args):
    buf = open(args.input, "rb").read()
    counts = collections.Counter()
    shown = 0
    for tok in pclparse.tokenize(buf):
        if tok.kind == "pjl":
            print(f"{tok.offset:08x}  {tok.text}")
            continue
        # Collapse repeated commands (the raster rows) to keep the output readable.
        key = tok.text if tok.kind == "byte" else re.sub(r"-?\d+", "#", tok.text)
        counts[key] += 1
        if key in ("ESC*b#W", "ESC*b#Y", "ESC*b#V") and counts[key] > 3:
            continue
        if tok.kind == "byte" and tok.text != "0x0C":
            if shown < 20:
                print(f"{tok.offset:08x}  stray byte {tok.text}")
            shown += 1
            continue
        extra = f"  data={tok.data[:24].hex(' ')}{'…' if len(tok.data) > 24 else ''}" if tok.data else ""
        print(f"{tok.offset:08x}  {tok.text}{extra}")
    print("\nsummary:")
    for k, v in counts.most_common():
        print(f"  {v:7d}  {k}")
    if args.png:
        for i, page in enumerate(pclparse.render(buf), 1):
            for note in page.notes:
                print(f"page {i}: {note}")
            name = f"{args.png}-p{i}.png"
            page.image().save(name)
            print(f"page {i}: {page.width}x{len(page.rows)} -> {name}")


def cmd_usbpcap(args):
    buf = open(args.input, "rb").read()
    streams = usbpcap.bulk_streams(buf)
    if not streams:
        print("no bulk transfers in the capture", file=sys.stderr)
        sys.exit(1)
    for (bus, dev, ep), data in sorted(streams.items()):
        direction = "IN " if ep & 0x80 else "OUT"
        name = f"{args.out}-bus{bus}-dev{dev}-ep{ep & 0x7f}-{direction.strip().lower()}.bin"
        open(name, "wb").write(data)
        print(f"bus {bus} dev {dev} ep 0x{ep:02x} {direction} {len(data):9d} bytes -> {name}"
              f"  starts with: {data[:16]!r}")


def _usb():
    from . import usb_windows
    return usb_windows.UsbPrinter()


def cmd_print(args):
    if args.input.lower().endswith((".prn", ".pcl")):
        data = open(args.input, "rb").read()
    else:
        opt = pcl3gui.JobOptions(paper=args.paper)
        img = pcl3gui.fit_to_printable(Image.open(args.input), opt)
        data = pcl3gui.encode_job([img], opt)
    with _usb() as printer:
        printer.write(data)
        print(f"sent {len(data)} bytes to {printer.path}")


def cmd_device_id(args):
    with _usb() as printer:
        print(printer.device_id())


def cmd_serve(args):
    import asyncio
    import logging
    from . import server
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    sink = server.FileSink(args.to_dir) if args.to_dir else server.UsbSink()
    try:
        asyncio.run(server.serve(args.port, sink, mdns=not args.no_mdns, ip=args.ip, name=args.name))
    except KeyboardInterrupt:
        pass


def main():
    ap = argparse.ArgumentParser(prog="inktank")
    sub = ap.add_subparsers(required=True)

    p = sub.add_parser("testpage", help="generate a PCL3GUI test page")
    p.add_argument("output")
    p.add_argument("--paper", default="letter", choices=pcl3gui.PAPER_SIZES)
    p.set_defaults(func=cmd_testpage)

    p = sub.add_parser("image", help="convert an image into a PCL3GUI job")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("--paper", default="letter", choices=pcl3gui.PAPER_SIZES)
    p.set_defaults(func=cmd_image)

    p = sub.add_parser("inspect", help="list the commands in a .prn and optionally render it")
    p.add_argument("input")
    p.add_argument("--png", help="prefix for saving each page as PNG")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("usbpcap", help="extract bulk data from a USBPcap capture")
    p.add_argument("input")
    p.add_argument("--out", default="capture")
    p.set_defaults(func=cmd_usbpcap)

    p = sub.add_parser("print", help="print a .prn or an image straight over USB (Windows)")
    p.add_argument("input")
    p.add_argument("--paper", default="letter", choices=pcl3gui.PAPER_SIZES)
    p.set_defaults(func=cmd_print)

    p = sub.add_parser("device-id", help="show the printer's IEEE 1284 Device ID (Windows)")
    p.set_defaults(func=cmd_device_id)

    p = sub.add_parser("serve", help="IPP Everywhere server for the local network")
    p.add_argument("--port", type=int, default=8631)
    p.add_argument("--ip", help="IP to advertise over mDNS (default: the default-route interface)")
    p.add_argument("--name", default="HP Ink Tank 315 (inktank)")
    p.add_argument("--no-mdns", action="store_true", help="do not advertise over mDNS")
    p.add_argument("--to-dir", help="save jobs as .prn files instead of printing")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_serve)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
