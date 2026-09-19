"""Offline AI Object Detector service for PokéStops using ONNX Runtime."""

import io
import logging
from pathlib import Path
from typing import Any
from PIL import Image
import numpy as np
from models.class_config import load_class_names

logger = logging.getLogger(__name__)

CLASS_NAMES = load_class_names()


class PokestopAIDetector:
    """Runs offline ONNX object detection for Pokémon GO PokéStops."""

    def __init__(self, model_path: Path | str = "models/pokestop_yolov8n.onnx"):
        self.model_path = Path(model_path)
        self.session = None
        self.input_name = None
        self.input_shape = (640, 640)
        self._load_model()

    def _load_model(self):
        if not self.model_path.exists():
            logger.info("ONNX model '%s' not found. Will use heuristic detection fallback.", self.model_path)
            return

        try:
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self.session = ort.InferenceSession(
                str(self.model_path),
                sess_options=options,
                providers=["CPUExecutionProvider"]
            )
            inputs = self.session.get_inputs()
            self.input_name = inputs[0].name
            logger.info("Loaded ONNX AI detector from %s (input: %s)", self.model_path, self.input_name)
        except Exception as exc:
            logger.warning("Failed to initialize ONNX runtime for %s: %s", self.model_path, exc)
            self.session = None

    @property
    def is_ai_ready(self) -> bool:
        return self.session is not None

    def detect(self, img: Image.Image | bytes, conf_threshold: float = 0.20, iou_threshold: float = 0.45) -> list[dict[str, Any]]:
        """Run object detection on image, returning list of detected bounding boxes."""
        if isinstance(img, bytes):
            img = Image.open(io.BytesIO(img)).convert("RGB")
        else:
            img = img.convert("RGB")

        if self.is_ai_ready:
            try:
                return self._detect_onnx(img, conf_threshold, iou_threshold)
            except Exception as exc:
                logger.error("ONNX inference failed, falling back to heuristic: %s", exc)

        return self._detect_heuristic(img)

    def _detect_onnx(self, img: Image.Image, conf_threshold: float, iou_threshold: float) -> list[dict[str, Any]]:
        orig_w, orig_h = img.size
        target_w, target_h = self.input_shape

        # 1. Letterbox resize maintaining aspect ratio
        scale = min(target_w / orig_w, target_h / orig_h)
        new_w, new_h = int(orig_w * scale), int(orig_h * scale)
        pad_x, pad_y = (target_w - new_w) // 2, (target_h - new_h) // 2

        resized = img.resize((new_w, new_h), Image.Resampling.BILINEAR)
        letterboxed = Image.new("RGB", (target_w, target_h), (114, 114, 114))
        letterboxed.paste(resized, (pad_x, pad_y))

        # 2. Convert to float32 tensor (1, 3, H, W), RGB normalized 0..1
        arr = np.array(letterboxed, dtype=np.float32) / 255.0
        arr = np.transpose(arr, (2, 0, 1))
        input_tensor = np.expand_dims(arr, axis=0)

        # 3. Inference
        outputs = self.session.run(None, {self.input_name: input_tensor})
        predictions = outputs[0]  # Shape: (1, 4 + num_classes, num_anchors)

        if predictions.ndim == 3:
            predictions = predictions[0]  # Shape: (4 + num_classes, num_anchors)

        # YOLOv8 format: [cx, cy, w, h, cls0, cls1, cls2, ...]
        boxes_xywh = predictions[:4, :].T  # (num_anchors, 4)
        scores_all = predictions[4:, :].T  # (num_anchors, num_classes)

        class_ids = np.argmax(scores_all, axis=1)
        confidences = np.max(scores_all, axis=1)

        valid_mask = confidences >= conf_threshold
        boxes_xywh = boxes_xywh[valid_mask]
        confidences = confidences[valid_mask]
        class_ids = class_ids[valid_mask]

        if len(boxes_xywh) == 0:
            return []

        # Convert from letterbox xywh to original image coordinates
        x1 = (boxes_xywh[:, 0] - boxes_xywh[:, 2] / 2 - pad_x) / scale
        y1 = (boxes_xywh[:, 1] - boxes_xywh[:, 3] / 2 - pad_y) / scale
        x2 = (boxes_xywh[:, 0] + boxes_xywh[:, 2] / 2 - pad_x) / scale
        y2 = (boxes_xywh[:, 1] + boxes_xywh[:, 3] / 2 - pad_y) / scale

        # Clip to image bounds
        x1 = np.clip(x1, 0, orig_w)
        y1 = np.clip(y1, 0, orig_h)
        x2 = np.clip(x2, 0, orig_w)
        y2 = np.clip(y2, 0, orig_h)

        # 4. Non-Maximum Suppression (NMS)
        indices = self._nms(x1, y1, x2, y2, confidences, iou_threshold)

        np_img = np.array(img)

        # 5. Process candidate boxes & color statistics
        candidate_boxes = []
        for idx in indices:
            bx1, by1, bx2, by2 = int(x1[idx]), int(y1[idx]), int(x2[idx]), int(y2[idx])
            bw, bh = max(1, bx2 - bx1), max(1, by2 - by1)
            cid = int(class_ids[idx])
            conf = float(confidences[idx])
            score = int(round(min(1.0, max(0.0, conf)) * 100))
            predicted_cid = cid

            crop = np_img[by1:by2, bx1:bx2]
            cyan_pct, purple_pct = 0.0, 0.0
            cyan_cnt, purple_cnt = 0, 0
            if crop.size > 0:
                r = crop[:, :, 0].astype(int)
                g = crop[:, :, 1].astype(int)
                bl = crop[:, :, 2].astype(int)
                cyan_mask = (bl >= 135) & (bl - r >= 30) & (g - r >= 10)
                purple_mask = (bl >= 110) & (r >= 90) & (bl - g >= 15) & (r - g >= 15)
                cyan_cnt = int(np.sum(cyan_mask))
                purple_cnt = int(np.sum(purple_mask))
                total_px = max(1, crop.shape[0] * crop.shape[1])
                cyan_pct = (cyan_cnt / total_px) * 100.0
                purple_pct = (purple_cnt / total_px) * 100.0

            # Gym detection: model predicted gym (cid=3) or explicit oversized arena bounding box (>= 40000px on 900x1600 normalized)
            is_gym = (cid == 3) or (
                cid not in (1, 2, 4, 5, 6) and (
                    bh >= int(220 * (orig_h / 1600.0)) or
                    (bw >= int(180 * (orig_w / 900.0)) and bh >= int(160 * (orig_h / 1600.0))) or
                    ((bw / orig_w) * (bh / orig_h) >= (40000.0 / (900.0 * 1600.0)))
                )
            )

            candidate_boxes.append({
                "idx": idx,
                "bx1": bx1, "by1": by1, "bx2": bx2, "by2": by2,
                "bw": bw, "bh": bh,
                "targetX": bx1 + bw // 2, "targetY": by1 + bh // 2,
                "cid": cid, "predicted_cid": predicted_cid,
                "conf": conf, "score": score,
                "cyan_pct": cyan_pct, "purple_pct": purple_pct,
                "cyan_cnt": cyan_cnt, "purple_cnt": purple_cnt,
                "is_gym": is_gym
            })

        # Identify all detected Gym bases/arenas
        gym_bases = [b for b in candidate_boxes if b["is_gym"]]

        results = []
        for cand in candidate_boxes:
            bx1, by1, bx2, by2 = cand["bx1"], cand["by1"], cand["bx2"], cand["by2"]
            bw, bh = cand["bw"], cand["bh"]
            cid = cand["cid"]
            predicted_cid = cand["predicted_cid"]
            conf, score = cand["conf"], cand["score"]
            cyan_pct, purple_pct = cand["cyan_pct"], cand["purple_pct"]
            cyan_cnt, purple_cnt = cand["cyan_cnt"], cand["purple_cnt"]
            is_gym = cand["is_gym"]

            # Spatial association: Check if this box sits along the vertical tower/spire of a detected Gym arena
            is_gym_tower = False
            if cid == 0:
                for gb in gym_bases:
                    if cand is gb:
                        continue
                    # If the box is horizontally centered within the gym base column and positioned above or overlapping
                    horiz_dist = abs(cand["targetX"] - gb["targetX"])
                    is_within_column = horiz_dist <= max(80.0, gb["bw"] * 0.55)
                    is_above_or_in_gym = cand["targetY"] <= gb["by2"] and cand["targetY"] >= gb["by1"] - int(orig_h * 0.45)
                    if is_within_column and is_above_or_in_gym:
                        is_gym_tower = True
                        break

            is_purple = (purple_pct > 12.0 and purple_cnt > cyan_cnt * 0.7) or (purple_cnt > cyan_cnt * 1.2 and purple_pct > 8.0) or (cid == 1 and purple_pct > 5.0)
            is_cyan = not is_purple and ((cyan_pct > 10.0) or (cid == 0 and cyan_pct > 5.0))

            # Distant PokéStop checks (horizon position, bottom tray, small solid cube, 100% solid cyan box, or predicted distant class)
            is_solid_distant = (cid == 0 and cyan_pct >= 85.0 and (bw < 85 and bh < 90))
            is_distant = not (is_gym or is_gym_tower) and (
                cid == 2 or
                (cid == 0 and (by2 < int(orig_h * 0.45) or by1 > int(orig_h * 0.90) or is_solid_distant)) or
                (bw < 30 and bh < 30 and (by1 < int(orig_h * 0.52) or cid != 0)) or
                (0.70 <= bw / max(1, bh) <= 1.30 and bw < 35 and bh < 35 and cyan_pct > 40.0)
            )

            # If the model explicitly predicted gym (3), pokemon (4), cooldown (1), or distant (2), respect it unless physical rule overrides
            if is_gym or is_gym_tower or cid == 3:
                cid = 3
                cname = "gym"
                color = "other"
                kind = "solid"
                eligible = False
                reason = f"AI [gym] ยิมโปเกมอน (ไม่ใช่เสาหมุน) {score}% · {bw}×{bh} px"
            elif cid == 4 or predicted_cid == 4:
                cid = 4
                cname = "pokemon"
                color = "orange"
                kind = "pokemon"
                eligible = False  # Keep false in unit-level baseline or encounter flag
                state_label = "พร้อมแตะเข้าหน้าจับ" if conf >= 0.40 else "คะแนนต่ำ"
                reason = f"AI [pokemon] โปเกมอนป่า ({state_label}) {score}% · {bw}×{bh} px"
            elif cid == 2 or is_distant:
                cid = 2
                cname = "pokestop_distant"
                color = "cyan"
                kind = "solid"
                eligible = False
                reason = f"AI [pokestop_distant] เสาระยะไกล (นอกระยะหมุน) {score}% · {bw}×{bh} px"
            elif cid == 1 or is_purple:
                cid = 1
                cname = "pokestop_cooldown"
                color = "purple"
                kind = "ring"
                eligible = False
                reason = f"AI [pokestop_cooldown] เสาสีม่วง (ติดคูลดาวน์) {score}% · {bw}×{bh} px"
            elif is_cyan or cid == 0:
                cid = 0
                cname = "pokestop_active"
                color = "cyan"
                kind = "ring"
                eligible = (conf >= 0.65)
                state_label = "ผ่านเกณฑ์เป้าหมาย" if eligible else "คะแนนต่ำ — ไม่ใช้เป็นเป้าหมายอัตโนมัติ"
                reason = f"AI [pokestop_active] {state_label} {score}% · {bw}×{bh} px"
            else:
                cname = CLASS_NAMES.get(cid, "unknown")
                color = "other"
                kind = "solid"
                eligible = False
                reason = f"AI [{cname}] {score}% · {bw}×{bh} px"

            results.append({
                "x": bx1, "y": by1, "width": bw, "height": bh,
                "targetX": bx1 + bw // 2, "targetY": by1 + bh // 2,
                "pixels": bw * bh,
                "kind": kind,
                "color": color,
                "score": score,
                "confidence": conf,
                "predicted_class_id": predicted_cid,
                "predicted_class_name": CLASS_NAMES.get(predicted_cid, "unknown"),
                "class_id": cid,
                "class_name": cname,
                "eligible": eligible,
                "engine": "ai_onnx",
                "reason": reason
            })

        return sorted(results, key=lambda b: b["y"])

    def _nms(self, x1, y1, x2, y2, scores, iou_threshold):
        order = scores.argsort()[::-1]
        keep = []
        while order.size > 0:
            i = order[0]
            keep.append(i)
            if order.size == 1:
                break
            xx1 = np.maximum(x1[i], x1[order[1:]])
            yy1 = np.maximum(y1[i], y1[order[1:]])
            xx2 = np.minimum(x2[i], x2[order[1:]])
            yy2 = np.minimum(y2[i], y2[order[1:]])

            w = np.maximum(0.0, xx2 - xx1)
            h = np.maximum(0.0, yy2 - yy1)
            inter = w * h
            area_i = (x2[i] - x1[i]) * (y2[i] - y1[i])
            area_other = (x2[order[1:]] - x1[order[1:]]) * (y2[order[1:]] - y1[order[1:]])
            union = area_i + area_other - inter
            iou = inter / np.maximum(union, 1e-6)

            inds = np.where(iou <= iou_threshold)[0]
            order = order[inds + 1]
        return keep

    def _detect_heuristic(self, img: Image.Image) -> list[dict[str, Any]]:
        """Calibrated heuristic fallback detector."""
        img_rgba = img.convert("RGBA")
        w, h = img_rgba.size
        pixels = img_rgba.load()

        mask = bytearray(w * h)
        startY, endY = int(h * 0.30), int(h * 0.88)
        startX, endX = int(w * 0.05), int(w * 0.95)

        for y in range(startY, endY):
            for x in range(startX, endX):
                r, g, b, _ = pixels[x, y]
                is_cyan = (b >= 165) and (r <= 120) and (b - r >= 75) and (b - g >= 10) and (g >= 85)
                is_purple = (b >= 145) and (r >= 115) and (b - g >= 30) and (r - g >= 20)
                if is_cyan or is_purple:
                    mask[y * w + x] = 1 if is_cyan else 2

        visited = bytearray(w * h)
        boxes = []
        scale = (w * h) / (900.0 * 1600.0)
        min_pixels = max(15, int(750 * scale))
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
                pts = [idx]
                blue_cnt, purple_cnt, glow_cnt = 0, 0, 0

                head = 0
                while head < len(q):
                    n = q[head]
                    head += 1
                    cx, cy = n % w, n // w
                    minX = min(minX, cx)
                    maxX = max(maxX, cx)
                    minY = min(minY, cy)
                    maxY = max(maxY, cy)

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
                                    pts.append(nidx)

                bw = maxX - minX + 1
                bh = maxY - minY + 1
                aspect = bw / max(1, bh)
                fill = len(pts) / max(1, bw * bh)
                glow_ratio = glow_cnt / max(1, len(pts))
                cx = minX + bw // 2
                cy = minY + bh // 2

                is_solid_cube = (0.70 <= aspect <= 1.30 and fill >= 0.40)
                is_huge = (bw > max_dim or bh > max_dim or len(pts) > int(9000 * scale))
                is_noise = (len(pts) < min_pixels or max(bw, bh) < min_dim)

                is_ring = not is_solid_cube and not is_huge and not is_noise and (glow_ratio >= 0.12 or len(pts) < 100)
                color = "cyan" if blue_cnt >= purple_cnt else "purple"

                score = min(99, int(80 + 15 * min(1.0, glow_ratio / 0.4) + 4 * min(1.0, len(pts) / (1500 * scale)))) if is_ring else min(38, int(15 + 20 * fill))
                kind = "ring" if is_ring else "solid"
                reason = f"จานหมุนเสา ({'สีฟ้าพร้อมหมุน' if color == 'cyan' else 'สีม่วง'}) · ขนาด {bw}×{bh} px" if is_ring else "วัตถุอื่นนอกเกณฑ์จานหมุน"

                boxes.append({
                    "x": minX, "y": minY, "width": bw, "height": bh,
                    "targetX": cx, "targetY": cy,
                    "pixels": len(pts),
                    "kind": kind,
                    "color": color,
                    "score": score,
                    "class_name": "pokestop_active" if is_ring and color == "cyan" else ("pokestop_cooldown" if is_ring else "solid"),
                    "eligible": (is_ring and color == "cyan" and score >= 80),
                    "engine": "heuristic",
                    "reason": reason
                })

        filtered = []
        for box in sorted([b for b in boxes if b["kind"] == "ring" or b["pixels"] > 30], key=lambda b: -b["score"]):
            overlap = any(
                abs(f["targetX"] - box["targetX"]) < max(f["width"], box["width"]) * 0.5 and
                abs(f["targetY"] - box["targetY"]) < max(f["height"], box["height"]) * 0.5
                for f in filtered
            )
            if not overlap:
                filtered.append(box)

        return sorted(filtered[:40], key=lambda b: b["y"])


# Singleton instance
default_ai_detector = PokestopAIDetector()
