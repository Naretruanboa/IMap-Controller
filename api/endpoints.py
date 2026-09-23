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
from typing import Literal
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
from services.catch_summary_ocr import has_total_label
from api.version import get_app_version

router = APIRouter()
logger = logging.getLogger(__name__)

_pokestop_screen_seen_at: dict[str, float] = {}
_pokemon_detail_seen_at: dict[str, float] = {}


def reset_screen_runtime_state() -> dict:
    pokestop_count = len(_pokestop_screen_seen_at)
    pokemon_detail_count = len(_pokemon_detail_seen_at)
    _pokestop_screen_seen_at.clear()
    _pokemon_detail_seen_at.clear()
    return {
        "ok": True,
        "cleared": {
            "pokestop_screen_seen_at": pokestop_count,
            "pokemon_detail_seen_at": pokemon_detail_count,
        },
    }


@router.get("/api/version")
async def get_version():
    return {
        "version": get_app_version(),
        "name": "Pokemon GO Controller",
        "description": "Pokemon GO Controller · AI Vision & Native Auto-Catch Edition",
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


@router.post("/api/screen/runtime/reset")
async def reset_screen_runtime():
    return reset_screen_runtime_state()


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
    """Detect if the current screen is genuinely a Pokémon encounter (catch) screen,
    a post-catch reward summary screen ('ตกลง' / OK), Pokémon detail page, or map screen.

    STRICT detection — map screen must NEVER be misidentified as encounter.
    Encounter requires running_man + CP text + sky_gradient + encounter_buttons.
    Ball colors are diagnostic only and never establish the screen type.
    """
    img = np.frombuffer(png_bytes, np.uint8)
    img = cv2.imdecode(img, cv2.IMREAD_COLOR)
    if img is None:
        return {
            "is_encounter": False,
            "is_catch_summary": False,
            "is_pokemon_detail": False,
            "is_map_screen": False,
            "reason": "invalid image",
        }
    h, w = img.shape[:2]

    # ── 1. Catch Summary Screen ('ตกลง' / OK & XP rewards card) ──
    # The OK button is a large prominent green capsule button at y: 0.65-0.73, x: 0.32-0.68
    ok_btn_roi = img[int(h * 0.65):int(h * 0.73), int(w * 0.32):int(w * 0.68)]
    hsv_ok = cv2.cvtColor(ok_btn_roi, cv2.COLOR_BGR2HSV)
    ok_mask = cv2.inRange(hsv_ok, np.array([35, 70, 70]), np.array([85, 255, 255]))
    green_ok_ratio = cv2.countNonZero(ok_mask) / max(ok_btn_roi.shape[0] * ok_btn_roi.shape[1], 1)

    card_roi = img[int(h * 0.25):int(h * 0.62), int(w * 0.12):int(w * 0.88)]
    hsv_card = cv2.cvtColor(card_roi, cv2.COLOR_BGR2HSV)
    card_light_mask = cv2.inRange(hsv_card, np.array([0, 0, 190]), np.array([180, 80, 255]))
    card_light_ratio = cv2.countNonZero(card_light_mask) / max(card_roi.shape[0] * card_roi.shape[1], 1)

    is_catch_summary = green_ok_ratio > 0.45 and card_light_ratio > 0.40

    # ── 2. Pokémon Detail / Stats Screen ──
    detail_cp_region = img[int(h * 0.05):int(h * 0.13), int(w * 0.30):int(w * 0.70)]
    detail_cp_gray = cv2.cvtColor(detail_cp_region, cv2.COLOR_BGR2GRAY)
    detail_cp_white = cv2.countNonZero(cv2.inRange(detail_cp_gray, 220, 255)) / max(detail_cp_gray.size, 1)
    has_detail_cp_text = detail_cp_white > 0.025

    star_roi = img[int(h * 0.05):int(h * 0.14), int(w * 0.82):int(w * 0.98)]
    camera_roi = img[int(h * 0.13):int(h * 0.23), int(w * 0.80):int(w * 0.98)]
    star_gray = cv2.cvtColor(star_roi, cv2.COLOR_BGR2GRAY)
    camera_gray = cv2.cvtColor(camera_roi, cv2.COLOR_BGR2GRAY)
    star_white = cv2.countNonZero(cv2.inRange(star_gray, 190, 255)) / max(star_gray.size, 1)
    camera_white = cv2.countNonZero(cv2.inRange(camera_gray, 190, 255)) / max(camera_gray.size, 1)
    star_edges = cv2.countNonZero(cv2.Canny(star_gray, 60, 160)) / max(star_gray.size, 1)
    camera_edges = cv2.countNonZero(cv2.Canny(camera_gray, 60, 160)) / max(camera_gray.size, 1)
    has_detail_star_camera = (
        star_white > 0.025
        and camera_white > 0.025
        and (star_edges + camera_edges) > 0.010
    )

    arc_roi = img[int(h * 0.12):int(h * 0.36), int(w * 0.08):int(w * 0.92)]
    arc_gray = cv2.cvtColor(arc_roi, cv2.COLOR_BGR2GRAY)
    arc_area = max(arc_gray.size, 1)
    arc_white = cv2.countNonZero(cv2.inRange(arc_gray, 225, 255)) / arc_area
    arc_edges = cv2.countNonZero(cv2.Canny(arc_gray, 55, 150)) / arc_area
    has_detail_cp_arc = 0.012 < arc_white < 0.25 and arc_edges > 0.004

    check_btn_roi = img[int(h * 0.88):int(h * 0.96), int(w * 0.38):int(w * 0.62)]
    check_gray = cv2.cvtColor(check_btn_roi, cv2.COLOR_BGR2GRAY)
    check_area = max(check_gray.size, 1)
    check_light_ratio = cv2.countNonZero(cv2.inRange(check_gray, 135, 255)) / check_area
    check_edge_ratio = cv2.countNonZero(cv2.Canny(check_gray, 55, 150)) / check_area
    check_contrast = float(np.std(check_gray))
    has_green_checkmark = check_edge_ratio > 0.006 and (check_light_ratio > 0.045 or check_contrast > 18.0)

    menu_roi = img[int(h * 0.84):int(h * 0.98), int(w * 0.72):int(w * 0.98)]
    menu_gray = cv2.cvtColor(menu_roi, cv2.COLOR_BGR2GRAY)
    menu_area = max(menu_gray.size, 1)
    menu_light = cv2.countNonZero(cv2.inRange(menu_gray, 135, 255)) / menu_area
    menu_edges = cv2.countNonZero(cv2.Canny(menu_gray, 55, 150)) / menu_area
    menu_contrast = float(np.std(menu_gray))
    has_detail_menu = menu_edges > 0.005 and (menu_light > 0.04 or menu_contrast > 18.0)

    detail_card = img[int(h * .44):int(h * .86), int(w * .08):int(w * .92)]
    detail_gray = cv2.cvtColor(detail_card, cv2.COLOR_BGR2GRAY)
    detail_white = cv2.countNonZero(cv2.inRange(detail_gray, 180, 255)) / max(detail_gray.size, 1)

    is_pokemon_detail = (
        not is_catch_summary
        and has_detail_cp_text
        and has_detail_star_camera
        and has_detail_cp_arc
        and has_green_checkmark
        and has_detail_menu
        and detail_white > 0.35
    )

    # ── 3. Map Screen Detection (Trainer HUD on map) ──
    portrait = img[int(h * 0.84):int(h * 0.95), :int(w * 0.20)]
    portrait_gray = cv2.cvtColor(portrait, cv2.COLOR_BGR2GRAY)
    portrait_edges = cv2.Canny(portrait_gray, 50, 150)
    portrait_edge_ratio = cv2.countNonZero(portrait_edges) / max(portrait_gray.size, 1)

    name_roi = img[int(h * 0.955):int(h * 0.985), int(w * 0.01):int(w * 0.20)]
    name_gray = cv2.cvtColor(name_roi, cv2.COLOR_BGR2GRAY)
    name_white = cv2.countNonZero(cv2.inRange(name_gray, 220, 255)) / max(name_roi.shape[0] * name_roi.shape[1], 1)

    bottom_bar = img[int(h * 0.94):h, :]
    gray_bottom = cv2.cvtColor(bottom_bar, cv2.COLOR_BGR2GRAY)
    bottom_bar_area = max(bottom_bar.shape[0] * bottom_bar.shape[1], 1)
    bottom_light = cv2.countNonZero(cv2.inRange(gray_bottom, 160, 255)) / bottom_bar_area

    has_trainer_avatar = (
        not is_pokemon_detail
        and not is_catch_summary
        and portrait_edge_ratio > 0.10
        and (name_white > 0.05 or bottom_light > 0.30)
    )

    # ── 4. Encounter Screen Feature Detection ──
    # 4A. Ball in center bottom (Pokeball / Great Ball / Ultra Ball / Premier Ball)
    ball_region = img[int(h * 0.70):int(h * 0.88), int(w * 0.30):int(w * 0.70)]
    hsv_ball = cv2.cvtColor(ball_region, cv2.COLOR_BGR2HSV)
    ball_area = max(ball_region.shape[0] * ball_region.shape[1], 1)

    mask_r1 = cv2.inRange(hsv_ball, np.array([0, 70, 70]), np.array([12, 255, 255]))
    mask_r2 = cv2.inRange(hsv_ball, np.array([160, 70, 70]), np.array([180, 255, 255]))
    red_ratio = (cv2.countNonZero(mask_r1) + cv2.countNonZero(mask_r2)) / ball_area

    yellow_mask = cv2.inRange(hsv_ball, np.array([18, 70, 70]), np.array([38, 255, 255]))
    yellow_ratio = cv2.countNonZero(yellow_mask) / ball_area

    blue_mask = cv2.inRange(hsv_ball, np.array([95, 70, 70]), np.array([135, 255, 255]))
    blue_ratio = cv2.countNonZero(blue_mask) / ball_area

    white_mask = cv2.inRange(hsv_ball, np.array([0, 0, 180]), np.array([180, 50, 255]))
    white_ratio = cv2.countNonZero(white_mask) / ball_area

    dark_mask = cv2.inRange(hsv_ball, np.array([0, 0, 0]), np.array([180, 255, 60]))
    dark_ratio = cv2.countNonZero(dark_mask) / ball_area

    has_pokeball = red_ratio > 0.04
    has_ultraball = yellow_ratio > 0.035 and dark_ratio > 0.04
    # Great ball has blue body and red/white accents
    has_greatball = blue_ratio > 0.06 and (red_ratio > 0.005 or white_ratio > 0.04) and not has_ultraball
    has_premierball = white_ratio > 0.15 and dark_ratio > 0.04 and not is_pokemon_detail

    # Some large/flying encounters render the selected ball oversized and lower than
    # the normal 70-88% band. Keep the original ratios for ball type, but use this
    # wider lower ROI only as a ready-to-throw fallback after encounter controls pass.
    lower_ball_region = img[int(h * 0.68):int(h * 0.94), int(w * 0.26):int(w * 0.74)]
    hsv_lower_ball = cv2.cvtColor(lower_ball_region, cv2.COLOR_BGR2HSV)
    lower_ball_area = max(lower_ball_region.shape[0] * lower_ball_region.shape[1], 1)
    lower_red = (
        cv2.countNonZero(cv2.inRange(hsv_lower_ball, np.array([0, 70, 70]), np.array([12, 255, 255]))) +
        cv2.countNonZero(cv2.inRange(hsv_lower_ball, np.array([160, 70, 70]), np.array([180, 255, 255])))
    ) / lower_ball_area
    lower_yellow = cv2.countNonZero(
        cv2.inRange(hsv_lower_ball, np.array([18, 70, 70]), np.array([38, 255, 255]))
    ) / lower_ball_area
    lower_blue = cv2.countNonZero(
        cv2.inRange(hsv_lower_ball, np.array([95, 70, 70]), np.array([135, 255, 255]))
    ) / lower_ball_area
    lower_white = cv2.countNonZero(
        cv2.inRange(hsv_lower_ball, np.array([0, 0, 180]), np.array([180, 55, 255]))
    ) / lower_ball_area
    lower_dark = cv2.countNonZero(
        cv2.inRange(hsv_lower_ball, np.array([0, 0, 0]), np.array([180, 255, 70]))
    ) / lower_ball_area
    has_large_low_ball = lower_white > 0.10 and (lower_red > 0.025 or lower_yellow > 0.025 or lower_blue > 0.025)
    has_large_low_white_ball = lower_white > 0.16 and (lower_red > 0.006 or lower_dark > 0.008)
    has_catch_ball = (
        has_pokeball
        or has_greatball
        or has_ultraball
        or has_premierball
        or (white_ratio > 0.06 and (red_ratio > 0.02 or yellow_ratio > 0.02 or blue_ratio > 0.02))
        or has_large_low_ball
        or has_large_low_white_ball
    )

    # 4B. Berry menu button bottom left & Ball selector menu button bottom right
    bl = img[int(h * 0.80):int(h * 0.94), int(w * 0.04):int(w * 0.22)]
    br = img[int(h * 0.80):int(h * 0.94), int(w * 0.78):int(w * 0.96)]
    gray_bl = cv2.cvtColor(bl, cv2.COLOR_BGR2GRAY)
    gray_br = cv2.cvtColor(br, cv2.COLOR_BGR2GRAY)
    bl_ratio = cv2.countNonZero(cv2.inRange(gray_bl, 140, 255)) / max(bl.shape[0] * bl.shape[1], 1)
    br_ratio = cv2.countNonZero(cv2.inRange(gray_br, 140, 255)) / max(br.shape[0] * br.shape[1], 1)
    has_berry_button = bl_ratio > 0.05
    has_ball_selector = br_ratio > 0.05
    has_encounter_buttons = has_berry_button and has_ball_selector

    # 4C. Running man flee icon top left
    top_left = img[int(h * 0.03):int(h * 0.14), int(w * 0.03):int(w * 0.18)]
    gray_tl = cv2.cvtColor(top_left, cv2.COLOR_BGR2GRAY)
    white_tl = cv2.countNonZero(cv2.inRange(gray_tl, 180, 255)) / max(top_left.shape[0] * top_left.shape[1], 1)
    has_running_man = white_tl > 0.03

    # 4D. Top CP / Running man text
    cp_region = img[int(h * 0.22):int(h * 0.30), int(w * 0.25):int(w * 0.75)]
    gray_cp = cv2.cvtColor(cp_region, cv2.COLOR_BGR2GRAY)
    white_cp = cv2.countNonZero(cv2.inRange(gray_cp, 220, 255)) / max(cp_region.shape[0] * cp_region.shape[1], 1)
    has_cp_text = white_cp > 0.015

    # Map nearby radar at bottom-right
    map_nearby = img[int(h * 0.88):int(h * 0.98), int(w * 0.70):int(w * 0.98)]
    gray_nearby = cv2.cvtColor(map_nearby, cv2.COLOR_BGR2GRAY)
    nearby_white = cv2.countNonZero(cv2.inRange(gray_nearby, 200, 255)) / max(map_nearby.shape[0] * map_nearby.shape[1], 1)

    trainer_avatar_blocks_encounter = has_trainer_avatar and (name_white > 0.05 or not has_encounter_buttons)
    is_pokestop_spin_screen = _is_pokestop_spin_screen(png_bytes)
    is_encounter = (
        not is_catch_summary
        and not is_pokemon_detail
        and not is_pokestop_spin_screen
        and not trainer_avatar_blocks_encounter
        and has_running_man
        and has_encounter_buttons
        and has_catch_ball
    )

    is_map_screen = not is_catch_summary and not is_pokemon_detail and not is_encounter

    ball_type = "ultraball" if has_ultraball else ("pokeball" if has_pokeball else ("greatball" if has_greatball else ("premierball" if has_premierball else ("pokeball" if is_encounter else "unknown"))))

    return {
        "is_encounter": is_encounter,
        "ready_to_throw": is_encounter,
        "is_catch_summary": is_catch_summary,
        "is_pokemon_detail": is_pokemon_detail,
        "is_pokestop_spin_screen": is_pokestop_spin_screen,
        "is_map_screen": is_map_screen,
        "has_trainer_avatar": has_trainer_avatar,
        "trainer_name_letters": int(name_white * 10),
        "trainer_portrait_edges": round(portrait_edge_ratio, 4),
        "ball_type": ball_type if is_encounter else "unknown",
        "has_running_man": has_running_man,
        "has_berry_button": has_berry_button,
        "has_ball_selector": has_ball_selector,
        "has_pokeball": is_encounter and has_catch_ball,
        "has_cp_text": has_cp_text,
        "has_encounter_buttons": has_encounter_buttons,
        "has_sky_gradient": True,
        "green_ok_ratio": round(green_ok_ratio, 4),
        "card_light_ratio": round(card_light_ratio, 4),
        "hp_green_ratio": 0.0,
        "has_detail_cp": is_pokemon_detail,
        "has_detail_cp_text": has_detail_cp_text,
        "has_detail_star_camera": has_detail_star_camera,
        "has_detail_cp_arc": has_detail_cp_arc,
        "has_detail_menu": has_detail_menu,
        "detail_arc_white": round(arc_white, 4),
        "detail_arc_edges": round(arc_edges, 4),
        "has_green_checkmark": has_green_checkmark,
        "check_green_ratio": round(check_light_ratio, 4),
        "check_edge_ratio": round(check_edge_ratio, 4),
        "check_contrast": round(check_contrast, 2),
        "menu_edge_ratio": round(menu_edges, 4),
        "menu_contrast": round(menu_contrast, 2),
        "yellow_ratio": round(yellow_ratio, 4),
        "dark_ratio": round(dark_ratio, 4),
        "lower_white_ratio": round(lower_white, 4),
        "lower_dark_ratio": round(lower_dark, 4),
        "lower_red_ratio": round(lower_red, 4),
        "red_ratio": round(red_ratio, 4),
        "blue_ratio": round(blue_ratio, 4),
        "white_tl_ratio": round(white_tl, 4),
        "white_cp_ratio": round(white_cp, 4),
        "sky_blue_ratio": 0.5,
        "nearby_white": round(nearby_white, 4),
        "bottom_light": round(bottom_light, 4),
    }


class ThrowBallRequest(BaseModel):
    serial: str = Field(min_length=1, max_length=200)
    strength: float = Field(default=0.5, ge=0.1, le=1.0, description="Throw strength: 0.1=soft, 0.5=normal, 1.0=max")
    curveball: bool = Field(default=True, description="True for Curveball (spin+arc), False for Straight throw")


class DismissCatchRequest(BaseModel):
    serial: str = Field(min_length=1, max_length=200)


def _is_pokestop_spin_screen(png_bytes: bytes) -> bool:
    """Detect the PokéStop photo-disc screen without confusing the map or encounter screen."""
    img = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return False
    if _has_full_map_hud(png_bytes):
        return False
    if _has_map_center_pokeball_hud(png_bytes) and _has_trainer_avatar_hud(png_bytes):
        return False
    if _has_encounter_hud(png_bytes):
        return False
    height, width = img.shape[:2]

    # The spin page has a large photo disc and a cyan/white close button at the
    # bottom. Some game themes show a pink instruction pill and some do not.
    photo = img[int(height * 0.25):int(height * 0.70), int(width * 0.10):int(width * 0.90)]
    photo_std = float(np.std(cv2.cvtColor(photo, cv2.COLOR_BGR2GRAY)))

    prompt = img[int(height * 0.78):int(height * 0.90), int(width * 0.12):int(width * 0.88)]
    prompt_hsv = cv2.cvtColor(prompt, cv2.COLOR_BGR2HSV)
    pink_mask = cv2.inRange(prompt_hsv, np.array([145, 70, 100]), np.array([179, 255, 255]))
    pink_ratio = cv2.countNonZero(pink_mask) / max(prompt.shape[0] * prompt.shape[1], 1)

    close = img[int(height * 0.88):int(height * 0.99), int(width * 0.36):int(width * 0.64)]
    close_hsv = cv2.cvtColor(close, cv2.COLOR_BGR2HSV)
    cyan_mask = cv2.inRange(close_hsv, np.array([75, 45, 100]), np.array([105, 255, 255]))
    light_mask = cv2.inRange(close_hsv, np.array([0, 0, 180]), np.array([180, 80, 255]))
    close_ratio = cv2.countNonZero(cyan_mask | light_mask) / max(close.shape[0] * close.shape[1], 1)
    close_core = img[int(height * 0.90):int(height * 0.965), int(width * 0.44):int(width * 0.56)]
    close_core_hsv = cv2.cvtColor(close_core, cv2.COLOR_BGR2HSV)
    close_core_light = cv2.countNonZero(cv2.inRange(
        close_core_hsv, np.array([0, 0, 180]), np.array([180, 100, 255])
    )) / max(close_core.shape[0] * close_core.shape[1], 1)

    purple_background = img[int(height * 0.04):int(height * 0.25), :]
    purple_hsv = cv2.cvtColor(purple_background, cv2.COLOR_BGR2HSV)
    purple_mask = cv2.inRange(purple_hsv, np.array([130, 45, 80]), np.array([179, 255, 255]))
    purple_ratio = cv2.countNonZero(purple_mask) / max(purple_background.shape[0] * purple_background.shape[1], 1)

    has_close_button = close_ratio > 0.12 or close_core_light > 0.20

    # Stable controls shared by the different game themes: the white action
    # pill with '+' above the disc and the circular '>' button at top-right.
    action_pill = img[int(height * 0.20):int(height * 0.35), int(width * 0.32):int(width * 0.68)]
    action_hsv = cv2.cvtColor(action_pill, cv2.COLOR_BGR2HSV)
    action_light = cv2.countNonZero(cv2.inRange(
        action_hsv, np.array([0, 0, 170]), np.array([180, 120, 255])
    )) / max(action_pill.shape[0] * action_pill.shape[1], 1)
    action_cyan = cv2.countNonZero(cv2.inRange(
        action_hsv, np.array([75, 35, 80]), np.array([110, 255, 255])
    )) / max(action_pill.shape[0] * action_pill.shape[1], 1)

    next_button = img[int(height * 0.06):int(height * 0.19), int(width * 0.80):int(width * 0.98)]
    next_hsv = cv2.cvtColor(next_button, cv2.COLOR_BGR2HSV)
    next_light = cv2.countNonZero(cv2.inRange(
        next_hsv, np.array([0, 0, 170]), np.array([180, 120, 255])
    )) / max(next_button.shape[0] * next_button.shape[1], 1)
    next_cyan = cv2.countNonZero(cv2.inRange(
        next_hsv, np.array([75, 35, 80]), np.array([110, 255, 255])
    )) / max(next_button.shape[0] * next_button.shape[1], 1)
    action_edges = cv2.Canny(cv2.cvtColor(action_pill, cv2.COLOR_BGR2GRAY), 60, 160)
    next_edges = cv2.Canny(cv2.cvtColor(next_button, cv2.COLOR_BGR2GRAY), 60, 160)

    title = img[int(height * 0.07):int(height * 0.22), int(width * 0.03):int(width * 0.70)]
    title_hsv = cv2.cvtColor(title, cv2.COLOR_BGR2HSV)
    title_light = cv2.inRange(title_hsv, np.array([0, 0, 190]), np.array([180, 100, 255]))
    title_edges = cv2.Canny(cv2.cvtColor(title, cv2.COLOR_BGR2GRAY), 80, 180)
    title_signature = (
        cv2.countNonZero(title_light) / max(title.shape[0] * title.shape[1], 1) > 0.012
        and cv2.countNonZero(title_edges) / max(title.shape[0] * title.shape[1], 1) > 0.015
    )

    next_signature = next_light > 0.10 and cv2.countNonZero(next_edges) / max(next_button.shape[0] * next_button.shape[1], 1) > 0.01
    plus_signature = action_light > 0.45 or (
        action_light > 0.18
        and cv2.countNonZero(action_edges) / max(action_pill.shape[0] * action_pill.shape[1], 1) > 0.005
    )
    # A real PokéStop page always has the top-right next arrow and either the
    # central '+' action pill or a landmark title in the upper-left. Colors
    # alone are deliberately insufficient because the map contains both.
    controls_signature = next_signature and (plus_signature or title_signature)
    return photo_std > 28.0 and has_close_button and controls_signature


def _is_dynamax_screen(png_bytes: bytes) -> bool:
    """Detect a Dynamax/Max Battle lobby screen with the bottom-center close button."""
    img = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return False
    if _has_full_map_hud(png_bytes):
        return False
    if _has_map_center_pokeball_hud(png_bytes) and _has_trainer_avatar_hud(png_bytes):
        return False
    height, width = img.shape[:2]

    arena = img[int(height * 0.05):int(height * 0.80), :]
    arena_hsv = cv2.cvtColor(arena, cv2.COLOR_BGR2HSV)
    purple = cv2.inRange(arena_hsv, np.array([125, 35, 55]), np.array([175, 255, 255]))
    purple_ratio = cv2.countNonZero(purple) / max(arena.shape[0] * arena.shape[1], 1)

    timer = img[int(height * 0.45):int(height * 0.58), int(width * 0.68):int(width * 0.96)]
    timer_gray = cv2.cvtColor(timer, cv2.COLOR_BGR2GRAY)
    timer_dark = cv2.countNonZero(cv2.inRange(timer_gray, 0, 95)) / max(timer_gray.size, 1)
    timer_light = cv2.countNonZero(cv2.inRange(timer_gray, 175, 255)) / max(timer_gray.size, 1)
    timer_edges = cv2.countNonZero(cv2.Canny(timer_gray, 45, 140)) / max(timer_gray.size, 1)
    has_timer_pill = timer_dark > 0.12 and timer_light > 0.015 and timer_edges > 0.006

    mp = img[int(height * 0.88):int(height * 0.98), int(width * 0.72):]
    mp_gray = cv2.cvtColor(mp, cv2.COLOR_BGR2GRAY)
    mp_dark = cv2.countNonZero(cv2.inRange(mp_gray, 0, 105)) / max(mp_gray.size, 1)
    mp_light = cv2.countNonZero(cv2.inRange(mp_gray, 165, 255)) / max(mp_gray.size, 1)
    has_mp_pill = mp_dark > 0.18 and mp_light > 0.015

    return _has_bottom_center_x_button(png_bytes) and purple_ratio > 0.18 and (has_timer_pill or has_mp_pill)


def _has_encounter_hud(png_bytes: bytes) -> bool:
    """Detect the throw/catch screen controls so PokéStop recovery never runs during encounters."""
    img = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return False
    height, width = img.shape[:2]

    berry = img[int(height * 0.80):int(height * 0.96), :int(width * 0.24)]
    selector = img[int(height * 0.80):int(height * 0.96), int(width * 0.76):]
    ball = img[int(height * 0.76):int(height * 0.99), int(width * 0.30):int(width * 0.70)]

    def light_circle_score(region):
        gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
        light = cv2.countNonZero(cv2.inRange(gray, 160, 255)) / max(gray.size, 1)
        edges = cv2.countNonZero(cv2.Canny(gray, 45, 140)) / max(gray.size, 1)
        return light, edges

    berry_light, berry_edges = light_circle_score(berry)
    selector_light, selector_edges = light_circle_score(selector)

    ball_hsv = cv2.cvtColor(ball, cv2.COLOR_BGR2HSV)
    red_low = cv2.inRange(ball_hsv, np.array([0, 80, 70]), np.array([15, 255, 255]))
    red_high = cv2.inRange(ball_hsv, np.array([165, 80, 70]), np.array([180, 255, 255]))
    white = cv2.inRange(ball_hsv, np.array([0, 0, 155]), np.array([180, 100, 255]))
    ball_area = max(ball.shape[0] * ball.shape[1], 1)
    red_ratio = cv2.countNonZero(red_low | red_high) / ball_area
    white_ratio = cv2.countNonZero(white) / ball_area

    top_controls = img[int(height * 0.04):int(height * 0.15), :]
    top_gray = cv2.cvtColor(top_controls, cv2.COLOR_BGR2GRAY)
    top_light = cv2.countNonZero(cv2.inRange(top_gray, 170, 255)) / max(top_gray.size, 1)

    has_side_controls = berry_light > 0.05 and berry_edges > 0.006 and selector_light > 0.05 and selector_edges > 0.006
    has_throw_ball = red_ratio > 0.035 and white_ratio > 0.030
    return has_side_controls and has_throw_ball and top_light > 0.015


def _is_gym_screen(png_bytes: bytes) -> bool:
    """Detect a Gym detail/occupants screen with the bottom-center close button."""
    img = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return False
    if _has_full_map_hud(png_bytes):
        return False
    if _has_map_center_pokeball_hud(png_bytes) and _has_trainer_avatar_hud(png_bytes):
        return False
    height, width = img.shape[:2]

    pokemon_area = img[int(height * 0.34):int(height * 0.72), int(width * 0.02):int(width * 0.98)]
    hsv = cv2.cvtColor(pokemon_area, cv2.COLOR_BGR2HSV)
    pink = cv2.inRange(hsv, np.array([145, 55, 110]), np.array([179, 255, 255]))
    pink_ratio = cv2.countNonZero(pink) / max(pokemon_area.shape[0] * pokemon_area.shape[1], 1)

    right_actions = img[int(height * 0.72):int(height * 0.97), int(width * 0.72):int(width * 0.98)]
    right_gray = cv2.cvtColor(right_actions, cv2.COLOR_BGR2GRAY)
    right_light = cv2.countNonZero(cv2.inRange(right_gray, 130, 255)) / max(right_gray.size, 1)
    right_edges = cv2.countNonZero(cv2.Canny(right_gray, 45, 140)) / max(right_gray.size, 1)
    has_action_buttons = right_light > 0.04 and right_edges > 0.008

    title = img[int(height * 0.04):int(height * 0.18), int(width * 0.20):int(width * 0.90)]
    title_gray = cv2.cvtColor(title, cv2.COLOR_BGR2GRAY)
    title_light = cv2.countNonZero(cv2.inRange(title_gray, 175, 255)) / max(title_gray.size, 1)

    return _has_bottom_center_x_button(png_bytes) and pink_ratio > 0.008 and has_action_buttons and title_light > 0.04


def _is_gangrocket_screen(png_bytes: bytes) -> bool:
    """Detect a Team GO Rocket grunt screen with battle and close controls."""
    img = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return False
    if _has_map_center_pokeball_hud(png_bytes) and _has_trainer_avatar_hud(png_bytes):
        return False
    height, width = img.shape[:2]

    background = img[int(height * 0.05):int(height * 0.90), :]
    hsv = cv2.cvtColor(background, cv2.COLOR_BGR2HSV)
    purple = cv2.inRange(hsv, np.array([120, 20, 45]), np.array([170, 210, 230]))
    purple_ratio = cv2.countNonZero(purple) / max(background.shape[0] * background.shape[1], 1)
    background_gray = cv2.cvtColor(background, cv2.COLOR_BGR2GRAY)
    dark_ratio = cv2.countNonZero(cv2.inRange(background_gray, 0, 95)) / max(background_gray.size, 1)

    chest = img[int(height * 0.28):int(height * 0.68), int(width * 0.26):int(width * 0.76)]
    chest_hsv = cv2.cvtColor(chest, cv2.COLOR_BGR2HSV)
    red_low = cv2.inRange(chest_hsv, np.array([0, 65, 70]), np.array([20, 255, 255]))
    red_high = cv2.inRange(chest_hsv, np.array([160, 65, 70]), np.array([180, 255, 255]))
    red_ratio = cv2.countNonZero(red_low | red_high) / max(chest.shape[0] * chest.shape[1], 1)

    battle = img[int(height * 0.68):int(height * 0.84), int(width * 0.18):int(width * 0.84)]
    battle_hsv = cv2.cvtColor(battle, cv2.COLOR_BGR2HSV)
    green = cv2.inRange(battle_hsv, np.array([35, 45, 95]), np.array([100, 255, 255]))
    green_ratio = cv2.countNonZero(green) / max(battle.shape[0] * battle.shape[1], 1)
    battle_edges = cv2.countNonZero(cv2.Canny(cv2.cvtColor(battle, cv2.COLOR_BGR2GRAY), 45, 140)) / max(
        battle.shape[0] * battle.shape[1], 1
    )

    close = img[int(height * 0.84):int(height * 0.995), int(width * 0.34):int(width * 0.66)]
    close_hsv = cv2.cvtColor(close, cv2.COLOR_BGR2HSV)
    cyan_green = cv2.inRange(close_hsv, np.array([60, 35, 80]), np.array([105, 255, 255]))
    close_ratio = cv2.countNonZero(cyan_green) / max(close.shape[0] * close.shape[1], 1)

    text_band = img[int(height * 0.50):int(height * 0.68), :int(width * 0.55)]
    text_hsv = cv2.cvtColor(text_band, cv2.COLOR_BGR2HSV)
    orange = cv2.inRange(text_hsv, np.array([8, 70, 90]), np.array([30, 255, 255]))
    orange_ratio = cv2.countNonZero(orange) / max(text_band.shape[0] * text_band.shape[1], 1)

    return (
        red_ratio > 0.004
        and green_ratio > 0.12
        and battle_edges > 0.003
        and close_ratio > 0.025
        and (purple_ratio > 0.08 or dark_ratio > 0.28 or orange_ratio > 0.004)
    )


def _is_main_map_hud(png_bytes: bytes) -> bool:
    """Detect the main map HUD so recovery can never tap the map screen."""
    return _has_full_map_hud(png_bytes)


def _has_full_map_hud(png_bytes: bytes) -> bool:
    """Detect the normal map HUD: center Poké Ball, trainer avatar, and right-side buttons."""
    return (
        _has_map_center_pokeball_hud(png_bytes)
        and _has_trainer_avatar_hud(png_bytes)
        and _has_map_side_hud_icons(png_bytes)
    )


def _has_map_center_pokeball_hud(png_bytes: bytes) -> bool:
    """Detect the red/white Poké Ball menu button at the bottom-center of the map."""
    img = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return False
    height, width = img.shape[:2]

    ball = img[int(height * 0.84):int(height * 0.99), int(width * 0.34):int(width * 0.66)]
    ball_hsv = cv2.cvtColor(ball, cv2.COLOR_BGR2HSV)
    red_low = cv2.inRange(ball_hsv, np.array([0, 75, 70]), np.array([15, 255, 255]))
    red_high = cv2.inRange(ball_hsv, np.array([165, 75, 70]), np.array([180, 255, 255]))
    white = cv2.inRange(ball_hsv, np.array([0, 0, 165]), np.array([180, 85, 255]))
    area = max(ball.shape[0] * ball.shape[1], 1)
    red_ratio = cv2.countNonZero(red_low | red_high) / area
    white_ratio = cv2.countNonZero(white) / area
    # The map action button is a real Poké Ball: it must contain both red and
    # white in the bottom-center. A close X button can be bright, but not red.
    has_ball = red_ratio > 0.018 and white_ratio > 0.030
    return has_ball


def _has_trainer_avatar_hud(png_bytes: bytes) -> bool:
    """Detect the lower-left trainer portrait/name HUD on the map."""
    img = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return False
    height, width = img.shape[:2]

    portrait = img[int(height * 0.82):int(height * 0.99), :int(width * 0.28)]
    portrait_edges = cv2.Canny(cv2.cvtColor(portrait, cv2.COLOR_BGR2GRAY), 50, 150)
    portrait_edge_ratio = cv2.countNonZero(portrait_edges) / max(portrait_edges.size, 1)

    name_roi = img[int(height * 0.94):int(height * 0.99), :int(width * 0.28)]
    name_gray = cv2.cvtColor(name_roi, cv2.COLOR_BGR2GRAY)
    name_light = cv2.countNonZero(cv2.inRange(name_gray, 175, 255)) / max(name_gray.size, 1)

    return (portrait_edge_ratio > 0.020 and name_light > 0.035) or portrait_edge_ratio > 0.075


def _has_map_side_hud_icons(png_bytes: bytes) -> bool:
    """Detect stacked right-side map HUD buttons such as binoculars and calendar."""
    img = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return False
    height, width = img.shape[:2]

    # Map buttons can sit high on some layouts or down near the nearby strip
    # when zoomed/tilted, so scan the full right-side HUD column.
    roi = img[int(height * 0.42):int(height * 0.94), int(width * 0.72):int(width * 0.99)]
    if roi.size == 0:
        return False
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 45, 140)
    light = cv2.inRange(gray, 165, 255)
    mask = cv2.dilate(light | edges, np.ones((5, 5), np.uint8), iterations=1)
    components, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)

    icon_like = 0
    roi_area = max(roi.shape[0] * roi.shape[1], 1)
    for label in range(1, components):
        x, y, w_box, h_box, area = stats[label]
        if area / roi_area > 0.22:
            continue
        if int(width * 0.035) <= w_box <= int(width * 0.16) and int(height * 0.025) <= h_box <= int(height * 0.11):
            if area > max(60, int(width * height * 0.00025)):
                icon_like += 1

    return icon_like >= 2



