"""Minimal IPP Everywhere server for the HP Ink Tank 315.

Speaks IPP and keeps the job queue; turning documents into PCL3GUI is
printing.py's job, and where the bytes go is the sink's (printing.py).
"""

import asyncio
import logging
import socket
import time
import uuid
from dataclasses import dataclass, field

from aiohttp import web

from . import ipp, printing
from .printing import FORMATS

log = logging.getLogger("inktank")

MAKE_MODEL = "HP Ink Tank 315 (inktank)"
URF = ["V1.4", "CP1", "PQ3-4", "RS300-600", "SRGB24", "W8"]

# Sizes in hundredths of a mm; 3 mm margins (the hardware uses 2.96 mm).
MEDIA = {
    "na_letter_8.5x11in": (21590, 27940),
    "iso_a4_210x297mm": (21000, 29700),
}
MARGIN = 300

# job-state
PENDING, PROCESSING, CANCELED, ABORTED, COMPLETED = 3, 5, 7, 8, 9
STATE_NAMES = {PENDING: "pending", PROCESSING: "processing", CANCELED: "canceled",
               ABORTED: "aborted", COMPLETED: "completed"}


# --- Jobs --------------------------------------------------------------------

@dataclass
class Job:
    id: int
    name: str
    user: str
    uri: str
    state: int = PENDING
    reasons: list = field(default_factory=lambda: ["none"])
    created: float = field(default_factory=time.time)
    completed: float | None = None
    document: bytes | None = None
    format: str = "application/octet-stream"
    copies: int = 1
    pages: int = 0
    message: str = ""

    def attributes(self, printer_uri: str) -> list:
        A = ipp.Attr
        return [
            A(ipp.INTEGER, "job-id", [self.id]),
            A(ipp.URI, "job-uri", [f"{printer_uri}/{self.id}"]),
            A(ipp.URI, "job-printer-uri", [printer_uri]),
            A(ipp.NAME, "job-name", [self.name]),
            A(ipp.NAME, "job-originating-user-name", [self.user]),
            A(ipp.ENUM, "job-state", [self.state]),
            A(ipp.KEYWORD, "job-state-reasons", self.reasons),
            A(ipp.TEXT, "job-state-message", [self.message or STATE_NAMES[self.state]]),
            A(ipp.INTEGER, "job-impressions-completed", [self.pages]),
            A(ipp.INTEGER, "time-at-creation", [int(self.created - START)]),
            A(ipp.INTEGER, "time-at-processing", [int(self.created - START)]),
            A(ipp.INTEGER, "time-at-completed", [int((self.completed or self.created) - START)]),
            A(ipp.INTEGER, "copies", [self.copies]),
        ]


START = time.time()


