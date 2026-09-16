"""Bounded touchscreen commands for the screen workflow."""

import re
import struct
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from services.screen_capture import adb_read, capture_screen


class ScreenInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    serial: str = Field(min_length=1, max_length=200)
    action: Literal["tap", "swipe", "back"]
    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.5, ge=0, le=1)
    end_x: float = Field(default=0.8, ge=0, le=1)
    end_y: float = Field(default=0.5, ge=0, le=1)


async def screen_input(body: ScreenInput) -> dict:
    # Revalidate the device and dimensions, not coordinates supplied by an old screenshot.
    png = await capture_screen(body.serial)
    if len(png) < 24:
        raise ConnectionError("Invalid screenshot dimensions")
    width, height = struct.unpack(">II", png[16:24])
    if not (100 <= width <= 10000 and 100 <= height <= 10000):
        raise ValueError("Unsupported screen dimensions")
    try:
        activity = (await adb_read("-s", body.serial, "shell", "dumpsys", "activity", "activities")).decode(
            errors="replace"
        )
        if not re.search(
            r"(?:mResumedActivity|topResumedActivity)[^\n]*\bcom\.nianticlabs\.pokemongo/", activity
        ):
            raise ValueError("Pokémon GO must be the foreground app")

        def point(x, y):
            return str(round(x * (width - 1))), str(round(y * (height - 1)))

        if body.action == "tap":
            args = ("tap", *point(body.x, body.y))
            await adb_read("-s", body.serial, "shell", "input", *args)
        elif body.action == "swipe":
            args = ("swipe", *point(body.x, body.y), *point(body.end_x, body.end_y), "300")
            await adb_read("-s", body.serial, "shell", "input", *args)
        elif body.action == "back":
            # Primary exit for PokéStop/Encounter/Modals: tap bottom center (X) button (0.50, 0.92) and send keyevent 4
            close_x, close_y = point(0.50, 0.92)
            await adb_read("-s", body.serial, "shell", f"input tap {close_x} {close_y}; input keyevent 4")
    except TimeoutError:
        raise ConnectionError("Touch command timed out; check the device before retrying") from None
    return {"sent": body.action}
