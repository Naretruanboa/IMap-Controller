import io
from PIL import Image
import pytest
from services import catch_summary_ocr as module


@pytest.mark.parametrize("text,expected", [
    ("รวมทั้งหมด 170 XP", True),
    ("รวม ทั้งหมด\n170 XP", True),
    ("จับโปเกมอน 100 XP", False),
    ("พาวเวอร์อัป", False),
])
async def test_requires_exact_total_label(monkeypatch, text, expected):
    image = Image.new("RGB", (528, 976), "white")
    data = io.BytesIO()
    image.save(data, format="PNG")
    class Process:
        returncode = 0
        async def communicate(self, png):
            assert png.startswith(b"\x89PNG")
            return text.encode(), b""
    async def spawn(*args, **kwargs):
        assert "tha" in args
        return Process()
    monkeypatch.setattr(module.shutil, "which", lambda _: "/mock/tesseract")
    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
    assert await module.has_total_label(data.getvalue()) is expected


async def test_missing_ocr_never_authorizes_tap(monkeypatch):
    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    assert await module.has_total_label(b"") is False
