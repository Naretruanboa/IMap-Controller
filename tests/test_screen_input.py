import struct
import time

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


def test_throw_ball_center_estimator_handles_low_ultraball():
    import cv2
    import numpy as np
    from api.endpoints import _build_straight_script, _estimate_throw_ball_center

    h, w = 1600, 900
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (160, 190, 215)
    cv2.circle(img, (int(w * 0.50), int(h * 0.89)), int(w * 0.16), (245, 245, 245), -1)
    cv2.ellipse(img, (int(w * 0.50), int(h * 0.84)), (int(w * 0.16), int(h * 0.09)), 0, 180, 360, (15, 20, 25), -1)
    cv2.rectangle(img, (int(w * 0.43), int(h * 0.72)), (int(w * 0.47), int(h * 0.86)), (0, 230, 230), -1)
    cv2.rectangle(img, (int(w * 0.53), int(h * 0.72)), (int(w * 0.57), int(h * 0.86)), (0, 230, 230), -1)
    _, png = cv2.imencode(".png", img)

    ball_x, ball_y = _estimate_throw_ball_center(png.tobytes(), w, h)
    assert abs(ball_x - int(w * 0.50)) < 20
    assert ball_y > int(h * 0.84)

    script = _build_straight_script("/dev/input/event1", 1000, 1000, w, h, strength=0.6, ball_center=(ball_x, ball_y))
    expected_start_y = int((ball_y / h) * 1000)
    assert f"sendevent /dev/input/event1 3 54 {expected_start_y}" in script


