"""Minimal IPP client on top of our codec: used by tests and `recipes printer-test`."""
from __future__ import annotations

from pathlib import Path

import httpx

from . import codec as C
from .codec import Group, Message


def request(op: int, printer_uri: str, extra: list[tuple[str, int, list]] | None = None,
            job_attrs: list[tuple[str, int, list]] | None = None, document: bytes = b"",
            request_id: int = 1, chunked: bool = False) -> Message:
    msg = Message(version=(2, 0), code=op, request_id=request_id)
    g = Group(C.OPERATION_GROUP)
    g.add("attributes-charset", C.CHARSET, "utf-8")
    g.add("attributes-natural-language", C.LANGUAGE, "en")
    g.add("printer-uri", C.URI, printer_uri)
    for name, tag, values in extra or []:
        g.add(name, tag, *values)
    msg.groups.append(g)
    if job_attrs:
        jg = Group(C.JOB_GROUP)
        for name, tag, values in job_attrs:
            jg.add(name, tag, *values)
        msg.groups.append(jg)
    body = C.encode(msg) + document
    url = printer_uri.replace("ipp://", "http://")
    headers = {"Content-Type": "application/ipp"}
    if chunked:
        def gen(data=body):
            for i in range(0, len(data), 7000):
                yield data[i:i + 7000]
        r = httpx.post(url, content=gen(), headers={**headers, "Transfer-Encoding": "chunked"}, timeout=30)
    else:
        r = httpx.post(url, content=body, headers=headers, timeout=30)
    r.raise_for_status()
    resp, _ = C.decode(r.content)
    return resp


def get_printer_attributes(uri: str, requested: list[str] | None = None) -> Message:
    extra = [("requested-attributes", C.KEYWORD, requested)] if requested else None
    return request(0x000B, uri, extra=extra)


def print_job(uri: str, document: bytes, fmt: str, name: str = "test", user: str = "tester",
              chunked: bool = False) -> Message:
    return request(0x0002, uri, extra=[("requesting-user-name", C.NAME, [user]), ("job-name", C.NAME, [name]),
                                       ("document-format", C.MIME, [fmt])], document=document, chunked=chunked)


def print_file(uri: str, path: Path, fmt: str = "application/pdf", name: str | None = None) -> Message:
    return print_job(uri, path.read_bytes(), fmt, name=name or path.stem)
