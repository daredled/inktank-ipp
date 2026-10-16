import asyncio

import numpy as np
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from inktank import ipp, ipp_server, pcl3gui, pclparse, printing, raster


def _request(op, attrs, job_attrs=None, version=(2, 0)):
    msg = ipp.Message(version, op, 7)
    g = msg.group(ipp.OPERATION)
    g.append(ipp.Attr(ipp.CHARSET, "attributes-charset", ["utf-8"]))
    g.append(ipp.Attr(ipp.LANGUAGE, "attributes-natural-language", ["en"]))
    g.extend(attrs)
    if job_attrs:
        msg.group(ipp.JOB).extend(job_attrs)
    return ipp.encode(msg)


def test_ipp_roundtrip_with_collections():
    printer = ipp_server.Printer(printing.FileSink("out/test-jobs"))
    msg = ipp.Message((2, 0), ipp.OK, 3)
    msg.group(ipp.PRINTER).extend(printer.attributes("ipp://x:1/ipp/print"))
    decoded, rest = ipp.decode(ipp.encode(msg))
    assert rest == b""
    db = decoded.get("media-col-database")
    assert len(db.values) == len(ipp_server.MEDIA)
    size = next(m for m in db.values[0] if m.name == "media-size").value
    assert {m.name: m.value for m in size} == {"x-dimension": 21590, "y-dimension": 27940}
    assert decoded.value("printer-resolution-default") == (600, 600, 3)


def _page():
    opt = pcl3gui.JobOptions()
    w, h = opt.page_px
    rgb = np.full((h, w, 3), 255, np.uint8)
    m = opt.margin_px
    rgb[m:m + 300, m:m + 600] = (255, 0, 0)          # red, top left
    rgb[h - m - 300:h - m, w - m - 600:w - m] = (0, 0, 255)   # blue, bottom right
    rgb[3000:3010, :] = 0                             # an edge-to-edge line
    return rgb


def test_print_job_pwg_end_to_end(tmp_path):
    async def run():
        printer = ipp_server.Printer(printing.FileSink(str(tmp_path)))
        app = web.Application(client_max_size=512 * 1024 * 1024)
        ipp_server.add_routes(app, printer)
        worker = asyncio.create_task(printer.worker())
        async with TestClient(TestServer(app)) as client:
            headers = {"Content-Type": "application/ipp"}
            r = await client.post("/ipp/print", headers=headers, data=_request(
                ipp.GET_PRINTER_ATTRIBUTES,
                [ipp.Attr(ipp.URI, "printer-uri", ["ipp://localhost/ipp/print"]),
                 ipp.Attr(ipp.KEYWORD, "requested-attributes", ["printer-state", "document-format-supported"])]))
            resp, _ = ipp.decode(await r.read())
            assert resp.code == ipp.OK
            assert resp.value("printer-state") == 3
            assert "image/pwg-raster" in resp.get("document-format-supported").values
            assert resp.get("media-col-database") is None   # filtered out

            pwg = raster.encode_pwg([_page()])
            r = await client.post("/ipp/print", headers=headers, data=_request(
                ipp.PRINT_JOB,
                [ipp.Attr(ipp.URI, "printer-uri", ["ipp://localhost/ipp/print"]),
                 ipp.Attr(ipp.NAME, "job-name", ["test"]),
                 ipp.Attr(ipp.MIME, "document-format", ["image/pwg-raster"])]) + pwg)
            resp, _ = ipp.decode(await r.read())
            assert resp.code == ipp.OK
            job_id = resp.value("job-id")
            for _ in range(200):
                if printer.jobs[job_id].state not in (ipp_server.PENDING, ipp_server.PROCESSING):
                    break
                await asyncio.sleep(0.05)
            assert printer.jobs[job_id].state == ipp_server.COMPLETED, printer.jobs[job_id].message

            r = await client.post("/ipp/print", headers=headers, data=_request(
                ipp.GET_JOB_ATTRIBUTES,
                [ipp.Attr(ipp.URI, "printer-uri", ["ipp://localhost/ipp/print"]),
                 ipp.Attr(ipp.INTEGER, "job-id", [job_id])]))
            resp, _ = ipp.decode(await r.read())
            assert resp.value("job-state") == ipp_server.COMPLETED
            assert resp.value("job-impressions-completed") == 1
        worker.cancel()
        return job_id

    job_id = asyncio.run(run())
    prn = (tmp_path / f"job-{job_id}.prn").read_bytes()
    pages = pclparse.render(prn)
    assert len(pages) == 1
    got = np.asarray(pages[0].image())
    opt = pcl3gui.JobOptions()
    m = opt.margin_px
    expected = _page()[m:m + opt.printable_px[1], m:m + opt.printable_px[0]] & 0xFE
    # The render only has rows up to the last one with content.
    assert np.array_equal(got & 0xFE, expected[:got.shape[0]] & 0xFE)
    assert got[0, 0].tolist() == [255, 0, 0]