def test_is_encounter_screen_classification():
    import numpy as np
    import cv2
    from api.endpoints import _is_encounter_screen

    # 1. Simulate Catch Summary Screen ("ตกลง" / OK + XP rewards popup)
    h, w = 1600, 900
    img_summary = np.zeros((h, w, 3), dtype=np.uint8) # Dark background
    # White/light mint card (y: 0.25 to 0.75, x: 0.05 to 0.95)
    img_summary[int(h * 0.25):int(h * 0.75), int(w * 0.05):int(w * 0.95)] = [245, 255, 245]
    # Green "ตกลง" button (y: 0.65 to 0.73, x: 0.20 to 0.80) -> BGR for green
    img_summary[int(h * 0.65):int(h * 0.73), int(w * 0.20):int(w * 0.80)] = [80, 200, 50]

    _, png_summary = cv2.imencode(".png", img_summary)
    res_summary = _is_encounter_screen(png_summary.tobytes())
    assert res_summary["is_catch_summary"] is True
    assert res_summary["is_encounter"] is False  # Must NEVER trigger encounter on summary screen!

    # 2. Simulate Map Screen with bottom menu pokeball (red top, white bottom) & radar
    img_map = np.zeros((h, w, 3), dtype=np.uint8)
    # Red top of menu ball
    img_map[int(h * 0.88):int(h * 0.93), int(w * 0.44):int(w * 0.56)] = [20, 20, 230]
    # White bottom of menu ball
    img_map[int(h * 0.93):int(h * 0.98), int(w * 0.44):int(w * 0.56)] = [255, 255, 255]
    img_map[int(h * 0.89):int(h * 0.97), int(w * 0.76):int(w * 0.97)] = [255, 255, 255] # radar bar
    _, png_map = cv2.imencode(".png", img_map)
    res_map = _is_encounter_screen(png_map.tobytes())
    assert res_map["is_map_screen"] is True
    assert res_map["is_pokemon_detail"] is False
    assert res_map["is_encounter"] is False

    # 3. Simulate Pokémon Detail Screen (Green HP bar + Green Power Up button + Teal Checkmark)
    img_detail = np.zeros((h, w, 3), dtype=np.uint8)
    # CP text
    img_detail[int(h * 0.06):int(h * 0.11), int(w * 0.36):int(w * 0.64)] = 255
    # Star and camera controls
    img_detail[int(h * 0.07):int(h * 0.12), int(w * 0.86):int(w * 0.94)] = 255
    img_detail[int(h * 0.16):int(h * 0.21), int(w * 0.84):int(w * 0.94)] = 255
    img_detail[int(h*.44):int(h*.86), int(w*.08):int(w*.92)] = 245
    # Green HP bar at y: 0.55
    img_detail[int(h * 0.53):int(h * 0.57), int(w * 0.25):int(w * 0.75)] = [50, 220, 80]
    # Green Power Up button at bottom left
    img_detail[int(h * 0.87):int(h * 0.93), int(w * 0.10):int(w * 0.40)] = [50, 200, 80]
    # Teal checkmark button at bottom center
    img_detail[int(h * 0.89):int(h * 0.96), int(w * 0.44):int(w * 0.56)] = [140, 180, 20]
    # Bottom-right menu button
    img_detail[int(h * 0.87):int(h * 0.96), int(w * 0.78):int(w * 0.96)] = [140, 180, 20]
    img_detail[int(h * 0.90):int(h * 0.93), int(w * 0.84):int(w * 0.91)] = 245
    _, png_detail = cv2.imencode(".png", img_detail)
    res_detail = _is_encounter_screen(png_detail.tobytes())
    assert res_detail["is_pokemon_detail"] is True
    assert res_detail["is_map_screen"] is False
    assert res_detail["is_encounter"] is False

    # 4. Simulate Genuine Encounter Screen
    img_enc = np.zeros((h, w, 3), dtype=np.uint8)
    # Running man icon
    img_enc[int(h * 0.03):int(h * 0.10), int(w * 0.03):int(w * 0.15)] = [255, 255, 255]
    # CP bar text
    img_enc[int(h * 0.24):int(h * 0.28), int(w * 0.25):int(w * 0.75)] = [255, 255, 255]
    # Sky and both encounter controls are required independently of ball color.
    img_enc[int(h * 0.10):int(h * 0.22), int(w * 0.20):int(w * 0.90)] = [200, 130, 50]
    img_enc[int(h * 0.81):int(h * 0.87), int(w * 0.05):int(w * 0.20)] = [240, 240, 240]
    img_enc[int(h * 0.81):int(h * 0.87), int(w * 0.80):int(w * 0.95)] = [240, 240, 240]
    # Red Pokéball at bottom center (y: 0.75-0.90, x: 0.35-0.65)
    img_enc[int(h * 0.75):int(h * 0.90), int(w * 0.35):int(w * 0.65)] = [20, 20, 230] # Vibrant red
    _, png_enc = cv2.imencode(".png", img_enc)
    res_enc = _is_encounter_screen(png_enc.tobytes())
    assert res_enc["is_encounter"] is True
    assert res_enc["is_catch_summary"] is False
    assert res_enc["ball_type"] == "pokeball"
    assert res_enc["ready_to_throw"] is True

    # 5. Large low Pokéball in flying encounter, like high/far Pokémon screens.
    img_low_ball = np.zeros((h, w, 3), dtype=np.uint8)
    img_low_ball[int(h * 0.03):int(h * 0.10), int(w * 0.03):int(w * 0.15)] = [255, 255, 255]
    img_low_ball[int(h * 0.24):int(h * 0.28), int(w * 0.25):int(w * 0.75)] = [255, 255, 255]
    img_low_ball[int(h * 0.10):int(h * 0.22), int(w * 0.20):int(w * 0.90)] = [200, 130, 50]
    img_low_ball[int(h * 0.81):int(h * 0.87), int(w * 0.05):int(w * 0.20)] = [240, 240, 240]
    img_low_ball[int(h * 0.81):int(h * 0.87), int(w * 0.80):int(w * 0.95)] = [240, 240, 240]
    # White top enters the normal band, while the red body sits mostly below it.
    img_low_ball[int(h * 0.72):int(h * 0.84), int(w * 0.34):int(w * 0.66)] = [245, 245, 245]
    img_low_ball[int(h * 0.88):int(h * 0.94), int(w * 0.34):int(w * 0.66)] = [20, 20, 230]
    _, png_low_ball = cv2.imencode(".png", img_low_ball)
    res_low_ball = _is_encounter_screen(png_low_ball.tobytes())
    assert res_low_ball["is_encounter"] is True
    assert res_low_ball["ready_to_throw"] is True

    # 5B. Large white ball with only a thin red/dark seam, like Premier Ball or
    # a Poké Ball rotated with the white face toward the camera.
    img_white_ball = np.zeros((h, w, 3), dtype=np.uint8)
    img_white_ball[int(h * 0.03):int(h * 0.10), int(w * 0.03):int(w * 0.15)] = [255, 255, 255]
    img_white_ball[int(h * 0.24):int(h * 0.28), int(w * 0.25):int(w * 0.75)] = [255, 255, 255]
    img_white_ball[int(h * 0.10):int(h * 0.22), int(w * 0.20):int(w * 0.90)] = [200, 130, 50]
    img_white_ball[int(h * 0.81):int(h * 0.87), int(w * 0.05):int(w * 0.20)] = [240, 240, 240]
    img_white_ball[int(h * 0.81):int(h * 0.87), int(w * 0.80):int(w * 0.95)] = [240, 240, 240]
    cv2.circle(img_white_ball, (int(w * 0.50), int(h * 0.82)), int(w * 0.15), [245, 245, 245], -1)
    cv2.ellipse(img_white_ball, (int(w * 0.50), int(h * 0.90)), (int(w * 0.15), int(h * 0.025)), 0, 0, 360, [20, 20, 230], -1)
    cv2.ellipse(img_white_ball, (int(w * 0.50), int(h * 0.89)), (int(w * 0.15), int(h * 0.012)), 0, 0, 360, [20, 20, 20], -1)
    _, png_white_ball = cv2.imencode(".png", img_white_ball)
    res_white_ball = _is_encounter_screen(png_white_ball.tobytes())
    assert res_white_ball["is_encounter"] is True
    assert res_white_ball["ready_to_throw"] is True

    # 6. Textured berry area can look like a trainer portrait, but encounter
    # controls on both sides must keep the screen throw-ready.
    img_textured_buttons = img_low_ball.copy()
    for y in range(int(h * 0.81), int(h * 0.94), 6):
        img_textured_buttons[y:y + 3, int(w * 0.05):int(w * 0.20)] = [255, 255, 255]
    _, png_textured_buttons = cv2.imencode(".png", img_textured_buttons)
    res_textured_buttons = _is_encounter_screen(png_textured_buttons.tobytes())
    assert res_textured_buttons["is_encounter"] is True
    assert res_textured_buttons["ready_to_throw"] is True

    # 7. Beach encounter with lots of cyan/blue background must not be blocked
    # by the PokéStop screen detector.
    img_beach = np.zeros((h, w, 3), dtype=np.uint8)
    img_beach[int(h * 0.03):int(h * 0.10), int(w * 0.03):int(w * 0.15)] = [255, 255, 255]
    img_beach[int(h * 0.24):int(h * 0.30), int(w * 0.18):int(w * 0.82)] = [245, 245, 245]
    img_beach[int(h * 0.08):int(h * 0.36), :] = [235, 245, 250]
    img_beach[int(h * 0.18):int(h * 0.36), :] = [230, 180, 20]
    img_beach[int(h * 0.36):, :] = [160, 190, 215]
    img_beach[int(h * 0.81):int(h * 0.90), int(w * 0.04):int(w * 0.22)] = [240, 240, 240]
    img_beach[int(h * 0.81):int(h * 0.90), int(w * 0.78):int(w * 0.96)] = [240, 240, 240]
    cv2.circle(img_beach, (int(w * 0.50), int(h * 0.90)), int(w * 0.16), [20, 20, 230], -1)
    cv2.ellipse(img_beach, (int(w * 0.50), int(h * 0.94)), (int(w * 0.16), int(h * 0.055)), 0, 0, 360, [245, 245, 245], -1)
    _, png_beach = cv2.imencode(".png", img_beach)
    res_beach = _is_encounter_screen(png_beach.tobytes())
    assert res_beach["is_pokestop_spin_screen"] is False
    assert res_beach["is_encounter"] is True
    assert res_beach["ready_to_throw"] is True



async def test_dismiss_catch_summary_endpoint(monkeypatch):
    from api.endpoints import dismiss_catch_summary, DismissCatchRequest
    calls = []

    async def capture(serial):
        return b"\x89PNG\r\n\x1a\n" + b"0" * 8 + struct.pack(">II", 900, 1600)

    async def adb(*args):
        calls.append(args)
        if "dumpsys" in args:
            return b"mResumedActivity: com.nianticlabs.pokemongo/Main"
        return b""

    monkeypatch.setattr("api.endpoints.capture_screen", capture)
    monkeypatch.setattr("services.screen_capture.adb_read", adb)

    monkeypatch.setattr("api.endpoints._is_encounter_screen", lambda _: {
        "is_map_screen": False, "is_encounter": False,
        "is_catch_summary": True, "is_pokemon_detail": False,
    })
    async def total_label(_):
        return True
    monkeypatch.setattr("api.endpoints.has_total_label", total_label)
    req = DismissCatchRequest(serial="emulator")
    res = await dismiss_catch_summary(req)
    assert res["ok"] is True
    assert res["dismissed"] is True
    # Verify taps were issued for 'ตกลง' (y: ~0.69) and '(✓)' close (y: ~0.925)
    taps = [args for args in calls if len(args) >= 5 and args[3] == "input" and args[4] == "tap"]
    assert len(taps) == 1
    assert taps[0][-2:] == ("450", "1103")





