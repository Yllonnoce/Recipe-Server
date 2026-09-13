"""Find things on the local network: Ollama hosts, other Recipe Library
servers, and Recipe Library printers. TCP connect scan of the local /24
networks (plus mDNS for printers), a few hundred quick probes, no root."""
from __future__ import annotations

import concurrent.futures
import re
import socket
from dataclasses import dataclass, field

from .llmhost import _local_networks, test_host

KNOWN = {11434: "ollama", 8000: "recipe-web", 80: "web", 8631: "recipe-printer"}


@dataclass
class Found:
    ip: str
    port: int
    kind: str                    # ollama | recipe-web | recipe-printer | web | open
    name: str = ""               # reverse DNS / hostname
    detail: str = ""
    url: str = ""
    models: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return {"ollama": "Ollama", "recipe-web": "Recipe Library", "recipe-printer": "Recipe Library printer",
                "web": "Web server", "open": "Open port"}[self.kind]


def _rdns(ip: str) -> str:
    try:
        return socket.gethostbyaddr(ip)[0].split(".")[0]
    except OSError:
        return ""


def _identify(ip: str, port: int, timeout: float = 2.0) -> Found | None:
    import httpx
    base = f"http://{ip}:{port}"
    if port == 11434:
        t = test_host(base, timeout=timeout)
        if t["ok"]:
            return Found(ip, port, "ollama", _rdns(ip), f"Ollama {t['version']}", base, t["models"])
        return Found(ip, port, "open", _rdns(ip), "port open, not Ollama", base)
    try:
        r = httpx.get(base + "/", timeout=timeout, follow_redirects=True)
        body = r.text[:4000]
        server = r.headers.get("server", "")
        if "Recipe Library" in body and port != 8631:
            m = re.search(r"<title>([^<]*)</title>", body)
            return Found(ip, port, "recipe-web", _rdns(ip), (m.group(1).strip() if m else "Recipe Library"), base)
        if port == 8631 or "IPP endpoint" in body or server.startswith("RecipeLibrary"):
            return Found(ip, port, "recipe-printer", _rdns(ip), "prints into a Recipe Library", f"ipp://{ip}:{port}/ipp/print")
        return Found(ip, port, "web", _rdns(ip), (server or "http") + (" · " + re.search(r"<title>([^<]*)</title>", body).group(1).strip() if re.search(r"<title>([^<]*)</title>", body) else ""), base)
    except Exception:  # noqa: BLE001
        return Found(ip, port, "open", _rdns(ip), "port open", base)


def scan(ports: list[int] | None = None, timeout: float = 0.35, networks: list[str] | None = None) -> list[Found]:
    ports = ports or list(KNOWN)
    nets = networks or _local_networks()
    targets = [(f"{p}.{i}", port) for p in nets for i in range(1, 255) for port in ports]

    def probe(t):
        ip, port = t
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                return t
        except OSError:
            return None
    hits = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=128) as ex:
        hits = [t for t in ex.map(probe, targets) if t]
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as ex:
        found = [f for f in ex.map(lambda t: _identify(*t), hits) if f]
    # printers announced over mDNS (may be on other subnets or non-default ports)
    try:
        found += _mdns_printers(timeout=2.5, seen={(f.ip, f.port) for f in found})
    except Exception:  # noqa: BLE001
        pass
    order = {"recipe-web": 0, "recipe-printer": 1, "ollama": 2, "web": 3, "open": 4}
    return sorted(found, key=lambda f: (order[f.kind], f.ip, f.port))


def _mdns_printers(timeout: float, seen: set) -> list[Found]:
    from zeroconf import ServiceBrowser, Zeroconf
    out: list[Found] = []
    zc = Zeroconf()
    names: list[str] = []

    class L:
        def add_service(self, z, type_, name):
            names.append(name)
        def update_service(self, *a): pass
        def remove_service(self, *a): pass
    ServiceBrowser(zc, "_ipp._tcp.local.", L())
    import time
    time.sleep(timeout)
    for name in names:
        info = zc.get_service_info("_ipp._tcp.local.", name, timeout=1500)
        if not info:
            continue
        props = {k.decode(): v.decode(errors="replace") for k, v in (info.properties or {}).items() if v is not None}
        if "Recipe Library" not in (props.get("ty", "") + name):
            continue
        for addr in info.parsed_addresses():
            if (addr, info.port) in seen:
                continue
            out.append(Found(addr, info.port, "recipe-printer", (info.server or "").rstrip(".").split(".")[0],
                             props.get("ty", "Recipe Library printer"), f"ipp://{addr}:{info.port}/{props.get('rp', 'ipp/print')}"))
    zc.close()
    return out
