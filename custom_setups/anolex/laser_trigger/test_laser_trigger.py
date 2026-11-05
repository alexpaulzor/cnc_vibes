"""python3 -m pytest custom_setups/anolex/laser_trigger -q   (no hardware or cncjs needed)"""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("lt", Path(__file__).with_name("laser_trigger.py"))
lt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lt)


def make(**kw):
    sent = []
    c = lt.LaserControl(lambda *a: sent.append(a), log=lambda *_: None, **kw)
    return c, sent


def test_needs_arm():
    c, sent = make()
    c.update(False, 0.9, 0)
    assert sent == [] and not c.on


def test_power_set_before_firing():
    c, sent = make()
    c.update(True, 0.5, 0)
    assert sent[-1] == ("lasertest:on", 100, 0, 1000)
    assert all(s[0] == "spindleOverride" for s in sent[:-1]) and c.ovr == 50


def test_trigger_range_clamped():
    c, _ = make()
    c.update(True, 1.0, 0)
    assert c.ovr == 100
    c.update(True, 0.06, 0.1)
    assert c.ovr == 10


def test_release_cancels_jog_then_m5():
    c, sent = make()
    c.update(True, 0.7, 0)
    sent.clear()
    c.update(True, 0.0, 0.1)
    assert sent[-2:] == [("jogCancel",), ("lasertest:off",)] and c.ovr == 10 and not c.on


def test_keep_jog():
    c, sent = make(cancel_jog=False)
    c.update(True, 0.7, 0)
    sent.clear()
    c.update(False, 0.7, 0.1)
    assert ("jogCancel",) not in sent and sent[-1] == ("lasertest:off",)


def test_max_on_locks_out_until_release():
    c, _ = make(max_on=5)
    c.update(True, 0.7, 0)
    c.update(True, 0.7, 6)
    assert not c.on
    c.update(True, 0.7, 7)
    assert not c.on
    c.update(True, 0.0, 8)
    c.update(True, 0.7, 9)
    assert c.on


def test_not_while_running_a_job():
    c, _ = make()
    c.status("Run", None)
    c.update(True, 0.7, 0)
    assert not c.on
    c.status("Jog", 100)
    c.update(True, 0.7, 1)
    assert c.on