def test_bottom_menu_ball_cannot_establish_encounter():
    import cv2
    import numpy as np
    from api.endpoints import _is_encounter_screen

    h, w = 1600, 900
    # Map-like cyan background, white stop highlights and bottom menu ball.
    img = np.full((h, w, 3), [180, 160, 60], dtype=np.uint8)
    img[64:160, 36:135] = 255
    img[int(h * .89):int(h * .93), int(w * .44):int(w * .56)] = [0, 0, 255]
    img[int(h * .93):int(h * .97), int(w * .44):int(w * .56)] = 255
    _, png = cv2.imencode(".png", img)
    result = _is_encounter_screen(png.tobytes())
    assert result["is_encounter"] is False
    assert result["has_pokeball"] is False
    assert result["ball_type"] == "unknown"
    assert result["red_ratio"] == 0

    # Even a large red patch above the excluded menu cannot replace CP text.
    img[int(h * .70):int(h * .88), int(w * .30):int(w * .70)] = [0, 0, 255]
    _, png = cv2.imencode(".png", img)
    result = _is_encounter_screen(png.tobytes())
    assert result["is_encounter"] is False
    assert result["has_pokeball"] is False

    # PokéStop photo-disc screens can contain bright controls and large dark/white
    # regions, but must never be treated as catch encounters.
    stop = np.full((h, w, 3), [230, 210, 0], dtype=np.uint8)
    stop[int(h * .08):int(h * .18), int(w * .04):int(w * .70)] = 255
    cv2.circle(stop, (int(w * .50), int(h * .48)), int(w * .34), (120, 80, 30), -1)
    cv2.circle(stop, (int(w * .50), int(h * .48)), int(w * .36), (255, 220, 0), 18)
    stop[int(h * .23):int(h * .29), int(w * .36):int(w * .64)] = 245
    cv2.circle(stop, (int(w * .90), int(h * .12)), int(w * .045), (245, 245, 245), -1)
    stop[int(h * .80):int(h * .86), int(w * .18):int(w * .82)] = [180, 80, 235]
    cv2.circle(stop, (int(w * .50), int(h * .925)), int(w * .055), (245, 245, 245), -1)
    _, png = cv2.imencode(".png", stop)
    result = _is_encounter_screen(png.tobytes())
    assert result["is_encounter"] is False
    assert result["ready_to_throw"] is False
    assert result["is_pokestop_spin_screen"] is True

@pytest.mark.parametrize("size", [(900, 1600), (450, 800)])
def test_trainer_hud_overrides_encounter_like_map(size):
    import cv2
    import numpy as np
    from api.endpoints import _is_encounter_screen

    w, h = size
    img = np.zeros((h, w, 3), dtype=np.uint8)
    # All existing encounter cues, including a large ball, can coexist with map colors.
    img[int(h*.04):int(h*.10), int(w*.04):int(w*.18)] = 255
    img[int(h*.10):int(h*.22), int(w*.20):int(w*.90)] = [200, 130, 50]
    img[int(h*.24):int(h*.28), int(w*.25):int(w*.75)] = 255
    img[int(h*.81):int(h*.87), int(w*.05):int(w*.20)] = 240
    img[int(h*.81):int(h*.87), int(w*.80):int(w*.95)] = 240
    img[int(h*.75):int(h*.90), int(w*.35):int(w*.65)] = [20, 20, 230]
    _, png = cv2.imencode(".png", img)
    assert _is_encounter_screen(png.tobytes())["is_encounter"] is True

    # Textured portrait and player-name glyphs; no center-bottom menu required.
    for y in range(int(h*.85), int(h*.95), max(2, int(h*.004))):
        cv2.line(img, (0, y), (int(w*.18), y), (50, 120, 200), 1)
    for x in range(int(w*.01), int(w*.20), max(3, int(w*.025))):
        cv2.rectangle(img, (x, int(h*.96)), (x+max(2, int(w*.012)), int(h*.975)), (255,255,255), -1)
    _, png = cv2.imencode(".png", img)
    result = _is_encounter_screen(png.tobytes())
    assert result["has_trainer_avatar"] is True
    assert result["is_map_screen"] is True
    assert result["is_encounter"] is False
    assert result["is_catch_summary"] is False
    assert result["is_pokemon_detail"] is False


async def test_throw_rejects_map_before_sending_input(monkeypatch):
    from api import endpoints
    from fastapi import HTTPException

    async def capture(serial):
        return b"mock"
    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen",
                        lambda _: {"is_map_screen": True, "is_encounter": True})
    with pytest.raises(HTTPException) as exc:
        await endpoints.throw_ball(endpoints.ThrowBallRequest(serial="emulator"))
    assert exc.value.status_code == 400


async def test_ensure_touch_injector_replaces_binary_without_ball_center_flags(monkeypatch):
    from api import endpoints

    calls = []
    pushed = []

    async def adb(*args):
        calls.append(args)
        if "-ball-x" in args[-1]:
            return b"0"
        if "uname" in args:
            return b"arm64"
        return b""

    class Proc:
        async def communicate(self):
            return b"", b""

    async def create_subprocess_exec(*args, **_kwargs):
        pushed.append(args)
        return Proc()

    monkeypatch.setattr("services.screen_capture.adb_read", adb)
    monkeypatch.setattr("asyncio.create_subprocess_exec", create_subprocess_exec)

    assert await endpoints._ensure_touch_injector("emulator") is True
    assert pushed
    assert pushed[0][-1] == "/data/local/tmp/touch_injector"
    assert any("chmod" in args[-1] for args in calls)


@pytest.mark.parametrize("state", [
    {"is_map_screen": True, "is_encounter": False, "is_catch_summary": True},
    {"is_map_screen": False, "is_encounter": False, "is_catch_summary": False, "is_pokemon_detail": False},
])
async def test_dismiss_never_taps_map_or_unknown(monkeypatch, state):
    from api import endpoints
    calls = []
    async def capture(serial):
        return b"\x89PNG\r\n\x1a\n" + b"0" * 8 + struct.pack(">II", 900, 1600)
    async def adb(*args):
        calls.append(args)
        return b"mResumedActivity: com.nianticlabs.pokemongo/Main"
    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen", lambda _: state)
    monkeypatch.setattr("services.screen_capture.adb_read", adb)
    result = await endpoints.dismiss_catch_summary(endpoints.DismissCatchRequest(serial="emulator"))
    assert result["dismissed"] is False
    assert not any("input" in args for args in calls)

