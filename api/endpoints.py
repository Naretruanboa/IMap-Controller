import asyncio
import json
import logging
import time
import uuid
from contextlib import suppress

import io
from PIL import Image
from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, Field, ValidationError

from models.schemas import (
    Coordinates,
    DeviceSelection,
    Favorite,
    GPXRequest,
    Movement,
    RandomRouteRequest,
    RouteRequest,
    Speed,
)
from services.geocoder import GeocodingError
from services.gpx_parser import parse_gpx
from services.route_engine import RandomRouteEngine, RouteEngine, generate_random_points
from services.ai_detector import default_ai_detector
from services.screen_capture import capture_screen, screen_devices
from services.screen_input import ScreenInput, screen_input

router = APIRouter()
logger = logging.getLogger(__name__)

VERSION = "2.6.0"


@router.get("/api/version")
async def get_version():
    return {
        "version": VERSION,
        "name": "Location Studio",
        "description": "iPhone GPS Web Controller · AI Vision & Native Auto-Catch Edition",
        "features": [
            "iOS & Android GPS Controller",
            "Joystick, Teleport & GPX Routing",
            "YOLOv8 AI PokéStop Vision Detector",
            "Auto-Spin PokéStop Workflow",
            "Native Zero-Fork Touch Injector (ARM64 & x86_64)",
            "Auto-Catch Encounter Engine & Calibrated Curveball",
        ],
    }


@router.get("/api/screen/devices")
async def list_screen_devices():
    return await screen_devices()


@router.post("/api/screen/input")
async def send_screen_input(body: ScreenInput):
    return await screen_input(body)


@router.get("/api/screen/capture")
async def get_screen_capture(serial: str = Query(min_length=1, max_length=200)):
    return Response(await capture_screen(serial), media_type="image/png", headers={"Cache-Control": "no-store"})


@router.get("/api/screen/detect_stops")
async def get_screen_detect_stops(
    serial: str = Query(min_length=1, max_length=200),
    engine: str = Query(default="ai", pattern="^(ai|heuristic)$")
):
    png_bytes = await capture_screen(serial)
    if engine == "heuristic":
        boxes = default_ai_detector._detect_heuristic(Image.open(io.BytesIO(png_bytes)))
    else:
        boxes = default_ai_detector.detect(png_bytes)
    return {
        "ok": True,
        "engine": "ai_onnx" if (engine == "ai" and default_ai_detector.is_ai_ready) else "heuristic",
        "boxes": boxes,
        "eligible_count": sum(1 for b in boxes if b.get("eligible")),
        "total_count": len(boxes)
    }


# ---------- Pokémon Encounter: Detect & Throw Pokéball ----------

import random
import struct
import numpy as np
import cv2


