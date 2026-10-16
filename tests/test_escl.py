import asyncio

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from inktank import escl_server
from inktank.transport import usb_http


def _recv_from(*packets):
    it = iter(packets)
    return lambda: next(it)


def test_read_response_content_length_split_across_packets():
    status, headers, body = usb_http.read_response(_recv_from(
        b"HTTP/1.1 201 Created\r\nLocation: http://local",
        b"host/eSCL/ScanJobs/42\r\nContent-Length: 5\r\n\r\nab",
        b"cde"))
    assert status == 201
    assert dict(headers)["Location"] == "http://localhost/eSCL/ScanJobs/42"
    assert body == b"abcde"


def test_read_response_chunked():
    status, _, body = usb_http.read_response(_recv_from(
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\nab",
        b"cd\r\nA;ext=1\r\n0123456789\r\n0\r\n\r\n"))
    assert status == 200
    assert body == b"abcd0123456789"


def test_read_response_without_body():
    status, _, body = usb_http.read_response(_recv_from(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n"))
    assert (status, body) == (404, b"")


CAPS = b"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScannerCapabilities xmlns:scan="x" xmlns:pwg="y">
\t<pwg:MakeAndModel>xxxx</pwg:MakeAndModel>
\t<scan:UUID>abcd434e-3037-3436-4d30-3439344d3430</scan:UUID>
</scan:ScannerCapabilities>
"""


class FakeScanner:
    def __init__(self):
        self.calls = []

    def request(self, method, path, body=b"", headers=None, timeout=120):
        self.calls.append((method, path, body, headers))
        if path == "/eSCL/ScannerCapabilities":
            return 200, [("Content-Type", "text/xml"), ("Server", "HP"), ("Content-Length", "1")], CAPS
        if path == "/eSCL/ScanJobs":
            return 201, [("Location", "http://localhost/eSCL/ScanJobs/7"), ("Content-Length", "0")], b""
        if path == "/eSCL/ScanJobs/7/NextDocument":
            return 200, [("Content-Type", "image/jpeg")], b"\xff\xd8jpeg"
        raise OSError("printer is off")


def test_proxy():
    async def run():
        fake = FakeScanner()
        app = web.Application()
        escl_server.add_routes(app, fake, "HP Ink Tank 315 (inktank)", "1234")
        async with TestClient(TestServer(app)) as client:
            r = await client.get("/eSCL/ScannerCapabilities")
            assert r.status == 200
            assert r.headers.get("Server") != "HP"
            text = await r.text()
            assert "<pwg:MakeAndModel>HP Ink Tank 315 (inktank)</pwg:MakeAndModel>" in text
            assert "<scan:UUID>1234</scan:UUID>" in text

            r = await client.post("/eSCL/ScanJobs", data=b"<settings/>", headers={"Content-Type": "text/xml"})
            assert r.status == 201
            assert r.headers["Location"] == f"http://{client.host}:{client.port}/eSCL/ScanJobs/7"
            assert fake.calls[-1] == ("POST", "/eSCL/ScanJobs", b"<settings/>", {"Content-Type": "text/xml"})

            r = await client.get("/eSCL/ScanJobs/7/NextDocument")
            assert r.headers["Content-Type"] == "image/jpeg"
            assert await r.read() == b"\xff\xd8jpeg"

            r = await client.get("/eSCL/ScannerStatus")
            assert r.status == 503

    asyncio.run(run())
