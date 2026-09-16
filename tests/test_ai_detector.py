import io
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from app import app
from services.ai_detector import PokestopAIDetector, default_ai_detector


def create_synthetic_frame(color="cyan"):
    """Generate a synthetic test image with a PokéStop disc."""
    width, height = 450, 800
    img = Image.new("RGBA", (width, height), (90, 180, 160, 255))
    pixels = img.load()

    # Draw cyan or purple disc at (200, 450)
    for dy in range(-15, 16):
        for dx in range(-35, 36):
            r_norm = (dx / 30.0) ** 2 + (dy / 14.0) ** 2
            if 0.50 <= r_norm <= 1.10:
                x, y = 200 + dx, 450 + dy
                if color == "cyan":
                    pixels[x, y] = (30, 210, 255, 255)
                else:
                    pixels[x, y] = (210, 80, 240, 255)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_ai_detector_heuristic_fallback():
    detector = PokestopAIDetector("models/non_existent.onnx")
    assert not detector.is_ai_ready

    # Test cyan disc detection
    png_bytes = create_synthetic_frame("cyan")
    results = detector.detect(png_bytes)
    assert len(results) >= 1
    assert results[0]["kind"] == "ring"
    assert results[0]["color"] == "cyan"
    assert results[0]["eligible"] is True
    assert results[0]["score"] >= 80

    # Test purple disc detection
    purple_bytes = create_synthetic_frame("purple")
    purple_results = detector.detect(purple_bytes)
    assert len(purple_results) >= 1
    assert purple_results[0]["kind"] == "ring"
    assert purple_results[0]["color"] == "purple"
    assert purple_results[0]["eligible"] is False


def test_api_detect_stops_endpoint(monkeypatch):
    client = TestClient(app)

    async def mock_capture(serial):
        return create_synthetic_frame("cyan")

    monkeypatch.setattr("api.endpoints.capture_screen", mock_capture)

    response = client.get("/api/screen/detect_stops?serial=emulator-5554&engine=heuristic")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["engine"] == "heuristic"
    assert data["eligible_count"] >= 1
    assert len(data["boxes"]) >= 1
    assert data["boxes"][0]["eligible"] is True


@pytest.mark.parametrize('class_id', [2, 3, 4])
@pytest.mark.parametrize('color', [(20, 200, 250), (190, 80, 230)])
def test_onnx_never_promotes_other_classes_by_color(class_id, color):
    import numpy as np

    detector = PokestopAIDetector('models/non_existent.onnx')
    prediction = np.zeros((1, 9, 1), dtype=np.float32)
    prediction[0, :4, 0] = [320, 400, 100, 80]
    prediction[0, 4 + class_id, 0] = .6

    class FakeSession:
        def run(self, *args):
            return [prediction]

    detector.session = FakeSession()
    detector.input_name = 'images'
    result = detector.detect(Image.new('RGB', (640, 640), color))[0]
    assert result['class_id'] == class_id
    assert result['predicted_class_id'] == class_id
    assert result['eligible'] is False
    assert result['score'] == 60


def test_onnx_active_confidence_is_not_doubled():
    import numpy as np

    detector = PokestopAIDetector('models/non_existent.onnx')
    prediction = np.zeros((1, 8, 1), dtype=np.float32)
    prediction[0, :4, 0] = [320, 400, 100, 80]
    prediction[0, 4, 0] = .45

    class FakeSession:
        def run(self, *args):
            return [prediction]

    detector.session = FakeSession()
    detector.input_name = 'images'
    result = detector.detect(Image.new('RGB', (640, 640), (20, 200, 250)))[0]
    assert result['class_name'] == 'pokestop_active'
    assert result['score'] == 45
    assert result['eligible'] is False