def _is_encounter_screen(png_bytes: bytes) -> dict:
    """Detect if the current screen is genuinely a Pokémon encounter (catch) screen.
    Supports Poké Ball (red/white), Great Ball (blue), Ultra Ball (black/yellow),
    and Premier Ball (white/dark), checking header indicators and bottom action buttons
    while strictly excluding the map screen."""
    img = np.frombuffer(png_bytes, np.uint8)
    img = cv2.imdecode(img, cv2.IMREAD_COLOR)
    if img is None:
        return {"is_encounter": False, "reason": "invalid image"}
    h, w = img.shape[:2]

    # 1. Check for Running Man (exit encounter) icon at top-left (x: 0.02-0.20, y: 0.02-0.15)
    top_left = img[int(h * 0.02):int(h * 0.15), int(w * 0.02):int(w * 0.20)]
    gray_tl = cv2.cvtColor(top_left, cv2.COLOR_BGR2GRAY)
    white_tl = cv2.countNonZero(cv2.inRange(gray_tl, 210, 255)) / max(top_left.shape[0] * top_left.shape[1], 1)
    has_running_man = white_tl > 0.015

    # 2. Check for CP / Pokémon Name capsule in upper area (x: 0.15-0.85, y: 0.02-0.25)
    cp_region = img[int(h * 0.02):int(h * 0.25), int(w * 0.15):int(w * 0.85)]
    gray_cp = cv2.cvtColor(cp_region, cv2.COLOR_BGR2GRAY)
    white_cp = cv2.countNonZero(cv2.inRange(gray_cp, 210, 255)) / max(cp_region.shape[0] * cp_region.shape[1], 1)
    has_cp_text = white_cp > 0.012

    # 3. Check for Giant Catch Ball at bottom-center (x: 0.30-0.70, y: 0.70-0.98)
    ball_region = img[int(h * 0.70):int(h * 0.98), int(w * 0.30):int(w * 0.70)]
    hsv_ball = cv2.cvtColor(ball_region, cv2.COLOR_BGR2HSV)
    ball_area = max(ball_region.shape[0] * ball_region.shape[1], 1)

    # Red (Classic Poké Ball)
    mask_r1 = cv2.inRange(hsv_ball, np.array([0, 100, 80]), np.array([12, 255, 255]))
    mask_r2 = cv2.inRange(hsv_ball, np.array([160, 100, 80]), np.array([180, 255, 255]))
    red_ratio = (cv2.countNonZero(mask_r1) + cv2.countNonZero(mask_r2)) / ball_area

    # Yellow & Dark/Black (Ultra Ball)
    yellow_mask = cv2.inRange(hsv_ball, np.array([18, 90, 80]), np.array([38, 255, 255]))
    yellow_ratio = cv2.countNonZero(yellow_mask) / ball_area
    dark_mask = cv2.inRange(hsv_ball, np.array([0, 0, 0]), np.array([180, 255, 60]))
    dark_ratio = cv2.countNonZero(dark_mask) / ball_area

    # Blue (Great Ball)
    blue_mask = cv2.inRange(hsv_ball, np.array([95, 80, 70]), np.array([135, 255, 255]))
    blue_ratio = cv2.countNonZero(blue_mask) / ball_area

    # White / Premier Ball
    white_mask = cv2.inRange(hsv_ball, np.array([0, 0, 180]), np.array([180, 50, 255]))
    white_ratio = cv2.countNonZero(white_mask) / ball_area

    has_pokeball = red_ratio > 0.08
    has_ultraball = yellow_ratio > 0.035 and dark_ratio > 0.08
    has_greatball = blue_ratio > 0.08 and not has_ultraball
    has_premierball = white_ratio > 0.15 and dark_ratio > 0.05
    has_catch_ball = has_pokeball or has_greatball or has_ultraball or has_premierball

    # 4. Check for bottom encounter action buttons (Berry button on left, Ball selector on right)
    bl = img[int(h * 0.76):int(h * 0.95), int(w * 0.03):int(w * 0.25)]
    br = img[int(h * 0.76):int(h * 0.95), int(w * 0.75):int(w * 0.97)]
    gray_bl = cv2.cvtColor(bl, cv2.COLOR_BGR2GRAY)
    gray_br = cv2.cvtColor(br, cv2.COLOR_BGR2GRAY)
    has_encounter_buttons = (
        (cv2.countNonZero(cv2.inRange(gray_bl, 160, 255)) / max(bl.shape[0] * bl.shape[1], 1) > 0.05) and
        (cv2.countNonZero(cv2.inRange(gray_br, 160, 255)) / max(br.shape[0] * br.shape[1], 1) > 0.05)
    )

    # 5. Explicit Map screen exclusion: Nearby Pokémon radar bar at bottom-right (x: 0.75-0.98, y: 0.88-0.98)
    map_nearby = img[int(h * 0.88):int(h * 0.98), int(w * 0.75):int(w * 0.98)]
    gray_nearby = cv2.cvtColor(map_nearby, cv2.COLOR_BGR2GRAY)
    nearby_white = cv2.countNonZero(cv2.inRange(gray_nearby, 200, 255)) / max(map_nearby.shape[0] * map_nearby.shape[1], 1)
    is_map_screen = nearby_white > 0.25

    # Encounter is confirmed if header indicator (Running Man or CP bar) exists AND catch ball / encounter buttons exist AND not on map
    is_encounter = (has_running_man or has_cp_text) and (has_catch_ball or has_encounter_buttons) and not is_map_screen

    ball_type = "ultraball" if has_ultraball else ("pokeball" if has_pokeball else ("greatball" if has_greatball else ("premierball" if has_premierball else "unknown")))

    return {
        "is_encounter": is_encounter,
        "ball_type": ball_type,
        "has_running_man": has_running_man,
        "has_pokeball": has_catch_ball,
        "has_cp_text": has_cp_text,
        "has_encounter_buttons": has_encounter_buttons,
        "is_map_screen": is_map_screen,
        "yellow_ratio": round(yellow_ratio, 4),
        "dark_ratio": round(dark_ratio, 4),
        "red_ratio": round(red_ratio, 4),
        "blue_ratio": round(blue_ratio, 4),
    }


