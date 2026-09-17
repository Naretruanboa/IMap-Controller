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
    img_detail[int(h*.44):int(h*.86), int(w*.08):int(w*.92)] = 245
    # Green HP bar at y: 0.55
    img_detail[int(h * 0.53):int(h * 0.57), int(w * 0.25):int(w * 0.75)] = [50, 220, 80]
    # Green Power Up button at bottom left
    img_detail[int(h * 0.87):int(h * 0.93), int(w * 0.10):int(w * 0.40)] = [50, 200, 80]
    # Teal checkmark button at bottom center
    img_detail[int(h * 0.89):int(h * 0.96), int(w * 0.44):int(w * 0.56)] = [140, 180, 20]
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
    # Layout from the reported detail page: white card extends to the bottom.
    img[int(h*.407):, int(w*.024):int(w*.97)] = 255
    cv2.line(img, (int(w*.25), int(h*.555)),
             (int(w*.74), int(h*.555)), (170, 220, 0), 10)
    img[int(h*.87):int(h*.93), int(w*.07):int(w*.46)] = [140, 210, 60]
    cv2.circle(img, (int(w*.5), int(h*.925)), int(w*.06), (160, 150, 0), -1)
    _, png = cv2.imencode(".png", img)
    state = _is_encounter_screen(png.tobytes())
    assert state["nearby_white"] > .10
    assert state["is_pokemon_detail"] is True
    assert state["is_map_screen"] is False
    assert state["is_catch_summary"] is False
    assert state["ready_to_throw"] is False


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
