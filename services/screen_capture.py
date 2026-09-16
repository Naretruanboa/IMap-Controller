"""Read-only ADB screen capture. Never sends touch or location commands."""

import asyncio

from services.adb import executable


async def adb_read(*args: str) -> bytes:
    try:
        process = await asyncio.create_subprocess_exec(
            executable(), *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except OSError as exc:
        raise ConnectionError("ADB unavailable. Check ADB_PATH.") from exc
    try:
        output, error = await asyncio.wait_for(process.communicate(), 12)
    except (TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            process.kill()
        await process.communicate()
        raise
    if process.returncode:
        raise ConnectionError(error.decode(errors="replace").strip()[:300] or "ADB command failed")
    return output


async def screen_devices() -> list[str]:
    try:
        output = await adb_read("devices")
    except TimeoutError:
        raise ConnectionError("ADB device discovery timed out") from None
    return [
        parts[0]
        for line in output.decode(errors="replace").splitlines()
        if len(parts := line.split()) >= 2 and parts[1] == "device"
    ]


async def capture_screen(serial: str) -> bytes:
    if serial not in await screen_devices():
        raise ValueError("Select an online, authorized Android device")
    try:
        png = await adb_read("-s", serial, "exec-out", "screencap", "-p")
    except TimeoutError:
        raise ConnectionError("Screen capture timed out") from None
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ConnectionError("ADB did not return a PNG screenshot")
    return png
