"""IPP message encoding and decoding (RFC 8010).

An attribute is (tag, name, [values]). Collections are lists of member
attributes. Values use plain Python types: int, bool, str, bytes, and
tuples for resolution (x, y, units) and rangeOfInteger (min, max).
"""

import struct
from dataclasses import dataclass, field

# Group delimiters
OPERATION = 0x01
JOB = 0x02
END = 0x03
PRINTER = 0x04
UNSUPPORTED = 0x05

# Value tags
UNSUPPORTED_VALUE = 0x10
UNKNOWN = 0x12
NO_VALUE = 0x13
INTEGER = 0x21
BOOLEAN = 0x22
ENUM = 0x23
OCTET_STRING = 0x30
DATETIME = 0x31
RESOLUTION = 0x32
RANGE = 0x33
BEGIN_COLLECTION = 0x34
TEXT_LANG = 0x35
NAME_LANG = 0x36
END_COLLECTION = 0x37
TEXT = 0x41
NAME = 0x42
KEYWORD = 0x44
URI = 0x45
URI_SCHEME = 0x46
CHARSET = 0x47
LANGUAGE = 0x48
MIME = 0x49
MEMBER_NAME = 0x4A

# Operations
PRINT_JOB = 0x0002
VALIDATE_JOB = 0x0004
CREATE_JOB = 0x0005
SEND_DOCUMENT = 0x0006
CANCEL_JOB = 0x0008
GET_JOB_ATTRIBUTES = 0x0009
GET_JOBS = 0x000A
GET_PRINTER_ATTRIBUTES = 0x000B
CLOSE_JOB = 0x003B
IDENTIFY_PRINTER = 0x003C

# Status codes
OK = 0x0000
OK_IGNORED = 0x0001
BAD_REQUEST = 0x0400
NOT_POSSIBLE = 0x0404
NOT_FOUND = 0x0406
FORMAT_NOT_SUPPORTED = 0x040A
INTERNAL_ERROR = 0x0500
OPERATION_NOT_SUPPORTED = 0x0501
VERSION_NOT_SUPPORTED = 0x0503

_INT_TAGS = (INTEGER, ENUM)
_STR_TAGS = (TEXT, NAME, KEYWORD, URI, URI_SCHEME, CHARSET, LANGUAGE, MIME, MEMBER_NAME)


@dataclass
class Attr:
    tag: int
    name: str
    values: list

    @property
    def value(self):
        return self.values[0] if self.values else None


@dataclass
class Message:
    version: tuple = (2, 0)
    code: int = 0          # operation in requests, status in responses
    request_id: int = 1
    groups: list = field(default_factory=list)   # [(delimiter, [Attr])]

    def group(self, tag: int) -> list:
        for t, attrs in self.groups:
            if t == tag:
                return attrs
        attrs = []
        self.groups.append((tag, attrs))
        return attrs

    def get(self, name: str, tag: int | None = None):
        for t, attrs in self.groups:
            if tag is not None and t != tag:
                continue
            for a in attrs:
                if a.name == name:
                    return a
        return None

    def value(self, name: str, default=None, tag: int | None = None):
        a = self.get(name, tag)
        return a.value if a is not None and a.values else default


# --- Decoding ----------------------------------------------------------------

def _decode_value(tag: int, raw: bytes):
    if tag in _INT_TAGS:
        return struct.unpack(">i", raw)[0]
    if tag == BOOLEAN:
        return raw != b"\x00"
    if tag == RESOLUTION:
        return struct.unpack(">iib", raw)
    if tag == RANGE:
        return struct.unpack(">ii", raw)
    if tag in (TEXT_LANG, NAME_LANG):
        n = struct.unpack(">H", raw[:2])[0]
        m = struct.unpack(">H", raw[2 + n:4 + n])[0]
        return raw[4 + n:4 + n + m].decode("utf-8", "replace")
    if tag in _STR_TAGS:
        return raw.decode("utf-8", "replace")
    return raw