@pytest.mark.parametrize("background", [(180, 70, 100), (60, 160, 30)])
def test_full_white_detail_card_is_not_map(background):
    import cv2
    import numpy as np
    from api.endpoints import _is_encounter_screen
    h, w = 1470, 826
    img = np.full((h, w, 3), background, dtype=np.uint8)
    img[int(h * 0.06):int(h * 0.11), int(w * 0.36):int(w * 0.64)] = 255
    img[int(h * 0.07):int(h * 0.12), int(w * 0.86):int(w * 0.94)] = 255
    img[int(h * 0.16):int(h * 0.21), int(w * 0.84):int(w * 0.94)] = 255
    # Layout from the reported detail page: white card extends to the bottom.
    img[int(h*.407):, int(w*.024):int(w*.97)] = 255
    cv2.line(img, (int(w*.25), int(h*.555)),
             (int(w*.74), int(h*.555)), (170, 220, 0), 10)
    img[int(h*.87):int(h*.93), int(w*.07):int(w*.46)] = [140, 210, 60]
    cv2.circle(img, (int(w*.5), int(h*.925)), int(w*.06), (160, 150, 0), -1)
    img[int(h * 0.87):int(h * 0.96), int(w * 0.78):int(w * 0.96)] = [140, 180, 20]
    img[int(h * 0.90):int(h * 0.93), int(w * 0.84):int(w * 0.91)] = 245
    _, png = cv2.imencode(".png", img)
    state = _is_encounter_screen(png.tobytes())
    assert state["nearby_white"] > .10
    assert state["is_pokemon_detail"] is True
    assert state["is_map_screen"] is False
    assert state["is_catch_summary"] is False
    assert state["ready_to_throw"] is False


def test_main_map_hud_requires_real_pokeball_button():
    import cv2
    import numpy as np
    from api.endpoints import _is_main_map_hud

    h, w = 1600, 900
    img_map = np.zeros((h, w, 3), dtype=np.uint8)
    img_map[int(h * 0.84):int(h * 0.99), :int(w * 0.28)] = 80
    cv2.circle(img_map, (int(w * 0.50), int(h * 0.915)), int(w * 0.06), (245, 245, 245), -1)
    cv2.ellipse(img_map, (int(w * 0.50), int(h * 0.895)), (int(w * 0.06), int(h * 0.03)), 0, 180, 360, (0, 0, 230), -1)
    cv2.rectangle(img_map, (int(w * 0.76), int(h * 0.86)), (int(w * 0.98), int(h * 0.96)), (240, 240, 240), -1)
    cv2.rectangle(img_map, (int(w * 0.79), int(h * 0.89)), (int(w * 0.84), int(h * 0.94)), (80, 80, 80), 4)
    _, png_map = cv2.imencode(".png", img_map)
    assert _is_main_map_hud(png_map.tobytes()) is True

    img_close = np.zeros((h, w, 3), dtype=np.uint8)
    cv2.circle(img_close, (int(w * 0.50), int(h * 0.925)), int(w * 0.055), (245, 245, 245), -1)
    cv2.line(img_close, (int(w * 0.475), int(h * 0.905)), (int(w * 0.525), int(h * 0.945)), (80, 80, 80), 5)
    cv2.line(img_close, (int(w * 0.525), int(h * 0.905)), (int(w * 0.475), int(h * 0.945)), (80, 80, 80), 5)
    cv2.rectangle(img_close, (int(w * 0.76), int(h * 0.86)), (int(w * 0.98), int(h * 0.96)), (240, 240, 240), -1)
    _, png_close = cv2.imencode(".png", img_close)
    assert _is_main_map_hud(png_close.tobytes()) is False


def test_pokestop_detector_rejects_zoomed_map_with_full_hud():
    import cv2
    import numpy as np
    from api.endpoints import _is_pokestop_spin_screen

    h, w = 1600, 900
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (90, 80, 50)

    # Large nearby PokéStop-like ring on the map; this used to confuse the
    # PokéStop page detector when zoomed in.
    cv2.circle(img, (int(w * 0.72), int(h * 0.42)), int(w * 0.11), (255, 235, 0), 14)
    cv2.circle(img, (int(w * 0.72), int(h * 0.42)), int(w * 0.075), (255, 235, 0), 6)

    # Full map HUD: trainer avatar/name, center Poké Ball, right-side calendar/binoculars.
    portrait = img[int(h * 0.82):int(h * 0.99), :int(w * 0.28)]
    portrait[:] = (70, 70, 70)
    cv2.circle(portrait, (int(w * 0.12), int(h * 0.08)), int(w * 0.07), (210, 210, 210), 6)
    cv2.rectangle(img, (int(w * 0.02), int(h * 0.95)), (int(w * 0.24), int(h * 0.985)), (230, 230, 230), -1)
    cv2.circle(img, (int(w * 0.50), int(h * 0.915)), int(w * 0.06), (245, 245, 245), -1)
    cv2.ellipse(img, (int(w * 0.50), int(h * 0.895)), (int(w * 0.06), int(h * 0.03)), 0, 180, 360, (0, 0, 230), -1)
    for cy in (0.76, 0.85):
        cv2.circle(img, (int(w * 0.91), int(h * cy)), int(w * 0.045), (238, 238, 238), -1)
        cv2.circle(img, (int(w * 0.91), int(h * cy)), int(w * 0.045), (90, 90, 90), 3)

    _, png = cv2.imencode(".png", img)
    assert _is_pokestop_spin_screen(png.tobytes()) is False


def test_gym_and_dynamax_detectors_reject_zoomed_map_with_full_hud():
    import cv2
    import numpy as np
    from api.endpoints import _is_dynamax_screen, _is_gym_screen

    h, w = 1600, 900
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (95, 85, 70)

    # Distant gym-like structure and pink/red markers on the map.
    cv2.circle(img, (int(w * 0.82), int(h * 0.38)), int(w * 0.09), (235, 235, 245), -1)
    cv2.circle(img, (int(w * 0.82), int(h * 0.38)), int(w * 0.07), (180, 80, 235), 8)
    for cx, cy in ((0.18, 0.48), (0.34, 0.44), (0.68, 0.46)):
        cv2.circle(img, (int(w * cx), int(h * cy)), int(w * 0.035), (240, 70, 155), -1)

    # Full map HUD.
    portrait = img[int(h * 0.82):int(h * 0.99), :int(w * 0.28)]
    portrait[:] = (70, 70, 70)
    cv2.circle(portrait, (int(w * 0.12), int(h * 0.08)), int(w * 0.07), (210, 210, 210), 6)
    cv2.rectangle(img, (int(w * 0.02), int(h * 0.95)), (int(w * 0.24), int(h * 0.985)), (230, 230, 230), -1)
    cv2.circle(img, (int(w * 0.50), int(h * 0.915)), int(w * 0.06), (245, 245, 245), -1)
    cv2.ellipse(img, (int(w * 0.50), int(h * 0.895)), (int(w * 0.06), int(h * 0.03)), 0, 180, 360, (0, 0, 230), -1)
    for cy in (0.76, 0.85):
        cv2.circle(img, (int(w * 0.91), int(h * cy)), int(w * 0.045), (238, 238, 238), -1)
        cv2.circle(img, (int(w * 0.91), int(h * cy)), int(w * 0.045), (90, 90, 90), 3)

    _, png = cv2.imencode(".png", img)
    assert _is_gym_screen(png.tobytes()) is False
    assert _is_dynamax_screen(png.tobytes()) is False


