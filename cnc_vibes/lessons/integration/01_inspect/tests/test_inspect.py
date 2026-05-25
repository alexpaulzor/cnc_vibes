"""Tests for inspect.py — pure response parsers and report formatter."""

import sys
from pathlib import Path

import pytest

LESSON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LESSON_DIR))

from grbl_inspect import (  # noqa: E402
    MachineStatus,
    Parameters,
    WifiInfo,
    format_report,
    parse_parameters,
    parse_settings,
    parse_status,
    parse_version,
    parse_wifi,
)


# ---------------------------------------------------------------------------
# parse_status
# ---------------------------------------------------------------------------


def test_status_idle_basic():
    s = parse_status("<Idle|MPos:0.000,0.000,0.000|FS:0,0>")
    assert s.state == "Idle"
    assert s.mpos == (0.0, 0.0, 0.0)
    assert s.feed == 0.0
    assert s.spindle == 0.0


def test_status_run_with_position():
    s = parse_status("<Run|MPos:10.123,20.456,-2.500|Bf:15,128|FS:1000,12000>")
    assert s.state == "Run"
    assert s.mpos == (10.123, 20.456, -2.5)
    assert s.feed == 1000.0
    assert s.spindle == 12000.0
    assert s.buffer == (15, 128)


def test_status_alarm():
    s = parse_status("<Alarm|MPos:0.000,0.000,0.000|FS:0,0>")
    assert s.state == "Alarm"


def test_status_malformed_returns_unknown():
    s = parse_status("not a status line")
    assert s.state == "Unknown"


def test_status_partial_fields():
    s = parse_status("<Idle|MPos:1.0,2.0,3.0>")
    assert s.state == "Idle"
    assert s.mpos == (1.0, 2.0, 3.0)
    assert s.feed is None


# ---------------------------------------------------------------------------
# parse_settings
# ---------------------------------------------------------------------------


def test_settings_typical():
    lines = ["$0=10", "$1=25", "$32=1", "$130=400.000", "ok"]
    s = parse_settings(lines)
    assert s == {0: 10.0, 1: 25.0, 32: 1.0, 130: 400.0}


def test_settings_ignores_noise():
    lines = ["welcome", "$13=0", "", "ok", "garbage"]
    s = parse_settings(lines)
    assert s == {13: 0.0}


def test_settings_handles_whitespace():
    lines = ["$32 = 1", " $130= 400.0 "]
    s = parse_settings(lines)
    assert s == {32: 1.0, 130: 400.0}


def test_settings_empty_input():
    assert parse_settings([]) == {}


# ---------------------------------------------------------------------------
# parse_parameters
# ---------------------------------------------------------------------------


def test_params_full_dump():
    lines = [
        "[G54:10.000,20.000,-3.000]",
        "[G55:0.000,0.000,0.000]",
        "[G56:0.000,0.000,0.000]",
        "[G57:0.000,0.000,0.000]",
        "[G58:0.000,0.000,0.000]",
        "[G59:0.000,0.000,0.000]",
        "[G28:0.000,0.000,0.000]",
        "[G30:0.000,0.000,0.000]",
        "[G92:0.000,0.000,0.000]",
        "[TLO:1.500]",
        "[PRB:5.123,6.789,-2.000:1]",
        "ok",
    ]
    p = parse_parameters(lines)
    assert p.wcs["G54"] == (10.0, 20.0, -3.0)
    assert p.wcs["G55"] == (0.0, 0.0, 0.0)
    assert p.g28 == (0.0, 0.0, 0.0)
    assert p.tlo == 1.5
    assert p.last_probe == (5.123, 6.789, -2.0, 1)


def test_params_probe_failed():
    p = parse_parameters(["[PRB:0.000,0.000,0.000:0]"])
    assert p.last_probe == (0.0, 0.0, 0.0, 0)


def test_params_ignores_noise():
    p = parse_parameters(["[G54:1,2,3]", "ok", "garbage"])
    assert p.wcs["G54"] == (1.0, 2.0, 3.0)


def test_params_empty_input_safe():
    p = parse_parameters([])
    assert p.wcs == {}
    assert p.tlo is None


# ---------------------------------------------------------------------------
# parse_version
# ---------------------------------------------------------------------------


def test_version_extraction():
    assert (
        parse_version(["[VER:1.1h.20190825:]", "[OPT:V,15,128]", "ok"])
        == "1.1h.20190825"
    )


def test_version_missing_returns_none():
    assert parse_version(["ok"]) is None


# ---------------------------------------------------------------------------
# parse_wifi — Grbl_ESP32 $I response, MSG line with Mode/SSID/IP/MAC
# ---------------------------------------------------------------------------


def test_wifi_typical_sta_msg():
    lines = [
        "[VER:1.3a.20221230:]",
        "[OPT:VNMHL,35,255]",
        "[MSG:Mode=STA:SSID=MyNet:Status=Connected:IP=192.168.4.116:MAC=AA-BB-CC-DD-EE-FF]",
        "ok",
    ]
    w = parse_wifi(lines)
    assert w.mode == "STA"
    assert w.ssid == "MyNet"
    assert w.ip == "192.168.4.116"
    assert w.mac == "AA-BB-CC-DD-EE-FF"
    assert w.status == "Connected"