class Printer:
    def __init__(self, sink, name: str = MAKE_MODEL):
        self.sink = sink
        self.name = name
        self.uuid = uuid.uuid5(uuid.NAMESPACE_DNS, socket.gethostname() + "-inktank")
        self.jobs: dict[int, Job] = {}
        self.next_id = 1
        self.queue: "asyncio.Queue[Job]" = asyncio.Queue()
        self.processing: Job | None = None

    # -- attributes --

    def attributes(self, uri: str) -> list:
        A = ipp.Attr
        K, I, B, E = ipp.KEYWORD, ipp.INTEGER, ipp.BOOLEAN, ipp.ENUM
        media_col = []
        for name, (x, y) in MEDIA.items():
            media_col.append([
                A(ipp.BEGIN_COLLECTION, "media-size", [[A(I, "x-dimension", [x]), A(I, "y-dimension", [y])]]),
                A(K, "media-size-name", [name]),
                A(I, "media-top-margin", [MARGIN]), A(I, "media-bottom-margin", [MARGIN]),
                A(I, "media-left-margin", [MARGIN]), A(I, "media-right-margin", [MARGIN]),
                A(K, "media-source", ["main"]),
                A(K, "media-type", ["stationery"]),
            ])
        state = 4 if self.processing else 3
        host = uri.split("/")[2]
        return [
            A(ipp.URI, "printer-uri-supported", [uri]),
            A(K, "uri-security-supported", ["none"]),
            A(K, "uri-authentication-supported", ["none"]),
            A(ipp.NAME, "printer-name", [self.name]),
            A(ipp.TEXT, "printer-info", [self.name]),
            A(ipp.TEXT, "printer-make-and-model", [MAKE_MODEL]),
            A(ipp.TEXT, "printer-location", [socket.gethostname()]),
            A(ipp.URI, "printer-more-info", [f"http://{host}/"]),
            A(ipp.URI, "printer-supply-info-uri", [f"http://{host}/"]),
            A(ipp.URI, "printer-uuid", [f"urn:uuid:{self.uuid}"]),
            A(ipp.TEXT, "printer-device-id", ["MFG:HP;MDL:Ink Tank 315 (inktank);CMD:PWGRaster,URF;"]),
            A(E, "printer-state", [state]),
            A(K, "printer-state-reasons", ["none"]),
            A(ipp.TEXT, "printer-state-message", ["printing" if self.processing else "idle"]),
            A(B, "printer-is-accepting-jobs", [True]),
            A(I, "queued-job-count", [sum(j.state in (PENDING, PROCESSING) for j in self.jobs.values())]),
            A(I, "printer-up-time", [int(time.time() - START)]),
            A(I, "printer-config-change-time", [0]),
            A(K, "ipp-versions-supported", ["1.1", "2.0"]),
            A(K, "ipp-features-supported", ["ipp-everywhere"]),
            A(E, "operations-supported", [ipp.PRINT_JOB, ipp.VALIDATE_JOB, ipp.CREATE_JOB, ipp.SEND_DOCUMENT,
                                          ipp.CANCEL_JOB, ipp.GET_JOB_ATTRIBUTES, ipp.GET_JOBS,
                                          ipp.GET_PRINTER_ATTRIBUTES, ipp.CLOSE_JOB, ipp.IDENTIFY_PRINTER]),
            A(ipp.CHARSET, "charset-configured", ["utf-8"]),
            A(ipp.CHARSET, "charset-supported", ["utf-8"]),
            A(ipp.LANGUAGE, "natural-language-configured", ["en"]),
            A(ipp.LANGUAGE, "generated-natural-language-supported", ["en"]),
            A(ipp.MIME, "document-format-default", ["application/octet-stream"]),
            A(ipp.MIME, "document-format-supported", FORMATS),
            A(K, "compression-supported", ["none"]),
            A(K, "pdl-override-supported", ["attempted"]),
            A(B, "multiple-document-jobs-supported", [False]),
            A(B, "color-supported", [True]),
            A(K, "printer-kind", ["document", "photo"]),
            A(K, "job-creation-attributes-supported", ["copies", "media", "media-col", "print-color-mode",
                                                       "print-quality", "printer-resolution", "sides"]),
            A(I, "copies-default", [1]),
            A(ipp.RANGE, "copies-supported", [(1, 99)]),
            A(K, "media-default", ["na_letter_8.5x11in"]),
            A(K, "media-supported", list(MEDIA)),
            A(K, "media-ready", list(MEDIA)),
            A(ipp.BEGIN_COLLECTION, "media-col-default", [media_col[0]]),
            A(ipp.BEGIN_COLLECTION, "media-col-ready", media_col),
            A(ipp.BEGIN_COLLECTION, "media-col-database", media_col),
            A(K, "media-col-supported", ["media-size", "media-size-name", "media-top-margin",
                                         "media-bottom-margin", "media-left-margin", "media-right-margin",
                                         "media-source", "media-type"]),
            A(ipp.BEGIN_COLLECTION, "media-size-supported",
              [[A(I, "x-dimension", [x]), A(I, "y-dimension", [y])] for x, y in MEDIA.values()]),
            A(I, "media-top-margin-supported", [MARGIN]),
            A(I, "media-bottom-margin-supported", [MARGIN]),
            A(I, "media-left-margin-supported", [MARGIN]),
            A(I, "media-right-margin-supported", [MARGIN]),
            A(K, "media-source-supported", ["main"]),
            A(K, "media-type-supported", ["stationery"]),
            A(K, "output-bin-default", ["face-up"]),
            A(K, "output-bin-supported", ["face-up"]),
            A(K, "print-color-mode-default", ["color"]),
            A(K, "print-color-mode-supported", ["auto", "color", "monochrome"]),
            A(E, "print-quality-default", [4]),
            A(E, "print-quality-supported", [3, 4, 5]),
            A(ipp.RESOLUTION, "printer-resolution-default", [(600, 600, 3)]),
            A(ipp.RESOLUTION, "printer-resolution-supported", [(300, 300, 3), (600, 600, 3)]),
            A(K, "sides-default", ["one-sided"]),
            A(K, "sides-supported", ["one-sided"]),
            A(E, "orientation-requested-default", [3]),
            A(E, "orientation-requested-supported", [3, 4, 5, 6]),
            A(K, "print-scaling-default", ["auto"]),
            A(K, "print-scaling-supported", ["auto", "fit", "fill", "none"]),
            A(ipp.RESOLUTION, "pwg-raster-document-resolution-supported", [(300, 300, 3), (600, 600, 3)]),
            A(K, "pwg-raster-document-sheet-back", ["normal"]),
            A(K, "pwg-raster-document-type-supported", ["sgray_8", "srgb_8"]),
            A(K, "urf-supported", URF),
            A(K, "which-jobs-supported", ["completed", "not-completed", "all"]),
            A(K, "identify-actions-supported", ["display"]),
        ]

    # -- jobs --

    def new_job(self, req: ipp.Message, uri: str) -> Job:
        job = Job(id=self.next_id,
                  name=req.value("job-name", "untitled"),
                  user=req.value("requesting-user-name", "anonymous"),
                  uri=uri,
                  format=req.value("document-format", "application/octet-stream"),
                  copies=int(req.value("copies", 1, tag=ipp.JOB) or 1))
        self.next_id += 1
        self.jobs[job.id] = job
        # Keep only the last 50 jobs.
        for old in sorted(self.jobs)[:-50]:
            del self.jobs[old]
        return job

    async def worker(self):
        while True:
            job = await self.queue.get()
            if job.state == CANCELED:
                continue
            self.processing = job
            job.state = PROCESSING
            job.reasons = ["job-printing"]
            try:
                await asyncio.to_thread(self._print, job)
                job.state = COMPLETED
                job.reasons = ["job-completed-successfully"]
            except Exception as e:     # noqa: BLE001 - reported to the client
                log.exception("job %d failed", job.id)
                job.state = ABORTED
                job.reasons = ["aborted-by-system"]
                job.message = str(e)
            finally:
                job.completed = time.time()
                job.document = None
                self.processing = None

    def _print(self, job: Job):
        doc = job.document or b""
        fmt = printing.detect_format(doc, job.format)
        log.info("job %d: %s, %d bytes, %d copy(ies)", job.id, fmt, len(doc), job.copies)
        t0 = time.time()

        def count_page():
            job.pages += 1

        self.sink.send(job.id, printing.encode_job(doc, fmt, job.copies, count_page))
        log.info("job %d: %d page(s) in %.1fs", job.id, job.pages, time.time() - t0)