def _has_bottom_center_x_button(png_bytes: bytes) -> bool:
    """Detect the common bottom-center X close button used by menus, bag, gyms, and stops."""
    img = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return False
    height, width = img.shape[:2]

    button = img[int(height * 0.84):int(height * 0.985), int(width * 0.36):int(width * 0.64)]
    button_gray = cv2.cvtColor(button, cv2.COLOR_BGR2GRAY)
    button_area = max(button_gray.size, 1)
    button_light = cv2.countNonZero(cv2.inRange(button_gray, 145, 255)) / button_area
    button_edges = cv2.countNonZero(cv2.Canny(button_gray, 45, 140)) / button_area

    core = img[int(height * 0.895):int(height * 0.965), int(width * 0.43):int(width * 0.57)]
    if core.size == 0:
        return False
    core_gray = cv2.cvtColor(core, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(core_gray, 45, 140)
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180,
        threshold=max(8, int(min(core.shape[:2]) * 0.18)),
        minLineLength=max(10, int(min(core.shape[:2]) * 0.26)),
        maxLineGap=max(4, int(min(core.shape[:2]) * 0.12)),
    )

    has_pos_diag = False
    has_neg_diag = False
    if lines is not None:
        try:
            line_rows = np.asarray(lines, dtype=np.int32).reshape(-1, 4)
            for x1, y1, x2, y2 in line_rows:
                angle = np.degrees(np.arctan2(int(y2) - int(y1), int(x2) - int(x1)))
                if 25 <= angle <= 70:
                    has_pos_diag = True
                if -70 <= angle <= -25:
                    has_neg_diag = True
        except (TypeError, ValueError):
            has_pos_diag = False
            has_neg_diag = False

    edge_pixels = edges > 0
    diag_mask = np.eye(edge_pixels.shape[0], edge_pixels.shape[1], dtype=np.uint8) > 0
    anti_diag_mask = np.fliplr(diag_mask)
    diag_kernel = np.ones((7, 7), np.uint8)
    diag_mask = cv2.dilate(diag_mask.astype(np.uint8), diag_kernel, iterations=1).astype(bool)
    anti_diag_mask = cv2.dilate(anti_diag_mask.astype(np.uint8), diag_kernel, iterations=1).astype(bool)
    diag_hits = int(np.count_nonzero(edge_pixels & diag_mask))
    anti_diag_hits = int(np.count_nonzero(edge_pixels & anti_diag_mask))
    min_diag_hits = max(6, int(min(core.shape[:2]) * 0.16))
    has_edge_x = diag_hits >= min_diag_hits and anti_diag_hits >= min_diag_hits

    core_contrast = float(np.std(core_gray))
    has_button_shell = button_light > 0.08 and button_edges > 0.004
    return has_button_shell and core_contrast > 10.0 and ((has_pos_diag and has_neg_diag) or has_edge_x)