class ThrowBallRequest(BaseModel):
    serial: str = Field(min_length=1, max_length=200)
    strength: float = Field(default=0.5, ge=0.1, le=1.0, description="Throw strength: 0.1=soft, 0.5=normal, 1.0=max")
    curveball: bool = Field(default=True, description="True for Curveball (spin+arc), False for Straight throw")


@router.get("/api/screen/detect_encounter")
async def detect_encounter(serial: str = Query(min_length=1, max_length=200)):
    """Check if current screen is a Pokémon encounter (catch) screen."""
    png_bytes = await capture_screen(serial)
    result = _is_encounter_screen(png_bytes)
    return result


async def _get_touch_info(serial: str) -> tuple[str, int, int]:
    """Get touch event device node and max X/Y digitizer range."""
    from services.screen_capture import adb_read
    import re
    try:
        output = (await adb_read("-s", serial, "shell", "getevent", "-p")).decode(errors="replace")
    except Exception:
        return "/dev/input/event1", 32767, 32767

    dev = "/dev/input/event1"
    max_x, max_y = 32767, 32767
    current_dev = ""
    for line in output.splitlines():
        if "add device" in line and "/dev/input/" in line:
            parts = line.split(":")
            if len(parts) >= 2:
                current_dev = parts[-1].strip()
        if "0035" in line and "max" in line:
            dev = current_dev
            match = re.search(r"max\s+(\d+)", line)
            if match:
                max_x = int(match.group(1))
        if "0036" in line and "max" in line:
            match = re.search(r"max\s+(\d+)", line)
            if match:
                max_y = int(match.group(1))
    return dev, max_x, max_y


def _build_curveball_script(dev: str, max_x: int, max_y: int, width: int, height: int, strength: float) -> str:
    """Build a shell script that performs a 3-second spin charge followed by a curved Bézier arc throw."""
    import math
    import random

    def to_dev(x, y):
        dx = int((x / max(width, 1)) * max_x)
        dy = int((y / max(height, 1)) * max_y)
        return max(0, min(max_x, dx)), max(0, min(max_y, dy))

    # Ball center on screen (top half of ball to avoid bottom tray gesture zone)
    ball_cx = int(0.50 * width)
    ball_cy = int(0.80 * height)
    spin_radius = int(0.065 * min(width, height))

    target_y = int((0.55 - strength * 0.28) * height)
    curve_left = random.choice([True, False])

    # Phase 1: Fast Natural Spin Charging (60 points over ~0.95s, ~16ms per point, 3.5 revolutions)
    spin_pts = []
    n_spin = 60
    revs = 3.5
    direction = -1 if curve_left else 1
    for i in range(n_spin):
        angle = (i / n_spin) * 2 * math.pi * revs * direction
        px = ball_cx + spin_radius * math.cos(angle)
        py = ball_cy + spin_radius * math.sin(angle)
        spin_pts.append(to_dev(px, py))

    # Phase 2: Throw Arc (Quadratic Bézier Curve, 12 points, 8ms each, fast snappy release)
    sx, sy = spin_pts[-1]
    if curve_left:
        # Counter-clockwise spin curves left in flight -> release towards upper-right
        ctrl_x, ctrl_y = to_dev(int(0.82 * width), int(0.62 * height))
        end_x, end_y = to_dev(int(0.78 * width), target_y)
    else:
        # Clockwise spin curves right in flight -> release towards upper-left to land dead-center
        ctrl_x, ctrl_y = to_dev(int(0.18 * width), int(0.62 * height))
        end_x, end_y = to_dev(int(0.22 * width), target_y)

    arc_pts = []
    n_arc = 12
    for i in range(n_arc):
        t = i / (n_arc - 1)
        bx = int((1 - t) ** 2 * sx + 2 * (1 - t) * t * ctrl_x + t ** 2 * end_x)
        by = int((1 - t) ** 2 * sy + 2 * (1 - t) * t * ctrl_y + t ** 2 * end_y)
        arc_pts.append((bx, by))

    cmds = [
        "#!/system/bin/sh",
    ]

    # Dismiss tray if open
    tap_x, tap_y = to_dev(ball_cx, int(0.45 * height))
    cmds.extend([
        f"sendevent {dev} 3 47 0",
        f"sendevent {dev} 3 57 0",
        f"sendevent {dev} 3 53 {tap_x}",
        f"sendevent {dev} 3 54 {tap_y}",
        f"sendevent {dev} 1 330 1",
        f"sendevent {dev} 0 0 0",
        f"sendevent {dev} 3 57 -1",
        f"sendevent {dev} 1 330 0",
        f"sendevent {dev} 0 0 0",
        "usleep 80000",
    ])

    # Touch Down on ball center
    x0, y0 = spin_pts[0]
    cmds.extend([
        f"sendevent {dev} 3 47 0",
        f"sendevent {dev} 3 57 1",
        f"sendevent {dev} 3 53 {x0}",
        f"sendevent {dev} 3 54 {y0}",
        f"sendevent {dev} 1 330 1",
        f"sendevent {dev} 0 0 0",
        "usleep 16000",
    ])

    # Fast spin charging (~0.95s charging sparkles)
    for x, y in spin_pts[1:]:
        cmds.extend([
            f"sendevent {dev} 3 53 {x}",
            f"sendevent {dev} 3 54 {y}",
            f"sendevent {dev} 0 0 0",
            "usleep 16000",
        ])

    # Arc throw phase (~96ms fast flick release)
    for x, y in arc_pts:
        cmds.extend([
            f"sendevent {dev} 3 53 {x}",
            f"sendevent {dev} 3 54 {y}",
            f"sendevent {dev} 0 0 0",
            "usleep 8000",
        ])

    # Touch Up (release into air)
    cmds.extend([
        f"sendevent {dev} 3 57 -1",
        f"sendevent {dev} 1 330 0",
        f"sendevent {dev} 0 0 0",
    ])

    return "\n".join(cmds)


