import struct

import pytest
from pydantic import ValidationError

from services import screen_input as module


@pytest.mark.parametrize(
    "payload", [{"action": "shell"}, {"action": "tap", "x": 2}, {"action": "tap", "y": float("nan")}]
)
def test_input_rejects_unbounded_commands(payload):
    with pytest.raises(ValidationError):
        module.ScreenInput(serial="emulator", **payload)


async def test_input_checks_foreground_and_scales_coordinates(monkeypatch):
    calls = []
    foreground = False

    async def capture(serial):
        return b"\x89PNG\r\n\x1a\n" + b"0" * 8 + struct.pack(">II", 900, 1600)

    async def adb(*args):
        calls.append(args)
        if "dumpsys" in args:
            return (
                b"mResumedActivity: com.nianticlabs.pokemongo/Main"
                if foreground
                else b"mResumedActivity: settings/Main"
            )
        return b""

    monkeypatch.setattr(module, "capture_screen", capture)
    monkeypatch.setattr(module, "adb_read", adb)
    body = module.ScreenInput(serial="emulator", action="tap", x=0.5, y=0.5)
    with pytest.raises(ValueError, match="foreground"):
        await module.screen_input(body)
    assert not any("input" in args for args in calls)
    foreground = True
    await module.screen_input(body)
    assert calls[-1] == ("-s", "emulator", "shell", "input", "tap", "450", "800")


def test_throw_ball_script_builders():
    from api.endpoints import _build_straight_script, _build_curveball_script

    # Test straight throw script
    straight_script = _build_straight_script("/dev/input/event1", 32767, 32767, 1080, 2400, strength=0.6)
    assert "sendevent" in straight_script
    assert "usleep" in straight_script

    # Test curveball throw script (with fast natural spin)
    curve_script = _build_curveball_script("/dev/input/event1", 32767, 32767, 1080, 2400, strength=0.6)
    assert "sendevent" in curve_script
    assert "usleep 16000" in curve_script
    assert "usleep 8000" in curve_script