# --- HTTP / IPP --------------------------------------------------------------

PRINTER = web.AppKey("printer", Printer)

def _printer_uri(request: web.Request) -> str:
    host = request.headers.get("Host") or f"{request.url.host}:{request.url.port}"
    return f"ipp://{host}/ipp/print"


def _filter(attrs: list, requested: list) -> list:
    if not requested or {"all", "printer-description", "job-template"} & set(requested):
        return attrs
    wanted = set(requested)
    return [a for a in attrs if a.name in wanted]


async def handle_ipp(request: web.Request) -> web.Response:
    printer = request.app[PRINTER]
    body = await request.read()
    try:
        req, data = ipp.decode(body)
    except Exception as e:     # noqa: BLE001
        log.warning("invalid IPP request: %s", e)
        return web.Response(status=400, text="invalid IPP")
    uri = _printer_uri(request)
    op = req.code
    log.debug("op 0x%04x from %s", op, request.remote)

    if req.version[0] not in (1, 2):
        resp = ipp.response_for(req, ipp.VERSION_NOT_SUPPORTED)
    elif op == ipp.GET_PRINTER_ATTRIBUTES:
        resp = ipp.response_for(req, ipp.OK)
        requested = (req.get("requested-attributes") or ipp.Attr(0, "", [])).values
        resp.group(ipp.PRINTER).extend(_filter(printer.attributes(uri), requested))
    elif op == ipp.VALIDATE_JOB:
        resp = _check_format(req) or ipp.response_for(req, ipp.OK)
    elif op in (ipp.PRINT_JOB, ipp.CREATE_JOB):
        resp = _check_format(req)
        if resp is None:
            job = printer.new_job(req, uri)
            if op == ipp.PRINT_JOB:
                job.document = data
                await printer.queue.put(job)
            resp = ipp.response_for(req, ipp.OK)
            resp.group(ipp.JOB).extend(_job_summary(job, uri))
    elif op == ipp.SEND_DOCUMENT:
        job = printer.jobs.get(req.value("job-id"))
        if job is None:
            resp = ipp.response_for(req, ipp.NOT_FOUND)
        else:
            job.document = data
            job.format = req.value("document-format", job.format)
            await printer.queue.put(job)
            resp = ipp.response_for(req, ipp.OK)
            resp.group(ipp.JOB).extend(_job_summary(job, uri))
    elif op == ipp.CLOSE_JOB:
        job = printer.jobs.get(req.value("job-id"))
        resp = ipp.response_for(req, ipp.OK if job else ipp.NOT_FOUND)
    elif op == ipp.CANCEL_JOB:
        job = printer.jobs.get(req.value("job-id"))
        if job is None:
            resp = ipp.response_for(req, ipp.NOT_FOUND)
        elif job.state == PENDING:
            job.state, job.reasons, job.completed = CANCELED, ["job-canceled-by-user"], time.time()
            resp = ipp.response_for(req, ipp.OK)
        else:
            resp = ipp.response_for(req, ipp.NOT_POSSIBLE)
    elif op == ipp.GET_JOB_ATTRIBUTES:
        job = printer.jobs.get(req.value("job-id"))
        if job is None:
            resp = ipp.response_for(req, ipp.NOT_FOUND)
        else:
            resp = ipp.response_for(req, ipp.OK)
            resp.group(ipp.JOB).extend(job.attributes(uri))
    elif op == ipp.GET_JOBS:
        which = req.value("which-jobs", "not-completed")
        resp = ipp.response_for(req, ipp.OK)
        for job in sorted(printer.jobs.values(), key=lambda j: -j.id):
            done = job.state in (CANCELED, ABORTED, COMPLETED)
            if which == "all" or (which == "completed") == done:
                resp.groups.append((ipp.JOB, job.attributes(uri)))
    elif op == ipp.IDENTIFY_PRINTER:
        resp = ipp.response_for(req, ipp.OK)
    else:
        resp = ipp.response_for(req, ipp.OPERATION_NOT_SUPPORTED)

    return web.Response(body=ipp.encode(resp), content_type="application/ipp")