def _build_straight_script(dev: str, max_x: int, max_y: int, width: int, height: int, strength: float) -> str:
    """Build a shell script that performs a fast straight throw swipe."""
    def to_dev(x, y):
        dx = int((x / max(width, 1)) * max_x)
        dy = int((y / max(height, 1)) * max_y)
        return max(0, min(max_x, dx)), max(0, min(max_y, dy))

    ball_cx = int(0.50 * width)
    ball_cy = int(0.80 * height)
    target_y = int((0.55 - strength * 0.28) * height)

    start_x, start_y = to_dev(ball_cx, ball_cy)
    end_x, end_y = to_dev(ball_cx, target_y)

    pts = []
    n_pts = 10
    for i in range(n_pts):
        t = i / (n_pts - 1)
        x = int(start_x + t * (end_x - start_x))
        y = int(start_y + t * (end_y - start_y))
        pts.append((x, y))

    cmds = [
        "#!/system/bin/sh",
    ]

    # Dismiss tray if open
    tap_x, tap_y = to_dev(ball_cx, int(0.45 * height))
    cmds.extend([
        f"sendevent {dev} 3 47 0",
        f"sendevent {dev} 3 57 0",
        f"sendevent {dev} 3 53 {tap_x}",
        f"sendevent {dev} 3 54 {tap_y}",
        f"sendevent {dev} 1 330 1",
        f"sendevent {dev} 0 0 0",
        f"sendevent {dev} 3 57 -1",
        f"sendevent {dev} 1 330 0",
        f"sendevent {dev} 0 0 0",
        "usleep 80000",
    ])

    # Touch Down
    x0, y0 = pts[0]
    cmds.extend([
        f"sendevent {dev} 3 47 0",
        f"sendevent {dev} 3 57 1",
        f"sendevent {dev} 3 53 {x0}",
        f"sendevent {dev} 3 54 {y0}",
        f"sendevent {dev} 1 330 1",
        f"sendevent {dev} 0 0 0",
        "usleep 10000",
    ])

    # Fast swipe upward (8ms per point)
    for x, y in pts[1:]:
        cmds.extend([
            f"sendevent {dev} 3 53 {x}",
            f"sendevent {dev} 3 54 {y}",
            f"sendevent {dev} 0 0 0",
            "usleep 8000",
        ])

    # Touch Up
    cmds.extend([
        f"sendevent {dev} 3 57 -1",
        f"sendevent {dev} 1 330 0",
        f"sendevent {dev} 0 0 0",
    ])

    return "\n".join(cmds)