def test_close_screen_detectors_reject_map_with_center_ball_and_avatar_even_without_side_icons():
    import cv2
    import numpy as np
    from api.endpoints import _is_dynamax_screen, _is_gym_screen, _is_pokestop_spin_screen

    h, w = 1600, 900
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (95, 85, 70)

    # Gym/raid-like clutter in the map scene.
    cv2.circle(img, (int(w * 0.82), int(h * 0.38)), int(w * 0.09), (235, 235, 245), -1)
    cv2.circle(img, (int(w * 0.82), int(h * 0.38)), int(w * 0.07), (180, 80, 235), 8)
    cv2.circle(img, (int(w * 0.42), int(h * 0.42)), int(w * 0.08), (255, 235, 0), 10)

    # The two strongest map invariants visible in the reported screenshot.
    portrait = img[int(h * 0.82):int(h * 0.99), :int(w * 0.28)]
    portrait[:] = (70, 70, 70)
    cv2.circle(portrait, (int(w * 0.12), int(h * 0.08)), int(w * 0.07), (210, 210, 210), 6)
    cv2.rectangle(img, (int(w * 0.02), int(h * 0.95)), (int(w * 0.24), int(h * 0.985)), (230, 230, 230), -1)
    cv2.circle(img, (int(w * 0.50), int(h * 0.915)), int(w * 0.06), (245, 245, 245), -1)
    cv2.ellipse(img, (int(w * 0.50), int(h * 0.895)), (int(w * 0.06), int(h * 0.03)), 0, 180, 360, (0, 0, 230), -1)

    _, png = cv2.imencode(".png", img)
    assert _is_pokestop_spin_screen(png.tobytes()) is False
    assert _is_gym_screen(png.tobytes()) is False
    assert _is_dynamax_screen(png.tobytes()) is False


def test_gangrocket_detector_accepts_dark_blue_variant():
    import cv2
    import numpy as np
    from api.endpoints import _is_gangrocket_screen

    h, w = 1600, 900
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[:] = (45, 35, 20)
    img[int(h * 0.03):int(h * 0.10), :] = (70, 55, 40)
    img[int(h * 0.06):int(h * 0.15), int(w * 0.06):int(w * 0.45)] = (190, 190, 190)

    # Rocket body and red R.
    cv2.ellipse(img, (int(w * 0.50), int(h * 0.52)), (int(w * 0.22), int(h * 0.28)), 0, 0, 360, (45, 45, 45), -1)
    cv2.putText(img, "R", (int(w * 0.42), int(h * 0.50)), cv2.FONT_HERSHEY_SIMPLEX, 4.0, (20, 55, 230), 16)

    # Orange Rocket label, green battle button, teal close button.
    cv2.putText(img, "ROCKET", (int(w * 0.08), int(h * 0.63)), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 130, 255), 5)
    cv2.rectangle(img, (int(w * 0.24), int(h * 0.72)), (int(w * 0.76), int(h * 0.80)), (120, 220, 80), -1)
    cv2.rectangle(img, (int(w * 0.44), int(h * 0.90)), (int(w * 0.56), int(h * 0.975)), (190, 190, 30), -1)

    _, png = cv2.imencode(".png", img)
    assert _is_gangrocket_screen(png.tobytes()) is True


async def test_dismiss_detail_taps_checkmark(monkeypatch):
    from api import endpoints
    calls = []
    async def capture(serial):
        return b"\x89PNG\r\n\x1a\n" + b"0" * 8 + struct.pack(">II", 826, 1470)
    async def adb(*args):
        calls.append(args)
        return b"mResumedActivity: com.nianticlabs.pokemongo/Main"
    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen", lambda _: {
        "is_map_screen": False, "is_encounter": False,
        "is_catch_summary": False, "is_pokemon_detail": True,
    })
    monkeypatch.setattr("services.screen_capture.adb_read", adb)
    result = await endpoints.dismiss_catch_summary(endpoints.DismissCatchRequest(serial="emulator"))
    assert result["dismissed"] is True
    assert result["action"] == "detail_close"
    taps = [args for args in calls if "input" in args]
    assert len(taps) == 1
    assert taps[0][-2:] == ("412", "1344")


async def test_stuck_detail_closes_even_if_map_hud_heuristic_matches(monkeypatch):
    from api import endpoints
    calls = []
    async def capture(serial):
        return b"\x89PNG\r\n\x1a\n" + b"0" * 8 + struct.pack(">II", 826, 1470)
    async def adb(*args):
        calls.append(args)
        return b"mResumedActivity: com.nianticlabs.pokemongo/Main"
    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen", lambda _: {
        "is_map_screen": False, "is_encounter": False,
        "is_catch_summary": False, "is_pokemon_detail": True,
    })
    monkeypatch.setattr(endpoints, "_is_main_map_hud", lambda _: True)
    monkeypatch.setattr(endpoints, "_is_pokestop_spin_screen", lambda _: False)
    monkeypatch.setattr("services.screen_capture.adb_read", adb)
    endpoints._pokemon_detail_seen_at["emulator"] = time.monotonic() - 8
    result = await endpoints.dismiss_pokemon_detail(endpoints.DismissCatchRequest(serial="emulator"))
    assert result["dismissed"] is True
    assert result["action"] == "detail_close"
    taps = [args for args in calls if "input" in args]
    assert len(taps) == 1