@router.get("/api/screen/detect_encounter")
async def detect_encounter(serial: str = Query(min_length=1, max_length=200)):
    """Check screen state: encounter screen, post-catch summary ('ตกลง'), detail page, or map."""
    png_bytes = await capture_screen(serial)
    return _is_encounter_screen(png_bytes)


@router.post("/api/screen/dismiss_catch_summary")
async def dismiss_catch_summary(body: DismissCatchRequest):
    """Tap OK on catch summary ('ตกลง') or tap ✓ close on Pokemon detail screen."""
    from services.screen_capture import adb_read
    import re

    png = await capture_screen(body.serial)
    if len(png) < 24:
        raise HTTPException(status_code=400, detail="Invalid screenshot dimensions")
    width, height = struct.unpack(">II", png[16:24])

    activity = (await adb_read("-s", body.serial, "shell", "dumpsys", "activity", "activities")).decode(errors="replace")
    if not re.search(r"(?:mResumedActivity|topResumedActivity)[^\n]*\bcom\.nianticlabs\.pokemongo/", activity):
        raise HTTPException(status_code=400, detail="Pokémon GO must be the foreground app")

    def point(x, y):
        return str(round(x * (width - 1))), str(round(y * (height - 1)))

    state = _is_encounter_screen(png)
    if state["is_map_screen"] or state["is_encounter"]:
        return {"ok": True, "dismissed": False}

    if state["is_catch_summary"]:
        # Tap green OK ('ตกลง') button at y: 0.69
        x, y = point(0.50, 0.69)
        await adb_read("-s", body.serial, "shell", "input", "tap", x, y)
        return {"ok": True, "dismissed": True, "action": "ok_btn"}

    if state["is_pokemon_detail"]:
        # Tap green checkmark (✓) close button at y: 0.915
        x, y = point(0.50, 0.915)
        await adb_read("-s", body.serial, "shell", "input", "tap", x, y)
        return {"ok": True, "dismissed": True, "action": "detail_close"}

    return {"ok": True, "dismissed": False}


