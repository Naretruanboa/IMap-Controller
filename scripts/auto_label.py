#!/usr/bin/env python3
"""Auto-Labeling Tool: generates YOLO format annotations using calibrated heuristics."""

import argparse
import json
import os
from pathlib import Path
from PIL import Image
import numpy as np
import cv2


def detect_boxes(img: Image.Image, sensitivity: int = 50) -> list[dict]:
    """Detect PokéStop Photo Discs and distant elements on image."""
    img_rgba = img.convert("RGBA")
    w, h = img_rgba.size
    pixels = img_rgba.load()
    relaxed = sensitivity / 100.0

    mask = bytearray(w * h)
    startY, endY = int(h * 0.30), int(h * 0.88)
    startX, endX = int(w * 0.05), int(w * 0.95)

    for y in range(startY, endY):
        for x in range(startX, endX):
            r, g, b, a = pixels[x, y]
            is_cyan = (b >= 170 - relaxed * 10) and (r <= 115 + relaxed * 10) and \
                      (b - r >= 85 - relaxed * 10) and (b - g >= 15 - relaxed * 5) and (g >= 90)
            is_purple = (b >= 145 - relaxed * 20) and (r >= 115 - relaxed * 20) and \
                        (b - g >= 30 - relaxed * 15) and (r - g >= 20 - relaxed * 10)
            if is_cyan or is_purple:
                mask[y * w + x] = 1 if is_cyan else 2

    visited = bytearray(w * h)
    boxes = []
    scale = (w * h) / (900.0 * 1600.0)
    min_pixels = max(15, int(750 * scale - relaxed * 300 * scale))
    min_dim = max(8, int(28 * (w / 900.0)))
    max_dim = int(220 * (w / 900.0))

    for y in range(startY, endY):
        for x in range(startX, endX):
            idx = y * w + x
            if not mask[idx] or visited[idx]:
                continue

            q = [idx]
            visited[idx] = 1
            minX, maxX = x, x
            minY, maxY = y, y
            sumX, sumY = 0, 0
            pts_cnt = 0
            blue_cnt = 0
            purple_cnt = 0
            glow_cnt = 0

            head = 0
            while head < len(q):
                n = q[head]
                head += 1
                cx, cy = n % w, n // w
                minX = min(minX, cx)
                maxX = max(maxX, cx)
                minY = min(minY, cy)
                maxY = max(maxY, cy)
                sumX += cx
                sumY += cy
                pts_cnt += 1

                r, g, b, _ = pixels[cx, cy]
                if (b >= 205 and b - r >= 115) or (r >= 165 and b >= 180):
                    glow_cnt += 1

                if mask[n] == 1:
                    blue_cnt += 1
                else:
                    purple_cnt += 1

                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if dx == 0 and dy == 0:
                            continue
                        nx, ny = cx + dx, cy + dy
                        if startX <= nx < endX and startY <= ny < endY:
                            nidx = ny * w + nx
                            if mask[nidx] and not visited[nidx]:
                                visited[nidx] = 1
                                q.append(nidx)

            bw = maxX - minX + 1
            bh = maxY - minY + 1
            aspect = bw / max(1, bh)
            fill = pts_cnt / max(1, bw * bh)
            glow_ratio = glow_cnt / max(1, pts_cnt)

            # Gym: Tall arena tower, large height (bh >= 125), wide/tall arena, or huge area
            is_gym = (bh >= int(125 * (h / 1600.0))) or (bw >= int(115 * (w / 900.0)) and bh >= int(100 * (h / 1600.0))) or (bw * bh >= int(12500 * scale))
            
            # Distant: Solid cube/diamond, small size, or near top/bottom horizon
            is_solid_cube = (0.70 <= aspect <= 1.30 and fill >= 0.38)
            is_distant_pos = (minY < int(h * 0.42)) or (maxY > int(h * 0.90))
            is_small = (bw < int(30 * (w / 900.0)) and bh < int(30 * (h / 1600.0)))
            is_distant = is_distant_pos or is_small or (is_solid_cube and pts_cnt < int(2200 * scale))
            
            is_noise = (pts_cnt < max(15, int(600 * scale)) or bw < 18 or bh < 14)
            is_strip = (aspect < 0.25 or aspect > 3.8 or fill < 0.06)
            
            if is_noise or is_strip:
                continue
                
            if is_gym:
                cls_id = 3
                cls_name = "gym"
            elif is_distant:
                cls_id = 2
                cls_name = "pokestop_distant"
            elif blue_cnt >= purple_cnt and glow_ratio >= 0.08:
                cls_id = 0
                cls_name = "pokestop_active"
            elif purple_cnt > blue_cnt:
                cls_id = 1
                cls_name = "pokestop_cooldown"
            else:
                cls_id = 2
                cls_name = "pokestop_distant"

            # Normalized YOLO coordinates: x_center, y_center, width, height (0.0 to 1.0)
            x_center = (minX + bw / 2.0) / w
            y_center = (minY + bh / 2.0) / h
            norm_w = bw / w
            norm_h = bh / h

            boxes.append({
                "class_id": cls_id,
                "class_name": cls_name,
                "bbox_norm": [x_center, y_center, norm_w, norm_h],
                "bbox_abs": [minX, minY, bw, bh],
                "targetX": int(sumX / pts_cnt),
                "targetY": int(sumY / pts_cnt),
                "pixels": pts_cnt,
                "glow_ratio": glow_ratio
            })

    # Detect Wild Pokémon inside player spawn interaction radius
    np_img = np.array(img.convert("RGB"))
    hsv = cv2.cvtColor(np_img, cv2.COLOR_RGB2HSV)

    player_cx, player_cy = int(w * 0.50), int(h * 0.63)
    spawn_radius = int(w * 0.44)

    # Distinct color masks for wild pokemon in Pokemon GO:
    warm_mask = (hsv[:, :, 0] >= 10) & (hsv[:, :, 0] <= 42) & (hsv[:, :, 1] >= 40) & (hsv[:, :, 2] >= 60)
    green_mask = (hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 85) & (hsv[:, :, 1] >= 45) & (hsv[:, :, 2] >= 60)
    white_mask = (hsv[:, :, 1] <= 35) & (hsv[:, :, 2] >= 210)
    red_mask = ((hsv[:, :, 0] <= 10) | (hsv[:, :, 0] >= 165)) & (hsv[:, :, 1] >= 50) & (hsv[:, :, 2] >= 70)
    purple_mask = (hsv[:, :, 0] >= 130) & (hsv[:, :, 0] <= 160) & (hsv[:, :, 1] >= 40) & (hsv[:, :, 2] >= 60)

    pokemon_pixels = warm_mask | green_mask | white_mask | red_mask | purple_mask

    # Exclude UI areas and player avatar center
    pokemon_pixels[:int(h * 0.16), :] = False
    pokemon_pixels[int(h * 0.83):, :] = False
    pokemon_pixels[int(h * 0.59):int(h * 0.67), int(w * 0.45):int(w * 0.55)] = False

    # Exclude PokéStop cyan discs
    R, G, B = np_img[:, :, 0].astype(int), np_img[:, :, 1].astype(int), np_img[:, :, 2].astype(int)
    stop_pixel_mask = (B >= 135) & (B - R >= 28)
    pokemon_pixels[stop_pixel_mask] = False

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (12, 12))
    closed = cv2.morphologyEx(pokemon_pixels.astype(np.uint8), cv2.MORPH_CLOSE, kernel)

    num_labels, comp_labels, stats, centroids = cv2.connectedComponentsWithStats(closed, connectivity=8)

    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        bx = stats[i, cv2.CC_STAT_LEFT]
        by = stats[i, cv2.CC_STAT_TOP]
        bw = stats[i, cv2.CC_STAT_WIDTH]
        bh = stats[i, cv2.CC_STAT_HEIGHT]
        cx, cy = int(centroids[i][0]), int(centroids[i][1])

        dist = np.sqrt((cx - player_cx) ** 2 + (cy - player_cy) ** 2)
        if dist <= spawn_radius and 25 <= bw <= 160 and 25 <= bh <= 160 and area >= 280:
            x_center = (bx + bw / 2.0) / w
            y_center = (by + bh / 2.0) / h
            norm_w = bw / w
            norm_h = bh / h

            boxes.append({
                "class_id": 4,
                "class_name": "pokemon",
                "bbox_norm": [x_center, y_center, norm_w, norm_h],
                "bbox_abs": [bx, by, bw, bh],
                "targetX": cx,
                "targetY": cy,
                "pixels": area,
                "glow_ratio": 0.0
            })

    # Deduplicate overlapping boxes
    filtered = []
    for b in sorted(boxes, key=lambda x: -x["pixels"]):
        bx, by = b["targetX"], b["targetY"]
        bw, bh = b["bbox_abs"][2], b["bbox_abs"][3]
        overlap = any(
            abs(f["targetX"] - bx) < max(f["bbox_abs"][2], bw) * 0.5 and
            abs(f["targetY"] - by) < max(f["bbox_abs"][3], bh) * 0.5
            for f in filtered
        )
        if not overlap:
            filtered.append(b)

    return sorted(filtered, key=lambda x: x["bbox_abs"][1])


