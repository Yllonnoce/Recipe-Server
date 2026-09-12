from datetime import datetime, timezone

import pytest

from recipelib.ipp import codec as C
from recipelib.ipp.codec import Attr, Collection, Group, Message, Range, Resolution


def _roundtrip(msg: Message) -> Message:
    data = C.encode(msg)
    out, off = C.decode(data + b"DOC")
    assert data[off:] == b"" and off == len(data)
    assert C.encode(out) == data
    return out


def test_header_and_simple_attrs():
    m = Message(version=(2, 0), code=0x000B, request_id=42)
    g = Group(C.OPERATION_GROUP)
    g.add("attributes-charset", C.CHARSET, "utf-8")
    g.add("attributes-natural-language", C.LANGUAGE, "en")
    g.add("printer-uri", C.URI, "ipp://host:8631/ipp/print")
    g.add("requested-attributes", C.KEYWORD, "all", "media-col-database")
    g.add("limit", C.INTEGER, 5)
    g.add("my-jobs", C.BOOLEAN, True)
    g.add("job-state", C.ENUM, 9)
    m.groups.append(g)
    out = _roundtrip(m)
    assert out.version == (2, 0) and out.code == 0x000B and out.request_id == 42
    assert out.values("requested-attributes") == ["all", "media-col-database"]
    assert out.value("limit") == 5 and out.value("my-jobs") is True and out.value("job-state") == 9


def test_every_value_tag():
    g = Group(C.PRINTER_GROUP)
    g.add("res", C.RESOLUTION, Resolution(300, 300, 3), Resolution(600, 1200, 3))
    g.add("range", C.RANGE, Range(1, 99))
    g.add("when", C.DATE_TIME, datetime(2026, 9, 12, 10, 30, 5, 400000, tzinfo=timezone.utc))
    g.add("octets", C.OCTET_STRING, b"\x00\x01binary")
    g.add("tl", C.TEXT_LANG, ("en", "hello"))
    g.add("nl", C.NAME_LANG, ("fr", "bonjour"))
    g.add("nothing", C.NO_VALUE)
    g.add("unk", C.UNKNOWN)
    g.add("uns", C.UNSUPPORTED)
    g.add("mime", C.MIME, "application/pdf")
    m = Message(groups=[g])
    out = _roundtrip(m)
    pg = out.group(C.PRINTER_GROUP)
    assert pg.get("res").values == [Resolution(300, 300, 3), Resolution(600, 1200, 3)]
    assert pg.get("range").value == Range(1, 99)
    assert pg.get("when").value == datetime(2026, 9, 12, 10, 30, 5, 400000, tzinfo=timezone.utc)
    assert pg.get("octets").value == b"\x00\x01binary"
    assert pg.get("tl").value == ("en", "hello") and pg.get("nl").value == ("fr", "bonjour")
    assert pg.get("nothing").tag == C.NO_VALUE and pg.get("nothing").values == []
    assert pg.get("unk").tag == C.UNKNOWN and pg.get("uns").tag == C.UNSUPPORTED


def test_collections_nested_and_multi_valued():
    size = Collection([Attr("x-dimension", C.INTEGER, [21590]), Attr("y-dimension", C.INTEGER, [27940])])
    col = Collection([Attr("media-size", C.BEG_COLLECTION, [size]), Attr("media-type", C.KEYWORD, ["stationery", "photo"])])
    col2 = Collection([Attr("media-size", C.BEG_COLLECTION, [size]), Attr("media-type", C.KEYWORD, ["envelope"])])
    g = Group(C.PRINTER_GROUP, [Attr("media-col-database", C.BEG_COLLECTION, [col, col2]), Attr("after", C.INTEGER, [1])])
    out = _roundtrip(Message(groups=[g]))
    db = out.group(C.PRINTER_GROUP).get("media-col-database")
    assert db.tag == C.BEG_COLLECTION and len(db.values) == 2
    first = db.values[0]
    assert isinstance(first, Collection)
    assert first.get("media-size").value.get("x-dimension").value == 21590
    assert first.get("media-type").values == ["stationery", "photo"]
    assert db.values[1].get("media-type").values == ["envelope"]
    assert out.group(C.PRINTER_GROUP).get("after").value == 1


def test_document_offset_and_errors():
    m = Message(code=0x0002, groups=[Group(C.OPERATION_GROUP, [Attr("attributes-charset", C.CHARSET, ["utf-8"])])])
    data = C.encode(m)
    msg, off = C.decode(data + b"%PDF-1.4 ...")
    assert off == len(data) and msg.code == 2
    with pytest.raises(C.IPPError):
        C.decode(data[:-1])          # missing end tag
    with pytest.raises(C.IPPError):
        C.decode(b"\x02\x00")        # too short


def test_golden_bytes_get_printer_attributes():
    """A hand-assembled request as CUPS/ipptool sends it."""
    raw = (b"\x02\x00\x00\x0b\x00\x00\x00\x01"
           b"\x01"
           b"\x47\x00\x12attributes-charset\x00\x05utf-8"
           b"\x48\x00\x1battributes-natural-language\x00\x02en"
           b"\x45\x00\x0bprinter-uri\x00\x1eipp://localhost:8631/ipp/print"
           b"\x44\x00\x14requested-attributes\x00\x03all"
           b"\x44\x00\x00\x00\x12media-col-database"
           b"\x03")
    msg, off = C.decode(raw)
    assert off == len(raw)
    assert msg.code == 0x000B and msg.request_id == 1
    assert msg.value("printer-uri") == "ipp://localhost:8631/ipp/print"
    assert msg.values("requested-attributes") == ["all", "media-col-database"]
    assert C.encode(msg) == raw