@router.post("/api/screen/dismiss_pokemon_detail")
async def dismiss_pokemon_detail(body: DismissCatchRequest):
    """Close a Pokémon detail page only after it has stayed visible for 7 seconds."""
    from services.screen_capture import adb_read
    import re

    png = await capture_screen(body.serial)
    now = time.monotonic()
    state = _is_encounter_screen(png)
    if (
        (not state["is_pokemon_detail"] and _is_main_map_hud(png))
        or (not state["is_pokemon_detail"] and _is_pokestop_spin_screen(png))
        or state["is_map_screen"]
        or state["is_encounter"]
        or state["is_catch_summary"]
        or not state["is_pokemon_detail"]
    ):
        _pokemon_detail_seen_at.pop(body.serial, None)
        return {"ok": True, "detected": False, "dismissed": False, "reason": "not_pokemon_detail"}

    first_seen = _pokemon_detail_seen_at.setdefault(body.serial, now)
    if now - first_seen < 7.0:
        return {
            "ok": True,
            "detected": True,
            "dismissed": False,
            "age_seconds": round(now - first_seen, 1),
        }

    activity = (await adb_read("-s", body.serial, "shell", "dumpsys", "activity", "activities")).decode(errors="replace")
    if not re.search(r"(?:mResumedActivity|topResumedActivity)[^\n]*\bcom\.nianticlabs\.pokemongo/", activity):
        raise HTTPException(status_code=400, detail="Pokémon GO must be the foreground app")
    if len(png) < 24:
        raise HTTPException(status_code=400, detail="Invalid screenshot dimensions")
    width, height = struct.unpack(">II", png[16:24])
    x = str(round(0.50 * (width - 1)))
    y = str(round(0.915 * (height - 1)))
    await adb_read("-s", body.serial, "shell", "input", "tap", x, y)
    _pokemon_detail_seen_at.pop(body.serial, None)
    return {"ok": True, "detected": True, "dismissed": True, "action": "detail_close"}


