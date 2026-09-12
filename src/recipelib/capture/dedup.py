"""Duplicate detection helpers: normalised URLs (sha256 is handled by pdf.py)."""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_DROP_PARAMS = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "igshid", "_ga")


def normalize_url(url: str | None) -> str | None:
    if not url:
        return None
    u = url.strip()
    if not u:
        return None
    if "://" not in u:
        u = "https://" + u
    parts = urlsplit(u)
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
             if not k.lower().startswith(_DROP_PARAMS)]
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit(("https", host, path, urlencode(sorted(query)), ""))


def site_name(url: str | None) -> str | None:
    if not url:
        return None
    host = urlsplit(url if "://" in url else "https://" + url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host or None
