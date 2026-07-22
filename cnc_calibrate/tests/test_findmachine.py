"""Tests for the cnc_calibrate network-discovery tooling.

Covers:
  - find_cnc  : fingerprint matcher, SSDP header parser, probe, orchestrator
  - cnc_state : ~/.cnc_state.json load/save cache + freshness helpers
  - findmachine: the `ip` cache-else-scan resolver and `find-machine` CLI

Network-bound functions (scan_mdns, scan_ssdp, _probe_description_xml) are
tested with mocks; we never touch the real LAN or the user's real state file
(CNC_STATE_FILE is redirected to tmp_path). sys.path is set up by conftest.py.
"""

import socket
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import pytest

import cnc_state  # noqa: E402
import find_cnc  # noqa: E402
import findmachine  # noqa: E402


# ===========================================================================
# find_cnc.is_grbl_esp32_description — fingerprint matcher
# ===========================================================================


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


# ===========================================================================
# find_cnc._parse_ssdp_response — header parser
# ===========================================================================


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


# ===========================================================================
# find_cnc._probe_description_xml — uses urllib; mocked here.
# ===========================================================================


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


# ===========================================================================
# find_cnc.discover — orchestrator, with mocked scanners.
# ===========================================================================


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


# ===========================================================================
# cnc_state — load/save round-trip
# ===========================================================================


def _override_state_file(monkeypatch, path: Path):
    monkeypatch.setenv("CNC_STATE_FILE", str(path))


def test_load_missing_file_returns_empty_skeleton(tmp_path, monkeypatch):
    _override_state_file(monkeypatch, tmp_path / "nope.json")
    data = cnc_state.load_state()
    assert data == {"version": 1, "machines": {}}


def test_load_corrupt_file_returns_empty_skeleton(tmp_path, monkeypatch):
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    _override_state_file(monkeypatch, bad)
    assert cnc_state.load_state() == {"version": 1, "machines": {}}


def test_save_and_load_round_trip(tmp_path, monkeypatch):
    _override_state_file(monkeypatch, tmp_path / "state.json")
    cnc_state.save_state({"version": 1, "machines": {"k": {"ip": "10.0.0.5"}}})
    data = cnc_state.load_state()
    assert data["machines"]["k"]["ip"] == "10.0.0.5"