def _read_collection(buf: bytes, pos: int) -> tuple[list, int]:
    members: list[Attr] = []
    current: Attr | None = None
    while True:
        tag = buf[pos]
        nlen = struct.unpack(">H", buf[pos + 1:pos + 3])[0]
        pos += 3 + nlen
        vlen = struct.unpack(">H", buf[pos:pos + 2])[0]
        raw = buf[pos + 2:pos + 2 + vlen]
        pos += 2 + vlen
        if tag == END_COLLECTION:
            return members, pos
        if tag == MEMBER_NAME:
            current = Attr(0, raw.decode(), [])
            members.append(current)
            continue
        if current is None:
            raise ValueError("collection value without memberAttrName")
        if current.tag == 0:
            current.tag = tag
        if tag == BEGIN_COLLECTION:
            value, pos = _read_collection(buf, pos)
        else:
            value = _decode_value(tag, raw)
        current.values.append(value)


def decode(buf: bytes) -> tuple[Message, bytes]:
    """Decode an IPP message; returns the message and the data that follows it."""
    if len(buf) < 9:
        raise ValueError("IPP message too short")
    major, minor, code, request_id = struct.unpack(">BBHI", buf[:8])
    msg = Message((major, minor), code, request_id)
    pos = 8
    attrs: list | None = None
    last: Attr | None = None
    while pos < len(buf):
        tag = buf[pos]
        if tag == END:
            return msg, buf[pos + 1:]
        if tag < 0x10:
            attrs = []
            msg.groups.append((tag, attrs))
            last = None
            pos += 1
            continue
        nlen = struct.unpack(">H", buf[pos + 1:pos + 3])[0]
        name = buf[pos + 3:pos + 3 + nlen].decode("utf-8", "replace")
        pos += 3 + nlen
        vlen = struct.unpack(">H", buf[pos:pos + 2])[0]
        raw = buf[pos + 2:pos + 2 + vlen]
        pos += 2 + vlen
        if tag == BEGIN_COLLECTION:
            value, pos = _read_collection(buf, pos)
        elif tag in (UNSUPPORTED_VALUE, UNKNOWN, NO_VALUE):
            value = None
        else:
            value = _decode_value(tag, raw)
        if attrs is None:
            raise ValueError("attribute outside a group")
        if nlen == 0 and last is not None:
            last.values.append(value)
        else:
            last = Attr(tag, name, [] if value is None else [value])
            attrs.append(last)
    raise ValueError("missing end-of-attributes-tag")


# --- Encoding ----------------------------------------------------------------

def _encode_value(tag: int, value) -> bytes:
    if tag in _INT_TAGS:
        return struct.pack(">i", value)
    if tag == BOOLEAN:
        return b"\x01" if value else b"\x00"
    if tag == RESOLUTION:
        return struct.pack(">iib", *value)
    if tag == RANGE:
        return struct.pack(">ii", *value)
    if tag in (UNSUPPORTED_VALUE, UNKNOWN, NO_VALUE):
        return b""
    if isinstance(value, bytes):
        return value
    return str(value).encode("utf-8")


def _encode_attr(out: bytearray, a: Attr, member: bool = False) -> None:
    values = a.values or [None]
    for i, v in enumerate(values):
        name = a.name.encode() if i == 0 and not member else b""
        tag = a.tag if v is not None else NO_VALUE
        if tag == BEGIN_COLLECTION:
            out += struct.pack(">BH", tag, len(name)) + name + b"\x00\x00"
            for m in v:
                mname = m.name.encode()
                out += struct.pack(">BHH", MEMBER_NAME, 0, len(mname)) + mname
                _encode_attr(out, m, member=True)
            out += struct.pack(">BHH", END_COLLECTION, 0, 0)
        else:
            raw = _encode_value(tag, v)
            out += struct.pack(">BH", tag, len(name)) + name + struct.pack(">H", len(raw)) + raw


def encode(msg: Message) -> bytes:
    out = bytearray(struct.pack(">BBHI", msg.version[0], msg.version[1], msg.code, msg.request_id))
    for tag, attrs in msg.groups:
        out.append(tag)
        for a in attrs:
            _encode_attr(out, a)
    out.append(END)
    return bytes(out)


def response_for(req: Message, status: int) -> Message:
    """Response carrying the required operation attributes."""
    version = req.version if req.version[0] in (1, 2) else (2, 0)
    resp = Message(version, status, req.request_id)
    op = resp.group(OPERATION)
    op.append(Attr(CHARSET, "attributes-charset", ["utf-8"]))
    op.append(Attr(LANGUAGE, "attributes-natural-language", ["en"]))
    return resp
