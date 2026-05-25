#!/usr/bin/env python3
"""Discover Grbl_ESP32-based CNC controllers on the local network.

Standalone CLI — no Claude/LLM dependency. Used to skip the USB-cable
dance when the controller is already on WiFi (the boot reset and 5-sec
wait that opening /dev/cu.usbserial-* triggers).

Strategy
--------
Grbl_ESP32's WebServer registers an mDNS service: `_http._tcp` with the
controller's configured hostname. It also speaks SSDP (advertises as
`upnp:rootdevice` with a `description.xml` that contains modelName=ESP32
and a chip-derived UDN). We scan mDNS for `_http._tcp` and then probe
each candidate's `/description.xml` to filter for Grbl_ESP32 fingerprints
(modelName=ESP32 + Espressif manufacturer). Anything else on the LAN
that publishes `_http._tcp` (printers, routers, NAS) gets filtered out
by the description.xml probe.

Gotchas (documented per instructions, not silently swallowed):
- `_http._tcp` is a noisy service category on most home networks. The
  description.xml probe is the actual filter; mDNS alone is not enough.
- Grbl_ESP32 only registers mDNS/SSDP in STA mode (not AP fallback). If
  the controller failed to join WiFi and is hosting its own AP, you'll
  need to be on its AP first, then this script can find it at the
  gateway address.
- SSDP M-SEARCH is implemented as a fallback in case the controller's
  mDNS doesn't reach the host (multicast filtering on some routers).
  Both are tried in parallel; first hit wins under --first.
- Some routers block multicast between WiFi clients ("AP isolation").
  If both transports come up empty but you know the device is online,
  fall back to the cached IP (cnc_state) or a USB query.

Output format
-------------
One discovered host per line on stdout, as `IP\tHOSTNAME` (tab-separated).
Stderr carries progress/errors. Exit 0 if any host found, 1 otherwise.

Usage
-----
  scripts/find_cnc.py                    # scan for 5s, print all hits
  scripts/find_cnc.py --first            # exit on first hit
  scripts/find_cnc.py --timeout 10       # scan longer for slow networks
  scripts/find_cnc.py --no-probe         # skip description.xml filter
                                         # (faster but noisier output)
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Fingerprint detection — pure function, testable without network.
# ---------------------------------------------------------------------------


def is_grbl_esp32_description(xml_text: str) -> bool:
    """Return True if an SSDP description.xml looks like a Grbl_ESP32 device.

    Grbl_ESP32's WebServer hard-codes modelName=ESP32 and manufacturer=
    Espressif Systems in the description.xml it serves. The UDN starts
    with a fixed prefix (38323636-4558-4dda-9188-cda0e6...) followed by
    the chip ID. We require modelName=ESP32 AND either the Espressif
    manufacturer or the UDN prefix.

    Tolerant: case-insensitive, whitespace-insensitive, no XML parse
    (the description.xml from Grbl_ESP32 isn't strictly valid in all
    builds).
    """
    lo = xml_text.lower()
    has_esp32 = "<modelname>esp32</modelname>" in lo.replace(" ", "")
    has_espressif = "espressif" in lo
    has_udn_prefix = "38323636-4558-4dda-9188-cda0e6" in lo
    return has_esp32 and (has_espressif or has_udn_prefix)


# ---------------------------------------------------------------------------
# Probe — fetch description.xml from a candidate, classify the response.
# ---------------------------------------------------------------------------


@dataclass
class Discovery:
    ip: str
    hostname: str
    source: str  # "mdns" or "ssdp"
    confirmed: bool = False  # description.xml matched Grbl_ESP32


def _probe_description_xml(ip: str, port: int, timeout: float = 1.5) -> bool:
    """GET http://{ip}:{port}/description.xml and check the fingerprint."""
    url = f"http://{ip}:{port}/description.xml"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read(4096).decode("utf-8", errors="replace")
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError):
        return False
    return is_grbl_esp32_description(body)


# ---------------------------------------------------------------------------
# mDNS scanner — uses zeroconf, fails clearly if not installed.
# ---------------------------------------------------------------------------


def _import_zeroconf():
    try:
        from zeroconf import ServiceBrowser, Zeroconf  # type: ignore

        return ServiceBrowser, Zeroconf
    except ImportError:
        print(
            "error: zeroconf is not installed. Run:\n  python -m pip install zeroconf",
            file=sys.stderr,
        )
        sys.exit(2)


def scan_mdns(
    timeout: float,
    on_candidate,
    stop_event: threading.Event,
) -> None:
    """Browse `_http._tcp.local.` for the given timeout, calling on_candidate.

    on_candidate(ip: str, hostname: str, port: int) is invoked once per
    discovered service (best-effort dedup happens in the caller).
    """
    ServiceBrowser, Zeroconf = _import_zeroconf()
    zc = Zeroconf()

    class _Listener:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=1500)
            if info is None:
                return
            try:
                addrs = info.parsed_addresses()
            except Exception:  # noqa: BLE001
                addrs = []
            hostname = (info.server or name).rstrip(".")
            for addr in addrs:
                # Skip IPv6 for now (Grbl_ESP32 is IPv4-only in current builds).
                if ":" in addr:
                    continue
                on_candidate(addr, hostname, info.port or 80)

        def update_service(self, *a, **kw):
            pass

        def remove_service(self, *a, **kw):
            pass

    try:
        ServiceBrowser(zc, "_http._tcp.local.", _Listener())
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not stop_event.is_set():
            time.sleep(0.1)
    finally:
        zc.close()


