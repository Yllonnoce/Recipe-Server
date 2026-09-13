from pathlib import Path

from recipelib.ipp import attrs as A
from recipelib.ipp import codec as C
from recipelib.ipp import ops as O
from recipelib.ipp.codec import Group, Message


def _printer(tmp_path, docs):
    info = A.PrinterInfo("Test Printer", "Lab", "8b0a4d3e-1111-2222-3333-444444444444",
                         ["ipp://127.0.0.1:8631/ipp/print"], "http://127.0.0.1:8000/",
                         ["http://127.0.0.1:8631/icon-128.png"], paper="letter")
    return O.Printer(info, tmp_path / "spool", docs.append, first_job_id=7)


def _req(op, extra=None, job=None):
    m = Message(code=op, request_id=3)
    g = Group(C.OPERATION_GROUP)
    g.add("attributes-charset", C.CHARSET, "utf-8")
    g.add("attributes-natural-language", C.LANGUAGE, "en")
    g.add("printer-uri", C.URI, "ipp://127.0.0.1:8631/ipp/print")
    for n, t, v in extra or []:
        g.add(n, t, *v)
    m.groups.append(g)
    if job:
        jg = Group(C.JOB_GROUP)
        for n, t, v in job:
            jg.add(n, t, *v)
        m.groups.append(jg)
    return m


def test_get_printer_attributes_filters(tmp_path):
    p = _printer(tmp_path, [])
    r = p.handle(_req(O.GET_PRINTER_ATTRIBUTES), None)
    assert r.code == O.OK and r.request_id == 3
    pg = r.group(C.PRINTER_GROUP)
    names = [a.name for a in pg.attrs]
    for must in ("printer-uuid", "urf-supported", "ipp-features-supported", "media-ready", "media-col-ready",
                 "document-format-supported", "operations-supported", "media-col-default", "printer-icons"):
        assert must in names
    assert "media-col-database" not in names
    assert pg.get("printer-uuid").value == "urn:uuid:8b0a4d3e-1111-2222-3333-444444444444"
    assert pg.get("media-default").value == "na_letter_8.5x11in"
    assert "application/PCLm" not in pg.get("document-format-supported").values
    # the response must survive a wire round trip (collections included)
    again, _ = C.decode(C.encode(r))
    assert again.group(C.PRINTER_GROUP).get("media-col-default").value.get("media-size").value.get("x-dimension").value == 21590

    r2 = p.handle(_req(O.GET_PRINTER_ATTRIBUTES, [("requested-attributes", C.KEYWORD, ["media-col-database", "printer-name"])]), None)
    names2 = [a.name for a in r2.group(C.PRINTER_GROUP).attrs]
    assert set(names2) == {"media-col-database", "printer-name"}


def test_print_job_flow(tmp_path):
    docs = []
    p = _printer(tmp_path, docs)
    pdf = b"%PDF-1.4 fake"
    r = p.handle(_req(O.PRINT_JOB, [("job-name", C.NAME, ["Best Lasagna - Recipe"]), ("document-format", C.MIME, ["application/octet-stream"]),
                                    ("requesting-user-name", C.NAME, ["eric"])],
                      job=[("copies", C.INTEGER, [2])]), pdf)
    assert r.code == O.OK_IGNORED
    assert r.group(C.UNSUPPORTED_GROUP).get("copies") is not None
    jg = r.group(C.JOB_GROUP)
    assert jg.get("job-id").value == 7 and jg.get("job-state").value == O.COMPLETED
    assert jg.get("job-uri").value == "ipp://127.0.0.1:8631/ipp/print/7"
    assert len(docs) == 1 and docs[0].job.name == "Best Lasagna - Recipe" and docs[0].fmt == "application/pdf"
    assert Path(docs[0].path).read_bytes() == pdf

    jobs = p.handle(_req(O.GET_JOBS, [("which-jobs", C.KEYWORD, ["completed"])]), None)
    assert [g.get("job-id").value for g in jobs.groups if g.tag == C.JOB_GROUP] == [7]
    ga = p.handle(_req(O.GET_JOB_ATTRIBUTES, [("job-id", C.INTEGER, [7])]), None)
    assert ga.group(C.JOB_GROUP).get("job-state-reasons").value == "job-completed-successfully"
    assert p.handle(_req(O.CANCEL_JOB, [("job-id", C.INTEGER, [7])]), None).code == O.NOT_POSSIBLE


def test_create_job_send_document_and_cancel(tmp_path):
    docs = []
    p = _printer(tmp_path, docs)
    r = p.handle(_req(O.CREATE_JOB, [("job-name", C.NAME, ["Two part"])]), None)
    jid = r.group(C.JOB_GROUP).get("job-id").value
    assert r.group(C.JOB_GROUP).get("job-state").value == O.PENDING
    # not the last document yet
    r = p.handle(_req(O.SEND_DOCUMENT, [("job-id", C.INTEGER, [jid]), ("last-document", C.BOOLEAN, [False]),
                                        ("document-format", C.MIME, ["application/pdf"])]), b"%PDF-1")
    assert r.group(C.JOB_GROUP).get("job-state").value == O.PROCESSING and docs == []
    r = p.handle(_req(O.SEND_DOCUMENT, [("job-id", C.INTEGER, [jid]), ("last-document", C.BOOLEAN, [True]),
                                        ("document-format", C.MIME, ["application/pdf"])]), b"%PDF-2")
    assert r.group(C.JOB_GROUP).get("job-state").value == O.COMPLETED and len(docs) == 2

    r = p.handle(_req(O.CREATE_JOB, [("job-name", C.NAME, ["Cancelled"])]), None)
    jid2 = r.group(C.JOB_GROUP).get("job-id").value
    assert p.handle(_req(O.CANCEL_JOB, [("job-uri", C.URI, [f"ipp://127.0.0.1:8631/ipp/print/{jid2}"])]), None).code == O.OK
    assert p.jobs[jid2].state == O.CANCELED
    assert p.handle(_req(O.SEND_DOCUMENT, [("job-id", C.INTEGER, [jid2])]), b"x").code == O.NOT_POSSIBLE


def test_unknown_op_and_bad_format(tmp_path):
    p = _printer(tmp_path, [])
    assert p.handle(_req(0x0999), None).code == O.OP_NOT_SUPPORTED
    r = p.handle(_req(O.VALIDATE_JOB, [("document-format", C.MIME, ["application/PCLm"])]), None)
    assert r.code == O.DOC_FORMAT_ERROR
    assert p.handle(_req(O.VALIDATE_JOB, [("document-format", C.MIME, ["image/urf"])]), None).code == O.OK
    assert p.handle(_req(O.IDENTIFY_PRINTER), None).code == O.OK


def test_printer_display_name(monkeypatch):
    import socket
    from recipelib.ipp.server import printer_display_name
    monkeypatch.setattr(socket, "gethostname", lambda: "MacM5.local")
    assert printer_display_name("Recipe Library") == "Recipe Library (MacM5)"
    assert printer_display_name("Kitchen {host}") == "Kitchen MacM5"
    assert printer_display_name("Grandma's Recipes") == "Grandma's Recipes"
