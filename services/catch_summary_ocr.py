"""Read the Thai reward total label before allowing an automatic OK tap."""
import asyncio
import io
import shutil

from PIL import Image, ImageOps


async def has_total_label(png: bytes) -> bool:
    executable = shutil.which("tesseract")
    if not executable:
        return False
    try:
        with Image.open(io.BytesIO(png)) as image:
            w, h = image.size
            # Total row above OK; exclude XP item rows and the underlying game.
            crop = image.crop((int(w * .10), int(h * .53), int(w * .85), int(h * .64)))
            crop = ImageOps.grayscale(crop)
            crop = crop.resize((crop.width * 2, crop.height * 2))
            data = io.BytesIO()
            crop.save(data, format="PNG")
        process = await asyncio.create_subprocess_exec(
            executable, "stdin", "stdout", "-l", "tha", "--psm", "6",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(data.getvalue()), timeout=8)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.wait()
            raise
        text = "".join(stdout.decode("utf-8", errors="replace").split())
        return process.returncode == 0 and "รวมทั้งหมด" in text
    except (OSError, ValueError, asyncio.TimeoutError):
        return False