async def _ensure_touch_injector(serial: str) -> bool:
    """Ensure native high-performance touch_injector binary is installed on device."""
    from pathlib import Path
    import asyncio
    from services.screen_capture import adb_read

    base_dir = Path(__file__).resolve().parent.parent / "bin"
    check = (await adb_read("-s", serial, "shell", "[ -x /data/local/tmp/touch_injector ] && echo 1 || echo 0")).decode().strip()
    if check == "1":
        return True

    arch = (await adb_read("-s", serial, "shell", "uname -m")).decode().strip().lower()
    bin_name = "touch_injector_x86_64" if ("x86_64" in arch or "amd64" in arch) else "touch_injector_arm64"
    local_bin = base_dir / bin_name
    if not local_bin.exists():
        return False

    proc = await asyncio.create_subprocess_exec(
        "adb", "-s", serial, "push", str(local_bin), "/data/local/tmp/touch_injector",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    await proc.communicate()
    await adb_read("-s", serial, "shell", "chmod 755 /data/local/tmp/touch_injector")
    return True


@router.post("/api/screen/throw_ball")
async def throw_ball(body: ThrowBallRequest):
    """Throw a Pokéball using curveball or straight technique at native hardware speed."""
    from services.screen_capture import adb_read
    import re

    png_bytes = await capture_screen(body.serial)

    # Verify we're on encounter screen
    encounter = _is_encounter_screen(png_bytes)
    if not encounter["is_encounter"]:
        raise HTTPException(status_code=400, detail="ไม่พบหน้าจอ Encounter — ต้องอยู่ในหน้าจับ Pokémon ก่อน")

    # Verify Pokémon GO is foreground
    activity = (await adb_read("-s", body.serial, "shell", "dumpsys", "activity", "activities")).decode(errors="replace")
    if not re.search(r"(?:mResumedActivity|topResumedActivity)[^\n]*\bcom\.nianticlabs\.pokemongo/", activity):
        raise HTTPException(status_code=400, detail="Pokémon GO ต้องเป็น foreground app")

    # Screen dimensions from PNG header
    width, height = struct.unpack(">II", png_bytes[16:24])

    dev, max_x, max_y = await _get_touch_info(body.serial)
    action_name = "curveball" if body.curveball else "straight"

    has_native = await _ensure_touch_injector(body.serial)
    if has_native:
        inj_cmd = f"/data/local/tmp/touch_injector -dev '{dev}' -max-x {max_x} -max-y {max_y} -width {width} -height {height} -action '{action_name}' -strength {body.strength}"
        await adb_read("-s", body.serial, "shell", inj_cmd)
    else:
        if body.curveball:
            script = _build_curveball_script(dev, max_x, max_y, width, height, body.strength)
        else:
            script = _build_straight_script(dev, max_x, max_y, width, height, body.strength)
        cmd = f"cat << 'EOF' > /data/local/tmp/throw.sh\n{script}\nEOF\nsh /data/local/tmp/throw.sh"
        await adb_read("-s", body.serial, "shell", cmd)

    return {
        "ok": True,
        "action": action_name,
        "curveball": body.curveball,
        "strength": body.strength,
    }




# AI Dataset & Training endpoints
from services.training_service import default_training_service


class DatasetCollectRequest(BaseModel):
    count: int = Field(default=30, ge=1, le=500)
    interval: float = Field(default=2.0, ge=0.2, le=30.0)
    serial: str | None = None


class TrainRequest(BaseModel):
    epochs: int = Field(default=50, ge=1, le=300)
    imgsz: int = Field(default=640, ge=320, le=1280)


@router.get("/api/ai/dataset/status")
async def get_dataset_status():
    return default_training_service.get_dataset_stats()


@router.post("/api/ai/dataset/collect")
async def post_dataset_collect(body: DatasetCollectRequest):
    try:
        return await default_training_service.start_collection(body.count, body.interval, body.serial)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/ai/dataset/stop-collect")
async def post_dataset_stop_collect():
    return await default_training_service.stop_collection()


@router.post("/api/ai/dataset/auto-label")
async def post_dataset_auto_label():
    try:
        return default_training_service.auto_label()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/ai/train/start")
async def post_train_start(body: TrainRequest):
    try:
        return default_training_service.start_training(body.epochs, body.imgsz)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/api/ai/train/stop")
async def post_train_stop():
    return default_training_service.stop_training()


@router.get("/api/ai/train/status")
async def get_train_status():
    return default_training_service.get_training_status()


@router.get("/api/ai/model/status")
async def get_model_status():
    return {
        "ready": default_ai_detector.is_ai_ready,
        "model_path": str(default_ai_detector.model_path),
        "exists": default_ai_detector.model_path.exists(),
        "input_shape": default_ai_detector.input_shape
    }




def controller(request: Request):
    return request.app.state.controller


@router.get("/api/state")
async def state(request: Request):
    c = controller(request)
    last_loc = c.db.get_last_location()
    return {
        "location": c.state.snapshot(),
        "device": c.state.device_snapshot(),
        "provider": c.mode,
        "last_location": last_loc,
    }


@router.post("/api/movement/distance/reset")
async def reset_distance(request: Request):
    c = controller(request)
    async with c.state.lock:
        c.state.distance_m = 0
        c.broadcast()
        return c.state.snapshot()


@router.get("/api/search")
async def search_places(
    request: Request,
    q: str = Query(min_length=2, max_length=200),
    language: str = Query(default="en", min_length=2, max_length=32),
):
    try:
        return {"results": await controller(request).geocoder.search(q, language)}
    except GeocodingError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/api/devices")
async def devices(request: Request):
    c = controller(request)
    return [
        dict(
            d,
            status=c.state.status
            if c.state.selected_device and d["udid"] == c.state.selected_device["udid"]
            else d["status"],
        )
        for d in c.devices.devices
    ]


@router.post("/api/devices/connect")
async def connect(body: DeviceSelection, request: Request):
    c = controller(request)
    await c.connect(body.udid)
    return c.state.device_snapshot()


@router.post("/api/devices/disconnect")
async def disconnect(request: Request):
    c = controller(request)
    async with c.state.lock:
        await c.state.clear()
        await c.state.provider.disconnect()
        c.state.status, c.state.selected_device = "DISCONNECTED", None
        c.broadcast()
    return {"ok": True}


@router.post("/api/location/set")
async def set_location(body: Coordinates, request: Request):
    c = controller(request)
    async with c.state.lock:
        c.state.stop()
        await c.state.set_position(body)
        c.db.record(body, "Teleport")
        c.broadcast()
    logger.info("Teleport applied")
    return c.state.snapshot()


@router.post("/api/location/run")
async def run_to_location(body: Coordinates, request: Request):
    c = controller(request)
    async with c.state.lock:
        s = c.state
        s.require_connected()
        if not s.position or not s.simulation_active:
            raise ValueError("Set a starting location with Teleport first")
        if s.restore_pending:
            raise ValueError("Restore the pending simulated location before continuing")
        route = RouteEngine([s.position, body])
        s.stop()
        s.route = route
        c.broadcast()
    return route.snapshot()


@router.post("/api/location/clear")
async def clear_location(request: Request):
    c = controller(request)
    async with c.state.lock:
        try:
            await c.state.clear()
        finally:
            c.broadcast()
    return c.state.snapshot()


@router.post("/api/routes/start")
async def start_route(body: RouteRequest, request: Request):
    c = controller(request)
    route = RouteEngine(body.points, body.loops)
    async with c.state.lock:
        c.state.stop()
        await c.state.set_position(body.points[0])
        c.state.route = route
        c.db.record(body.points[0], "Route start")
        c.broadcast()
    logger.info("Route started")
    return route.snapshot()


@router.post("/api/routes/random/preview")
async def preview_random_route(body: RandomRouteRequest, request: Request):
    c = controller(request)
    start_pos = (
        c.state.position
        if (c.state.simulation_active and c.state.position is not None)
        else body.center
    )
    points = generate_random_points(
        center=body.center,
        radius_m=body.radius_m,
        count=body.point_count,
        start=start_pos,
    )
    return {
        "points": [p.model_dump() for p in points],
        "center": body.center.model_dump(),
        "radius_m": body.radius_m,
    }


@router.post("/api/routes/random")
async def start_random_route(body: RandomRouteRequest, request: Request):
    c = controller(request)
    async with c.state.lock:
        c.state.require_connected()
        if c.state.restore_pending:
            raise ValueError("Restore the pending simulated location before continuing")
        start_pos = (
            c.state.position
            if (c.state.simulation_active and c.state.position is not None)
            else body.center
        )
        route = RandomRouteEngine(
            center=body.center,
            radius_m=body.radius_m,
            point_count=body.point_count,
            continuous=body.continuous,
            start_position=start_pos,
            initial_points=body.initial_points,
        )
        c.state.stop()
        if not c.state.position or not c.state.simulation_active:
            await c.state.set_position(route.points[0])
        c.state.route = route
        c.db.record(body.center, f"Random route ({body.radius_m:.0f}m)")
        c.broadcast()
    logger.info("Random route started around %s with radius %.1fm", body.center, body.radius_m)
    return route.snapshot()


@router.post("/api/routes/random/reroll")
async def reroll_random_route(request: Request):
    c = controller(request)
    async with c.state.lock:
        c.state.require_connected()
        route = c.state.route
        if not isinstance(route, RandomRouteEngine):
            raise ValueError("Active route is not a random route")
        if not c.state.position:
            raise ValueError("No active location available")
        route.reroll(c.state.position)
        c.broadcast()
    return route.snapshot()


@router.post("/api/routes/{action}")
async def route_action(action: str, request: Request):
    if action not in ("pause", "resume", "stop"):
        raise HTTPException(404, "Unknown route action")
    c = controller(request)
    async with c.state.lock:
        route = c.state.route
        if not route:
            raise ValueError("No active route")
        if action == "resume":
            c.state.require_connected()
            if route.status != "paused":
                raise ValueError("Only paused routes can resume")
            route.status = "running"
        elif action == "pause":
            if route.status != "running":
                raise ValueError("Only running routes can pause")
            route.status = "paused"
        else:
            route.status = "stopped"
            snap = route.snapshot()
            c.state.route = None
            c.broadcast()
            return snap
        c.broadcast()
    return route.snapshot()


@router.post("/api/gpx/import")
async def import_gpx(body: GPXRequest):
    return {"points": [p.model_dump() for p in parse_gpx(body.xml)]}


@router.get("/api/favorites")
async def favorites(request: Request):
    return controller(request).db.favorites()


@router.post("/api/favorites", status_code=201)
async def add_favorite(body: Favorite, request: Request):
    return {"id": controller(request).db.add_favorite(body)}


@router.delete("/api/favorites/{identifier}")
async def delete_favorite(identifier: int, request: Request):
    if not controller(request).db.delete_favorite(identifier):
        raise HTTPException(404, "Favorite not found")
    return {"ok": True}


@router.get("/api/history")
async def history(request: Request):
    return controller(request).db.history()


@router.websocket("/ws")
async def websocket(ws: WebSocket):
    origin = ws.headers.get("origin")
    if origin and origin not in (
        "http://" + ws.headers.get("host", ""),
        "https://" + ws.headers.get("host", ""),
    ):
        await ws.close(code=1008)
        return
    await ws.accept()
    c = ws.app.state.controller
    identifier = uuid.uuid4().hex
    queue = asyncio.Queue(maxsize=32)
    c.clients[identifier] = queue
    c.broadcast()

    async def sender():
        while True:
            await asyncio.wait_for(ws.send_json(await queue.get()), 5)

    async def receiver():
        while True:
            raw = await ws.receive_text()
            try:
                if len(raw) > 2048:
                    raise ValueError("Message too large")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise ValueError("Expected an object")
                message = (
                    Speed.model_validate(data)
                    if data.get("type") == "speed"
                    else Movement.model_validate(data)
                )
                async with c.state.lock:
                    s = c.state
                    s.require_connected()
                    if isinstance(message, Speed):
                        s.speed_schedule = message.schedule
                        s.speed_schedule_elapsed = 0
                        s.speed_kmh = 5 if message.schedule == "target10k" else message.kmh
                        logger.info("Speed changed to %.1f km/h", message.kmh)
                    elif message.active:
                        if s.position is None:
                            raise ValueError("Teleport to a starting location first")
                        if s.owner and s.owner != identifier:
                            raise ValueError("Joystick is controlled by another browser")
                        s.stop()
                        s.moving, s.bearing, s.owner = True, message.bearing, identifier
                        s.last_input = time.monotonic()
                    elif s.owner == identifier:
                        s.moving, s.owner = False, None
                    c.broadcast()
            except (ValueError, ValidationError, ConnectionError) as exc:
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait({"type": "error", "code": "INVALID_COMMAND", "message": str(exc)})

    tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except (WebSocketDisconnect, RuntimeError, TimeoutError):
        pass
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError, TimeoutError):
                await task
        c.clients.pop(identifier, None)
        async with c.state.lock:
            if c.state.owner == identifier:
                c.state.moving, c.state.owner = False, None
            if not c.clients:
                c.state.stop()