# ---------------------------------------------------------------------------
# SSDP scanner — UDP M-SEARCH multicast, no extra deps.
# ---------------------------------------------------------------------------


SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
SSDP_MSEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    "HOST: 239.255.255.250:1900\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 2\r\n"
    "ST: upnp:rootdevice\r\n"
    "\r\n"
).encode("ascii")


def _parse_ssdp_response(data: bytes) -> dict[str, str]:
    """Parse an SSDP response (HTTP-like header block) into a dict."""
    out: dict[str, str] = {}
    for line in data.decode("ascii", errors="replace").splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip().lower()] = v.strip()
    return out


def scan_ssdp(
    timeout: float,
    on_candidate,
    stop_event: threading.Event,
) -> None:
    """Send SSDP M-SEARCH for upnp:rootdevice, invoke on_candidate per reply.

    Stops as soon as `stop_event` is set OR timeout elapses.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(0.5)
    try:
        try:
            sock.sendto(SSDP_MSEARCH, (SSDP_ADDR, SSDP_PORT))
        except OSError as e:
            # Multicast send can fail on networks without a default
            # multicast route. Not fatal; mDNS may still find it.
            print(f"(ssdp send failed: {e})", file=sys.stderr)
            return

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not stop_event.is_set():
            try:
                data, (ip, _port) = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            headers = _parse_ssdp_response(data)
            # Grbl_ESP32 advertises ST: upnp:rootdevice and LOCATION:
            # http://<ip>:<port>/description.xml
            location = headers.get("location", "")
            if "/description.xml" not in location:
                continue
            # Hostname unknown from SSDP alone; we'll fill from description.xml
            # if probing is enabled, otherwise leave blank.
            on_candidate(ip, "", 80)
    finally:
        sock.close()


# ---------------------------------------------------------------------------
# Orchestrator — run mDNS + SSDP in parallel, dedupe, probe, emit.
# ---------------------------------------------------------------------------


def discover(
    timeout: float = 5.0,
    first_only: bool = False,
    probe: bool = True,
) -> list[Discovery]:
    """Run both scanners; return the list of (unique-by-IP) discoveries.

    With probe=True, only IPs that pass the description.xml fingerprint
    are returned. With probe=False, returns every _http._tcp + SSDP
    rootdevice on the LAN (noisy; useful for debugging).
    """
    stop_event = threading.Event()
    found: dict[str, Discovery] = {}
    lock = threading.Lock()

    def handle(ip: str, hostname: str, port: int, source: str) -> None:
        with lock:
            if ip in found:
                # Upgrade hostname if mDNS later supplies one.
                if hostname and not found[ip].hostname:
                    found[ip].hostname = hostname
                return
        confirmed = (not probe) or _probe_description_xml(ip, port)
        if probe and not confirmed:
            return
        with lock:
            if ip in found:
                return
            found[ip] = Discovery(
                ip=ip, hostname=hostname, source=source, confirmed=confirmed
            )
            if first_only:
                stop_event.set()

    t_mdns = threading.Thread(
        target=scan_mdns,
        args=(timeout, lambda ip, h, p: handle(ip, h, p, "mdns"), stop_event),
        daemon=True,
    )
    t_ssdp = threading.Thread(
        target=scan_ssdp,
        args=(timeout, lambda ip, h, p: handle(ip, h, p, "ssdp"), stop_event),
        daemon=True,
    )
    t_mdns.start()
    t_ssdp.start()
    t_mdns.join(timeout + 1.0)
    t_ssdp.join(timeout + 1.0)

    return list(found.values())


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--timeout",
        type=float,
        default=5.0,
        help="how long to listen for mDNS/SSDP replies (seconds, default 5)",
    )
    p.add_argument(
        "--first",
        action="store_true",
        help="exit as soon as one match is found (for use in scripts)",
    )
    p.add_argument(
        "--no-probe",
        action="store_true",
        help=(
            "skip the description.xml fingerprint check. Faster but will "
            "emit every _http._tcp / SSDP rootdevice on the LAN."
        ),
    )
    p.add_argument(
        "--cache",
        action="store_true",
        help="also write the first confirmed hit to ~/.cnc_state.json",
    )
    args = p.parse_args()

    print(
        f"scanning mDNS (_http._tcp) and SSDP (upnp:rootdevice) for "
        f"{args.timeout:.1f}s...",
        file=sys.stderr,
    )
    hits = discover(
        timeout=args.timeout,
        first_only=args.first,
        probe=not args.no_probe,
    )

    if not hits:
        print(
            "no Grbl_ESP32 controllers found. Possible causes:\n"
            "  - controller is in AP-fallback mode (failed to join WiFi)\n"
            "  - router has AP isolation / multicast filtering enabled\n"
            "  - controller is off, or USB is connected and rebooting it",
            file=sys.stderr,
        )
        return 1

    # Cache the first confirmed hit (best-effort).
    if args.cache:
        try:
            sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))
            from cnc_state import save_machine  # type: ignore

            save_machine(hits[0].ip, hostname=hits[0].hostname or None)
        except Exception as e:  # noqa: BLE001
            print(f"(cache write failed: {e})", file=sys.stderr)

    for hit in hits:
        print(f"{hit.ip}\t{hit.hostname or '(unknown)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
