"""IPP wire encoding (RFC 8010) with typed Python values.

Message = 8-byte header (version, operation/status, request id) followed by
attribute groups. Each group starts with a delimiter tag (0x01 operation,
0x02 job, 0x04 printer, 0x05 unsupported), attributes are
tag | name-len | name | value-len | value, and additional values of the same
attribute repeat with a zero-length name. 0x03 ends the attributes; whatever
follows is the document data.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# delimiter tags
OPERATION_GROUP = 0x01
JOB_GROUP = 0x02
END = 0x03
PRINTER_GROUP = 0x04
UNSUPPORTED_GROUP = 0x05

# value tags
UNSUPPORTED = 0x10
UNKNOWN = 0x12
NO_VALUE = 0x13
INTEGER = 0x21
BOOLEAN = 0x22
ENUM = 0x23
OCTET_STRING = 0x30
DATE_TIME = 0x31
RESOLUTION = 0x32
RANGE = 0x33
BEG_COLLECTION = 0x34
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

TAG_NAMES = {
    UNSUPPORTED: "unsupported", UNKNOWN: "unknown", NO_VALUE: "no-value", INTEGER: "integer",
    BOOLEAN: "boolean", ENUM: "enum", OCTET_STRING: "octetString", DATE_TIME: "dateTime",
    RESOLUTION: "resolution", RANGE: "rangeOfInteger", BEG_COLLECTION: "collection",
    TEXT_LANG: "textWithLanguage", NAME_LANG: "nameWithLanguage", TEXT: "text", NAME: "name",
    KEYWORD: "keyword", URI: "uri", URI_SCHEME: "uriScheme", CHARSET: "charset",
    LANGUAGE: "naturalLanguage", MIME: "mimeMediaType",
}
STRING_TAGS = {OCTET_STRING, TEXT, NAME, KEYWORD, URI, URI_SCHEME, CHARSET, LANGUAGE, MIME}
OUT_OF_BAND = {UNSUPPORTED, UNKNOWN, NO_VALUE}


@dataclass(frozen=True)
class Resolution:
    x: int
    y: int
    units: int = 3          # 3 = dots per inch, 4 = dots per cm

    def __str__(self) -> str:
        return f"{self.x}x{self.y}{'dpi' if self.units == 3 else 'dpcm'}"


@dataclass(frozen=True)
class Range:
    low: int
    high: int


@dataclass
class Attr:
    name: str
    tag: int
    values: list[Any] = field(default_factory=list)

    @property
    def value(self) -> Any:
        return self.values[0] if self.values else None

    def __repr__(self) -> str:
        return f"Attr({self.name!r}, {TAG_NAMES.get(self.tag, hex(self.tag))}, {self.values!r})"


class Collection(list):
    """An ordered list of Attr (member name -> values)."""

    def get(self, name: str) -> Attr | None:
        for a in self:
            if a.name == name:
                return a
        return None

    def __repr__(self) -> str:
        return "Collection(" + list.__repr__(self) + ")"


@dataclass
class Group:
    tag: int
    attrs: list[Attr] = field(default_factory=list)

    def get(self, name: str) -> Attr | None:
        for a in self.attrs:
            if a.name == name:
                return a
        return None

    def add(self, name: str, tag: int, *values: Any) -> Attr:
        a = Attr(name, tag, list(values))
        self.attrs.append(a)
        return a


@dataclass
class Message:
    version: tuple[int, int] = (2, 0)
    code: int = 0                      # operation id (request) or status code (response)
    request_id: int = 1
    groups: list[Group] = field(default_factory=list)

    def group(self, tag: int) -> Group | None:
        for g in self.groups:
            if g.tag == tag:
                return g
        return None

    def get(self, name: str, tag: int | None = None) -> Attr | None:
        for g in self.groups:
            if tag is not None and g.tag != tag:
                continue
            a = g.get(name)
            if a is not None:
                return a
        return None

    def value(self, name: str, default: Any = None) -> Any:
        a = self.get(name)
        return a.value if a is not None and a.values else default

    def values(self, name: str) -> list[Any]:
        a = self.get(name)
        return list(a.values) if a is not None else []


class IPPError(ValueError):
    pass


# ---------------------------------------------------------------------------
# encoding
# ---------------------------------------------------------------------------

def encode(msg: Message) -> bytes:
    out = bytearray()
    out += struct.pack(">BBHI", msg.version[0], msg.version[1], msg.code & 0xFFFF, msg.request_id)
    for g in msg.groups:
        out.append(g.tag)
        for a in g.attrs:
            _encode_attr(out, a)
    out.append(END)
    return bytes(out)


def _encode_attr(out: bytearray, a: Attr) -> None:
    if not a.values:
        _put(out, a.tag if a.tag in OUT_OF_BAND else NO_VALUE, a.name, b"")
        return
    for i, v in enumerate(a.values):
        name = a.name if i == 0 else ""
        if a.tag == BEG_COLLECTION or isinstance(v, Collection):
            _put(out, BEG_COLLECTION, name, b"")
            for m in v:
                _put(out, MEMBER_NAME, "", m.name.encode())
                for j, mv in enumerate(m.values):
                    if isinstance(mv, Collection) or m.tag == BEG_COLLECTION:
                        _encode_attr(out, Attr("", BEG_COLLECTION, [mv]))
                    else:
                        _put(out, m.tag, "", _encode_value(m.tag, mv))
            _put(out, END_COLLECTION, "", b"")
        else:
            _put(out, a.tag, name, _encode_value(a.tag, v))


def _put(out: bytearray, tag: int, name: str, value: bytes) -> None:
    nb = name.encode()
    out.append(tag)
    out += struct.pack(">H", len(nb)) + nb + struct.pack(">H", len(value)) + value


def _encode_value(tag: int, v: Any) -> bytes:
    if tag in (INTEGER, ENUM):
        return struct.pack(">i", int(v))
    if tag == BOOLEAN:
        return b"\x01" if v else b"\x00"
    if tag in STRING_TAGS:
        return v if isinstance(v, bytes) else str(v).encode()
    if tag == DATE_TIME:
        return encode_datetime(v)
    if tag == RESOLUTION:
        return struct.pack(">iib", v.x, v.y, v.units)
    if tag == RANGE:
        return struct.pack(">ii", v.low, v.high)
    if tag in (TEXT_LANG, NAME_LANG):
        lang, text = v
        lb, tb = lang.encode(), text.encode()
        return struct.pack(">H", len(lb)) + lb + struct.pack(">H", len(tb)) + tb
    if tag in OUT_OF_BAND:
        return b""
    if isinstance(v, bytes):
        return v
    raise IPPError(f"cannot encode tag {tag:#x} value {v!r}")


def encode_datetime(dt: datetime) -> bytes:
    dt = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return struct.pack(">HBBBBBBcBB", dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second,
                       dt.microsecond // 100000, b"+", 0, 0)


# ---------------------------------------------------------------------------
# decoding
# ---------------------------------------------------------------------------

def decode(data: bytes) -> tuple[Message, int]:
    """Decode a message; returns (message, offset of the document data)."""
    if len(data) < 9:
        raise IPPError("message too short")
    major, minor, code, rid = struct.unpack(">BBHI", data[:8])
    msg = Message(version=(major, minor), code=code, request_id=rid)
    rd = _Reader(data, 8)
    group: Group | None = None
    while True:
        tok = rd.next()
        if tok is None:
            raise IPPError("missing end-of-attributes tag")
        if tok[0] == "delim":
            tag = tok[1]
            if tag == END:
                return msg, rd.pos
            group = Group(tag)
            msg.groups.append(group)
            continue
        _kind, tag, name, raw = tok
        if group is None:
            raise IPPError("attribute before any group delimiter")
        if tag == BEG_COLLECTION:
            _append(group.attrs, name, tag, _decode_collection(rd))
            continue
        if tag in (END_COLLECTION, MEMBER_NAME):
            raise IPPError("stray collection tag")
        _append(group.attrs, name, tag, _decode_value(tag, raw))


def _append(attrs: list, name: str, tag: int, value: Any) -> None:
    if name == "" and attrs:
        attrs[-1].values.append(value)
        return
    if tag in OUT_OF_BAND:
        attrs.append(Attr(name, tag, []))
    else:
        attrs.append(Attr(name, tag, [value]))


class _Reader:
    """Sequential token reader over the attribute bytes."""

    def __init__(self, data: bytes, pos: int):
        self.data = data
        self.pos = pos

    def next(self) -> tuple | None:
        data, pos, n = self.data, self.pos, len(self.data)
        if pos >= n:
            return None
        tag = data[pos]
        pos += 1
        if tag < 0x10:
            self.pos = pos
            return ("delim", tag)
        if pos + 2 > n:
            raise IPPError("truncated attribute")
        nl = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += 2
        name = data[pos:pos + nl].decode("utf-8", "replace")
        pos += nl
        if pos + 2 > n:
            raise IPPError("truncated attribute value length")
        vl = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += 2
        raw = data[pos:pos + vl]
        if len(raw) != vl:
            raise IPPError("truncated attribute value")
        self.pos = pos + vl
        return ("attr", tag, name, raw)


def _decode_collection(rd: _Reader) -> Collection:
    """Called just after a begCollection token; consumes through endCollection."""
    coll = Collection()
    member: str | None = None
    while True:
        tok = rd.next()
        if tok is None:
            raise IPPError("unterminated collection")
        if tok[0] == "delim":
            raise IPPError("group delimiter inside a collection")
        _kind, tag, _name, raw = tok
        if tag == END_COLLECTION:
            return coll
        if tag == MEMBER_NAME:
            member = raw.decode("utf-8", "replace")
            continue
        if member is None:
            raise IPPError("collection value without member name")
        if tag == BEG_COLLECTION:
            _append(coll, member, tag, _decode_collection(rd))
        else:
            _append(coll, member, tag, _decode_value(tag, raw))
        member = ""          # further values for this member arrive with an empty name


def _decode_value(tag: int, raw: bytes) -> Any:
    if tag in (INTEGER, ENUM):
        return struct.unpack(">i", raw)[0] if len(raw) == 4 else 0
    if tag == BOOLEAN:
        return bool(raw and raw[0])
    if tag in STRING_TAGS:
        return raw.decode("utf-8", "replace") if tag != OCTET_STRING else raw
    if tag == DATE_TIME:
        return decode_datetime(raw)
    if tag == RESOLUTION:
        x, y, u = struct.unpack(">iib", raw) if len(raw) == 9 else (0, 0, 3)
        return Resolution(x, y, u)
    if tag == RANGE:
        lo, hi = struct.unpack(">ii", raw) if len(raw) == 8 else (0, 0)
        return Range(lo, hi)
    if tag in (TEXT_LANG, NAME_LANG):
        ll = struct.unpack(">H", raw[:2])[0]
        lang = raw[2:2 + ll].decode("utf-8", "replace")
        tl = struct.unpack(">H", raw[2 + ll:4 + ll])[0]
        return (lang, raw[4 + ll:4 + ll + tl].decode("utf-8", "replace"))
    if tag in OUT_OF_BAND:
        return None
    return raw


def decode_datetime(raw: bytes) -> datetime:
    if len(raw) != 11:
        return datetime.now(timezone.utc)
    y, mo, d, h, mi, s, ds, sign, oh, om = struct.unpack(">HBBBBBBcBB", raw)
    try:
        dt = datetime(y, mo, d, h, mi, s, ds * 100000, tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)
    return dt


def describe(msg: Message) -> str:
    """Human-readable dump for logs and --dump-ipp."""
    lines = [f"IPP/{msg.version[0]}.{msg.version[1]} code={msg.code:#06x} request-id={msg.request_id}"]
    for g in msg.groups:
        lines.append(f"  group {g.tag:#04x}")
        for a in g.attrs:
            lines.append(f"    {a.name} ({TAG_NAMES.get(a.tag, hex(a.tag))}) = {a.values!r}"[:300])
    return "\n".join(lines)