@router.post("/api/screen/auto_close_buttons")
async def auto_close_buttons(body: DismissCatchRequest):
    """Immediately tap visible ✓ detail close or X PokéStop close buttons."""
    from services.screen_capture import adb_read
    import re

    try:
        png = await capture_screen(body.serial)
        if len(png) < 24:
            return {"ok": False, "detected": False, "dismissed": False, "reason": "invalid_screenshot"}

        state = _is_encounter_screen(png)
        has_encounter_hud = _has_encounter_hud(png)
        is_pokestop_screen = _is_pokestop_spin_screen(png)
        is_dynamax_screen = _is_dynamax_screen(png)
        is_gym_screen = _is_gym_screen(png)
        is_gangrocket_screen = _is_gangrocket_screen(png)
        has_bottom_x = _has_bottom_center_x_button(png)
        has_trainer_avatar = _has_trainer_avatar_hud(png)
        has_map_pokeball = _has_map_center_pokeball_hud(png)
        has_map_side_icons = _has_map_side_hud_icons(png)
        is_main_map_hud = _is_main_map_hud(png)
        map_hud_flags = {
            "has_map_pokeball": has_map_pokeball,
            "has_trainer_avatar": has_trainer_avatar,
            "has_map_side_icons": has_map_side_icons,
            "is_main_map_hud": is_main_map_hud,
        }
        if state["is_encounter"] or state["is_catch_summary"] or state.get("ready_to_throw") or has_encounter_hud:
            return {
                "ok": True,
                "detected": False,
                "dismissed": False,
                "reason": "not_close_button_screen",
                "has_encounter_hud": has_encounter_hud,
                **map_hud_flags,
            }
        if has_map_pokeball and has_trainer_avatar and has_map_side_icons:
            return {
                "ok": True,
                "detected": False,
                "dismissed": False,
                "reason": "main_map_hud",
                "has_bottom_x": has_bottom_x,
                "is_map_screen": state["is_map_screen"],
                "is_pokestop_screen": is_pokestop_screen,
                "is_dynamax_screen": is_dynamax_screen,
                "is_gym_screen": is_gym_screen,
                "is_gangrocket_screen": is_gangrocket_screen,
                **map_hud_flags,
            }
        if has_map_pokeball and has_trainer_avatar:
            return {
                "ok": True,
                "detected": False,
                "dismissed": False,
                "reason": "map_hud_partial",
                "has_bottom_x": has_bottom_x,
                "is_map_screen": state["is_map_screen"],
                "is_pokestop_screen": is_pokestop_screen,
                "is_dynamax_screen": is_dynamax_screen,
                "is_gym_screen": is_gym_screen,
                "is_gangrocket_screen": is_gangrocket_screen,
                **map_hud_flags,
            }

        action = None
        x_ratio = 0.50
        y_ratio = 0.925
        if state["is_pokemon_detail"]:
            action = "detail_close"
            y_ratio = 0.915
        elif is_gangrocket_screen:
            action = "gangrocket_close"
            y_ratio = 0.925
        elif is_dynamax_screen:
            action = "dynamax_close"
            y_ratio = 0.925
        elif is_gym_screen:
            action = "gym_close"
            y_ratio = 0.925
        elif is_pokestop_screen:
            action = "pokestop_close"
            y_ratio = 0.925
        else:
            if has_bottom_x:
                action = "bottom_x_close"
                y_ratio = 0.925
            else:
                return {
                    "ok": True,
                    "detected": False,
                    "dismissed": False,
                "reason": "no_close_button",
                "has_bottom_x": has_bottom_x,
                "is_pokestop_screen": is_pokestop_screen,
                "is_dynamax_screen": is_dynamax_screen,
                "is_gym_screen": is_gym_screen,
                "is_gangrocket_screen": is_gangrocket_screen,
                "is_map_screen": state["is_map_screen"],
                **map_hud_flags,
            }

        activity = (await adb_read("-s", body.serial, "shell", "dumpsys", "activity", "activities")).decode(errors="replace")
        if not re.search(r"(?:mResumedActivity|topResumedActivity)[^\n]*\bcom\.nianticlabs\.pokemongo/", activity):
            return {"ok": False, "detected": True, "dismissed": False, "reason": "pokemon_go_not_foreground"}

        width, height = struct.unpack(">II", png[16:24])
        x = str(round(x_ratio * (width - 1)))
        y = str(round(y_ratio * (height - 1)))
        await adb_read("-s", body.serial, "shell", "input", "tap", x, y)
        return {
            "ok": True,
            "detected": True,
            "dismissed": True,
            "action": action,
            "is_pokestop_screen": is_pokestop_screen,
            "is_dynamax_screen": is_dynamax_screen,
            "is_gym_screen": is_gym_screen,
            "is_gangrocket_screen": is_gangrocket_screen,
            **map_hud_flags,
        }
    except Exception as exc:
        return {
            "ok": False,
            "detected": False,
            "dismissed": False,
            "reason": "auto_close_error",
            "detail": str(exc),
        }


