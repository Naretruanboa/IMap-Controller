import pytest

from services import screen_capture


async def test_capture_only_uses_read_commands(monkeypatch):
    calls = []
    png = b"\x89PNG\r\n\x1a\nimage"

    async def run(*args):
        calls.append(args)
        return (
            b"List of devices attached\n127.0.0.1:5555\tdevice\nother\tunauthorized\n"
            if args == ("devices",)
            else png
        )

    monkeypatch.setattr(screen_capture, "adb_read", run)
    assert await screen_capture.capture_screen("127.0.0.1:5555") == png
    assert calls == [("devices",), ("-s", "127.0.0.1:5555", "exec-out", "screencap", "-p")]
    calls.clear()
    with pytest.raises(ValueError):
        await screen_capture.capture_screen("other")
    assert calls == [("devices",)]


async def test_capture_rejects_bad_image_and_timeout(monkeypatch):
    async def devices():
        return ["emulator-5554"]

    async def bad(*args):
        return b"not a screenshot"

    monkeypatch.setattr(screen_capture, "screen_devices", devices)
    monkeypatch.setattr(screen_capture, "adb_read", bad)
    with pytest.raises(ConnectionError, match="PNG"):
        await screen_capture.capture_screen("emulator-5554")

    async def timeout(*args):
        raise TimeoutError

    monkeypatch.setattr(screen_capture, "adb_read", timeout)
    with pytest.raises(ConnectionError, match="timed out"):
        await screen_capture.capture_screen("emulator-5554")