async def test_dismiss_pokestop_never_taps_main_map(monkeypatch):
    from api import endpoints

    calls = []

    async def capture(_):
        return b"map"

    async def adb(*args):
        calls.append(args)
        return b"mResumedActivity: com.nianticlabs.pokemongo/Main"

    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen", lambda _: {
        "is_map_screen": True,
        "is_encounter": False,
        "is_catch_summary": False,
        "is_pokemon_detail": False,
    })
    monkeypatch.setattr(endpoints, "_is_pokestop_spin_screen", lambda _: True)
    monkeypatch.setattr("services.screen_capture.adb_read", adb)

    result = await endpoints.dismiss_pokestop(endpoints.DismissCatchRequest(serial="emulator"))

    assert result["dismissed"] is False
    assert result["reason"] == "not_pokestop_screen"
    assert not any("input" in args for args in calls)


async def test_dismiss_pokestop_allows_confirmed_spin_screen(monkeypatch):
    from api import endpoints

    calls = []

    async def capture(_):
        return b"spin"

    async def adb(*args):
        calls.append(args)
        return b"mResumedActivity: com.nianticlabs.pokemongo/Main"

    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen", lambda _: {
        "is_map_screen": False,
        "is_encounter": False,
        "is_catch_summary": False,
        "is_pokemon_detail": False,
    })
    monkeypatch.setattr(endpoints, "_is_pokestop_spin_screen", lambda _: True)
    monkeypatch.setattr("services.screen_capture.adb_read", adb)
    endpoints._pokestop_screen_seen_at["emulator"] = time.monotonic() - 8

    result = await endpoints.dismiss_pokestop(endpoints.DismissCatchRequest(serial="emulator"))

    assert result["dismissed"] is True
    assert result["action"] == "pokestop_close"
    assert any("input" in args for args in calls)


async def test_dismiss_pokestop_never_reports_stuck_on_encounter(monkeypatch):
    from api import endpoints

    calls = []

    async def capture(_):
        return b"encounter"

    async def adb(*args):
        calls.append(args)
        return b"mResumedActivity: com.nianticlabs.pokemongo/Main"

    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen", lambda _: {
        "is_map_screen": False,
        "is_encounter": True,
        "ready_to_throw": True,
        "is_catch_summary": False,
        "is_pokemon_detail": False,
    })
    monkeypatch.setattr(endpoints, "_has_encounter_hud", lambda _: True)
    monkeypatch.setattr(endpoints, "_is_pokestop_spin_screen", lambda _: True)
    monkeypatch.setattr("services.screen_capture.adb_read", adb)

    result = await endpoints.dismiss_pokestop(endpoints.DismissCatchRequest(serial="emulator"))

    assert result["dismissed"] is False
    assert result["detected"] is False
    assert result["reason"] == "encounter_screen"
    assert not any("input" in args for args in calls)


async def test_auto_close_taps_generic_bottom_x(monkeypatch):
    import cv2
    import numpy as np
    from api import endpoints

    calls = []
    h, w = 1600, 900
    img = np.full((h, w, 3), 220, dtype=np.uint8)
    cv2.circle(img, (int(w * 0.50), int(h * 0.925)), int(w * 0.055), 235, -1)
    cv2.circle(img, (int(w * 0.50), int(h * 0.925)), int(w * 0.055), 120, 4)
    cv2.line(img, (int(w * 0.475), int(h * 0.905)), (int(w * 0.525), int(h * 0.945)), 80, 5)
    cv2.line(img, (int(w * 0.525), int(h * 0.905)), (int(w * 0.475), int(h * 0.945)), 80, 5)
    _, png = cv2.imencode(".png", img)

    async def capture(_):
        return png.tobytes()

    async def adb(*args):
        calls.append(args)
        return b"mResumedActivity: com.nianticlabs.pokemongo/Main"

    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen", lambda _: {
        "is_map_screen": True,
        "is_encounter": False,
        "is_catch_summary": False,
        "is_pokemon_detail": False,
    })
    monkeypatch.setattr(endpoints, "_is_pokestop_spin_screen", lambda _: False)
    monkeypatch.setattr("services.screen_capture.adb_read", adb)

    result = await endpoints.auto_close_buttons(endpoints.DismissCatchRequest(serial="emulator"))

    assert result["dismissed"] is True
    assert result["action"] == "bottom_x_close"
    taps = [args for args in calls if "input" in args]
    assert len(taps) == 1
    assert taps[0][-2:] == ("450", "1470")


async def test_auto_close_taps_generic_bottom_x_even_if_broad_map_state_matches(monkeypatch):
    from api import endpoints

    calls = []

    async def capture(_):
        return b"0" * 16 + struct.pack(">II", 900, 1600)

    async def adb(*args):
        calls.append(args)
        return b"mResumedActivity: com.nianticlabs.pokemongo/Main"

    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen", lambda _: {
        "is_map_screen": True,
        "is_encounter": False,
        "is_catch_summary": False,
        "is_pokemon_detail": False,
    })
    monkeypatch.setattr(endpoints, "_is_pokestop_spin_screen", lambda _: False)
    monkeypatch.setattr(endpoints, "_has_bottom_center_x_button", lambda _: True)
    monkeypatch.setattr(endpoints, "_is_main_map_hud", lambda _: False)
    monkeypatch.setattr("services.screen_capture.adb_read", adb)

    result = await endpoints.auto_close_buttons(endpoints.DismissCatchRequest(serial="emulator"))

    assert result["dismissed"] is True
    assert result["action"] == "bottom_x_close"
    assert any("input" in args for args in calls)


async def test_auto_close_never_taps_only_when_full_map_hud_is_visible(monkeypatch):
    import cv2
    import numpy as np
    from api import endpoints

    calls = []
    h, w = 1600, 900
    img = np.zeros((h, w, 3), dtype=np.uint8)
    portrait = img[int(h * 0.82):int(h * 0.99), :int(w * 0.28)]
    portrait[:] = (70, 70, 70)
    cv2.circle(portrait, (int(w * 0.12), int(h * 0.08)), int(w * 0.07), (210, 210, 210), 6)
    cv2.line(portrait, (int(w * 0.04), int(h * 0.02)), (int(w * 0.23), int(h * 0.15)), (230, 230, 230), 5)
    cv2.rectangle(img, (int(w * 0.02), int(h * 0.95)), (int(w * 0.24), int(h * 0.985)), (230, 230, 230), -1)
    for cy in (0.52, 0.64):
        cv2.circle(img, (int(w * 0.91), int(h * cy)), int(w * 0.045), (238, 238, 238), -1)
        cv2.circle(img, (int(w * 0.91), int(h * cy)), int(w * 0.045), (90, 90, 90), 3)
        cv2.line(img, (int(w * 0.885), int(h * cy)), (int(w * 0.935), int(h * cy)), (80, 80, 80), 4)
    cv2.circle(img, (int(w * 0.50), int(h * 0.915)), int(w * 0.06), (245, 245, 245), -1)
    cv2.ellipse(img, (int(w * 0.50), int(h * 0.895)), (int(w * 0.06), int(h * 0.03)), 0, 180, 360, (0, 0, 230), -1)
    _, png = cv2.imencode(".png", img)

    async def capture(_):
        return png.tobytes()

    async def adb(*args):
        calls.append(args)
        return b"mResumedActivity: com.nianticlabs.pokemongo/Main"

    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen", lambda _: {
        "is_map_screen": True,
        "is_encounter": False,
        "is_catch_summary": False,
        "is_pokemon_detail": False,
    })
    monkeypatch.setattr(endpoints, "_is_pokestop_spin_screen", lambda _: False)
    monkeypatch.setattr(endpoints, "_has_bottom_center_x_button", lambda _: True)
    monkeypatch.setattr("services.screen_capture.adb_read", adb)

    result = await endpoints.auto_close_buttons(endpoints.DismissCatchRequest(serial="emulator"))

    assert result["dismissed"] is False
    assert result["reason"] == "main_map_hud"
    assert not any("input" in args for args in calls)