@router.post("/api/screen/dismiss_pokestop")
async def dismiss_pokestop(body: DismissCatchRequest):
    """Close a PokéStop photo-disc page only when its visual signature is present."""
    from services.screen_capture import adb_read
    import re

    png = await capture_screen(body.serial)
    now = time.monotonic()
    state = _is_encounter_screen(png)
    has_encounter_hud = _has_encounter_hud(png)
    is_pokestop_screen = _is_pokestop_spin_screen(png)
    is_dynamax_screen = _is_dynamax_screen(png)
    is_gym_screen = _is_gym_screen(png)
    has_full_map_hud = (
        _has_map_center_pokeball_hud(png)
        and _has_trainer_avatar_hud(png)
        and _has_map_side_hud_icons(png)
    )
    if (
        has_full_map_hud
        or
        is_dynamax_screen
        or
        is_gym_screen
        or
        (state["is_map_screen"] and not is_pokestop_screen)
        or state["is_encounter"]
        or state.get("ready_to_throw")
        or has_encounter_hud
        or state["is_catch_summary"]
        or state["is_pokemon_detail"]
        or not is_pokestop_screen
    ):
        _pokestop_screen_seen_at.pop(body.serial, None)
        return {
            "ok": True,
            "detected": False,
            "dismissed": False,
            "reason": (
                "main_map_hud"
                if has_full_map_hud
                else "dynamax_screen"
                if is_dynamax_screen
                else "gym_screen"
                if is_gym_screen
                else "encounter_screen"
                if state["is_encounter"] or state.get("ready_to_throw") or has_encounter_hud
                else "not_pokestop_screen"
            ),
            "is_map_screen": state["is_map_screen"],
            "is_pokestop_screen": is_pokestop_screen,
            "is_dynamax_screen": is_dynamax_screen,
            "is_gym_screen": is_gym_screen,
            "has_encounter_hud": has_encounter_hud,
            "has_full_map_hud": has_full_map_hud,
        }
    first_seen = _pokestop_screen_seen_at.setdefault(body.serial, now)
    if now - first_seen < 7.0:
        return {
            "ok": True,
            "detected": True,
            "dismissed": False,
            "age_seconds": round(now - first_seen, 1),
        }
    activity = (await adb_read("-s", body.serial, "shell", "dumpsys", "activity", "activities")).decode(errors="replace")
    if not re.search(r"(?:mResumedActivity|topResumedActivity)[^\n]*\bcom\.nianticlabs\.pokemongo/", activity):
        raise HTTPException(status_code=400, detail="Pokémon GO must be the foreground app")
    width, height = struct.unpack(">II", png[16:24])
    x = str(round(0.50 * (width - 1)))
    y = str(round(0.925 * (height - 1)))
    await adb_read("-s", body.serial, "shell", "input", "tap", x, y)
    _pokestop_screen_seen_at.pop(body.serial, None)
    return {"ok": True, "detected": True, "dismissed": True, "action": "pokestop_close"}


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


