"""Tests for scripts/find_cnc.py — pure functions and orchestrator.

Network-bound functions (scan_mdns, scan_ssdp, _probe_description_xml)
are tested with mocks; we never hit the actual LAN from pytest.
"""

import socket
import sys
import threading
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import find_cnc  # noqa: E402


# ---------------------------------------------------------------------------
# is_grbl_esp32_description — fingerprint matcher
# ---------------------------------------------------------------------------


GRBL_ESP32_DESCRIPTION_XML = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <URLBase>http://192.168.4.116:80/</URLBase>
  <device>
    <deviceType>upnp:rootdevice</deviceType>
    <friendlyName>grblesp</friendlyName>
    <presentationURL>/</presentationURL>
    <serialNumber>0xCDA0E6123456</serialNumber>
    <modelName>ESP32</modelName>
    <modelNumber>Marlin</modelNumber>
    <modelURL>http://www.espressif.com/en/products/hardware/esp-wroom-32/overview</modelURL>
    <manufacturer>Espressif Systems</manufacturer>
    <manufacturerURL>http://espressif.com</manufacturerURL>
    <UDN>uuid:38323636-4558-4dda-9188-cda0e6123456</UDN>
  </device>
</root>
"""


PRINTER_DESCRIPTION_XML = """<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device>
    <deviceType>upnp:rootdevice</deviceType>
    <friendlyName>Brother HL-L2300D</friendlyName>
    <modelName>HL-L2300D</modelName>
    <manufacturer>Brother Industries, Ltd.</manufacturer>
  </device>