def test_save_state_is_atomic_via_tmp(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    _override_state_file(monkeypatch, target)
    cnc_state.save_state({"version": 1, "machines": {}})
    assert target.exists()
    assert not (target.with_suffix(".json.tmp")).exists()


# ===========================================================================
# cnc_state — save_machine + get_machine
# ===========================================================================


def test_save_machine_keyed_by_mac(tmp_path, monkeypatch):
    _override_state_file(monkeypatch, tmp_path / "s.json")
    rec = cnc_state.save_machine(
        ip="192.168.1.10", mac="AA-BB-CC-DD-EE-FF", ssid="Net1"
    )
    assert rec.ip == "192.168.1.10"
    data = cnc_state.load_state()
    assert "AA-BB-CC-DD-EE-FF" in data["machines"]
    assert data["machines"]["AA-BB-CC-DD-EE-FF"]["ssid"] == "Net1"


def test_save_machine_default_key_when_no_mac(tmp_path, monkeypatch):
    _override_state_file(monkeypatch, tmp_path / "s.json")
    cnc_state.save_machine(ip="192.168.1.11")
    data = cnc_state.load_state()
    assert "default" in data["machines"]


def test_save_machine_preserves_existing_fields_on_upsert(tmp_path, monkeypatch):
    _override_state_file(monkeypatch, tmp_path / "s.json")
    cnc_state.save_machine(
        ip="192.168.1.10", mac="MAC1", hostname="grbl.local", ssid="Net1"
    )
    # Second call only updates IP; hostname + ssid should be preserved
    cnc_state.save_machine(ip="192.168.1.99", mac="MAC1")
    rec = cnc_state.get_machine(key="MAC1")
    assert rec.ip == "192.168.1.99"
    assert rec.hostname == "grbl.local"
    assert rec.ssid == "Net1"


def test_get_machine_returns_none_when_empty(tmp_path, monkeypatch):
    _override_state_file(monkeypatch, tmp_path / "s.json")
    assert cnc_state.get_machine() is None


def test_get_machine_by_key(tmp_path, monkeypatch):
    _override_state_file(monkeypatch, tmp_path / "s.json")
    cnc_state.save_machine(ip="1.1.1.1", mac="A")
    cnc_state.save_machine(ip="2.2.2.2", mac="B")
    rec = cnc_state.get_machine(key="B")
    assert rec.ip == "2.2.2.2"


def test_get_machine_picks_most_recent_when_no_key(tmp_path, monkeypatch):
    _override_state_file(monkeypatch, tmp_path / "s.json")
    cnc_state.save_machine(ip="1.1.1.1", mac="A")
    # Force a later timestamp via direct save
    import time

    time.sleep(1.1)
    cnc_state.save_machine(ip="2.2.2.2", mac="B")
    rec = cnc_state.get_machine()
    assert rec.ip == "2.2.2.2"


# ===========================================================================
# cnc_state — freshness + age formatting
# ===========================================================================


def test_age_seconds_handles_z_suffix():
    now = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
    past = (now - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = cnc_state.MachineRecord(ip="x", last_seen=past)
    assert rec.age_seconds(now=now) == pytest.approx(300, abs=1)


def test_age_seconds_returns_none_for_empty():
    rec = cnc_state.MachineRecord(ip="x", last_seen="")
    assert rec.age_seconds() is None


def test_age_seconds_returns_none_for_garbage():
    rec = cnc_state.MachineRecord(ip="x", last_seen="garbage")
    assert rec.age_seconds() is None


def test_is_fresh_within_threshold():
    now = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
    past = (now - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = cnc_state.MachineRecord(ip="x", last_seen=past)
    assert cnc_state.is_fresh(rec, max_age_sec=6 * 3600, now=now) is True


def test_is_fresh_rejects_stale():
    now = datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc)
    past = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rec = cnc_state.MachineRecord(ip="x", last_seen=past)
    assert cnc_state.is_fresh(rec, max_age_sec=6 * 3600, now=now) is False


def test_is_fresh_false_for_empty_timestamp():
    rec = cnc_state.MachineRecord(ip="x", last_seen="")
    assert cnc_state.is_fresh(rec) is False


def test_format_age_human_readable():
    assert "seconds" in cnc_state.format_age(30)
    assert "minutes" in cnc_state.format_age(180)
    assert "hours" in cnc_state.format_age(3700)
    assert "days" in cnc_state.format_age(180000)


# ===========================================================================
# findmachine — the `ip` cache-else-scan resolver and dispatch
# ===========================================================================


def test_ip_uses_fresh_cache_without_scanning(tmp_path, monkeypatch, capsys):
    _override_state_file(monkeypatch, tmp_path / "s.json")
    cnc_state.save_machine(ip="192.168.4.116", mac="MAC1")
    # If discovery gets called on a fresh cache, that's a bug.
    with mock.patch.object(
        find_cnc, "discover", side_effect=AssertionError("should not scan")
    ):
        rc = findmachine.main(["ip"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "192.168.4.116"


def test_ip_no_discover_with_no_cache_errors(tmp_path, monkeypatch, capsys):
    _override_state_file(monkeypatch, tmp_path / "empty.json")
    rc = findmachine.main(["ip", "--no-discover"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no machine in cache" in err


def test_ip_scans_and_caches_when_cache_absent(tmp_path, monkeypatch, capsys):
    _override_state_file(monkeypatch, tmp_path / "s.json")
    hit = find_cnc.Discovery(
        ip="192.168.4.200", hostname="grbl.local.", source="mdns", confirmed=True
    )
    with mock.patch.object(find_cnc, "discover", return_value=[hit]) as disc:
        rc = findmachine.main(["ip"])
    assert rc == 0
    assert capsys.readouterr().out.strip() == "192.168.4.200"
    disc.assert_called_once()
    # The hit should have been persisted to the cache.
    assert cnc_state.get_machine().ip == "192.168.4.200"


def test_ip_stale_cache_fallback_on_scan_miss(tmp_path, monkeypatch, capsys):
    _override_state_file(monkeypatch, tmp_path / "s.json")
    stale = (datetime.now(timezone.utc) - timedelta(days=2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    cnc_state.save_state(
        {
            "version": 1,
            "machines": {"MAC1": {"ip": "192.168.4.50", "last_seen": stale}},
        }
    )
    with mock.patch.object(find_cnc, "discover", return_value=[]):
        rc = findmachine.main(["ip"])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.strip() == "192.168.4.50"
    assert "stale cache" in out.err


def test_ip_no_cache_fallback_suppresses_stale(tmp_path, monkeypatch, capsys):
    _override_state_file(monkeypatch, tmp_path / "s.json")
    stale = (datetime.now(timezone.utc) - timedelta(days=2)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    cnc_state.save_state(
        {
            "version": 1,
            "machines": {"MAC1": {"ip": "192.168.4.50", "last_seen": stale}},
        }
    )
    with mock.patch.object(find_cnc, "discover", return_value=[]):
        rc = findmachine.main(["ip", "--no-cache-fallback"])
    assert rc == 1


def test_find_machine_prints_hits(tmp_path, monkeypatch, capsys):
    _override_state_file(monkeypatch, tmp_path / "s.json")
    hits = [
        find_cnc.Discovery(
            ip="192.168.4.116", hostname="grbl.local.", source="mdns", confirmed=True
        )
    ]
    with mock.patch.object(find_cnc, "discover", return_value=hits):
        rc = findmachine.main(["find-machine", "--first"])
    assert rc == 0
    assert "192.168.4.116\tgrbl.local." in capsys.readouterr().out


def test_find_machine_no_hits_returns_one(tmp_path, monkeypatch, capsys):
    _override_state_file(monkeypatch, tmp_path / "s.json")
    with mock.patch.object(find_cnc, "discover", return_value=[]):
        rc = findmachine.main(["find-machine"])
    assert rc == 1


def test_main_unknown_command_returns_two():
    assert findmachine.main(["bogus"]) == 2
