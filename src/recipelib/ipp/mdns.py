"""Advertise the printer with DNS-SD (Bonjour) so phones, Macs, Windows and
Android find it without any setup. Registers _ipp._tcp plus the _universal
(AirPrint) and _print subtypes, and re-registers when the host's IPv4
addresses change (laptops roam).

Everything zeroconf-related runs on the advertiser's own thread: python-
zeroconf's blocking API deadlocks when called from a thread that is already
running an asyncio loop (FastAPI's lifespan, for instance).
"""
from __future__ import annotations

import logging
import re
import socket
import threading

log = logging.getLogger(__name__)
SUBTYPES = ("_universal", "_print")


def local_ipv4s() -> list[str]:
    ips: set[str] = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127.") and not ip.startswith("169.254."))


def txt_record(name: str, port: int, uuid: str, urf: list[str], formats: list[str], adminurl: str,
               location: str) -> dict[str, str]:
    return {
        "txtvers": "1",
        "qtotal": "1",
        "rp": "ipp/print",
        "ty": name,
        "product": "(Recipe Library)",
        "pdl": ",".join(f for f in formats if f != "application/octet-stream"),
        "URF": ",".join(urf),
        "UUID": uuid,
        "adminurl": adminurl,
        "note": location,
        "priority": "50",
        "kind": "document,photo",
        "Color": "T",
        "Duplex": "F",
        "Copies": "F",
        "PaperMax": "legal-A4",
        "print_wfds": "F",
        "Scan": "F",
        "Fax": "F",
        "usb_MFG": "RecipeLib",
        "usb_MDL": "Recipe Library",
        "mopria-certified": "1.3",
    }


class Advertiser:
    def __init__(self, name: str, port: int, txt: dict[str, str], web_port: int | None = None):
        self.name = name
        self.port = port
        self.txt = txt
        self.web_port = web_port
        self._zc = None
        self._infos: list = []
        self._ips: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.ready = threading.Event()
        self.error: str | None = None

    @property
    def hostname(self) -> str:
        # a host name of our own: the machine's real one is already owned by
        # avahi/Bonjour/mDNSResponder and zeroconf's conflict probe would fail
        slug = re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-") or "recipes"
        return f"{slug}.local."

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="mdns", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=8)

    def _run(self) -> None:
        import logging as _logging
        _logging.getLogger("zeroconf").setLevel(_logging.CRITICAL)   # its sendto tracebacks while Wi-Fi comes up are noise
        # at boot the daemon can start before Wi-Fi is up; wait for an address
        waited = 0
        while not local_ipv4s() and waited < 120 and not self._stop.is_set():
            self._stop.wait(5)
            waited += 5
        try:
            from zeroconf import Zeroconf
            self._zc = Zeroconf()
            self._register()
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {e}"
            log.warning("mDNS advertising failed (%s); will retry. The printer can still be added by address", self.error)
        finally:
            self.ready.set()
        # retry soon after a failed start, then hourly-ish checks for address changes
        delay = 15 if self.error else 60
        while not self._stop.wait(delay):
            try:
                if self._zc is None:
                    from zeroconf import Zeroconf
                    self._zc = Zeroconf()
                if local_ipv4s() != self._ips:
                    log.info("mDNS: addresses changed or not yet advertised, registering")
                    self._register()
                    self.error = None
                delay = 60
            except Exception as e:  # noqa: BLE001
                self.error = f"{type(e).__name__}: {e}"
                log.warning("mDNS register failed: %s (retrying)", self.error)
                delay = 30
        if self._zc is not None:
            try:
                self._zc.unregister_all_services()
                self._zc.close()
            except Exception:  # noqa: BLE001
                pass
            self._zc = None

    def _register(self) -> None:
        from zeroconf import ServiceInfo
        ips = local_ipv4s()
        if not ips:
            log.warning("mDNS: no IPv4 address to advertise yet")
            self._ips = []
            return
        addresses = [socket.inet_aton(ip) for ip in ips]
        server = self.hostname
        props = {k: v.encode() for k, v in self.txt.items()}
        svc_name = f"{self.name}._ipp._tcp.local."
        for info in self._infos:
            try:
                self._zc.unregister_service(info)
            except Exception:  # noqa: BLE001
                pass
        self._infos = []
        base = ServiceInfo("_ipp._tcp.local.", svc_name, addresses=addresses, port=self.port,
                           properties=props, server=server)
        try:
            self._zc.register_service(base, cooperating_responders=True)
        except Exception as e:  # noqa: BLE001
            # a previous attempt may have added the name to the registry before the
            # network send failed; updating instead of registering recovers that
            if type(e).__name__ in ("ServiceNameAlreadyRegistered", "NonUniqueNameException"):
                self._zc.update_service(base)
            else:
                raise
        self._infos.append(base)
        # Subtype pointers (_universal._sub._ipp._tcp is what AirPrint browses
        # for). zeroconf keys its registry by instance name, so a second
        # register_service() for the same name is refused; the PTR answers
        # come from the per-type index, so index a subtype ServiceInfo there.
        for sub in SUBTYPES:
            sub_type = f"{sub}._sub._ipp._tcp.local."
            si = ServiceInfo(sub_type, svc_name, addresses=addresses, port=self.port, properties=props, server=server)
            try:
                self._zc.registry.types.setdefault(sub_type.lower(), {})[si.key] = si
            except Exception as e:  # noqa: BLE001
                log.warning("mDNS: could not add subtype %s: %s", sub, e)
        if self.web_port:
            web = ServiceInfo("_http._tcp.local.", f"{self.name}._http._tcp.local.", addresses=addresses,
                              port=self.web_port, properties={b"path": b"/"}, server=server)
            try:
                self._zc.register_service(web, cooperating_responders=True)
                self._infos.append(web)
            except Exception as e:  # noqa: BLE001
                log.warning("mDNS: web service not registered: %s: %s", type(e).__name__, e)
        self._ips = ips
        log.info("mDNS: advertising '%s' as %s on %s port %d", self.name, server, ", ".join(ips), self.port)