async def test_auto_close_never_taps_main_map_even_if_x_detector_matches(monkeypatch):
    from api import endpoints

    calls = []

    async def capture(_):
        return b"map"

    async def adb(*args):
        calls.append(args)
        return b"mResumedActivity: com.nianticlabs.pokemongo/Main"

    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen", lambda _: {
        "is_map_screen": True,
        "is_encounter": False,
        "is_catch_summary": False,
        "is_pokemon_detail": False,
    })
    monkeypatch.setattr(endpoints, "_is_pokestop_spin_screen", lambda _: False)
    monkeypatch.setattr(endpoints, "_has_bottom_center_x_button", lambda _: True)
    monkeypatch.setattr(endpoints, "_is_main_map_hud", lambda _: True)
    monkeypatch.setattr("services.screen_capture.adb_read", adb)

    result = await endpoints.auto_close_buttons(endpoints.DismissCatchRequest(serial="emulator"))

    assert result["dismissed"] is False
    assert result["reason"] == "main_map_hud"
    assert not any("input" in args for args in calls)

async def test_auto_close_blocks_pokestop_false_positive_when_full_map_hud_matches(monkeypatch):
    from api import endpoints

    calls = []

    async def capture(_):
        return b"0" * 16 + struct.pack(">II", 900, 1600)

    async def adb(*args):
        calls.append(args)
        return b"mResumedActivity: com.nianticlabs.pokemongo/Main"

    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen", lambda _: {
        "is_map_screen": True,
        "is_encounter": False,
        "is_catch_summary": False,
        "is_pokemon_detail": False,
    })
    monkeypatch.setattr(endpoints, "_is_pokestop_spin_screen", lambda _: True)
    monkeypatch.setattr(endpoints, "_has_bottom_center_x_button", lambda _: True)
    monkeypatch.setattr(endpoints, "_has_map_center_pokeball_hud", lambda _: True)
    monkeypatch.setattr(endpoints, "_has_trainer_avatar_hud", lambda _: True)
    monkeypatch.setattr(endpoints, "_has_map_side_hud_icons", lambda _: True)
    monkeypatch.setattr("services.screen_capture.adb_read", adb)

    result = await endpoints.auto_close_buttons(endpoints.DismissCatchRequest(serial="emulator"))

    assert result["dismissed"] is False
    assert result["reason"] == "main_map_hud"
    assert result["is_pokestop_screen"] is True
    assert not any("input" in args for args in calls)


async def test_auto_close_allows_confirmed_pokestop_without_full_map_hud(monkeypatch):
    from api import endpoints

    calls = []

    async def capture(_):
        return b"0" * 16 + struct.pack(">II", 900, 1600)

    async def adb(*args):
        calls.append(args)
        return b"mResumedActivity: com.nianticlabs.pokemongo/Main"

    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen", lambda _: {
        "is_map_screen": False,
        "is_encounter": False,
        "is_catch_summary": False,
        "is_pokemon_detail": False,
    })
    monkeypatch.setattr(endpoints, "_is_pokestop_spin_screen", lambda _: True)
    monkeypatch.setattr(endpoints, "_has_bottom_center_x_button", lambda _: True)
    monkeypatch.setattr(endpoints, "_has_map_center_pokeball_hud", lambda _: False)
    monkeypatch.setattr(endpoints, "_has_trainer_avatar_hud", lambda _: False)
    monkeypatch.setattr(endpoints, "_has_map_side_hud_icons", lambda _: False)
    monkeypatch.setattr("services.screen_capture.adb_read", adb)

    result = await endpoints.auto_close_buttons(endpoints.DismissCatchRequest(serial="emulator"))

    assert result["dismissed"] is True
    assert result["action"] == "pokestop_close"
    assert any("input" in args for args in calls)


async def test_auto_close_allows_pokestop_when_map_state_matches_without_full_map_hud(monkeypatch):
    from api import endpoints

    calls = []

    async def capture(_):
        return b"0" * 16 + struct.pack(">II", 900, 1600)

    async def adb(*args):
        calls.append(args)
        return b"mResumedActivity: com.nianticlabs.pokemongo/Main"

    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen", lambda _: {
        "is_map_screen": True,
        "is_encounter": False,
        "is_catch_summary": False,
        "is_pokemon_detail": False,
    })
    monkeypatch.setattr(endpoints, "_is_pokestop_spin_screen", lambda _: True)
    monkeypatch.setattr(endpoints, "_has_bottom_center_x_button", lambda _: True)
    monkeypatch.setattr(endpoints, "_has_map_center_pokeball_hud", lambda _: False)
    monkeypatch.setattr(endpoints, "_has_trainer_avatar_hud", lambda _: False)
    monkeypatch.setattr(endpoints, "_has_map_side_hud_icons", lambda _: False)
    monkeypatch.setattr("services.screen_capture.adb_read", adb)

    result = await endpoints.auto_close_buttons(endpoints.DismissCatchRequest(serial="emulator"))

    assert result["dismissed"] is True
    assert result["action"] == "pokestop_close"
    assert result["is_pokestop_screen"] is True
    assert any("input" in args for args in calls)