</root>
"""


def test_fingerprint_matches_grbl_esp32():
    assert find_cnc.is_grbl_esp32_description(GRBL_ESP32_DESCRIPTION_XML)


def test_fingerprint_rejects_random_printer():
    assert not find_cnc.is_grbl_esp32_description(PRINTER_DESCRIPTION_XML)


def test_fingerprint_rejects_empty():
    assert not find_cnc.is_grbl_esp32_description("")


def test_fingerprint_rejects_garbage_html():
    assert not find_cnc.is_grbl_esp32_description("<html><body>404</body></html>")


def test_fingerprint_case_insensitive():
    upper = GRBL_ESP32_DESCRIPTION_XML.upper()
    # The lowercase substring checks should still match on upper-cased input.
    assert find_cnc.is_grbl_esp32_description(upper)


def test_fingerprint_accepts_udn_prefix_without_espressif_string():
    # Some custom builds strip the manufacturer line but keep the UDN.
    xml = (
        "<root><device>"
        "<modelName>ESP32</modelName>"
        "<UDN>uuid:38323636-4558-4dda-9188-cda0e6deadbe</UDN>"
        "</device></root>"
    )
    assert find_cnc.is_grbl_esp32_description(xml)


# ---------------------------------------------------------------------------
# _parse_ssdp_response — header parser
# ---------------------------------------------------------------------------


def test_ssdp_response_parser_basic():
    raw = (
        b"HTTP/1.1 200 OK\r\n"
        b"CACHE-CONTROL: max-age=1800\r\n"
        b"ST: upnp:rootdevice\r\n"
        b"LOCATION: http://192.168.4.116:80/description.xml\r\n"
        b"SERVER: ESP32 UPnP/1.0\r\n"
        b"\r\n"
    )
    h = find_cnc._parse_ssdp_response(raw)
    assert h["st"] == "upnp:rootdevice"
    assert h["location"] == "http://192.168.4.116:80/description.xml"
    assert h["server"] == "ESP32 UPnP/1.0"


def test_ssdp_response_parser_tolerates_lowercase():
    h = find_cnc._parse_ssdp_response(b"location: http://1.2.3.4/description.xml\r\n")
    assert h["location"] == "http://1.2.3.4/description.xml"


def test_ssdp_response_parser_empty():
    assert find_cnc._parse_ssdp_response(b"") == {}


# ---------------------------------------------------------------------------
# _probe_description_xml — uses urllib; mocked here.
# ---------------------------------------------------------------------------


def test_probe_returns_true_on_match():
    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = GRBL_ESP32_DESCRIPTION_XML.encode()
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda *a: None
    with mock.patch("find_cnc.urllib.request.urlopen", return_value=fake_resp):
        assert find_cnc._probe_description_xml("192.168.4.116", 80) is True


def test_probe_returns_false_on_non_match():
    fake_resp = mock.MagicMock()
    fake_resp.read.return_value = PRINTER_DESCRIPTION_XML.encode()
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda *a: None
    with mock.patch("find_cnc.urllib.request.urlopen", return_value=fake_resp):
        assert find_cnc._probe_description_xml("192.168.4.50", 80) is False


def test_probe_returns_false_on_timeout():
    with mock.patch(
        "find_cnc.urllib.request.urlopen", side_effect=socket.timeout("boom")
    ):
        assert find_cnc._probe_description_xml("192.168.4.99", 80) is False


def test_probe_returns_false_on_connection_refused():
    with mock.patch(
        "find_cnc.urllib.request.urlopen",
        side_effect=ConnectionRefusedError("nope"),
    ):
        assert find_cnc._probe_description_xml("192.168.4.99", 80) is False


# ---------------------------------------------------------------------------
# discover — orchestrator, with mocked scanners.
# ---------------------------------------------------------------------------


def _fake_mdns(hosts):
    """Build a scan_mdns replacement that yields the given (ip,host,port)s."""

    def _scan(timeout, on_candidate, stop_event):
        for ip, hostname, port in hosts:
            if stop_event.is_set():
                break
            on_candidate(ip, hostname, port)

    return _scan


def _fake_ssdp(hosts):
    def _scan(timeout, on_candidate, stop_event):
        for ip, port in hosts:
            if stop_event.is_set():
                break
            on_candidate(ip, "", port)

    return _scan


def test_discover_returns_only_probed_hosts():
    with (
        mock.patch.object(
            find_cnc,
            "scan_mdns",
            _fake_mdns(
                [
                    ("192.168.4.116", "grblesp.local.", 80),
                    ("192.168.4.50", "printer.local.", 80),
                ]
            ),
        ),
        mock.patch.object(find_cnc, "scan_ssdp", _fake_ssdp([])),
        mock.patch.object(
            find_cnc,
            "_probe_description_xml",
            side_effect=lambda ip, port: ip == "192.168.4.116",
        ),
    ):
        hits = find_cnc.discover(timeout=0.1, first_only=False, probe=True)
    assert len(hits) == 1
    assert hits[0].ip == "192.168.4.116"
    assert hits[0].hostname == "grblesp.local."
    assert hits[0].source == "mdns"
    assert hits[0].confirmed


def test_discover_no_probe_returns_everything():
    with (
        mock.patch.object(
            find_cnc,
            "scan_mdns",
            _fake_mdns(
                [("10.0.0.1", "router.local.", 80), ("10.0.0.2", "nas.local.", 80)]
            ),
        ),
        mock.patch.object(find_cnc, "scan_ssdp", _fake_ssdp([])),
    ):
        hits = find_cnc.discover(timeout=0.1, first_only=False, probe=False)
    ips = {h.ip for h in hits}
    assert ips == {"10.0.0.1", "10.0.0.2"}


def test_discover_dedupes_across_transports():
    # Same IP discovered via both mDNS and SSDP — should collapse to one
    # Discovery, mDNS hostname preserved.
    with (
        mock.patch.object(
            find_cnc, "scan_mdns", _fake_mdns([("192.168.4.116", "grblesp.local.", 80)])
        ),
        mock.patch.object(find_cnc, "scan_ssdp", _fake_ssdp([("192.168.4.116", 80)])),
        mock.patch.object(find_cnc, "_probe_description_xml", return_value=True),
    ):
        hits = find_cnc.discover(timeout=0.1, first_only=False, probe=True)
    assert len(hits) == 1
    assert hits[0].ip == "192.168.4.116"


def test_discover_first_only_stops_early():
    # With first_only=True, only the first confirmed hit is returned.
    with (
        mock.patch.object(
            find_cnc,
            "scan_mdns",
            _fake_mdns(
                [
                    ("192.168.4.116", "a.local.", 80),
                    ("192.168.4.117", "b.local.", 80),
                    ("192.168.4.118", "c.local.", 80),
                ]
            ),
        ),
        mock.patch.object(find_cnc, "scan_ssdp", _fake_ssdp([])),
        mock.patch.object(find_cnc, "_probe_description_xml", return_value=True),
    ):
        hits = find_cnc.discover(timeout=0.1, first_only=True, probe=True)
    assert len(hits) == 1
