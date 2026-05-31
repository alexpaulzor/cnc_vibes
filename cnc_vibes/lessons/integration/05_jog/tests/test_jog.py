"""Unit tests for lessons/integration/05_jog/jog.py — pure-function core only.

No pygame init, no serial open, no termios. Tests exercise translate_*(),
build_probe_sequence(), render_button_map(), and the hold-1s home semantics.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

LESSON_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LESSON_DIR))
import jog  # noqa: E402


# ---------------------------------------------------------------------------
# render_button_map
# ---------------------------------------------------------------------------


def test_print_map_contains_both_columns():
    out = jog.render_button_map()
    # Xbox column
    assert "Xbox left stick" in out
    assert "Xbox A" in out
    assert "Xbox B" in out
    assert "Xbox RB" in out
    # Keyboard column
    assert "W A S D" in out
    assert "Keyboard p" in out
    assert "Keyboard Esc" in out
    # Action labels
    assert "Z-PROBE" in out
    assert "CANCEL" in out
    assert "HOME" in out


# ---------------------------------------------------------------------------
# translate_controller — D-pad step jog
# ---------------------------------------------------------------------------


def _settings(**kw) -> jog.JogSettings:
    return jog.JogSettings(**kw)


def _snap(**kw) -> jog.ControllerSnapshot:
    return jog.ControllerSnapshot(**kw)


def test_translate_dpad_press_emits_step_jog():
    state = jog.TranslatorState()
    settings = _settings(step_mm=1.0, base_feed=1500)
    snap = _snap(dpad_right=True)
    _, cmds = jog.translate_controller(state, snap, settings, now_ms=0)
    jogs = [c for c in cmds if c.kind == "jog"]
    assert len(jogs) == 1
    assert jogs[0].dx == pytest.approx(1.0)
    assert jogs[0].dy == 0.0
    assert jogs[0].dz == 0.0
    assert jogs[0].feed == 1500


def test_translate_dpad_only_fires_on_leading_edge():
    # Same D-pad held two ticks in a row -> one jog, not two
    settings = _settings()
    snap = _snap(dpad_up=True)
    state, cmds1 = jog.translate_controller(jog.TranslatorState(), snap, settings, 0)
    _, cmds2 = jog.translate_controller(state, snap, settings, 50)
    jogs1 = [c for c in cmds1 if c.kind == "jog"]
    jogs2 = [c for c in cmds2 if c.kind == "jog"]
    assert len(jogs1) == 1
    assert len(jogs2) == 0


def test_translate_rb_plus_dpad_up_emits_z_step():
    settings = _settings(step_mm=2.0)
    snap = _snap(rb=True, dpad_up=True)
    _, cmds = jog.translate_controller(jog.TranslatorState(), snap, settings, 0)
    jogs = [c for c in cmds if c.kind == "jog"]
    assert len(jogs) == 1
    assert jogs[0].dz == pytest.approx(2.0)
    assert jogs[0].dx == 0.0
    assert jogs[0].dy == 0.0


# ---------------------------------------------------------------------------
# translate_controller — analog stick
# ---------------------------------------------------------------------------


def test_translate_stick_deadzone_emits_noop():
    settings = _settings(deadzone=0.15)
    snap = _snap(left_x=0.10, left_y=-0.05)  # both inside deadzone
    _, cmds = jog.translate_controller(jog.TranslatorState(), snap, settings, 0)
    # No jog, no cancel (stick wasn't previously active)
    assert [c for c in cmds if c.kind == "jog"] == []
    assert [c for c in cmds if c.kind == "cancel"] == []


def test_translate_stick_full_deflection_uses_base_feed():
    settings = _settings(base_feed=1500, deadzone=0.15, tick_hz=20)
    snap = _snap(left_x=1.0)
    _, cmds = jog.translate_controller(jog.TranslatorState(), snap, settings, 0)
    jogs = [c for c in cmds if c.kind == "jog"]
    assert len(jogs) == 1
    assert jogs[0].feed == 1500  # no modifiers active -> base feed
    assert jogs[0].dx > 0.0
    assert jogs[0].dy == 0.0


def test_translate_stick_release_emits_cancel():
    settings = _settings()
    # Tick 1: stick active
    state, _ = jog.translate_controller(
        jog.TranslatorState(), _snap(left_x=1.0), settings, 0
    )
    # Tick 2: stick centered
    _, cmds = jog.translate_controller(state, _snap(left_x=0.0), settings, 50)
    cancels = [c for c in cmds if c.kind == "cancel"]
    assert len(cancels) == 1


# ---------------------------------------------------------------------------
# translate_controller — modifiers
# ---------------------------------------------------------------------------


def test_translate_slow_modifier_scales_feed_by_0_1():
    settings = _settings(base_feed=1500, slow_mult=0.1)
    snap = _snap(left_x=1.0, lb=True)
    _, cmds = jog.translate_controller(jog.TranslatorState(), snap, settings, 0)
    jogs = [c for c in cmds if c.kind == "jog"]
    assert len(jogs) == 1
    assert jogs[0].feed == 150  # 1500 * 0.1


def test_translate_fast_modifier_uses_fast_mult():
    settings = _settings(base_feed=1000, fast_mult=5.0)
    # Full RT trigger -> full fast multiplier
    snap = _snap(left_x=1.0, rt=1.0)
    _, cmds = jog.translate_controller(jog.TranslatorState(), snap, settings, 0)
    jogs = [c for c in cmds if c.kind == "jog"]
    assert len(jogs) == 1
    assert jogs[0].feed == 5000  # 1000 * 5.0


def test_translate_slow_beats_fast():
    settings = _settings(base_feed=1000, slow_mult=0.1, fast_mult=5.0)
    snap = _snap(left_x=1.0, lb=True, rt=1.0)
    _, cmds = jog.translate_controller(jog.TranslatorState(), snap, settings, 0)
    jogs = [c for c in cmds if c.kind == "jog"]
    assert jogs[0].feed == 100


# ---------------------------------------------------------------------------
# translate_controller — action buttons (leading edge)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "btn,kind",
    [
        ("a", "probe"),
        ("b", "cancel"),
        ("x", "zero_wcs"),
        ("back", "exit"),
        ("start", "reprint"),
    ],
)
def test_translate_action_buttons_fire_on_leading_edge(btn, kind):
    settings = _settings()
    snap = _snap(**{btn: True})
    _, cmds = jog.translate_controller(jog.TranslatorState(), snap, settings, 0)
    assert any(c.kind == kind for c in cmds)


def test_translate_action_buttons_do_not_repeat_when_held():
    settings = _settings()
    snap = _snap(a=True)
    state, _ = jog.translate_controller(jog.TranslatorState(), snap, settings, 0)
    _, cmds = jog.translate_controller(state, snap, settings, 50)
    assert not any(c.kind == "probe" for c in cmds)


# ---------------------------------------------------------------------------
# translate_controller — Y hold-1s home
# ---------------------------------------------------------------------------


def test_home_requires_hold_1s_before_emitting():
    settings = _settings()
    # Press Y at t=0
    state, cmds = jog.translate_controller(
        jog.TranslatorState(), _snap(y=True), settings, 0
    )
    assert not any(c.kind == "home" for c in cmds)
    # Still held at t=500 — no fire
    state, cmds = jog.translate_controller(state, _snap(y=True), settings, 500)
    assert not any(c.kind == "home" for c in cmds)
    # Still held at t=1000 — fires
    state, cmds = jog.translate_controller(state, _snap(y=True), settings, 1000)
    assert sum(1 for c in cmds if c.kind == "home") == 1
    # Still held at t=1500 — does NOT fire again
    state, cmds = jog.translate_controller(state, _snap(y=True), settings, 1500)
    assert not any(c.kind == "home" for c in cmds)


def test_home_resets_after_release():
    settings = _settings()
    # Press, hold past 1s, release, re-press, hold past 1s -> two homes total
    state = jog.TranslatorState()
    state, _ = jog.translate_controller(state, _snap(y=True), settings, 0)
    state, cmds = jog.translate_controller(state, _snap(y=True), settings, 1000)
    assert sum(1 for c in cmds if c.kind == "home") == 1
    # Release
    state, _ = jog.translate_controller(state, _snap(y=False), settings, 1100)
    # Re-press + hold
    state, _ = jog.translate_controller(state, _snap(y=True), settings, 1200)
    state, cmds = jog.translate_controller(state, _snap(y=True), settings, 2200)
    assert sum(1 for c in cmds if c.kind == "home") == 1


# ---------------------------------------------------------------------------
# translate_keyboard
# ---------------------------------------------------------------------------


def test_translate_keyboard_wasd_emits_step_jog():
    settings = _settings(step_mm=1.0, base_feed=1500)
    cases = {
        "w": (0.0, +1.0),
        "s": (0.0, -1.0),
        "a": (-1.0, 0.0),
        "d": (+1.0, 0.0),
    }
    for key, (ex_dx, ex_dy) in cases.items():
        cmd = jog.translate_keyboard(key, settings)
        assert cmd.kind == "jog", key
        assert cmd.dx == pytest.approx(ex_dx), key
        assert cmd.dy == pytest.approx(ex_dy), key
        assert cmd.feed == 1500


def test_translate_keyboard_uppercase_uses_slow_mult():
    settings = _settings(step_mm=1.0, base_feed=1500, slow_mult=0.1)
    cmd = jog.translate_keyboard("W", settings)
    assert cmd.kind == "jog"
    assert cmd.dy == pytest.approx(+1.0)
    assert cmd.feed == 150  # 1500 * 0.1


def test_translate_keyboard_arrows_emit_z_step_jog():
    settings = _settings(step_mm=2.0, base_feed=1500)
    up = jog.translate_keyboard(jog.KB_ARROW_UP, settings)
    dn = jog.translate_keyboard(jog.KB_ARROW_DOWN, settings)
    assert up.kind == "jog" and up.dz == pytest.approx(+2.0)
    assert dn.kind == "jog" and dn.dz == pytest.approx(-2.0)


def test_translate_keyboard_action_keys():
    s = _settings()
    assert jog.translate_keyboard("p", s).kind == "probe"
    assert jog.translate_keyboard(jog.KB_ESC, s).kind == "cancel"
    assert jog.translate_keyboard("0", s).kind == "zero_wcs"
    assert jog.translate_keyboard("H", s).kind == "home"
    assert jog.translate_keyboard("q", s).kind == "exit"
    assert jog.translate_keyboard("?", s).kind == "reprint"


def test_translate_keyboard_lowercase_h_is_not_home():
    # Deliberate: lowercase h is unmapped, only capital H fires HOME
    s = _settings()
    assert jog.translate_keyboard("h", s).kind == "noop"


def test_translate_keyboard_unknown_key_is_noop():
    s = _settings()
    assert jog.translate_keyboard("", s).kind == "noop"
    assert jog.translate_keyboard("z", s).kind == "noop"
    assert jog.translate_keyboard("\x00", s).kind == "noop"


# ---------------------------------------------------------------------------
# build_probe_sequence
# ---------------------------------------------------------------------------


def test_probe_sequence_two_stage_emits_two_g38_lines():
    cfg = jog.ProbeConfig(
        max_mm=250.0,
        feed_fast=200,
        feed_slow=25,
        retract_mm=2.0,
        plate_mm=0.0,
        set_wcs=True,
        two_stage=True,
    )
    lines = jog.build_probe_sequence(cfg)
    g38 = [l for l in lines if l.startswith("G38.2")]
    assert len(g38) == 2
    assert "F200" in g38[0]
    assert "Z-250.000" in g38[0]
    assert "F25" in g38[1]


def test_probe_sequence_one_stage_emits_one_g38_line():
    cfg = jog.ProbeConfig(two_stage=False)
    lines = jog.build_probe_sequence(cfg)
    g38 = [l for l in lines if l.startswith("G38.2")]
    assert len(g38) == 1


def test_probe_sequence_applies_plate_thickness_to_g10():
    cfg = jog.ProbeConfig(plate_mm=12.7, set_wcs=True)
    lines = jog.build_probe_sequence(cfg)
    g10 = [l for l in lines if l.startswith("G10")]
    assert len(g10) == 1
    assert "G10 L20 P1 Z12.700" == g10[0]


def test_probe_sequence_skips_g10_when_no_set_wcs():
    cfg = jog.ProbeConfig(set_wcs=False)
    lines = jog.build_probe_sequence(cfg)
    assert not any(l.startswith("G10") for l in lines)


def test_probe_sequence_ends_with_g90_retract():
    cfg = jog.ProbeConfig()
    lines = jog.build_probe_sequence(cfg)
    # Last 3 lines should be G91 / G0 Z<retract> / G90
    assert lines[-1] == "G90"
    assert lines[-2].startswith("G0 Z")
    assert lines[-3] == "G91"


# ---------------------------------------------------------------------------
# _feed_with_modifiers — direct unit coverage of the math
# ---------------------------------------------------------------------------


def test_feed_with_modifiers_no_mod_returns_base():
    s = _settings(base_feed=1500)
    assert jog._feed_with_modifiers(1500, s, slow=False, fast_amt=0.0) == 1500


def test_feed_with_modifiers_partial_fast_blends_linearly():
    s = _settings(fast_mult=5.0)
    # half trigger = 1.0 + (5.0 - 1.0) * 0.5 = 3.0x
    assert jog._feed_with_modifiers(1000, s, slow=False, fast_amt=0.5) == 3000


def test_feed_with_modifiers_floors_at_1():
    s = _settings(slow_mult=0.001)
    assert jog._feed_with_modifiers(10, s, slow=True, fast_amt=0.0) >= 1
