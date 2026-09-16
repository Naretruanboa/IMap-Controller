#!/usr/bin/env python3
"""Dataset Collector: captures game screenshots via ADB for AI training."""

import argparse
import asyncio
import os
import time
from datetime import datetime
from pathlib import Path

# Add project root to sys.path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.screen_capture import capture_screen, screen_devices


async def collect_images(output_dir: Path, count: int, interval: float, serial: str | None = None):
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if not serial:
        devices = await screen_devices()
        if not devices:
            print("Error: No ADB devices found. Ensure your Android emulator/device is connected.")
            return
        serial = devices[0]
        print(f"Using ADB device: {serial}")

    print(f"Starting capture: {count} images to '{output_dir}', interval={interval}s...")
    
    captured = 0
    for i in range(count):
        try:
            png_bytes = await capture_screen(serial)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            filename = output_dir / f"pogo_{timestamp}.png"
            filename.write_bytes(png_bytes)
            captured += 1
            print(f"[{captured}/{count}] Saved {filename.name} ({len(png_bytes) // 1024} KB)")
        except Exception as exc:
            print(f"Capture error at #{i+1}: {exc}")
        
        if i < count - 1:
            await asyncio.sleep(interval)
            
    print(f"\nDone! Captured {captured} images in {output_dir.resolve()}")


def main():
    parser = argparse.ArgumentParser(description="Collect Pokémon GO screenshots for YOLO dataset")
    parser.add_argument("--count", "-n", type=int, default=30, help="Number of images to capture (default: 30)")
    parser.add_argument("--interval", "-i", type=float, default=2.5, help="Interval in seconds between captures (default: 2.5)")
    parser.add_argument("--output", "-o", type=str, default="dataset/images", help="Output directory (default: dataset/images)")
    parser.add_argument("--serial", "-s", type=str, default=None, help="Specific ADB serial")
    args = parser.parse_args()

    asyncio.run(collect_images(Path(args.output), args.count, args.interval, args.serial))


if __name__ == "__main__":
    main()