def _estimate_throw_ball_center(png_bytes: bytes, width: int, height: int) -> tuple[int, int]:
    """Estimate the visible throw ball center so low/oversized encounter balls still start on the ball."""
    fallback = (int(0.50 * width), int(0.80 * height))
    try:
        arr = np.frombuffer(png_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    except Exception:
        return fallback
    if img is None or img.size == 0:
        return fallback

    y0, y1 = int(height * 0.66), int(height * 0.97)
    x0, x1 = int(width * 0.24), int(width * 0.76)
    roi = img[y0:y1, x0:x1]
    if roi.size == 0:
        return fallback

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    red = cv2.inRange(hsv, np.array([0, 65, 60]), np.array([15, 255, 255])) | cv2.inRange(
        hsv, np.array([160, 65, 60]), np.array([180, 255, 255])
    )
    yellow = cv2.inRange(hsv, np.array([18, 60, 70]), np.array([42, 255, 255]))
    blue = cv2.inRange(hsv, np.array([92, 60, 50]), np.array([140, 255, 255]))
    white = cv2.inRange(hsv, np.array([0, 0, 155]), np.array([180, 85, 255]))
    dark = cv2.inRange(hsv, np.array([0, 0, 0]), np.array([180, 255, 75]))
    mask = red | yellow | blue | white | dark
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((13, 13), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = max(160, int(width * height * 0.002))
    candidates = []
    roi_center_x = (x1 - x0) / 2
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if w < width * 0.08 or h < height * 0.05:
            continue
        cx = x + w / 2
        cy = y + h / 2
        center_penalty = abs(cx - roi_center_x) * height * 0.05
        candidates.append((area - center_penalty + cy * width * 0.02, x, y, w, h))

    if not candidates:
        return fallback

    _, x, y, w, h = max(candidates, key=lambda item: item[0])
    cx = x0 + x + w / 2
    # Use the lower half of the detected ball footprint as the touch anchor.
    # Low encounter balls are often partially clipped by the tray/safe area; the
    # geometric center of the visible mask can sit above the draggable ball body.
    cy = y0 + y + h * 0.65
    return int(max(width * 0.35, min(width * 0.65, cx))), int(max(height * 0.72, min(height * 0.93, cy)))


def _build_curveball_script(
    dev: str,
    max_x: int,
    max_y: int,
    width: int,
    height: int,
    strength: float,
    ball_center: tuple[int, int] | None = None,
) -> str:
    """Build a shell script that performs a 3-second spin charge followed by a curved Bézier arc throw."""
    import math
    import random

    def to_dev(x, y):
        dx = int((x / max(width, 1)) * max_x)
        dy = int((y / max(height, 1)) * max_y)
        return max(0, min(max_x, dx)), max(0, min(max_y, dy))

    ball_cx, ball_cy = ball_center or (int(0.50 * width), int(0.80 * height))
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


def _build_straight_script(
    dev: str,
    max_x: int,
    max_y: int,
    width: int,
    height: int,
    strength: float,
    ball_center: tuple[int, int] | None = None,
) -> str:
    """Build a shell script that performs a fast straight throw swipe."""
    def to_dev(x, y):
        dx = int((x / max(width, 1)) * max_x)
        dy = int((y / max(height, 1)) * max_y)
        return max(0, min(max_x, dx)), max(0, min(max_y, dy))

    ball_cx, ball_cy = ball_center or (int(0.50 * width), int(0.80 * height))
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
    supports_ball_center = (
        await adb_read(
            "-s",
            serial,
            "shell",
            "/data/local/tmp/touch_injector -h 2>&1 | grep -q -- '-ball-x' && echo 1 || echo 0",
        )
    ).decode().strip()
    if supports_ball_center == "1":
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
    if encounter.get("is_map_screen") or not encounter.get("ready_to_throw"):
        raise HTTPException(status_code=400, detail="ไม่พบหน้าจอ Encounter — ต้องอยู่ในหน้าจับ Pokémon ก่อน")

    # Verify Pokémon GO is foreground
    activity = (await adb_read("-s", body.serial, "shell", "dumpsys", "activity", "activities")).decode(errors="replace")
    if not re.search(r"(?:mResumedActivity|topResumedActivity)[^\n]*\bcom\.nianticlabs\.pokemongo/", activity):
        raise HTTPException(status_code=400, detail="Pokémon GO ต้องเป็น foreground app")

    # Screen dimensions from PNG header
    width, height = struct.unpack(">II", png_bytes[16:24])
    ball_cx, ball_cy = _estimate_throw_ball_center(png_bytes, width, height)

    dev, max_x, max_y = await _get_touch_info(body.serial)
    action_name = "curveball" if body.curveball else "straight"

    has_native = await _ensure_touch_injector(body.serial)
    if has_native:
        inj_cmd = f"/data/local/tmp/touch_injector -dev '{dev}' -max-x {max_x} -max-y {max_y} -width {width} -height {height} -action '{action_name}' -strength {body.strength} -ball-x {ball_cx} -ball-y {ball_cy}"
        await adb_read("-s", body.serial, "shell", inj_cmd)
    else:
        if body.curveball:
            script = _build_curveball_script(dev, max_x, max_y, width, height, body.strength, (ball_cx, ball_cy))
        else:
            script = _build_straight_script(dev, max_x, max_y, width, height, body.strength, (ball_cx, ball_cy))
        cmd = f"cat << 'EOF' > /data/local/tmp/throw.sh\n{script}\nEOF\nsh /data/local/tmp/throw.sh"
        await adb_read("-s", body.serial, "shell", cmd)

    return {
        "ok": True,
        "action": action_name,
        "curveball": body.curveball,
        "strength": body.strength,
        "ball_center": {"x": ball_cx, "y": ball_cy},
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
    label_source: Literal["auto", "manual"] = "auto"


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
        return default_training_service.start_training(body.epochs, body.imgsz, body.label_source)
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
    registry = default_training_service.list_models()
    return {
        "ready": default_ai_detector.is_ai_ready,
        "model_path": str(default_ai_detector.model_path),
        "exists": default_ai_detector.model_path.exists(),
        "input_shape": default_ai_detector.input_shape,
        "active_id": registry["active_id"],
        "models": registry["models"],
    }


@router.get("/api/ai/models")
async def list_models():
    return default_training_service.list_models()


@router.get("/api/ai/models/compare")
async def compare_models():
    return default_training_service.compare_models()


@router.get("/api/ai/models/{model_id}")
async def model_detail(model_id: str):
    try:
        return default_training_service.get_model_detail(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/api/ai/models/{model_id}/use")
async def use_model(model_id: str):
    try:
        return {"ok": True, "model": default_training_service.use_model(model_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.delete("/api/ai/models/{model_id}")
async def delete_model(model_id: str):
    try:
        return {"ok": True, **default_training_service.delete_model(model_id)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))




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