def test_wifi_field_order_independent():
    # Grbl_ESP32 forks reorder fields; parse by key, not position.
    lines = ["[MSG:IP=10.0.0.5:Mode=STA:MAC=11-22-33-44-55-66:SSID=Other]"]
    w = parse_wifi(lines)
    assert w.ip == "10.0.0.5"
    assert w.mode == "STA"
    assert w.ssid == "Other"
    assert w.mac == "11-22-33-44-55-66"


def test_wifi_ap_mode_no_ssid_client():
    lines = ["[MSG:Mode=AP:SSID=GRBL_ESP:IP=192.168.0.1:MAC=DE-AD-BE-EF-00-01]"]
    w = parse_wifi(lines)
    assert w.mode == "AP"
    assert w.ip == "192.168.0.1"


def test_wifi_absent_returns_empty():
    # Vanilla GRBL (AVR) never emits a MSG line with WiFi info.
    lines = ["[VER:1.1h.20190825:]", "[OPT:V,15,128]", "ok"]
    w = parse_wifi(lines)
    assert w.ip is None
    assert w.ssid is None
    assert w.mac is None
    assert w.mode is None


def test_wifi_ignores_non_wifi_msg_lines():
    # Some MSG lines carry no key=val pairs ("[MSG:SSDP Started]").
    # Must not crash, must not return a partial WifiInfo.
    lines = ["[MSG:SSDP Started]", "[MSG:Pgm End]", "ok"]
    w = parse_wifi(lines)
    assert w.ip is None
    assert w.raw is None


def test_wifi_first_msg_with_keys_wins():
    # If two valid WiFi-shaped MSG lines appear (unlikely but possible
    # if firmware re-prints), take the first.
    lines = [
        "[MSG:Mode=STA:IP=192.168.1.10:MAC=00-00-00-00-00-01]",
        "[MSG:Mode=STA:IP=192.168.1.99:MAC=00-00-00-00-00-99]",
    ]
    w = parse_wifi(lines)
    assert w.ip == "192.168.1.10"


# ---------------------------------------------------------------------------
# format_report — WiFi block rendering
# ---------------------------------------------------------------------------


def test_report_shows_wifi_block_when_present():
    inputs = _sample_inputs()
    inputs["wifi"] = WifiInfo(
        mode="STA", ssid="MyNet", ip="192.168.4.116", mac="AA-BB-CC-DD-EE-FF"
    )
    text, _ = format_report(**inputs)
    assert "WiFi" in text
    assert "192.168.4.116" in text
    assert "MyNet" in text
    assert "AA-BB-CC-DD-EE-FF" in text


def test_report_omits_wifi_block_when_absent():
    inputs = _sample_inputs()
    inputs["wifi"] = WifiInfo()
    text, _ = format_report(**inputs)
    assert "WiFi" not in text


def test_report_works_without_wifi_arg():
    # Back-compat: callers that don't pass wifi= shouldn't crash.
    text, flags = format_report(**_sample_inputs())
    assert "WiFi" not in text
    assert flags == []


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


def _sample_inputs(s32=0, g54=(0.0, 0.0, 0.0), state="Idle"):
    return dict(
        port="/dev/ttyUSB0",
        version="1.1h",
        status=MachineStatus(state=state, mpos=(0.0, 0.0, 0.0), feed=0, spindle=0),
        settings={13: 0, 20: 1, 21: 1, 22: 1, 32: s32, 130: 400, 131: 300, 132: 100},
        params=Parameters(wcs={"G54": g54}),
    )


def test_report_clean_machine_has_no_flags():
    text, flags = format_report(**_sample_inputs())
    assert flags == []
    assert "no anomalies detected" in text


def test_report_flags_alarm_state():
    text, flags = format_report(**_sample_inputs(state="Alarm"))
    assert any("ALARM" in f for f in flags)


def test_report_flags_head_mismatch():
    text, flags = format_report(**_sample_inputs(s32=1), expect_head="spindle")
    assert any("$32=1" in f for f in flags)
    assert "MISMATCH" in text


def test_report_no_mismatch_when_head_matches():
    _, flags = format_report(**_sample_inputs(s32=1), expect_head="laser")
    assert not any("$32" in f for f in flags)


def test_report_flags_soft_limits_off():
    inputs = _sample_inputs()
    inputs["settings"][20] = 0
    text, flags = format_report(**inputs)
    assert any("soft limits" in f for f in flags)


def test_report_lists_all_settings_when_verbose():
    inputs = _sample_inputs()
    inputs["settings"][100] = 250.0  # not in KEY_SETTINGS
    text, _ = format_report(verbose=True, **inputs)
    assert "$100" in text


def test_report_hides_non_key_settings_by_default():
    inputs = _sample_inputs()
    inputs["settings"][100] = 250.0
    text, _ = format_report(verbose=False, **inputs)
    assert "$100" not in text


def test_report_shows_wcs_offsets():
    text, _ = format_report(**_sample_inputs(g54=(10.0, 20.0, -3.0)))
    assert "G54" in text
    assert "10.000" in text


def test_report_shows_tlo_when_present():
    inputs = _sample_inputs()
    inputs["params"] = Parameters(wcs={"G54": (0, 0, 0)}, tlo=1.5)
    text, _ = format_report(**inputs)
    assert "TLO" in text
    assert "1.500" in text