async def test_auto_close_never_uses_bottom_x_when_map_ball_and_avatar_are_visible(monkeypatch):
    from api import endpoints

    calls = []

    async def capture(_):
        return b"0" * 16 + struct.pack(">II", 900, 1600)

    async def adb(*args):
        calls.append(args)
        return b"mResumedActivity: com.nianticlabs.pokemongo/Main"

    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen", lambda _: {
        "is_map_screen": True,
        "is_encounter": False,
        "is_catch_summary": False,
        "is_pokemon_detail": False,
    })
    monkeypatch.setattr(endpoints, "_is_dynamax_screen", lambda _: False)
    monkeypatch.setattr(endpoints, "_is_gym_screen", lambda _: False)
    monkeypatch.setattr(endpoints, "_is_pokestop_spin_screen", lambda _: False)
    monkeypatch.setattr(endpoints, "_has_bottom_center_x_button", lambda _: True)
    monkeypatch.setattr(endpoints, "_has_map_center_pokeball_hud", lambda _: True)
    monkeypatch.setattr(endpoints, "_has_trainer_avatar_hud", lambda _: True)
    monkeypatch.setattr(endpoints, "_has_map_side_hud_icons", lambda _: False)
    monkeypatch.setattr("services.screen_capture.adb_read", adb)

    result = await endpoints.auto_close_buttons(endpoints.DismissCatchRequest(serial="emulator"))

    assert result["dismissed"] is False
    assert result["reason"] == "map_hud_partial"
    assert not any("input" in args for args in calls)


async def test_auto_close_labels_dynamax_before_pokestop(monkeypatch):
    from api import endpoints

    calls = []

    async def capture(_):
        return b"0" * 16 + struct.pack(">II", 900, 1600)

    async def adb(*args):
        calls.append(args)
        return b"mResumedActivity: com.nianticlabs.pokemongo/Main"

    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen", lambda _: {
        "is_map_screen": True,
        "is_encounter": False,
        "is_catch_summary": False,
        "is_pokemon_detail": False,
    })
    monkeypatch.setattr(endpoints, "_is_dynamax_screen", lambda _: True)
    monkeypatch.setattr(endpoints, "_is_gym_screen", lambda _: False)
    monkeypatch.setattr(endpoints, "_is_pokestop_spin_screen", lambda _: True)
    monkeypatch.setattr(endpoints, "_has_bottom_center_x_button", lambda _: True)
    monkeypatch.setattr(endpoints, "_has_map_center_pokeball_hud", lambda _: False)
    monkeypatch.setattr(endpoints, "_has_trainer_avatar_hud", lambda _: False)
    monkeypatch.setattr(endpoints, "_has_map_side_hud_icons", lambda _: False)
    monkeypatch.setattr("services.screen_capture.adb_read", adb)

    result = await endpoints.auto_close_buttons(endpoints.DismissCatchRequest(serial="emulator"))

    assert result["dismissed"] is True
    assert result["action"] == "dynamax_close"
    assert result["is_dynamax_screen"] is True
    assert any("input" in args for args in calls)


async def test_auto_close_labels_gym_before_pokestop(monkeypatch):
    from api import endpoints

    calls = []

    async def capture(_):
        return b"0" * 16 + struct.pack(">II", 900, 1600)

    async def adb(*args):
        calls.append(args)
        return b"mResumedActivity: com.nianticlabs.pokemongo/Main"

    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen", lambda _: {
        "is_map_screen": True,
        "is_encounter": False,
        "is_catch_summary": False,
        "is_pokemon_detail": False,
    })
    monkeypatch.setattr(endpoints, "_is_dynamax_screen", lambda _: False)
    monkeypatch.setattr(endpoints, "_is_gym_screen", lambda _: True)
    monkeypatch.setattr(endpoints, "_is_pokestop_spin_screen", lambda _: True)
    monkeypatch.setattr(endpoints, "_has_bottom_center_x_button", lambda _: True)
    monkeypatch.setattr(endpoints, "_has_map_center_pokeball_hud", lambda _: False)
    monkeypatch.setattr(endpoints, "_has_trainer_avatar_hud", lambda _: False)
    monkeypatch.setattr(endpoints, "_has_map_side_hud_icons", lambda _: False)
    monkeypatch.setattr("services.screen_capture.adb_read", adb)

    result = await endpoints.auto_close_buttons(endpoints.DismissCatchRequest(serial="emulator"))

    assert result["dismissed"] is True
    assert result["action"] == "gym_close"
    assert result["is_gym_screen"] is True
    assert any("input" in args for args in calls)


async def test_auto_close_labels_gangrocket_even_without_generic_bottom_x(monkeypatch):
    from api import endpoints

    calls = []

    async def capture(_):
        return b"0" * 16 + struct.pack(">II", 900, 1600)

    async def adb(*args):
        calls.append(args)
        return b"mResumedActivity: com.nianticlabs.pokemongo/Main"

    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen", lambda _: {
        "is_map_screen": False,
        "is_encounter": False,
        "is_catch_summary": False,
        "is_pokemon_detail": False,
    })
    monkeypatch.setattr(endpoints, "_is_gangrocket_screen", lambda _: True)
    monkeypatch.setattr(endpoints, "_is_dynamax_screen", lambda _: False)
    monkeypatch.setattr(endpoints, "_is_gym_screen", lambda _: False)
    monkeypatch.setattr(endpoints, "_is_pokestop_spin_screen", lambda _: False)
    monkeypatch.setattr(endpoints, "_has_bottom_center_x_button", lambda _: False)
    monkeypatch.setattr(endpoints, "_has_map_center_pokeball_hud", lambda _: False)
    monkeypatch.setattr(endpoints, "_has_trainer_avatar_hud", lambda _: False)
    monkeypatch.setattr(endpoints, "_has_map_side_hud_icons", lambda _: False)
    monkeypatch.setattr("services.screen_capture.adb_read", adb)

    result = await endpoints.auto_close_buttons(endpoints.DismissCatchRequest(serial="emulator"))

    assert result["dismissed"] is True
    assert result["action"] == "gangrocket_close"
    assert result["is_gangrocket_screen"] is True
    assert any("input" in args for args in calls)


async def test_dismiss_catch_summary_taps_ok(monkeypatch):
    from api import endpoints
    calls = []
    async def capture(_):
        return b"0" * 16 + struct.pack(">II", 900, 1600)
    async def adb(*args):
        calls.append(args)
        return b"mResumedActivity: com.nianticlabs.pokemongo/Main"
    monkeypatch.setattr(endpoints, "capture_screen", capture)
    monkeypatch.setattr(endpoints, "_is_encounter_screen", lambda _: {
        "is_map_screen": False, "is_encounter": False, "is_catch_summary": True, "is_pokemon_detail": False})
    monkeypatch.setattr("services.screen_capture.adb_read", adb)
    result = await endpoints.dismiss_catch_summary(endpoints.DismissCatchRequest(serial="emulator"))
    assert result["dismissed"] is True
    assert result["action"] == "ok_btn"
    taps = [args for args in calls if "input" in args]
    assert len(taps) == 1
    assert taps[0][-2:] == ("450", "1103")