def generate_labels(images_dir: Path, labels_dir: Path, overwrite: bool = False):
    labels_dir.mkdir(parents=True, exist_ok=True)
    images = list(images_dir.glob("*.png")) + list(images_dir.glob("*.jpg"))
    
    if not images:
        print(f"No images found in {images_dir}. Run collect_dataset.py first.")
        return

    print(f"Processing {len(images)} images in {images_dir}...")
    total_boxes = 0

    for img_path in images:
        label_file = labels_dir / f"{img_path.stem}.txt"
        if label_file.exists() and not overwrite:
            continue
        with Image.open(img_path) as img:
            boxes = detect_boxes(img)

        lines = []
        for b in boxes:
            cls_id = b["class_id"]
            xc, yc, w, h = b["bbox_norm"]
            lines.append(f"{cls_id} {xc:.6f} {yc:.6f} {w:.6f} {h:.6f}\n")

        label_file.write_text("".join(lines))
        total_boxes += len(boxes)

    # Generate data.yaml
    dataset_root = images_dir.parent.resolve()
    data_yaml = dataset_root / "data.yaml"
    yaml_content = f"""# Pokémon GO PokéStop Object Detection Dataset
path: {dataset_root}
train: train/images
val: val/images

names:
  0: pokestop_active
  1: pokestop_cooldown
  2: pokestop_distant
  3: gym
  4: pokemon
"""
    if not data_yaml.exists():
        data_yaml.write_text(yaml_content)
    print(f"\nAuto-labeling complete! Generated {total_boxes} bounding boxes.")
    print(f"Dataset config written to {data_yaml}")


def main():
    parser = argparse.ArgumentParser(description="Auto-label dataset images with YOLO bounding boxes")
    parser.add_argument("--images", "-i", type=str, default="dataset/images", help="Images directory")
    parser.add_argument("--labels", "-l", type=str, default="dataset/labels", help="Labels output directory")
    args = parser.parse_args()

    generate_labels(Path(args.images), Path(args.labels))


if __name__ == "__main__":
    main()