def _check_format(req: ipp.Message) -> ipp.Message | None:
    fmt = req.value("document-format", "application/octet-stream")
    if fmt not in FORMATS:
        resp = ipp.response_for(req, ipp.FORMAT_NOT_SUPPORTED)
        resp.group(ipp.UNSUPPORTED).append(ipp.Attr(ipp.MIME, "document-format", [fmt]))
        return resp
    return None


def _job_summary(job: Job, uri: str) -> list:
    return [ipp.Attr(ipp.INTEGER, "job-id", [job.id]),
            ipp.Attr(ipp.URI, "job-uri", [f"{uri}/{job.id}"]),
            ipp.Attr(ipp.ENUM, "job-state", [job.state]),
            ipp.Attr(ipp.KEYWORD, "job-state-reasons", job.reasons)]


async def handle_index(request: web.Request) -> web.Response:
    printer = request.app[PRINTER]
    rows = "".join(
        f"<tr><td>{j.id}</td><td>{j.name}</td><td>{j.user}</td><td>{STATE_NAMES[j.state]}</td>"
        f"<td>{j.pages}</td><td>{j.message}</td></tr>"
        for j in sorted(printer.jobs.values(), key=lambda j: -j.id))
    state = "printing" if printer.processing else "idle"
    html = f"""<!doctype html><meta charset="utf-8"><title>{printer.name}</title>
<style>body{{font-family:system-ui;margin:2rem}}td,th{{padding:.3rem .8rem;text-align:left}}</style>
<h1>{printer.name}</h1><p>State: <b>{state}</b> · URI: <code>{_printer_uri(request)}</code></p>
<table><tr><th>#</th><th>Job</th><th>User</th><th>State</th><th>Pages</th><th></th></tr>{rows}</table>"""
    return web.Response(text=html, content_type="text/html")


def add_routes(app: web.Application, printer: Printer):
    app[PRINTER] = printer
    app.router.add_post("/ipp/print", handle_ipp)
    app.router.add_post("/", handle_ipp)
    app.router.add_get("/", handle_index)


def mdns_txt(printer: Printer, ip: str, port: int) -> dict:
    return {
        "txtvers": "1", "qtotal": "1", "rp": "ipp/print",
        "ty": MAKE_MODEL, "product": "(HP Ink Tank 315)", "note": socket.gethostname(),
        "adminurl": f"http://{ip}:{port}/",
        "pdl": ",".join(f for f in FORMATS if f != "application/octet-stream"),
        "URF": ",".join(URF), "UUID": str(printer.uuid),
        "Color": "T", "Duplex": "F", "kind": "document,photo", "PaperMax": "legal-A4",
        "printer-state": "3", "printer-type": "0x904E",
    }
