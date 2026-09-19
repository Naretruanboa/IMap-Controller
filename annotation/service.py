import random
import shutil
from pathlib import Path

import yaml
from PIL import Image

from .database import AnnotationDatabase

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

DEFAULT_COLORS = [
    "#22c55e", "#f97316", "#8b5cf6", "#ef4444", "#3b82f6",
    "#eab308", "#06b6d4", "#ec4899", "#14b8a6", "#f43f5e",
    "#a855f7", "#10b981", "#f59e0b", "#6366f1", "#84cc16",
]


class AnnotationService:
    """Coordinates annotation workflow between filesystem and database."""

    def __init__(self, dataset_path: str, db: AnnotationDatabase):
        self.dataset_path = Path(dataset_path)
        self.images_dir = self.dataset_path / "images"
        self.labels_dir = self.dataset_path / "labels"
        self.drafts_dir = self.dataset_path / "drafts" / "annotations"
        self.completed_images_dir = self.dataset_path / "completed" / "images"
        self.completed_labels_dir = self.dataset_path / "completed" / "labels"
        self.skipped_dir = self.dataset_path / "skipped" / "images"
        self.config_path = self.dataset_path / "data.yaml"
        self.db = db

        for d in [self.images_dir, self.labels_dir, self.drafts_dir,
                  self.completed_images_dir, self.completed_labels_dir, self.skipped_dir]:
            d.mkdir(parents=True, exist_ok=True)

    # ── Class configuration (data.yaml as single source of truth) ─

    def get_classes(self) -> dict[int, str]:
        if not self.config_path.exists():
            return {}
        with open(self.config_path) as f:
            config = yaml.safe_load(f) or {}
        return {int(k): v for k, v in config.get("names", {}).items()}

    def get_class_colors(self) -> dict[int, str]:
        classes = self.get_classes()
        return {cid: DEFAULT_COLORS[cid % len(DEFAULT_COLORS)] for cid in classes}

    def add_class(self, name: str) -> int:
        classes = self.get_classes()
        new_id = (max(classes.keys()) + 1) if classes else 0
        classes[new_id] = name
        self._write_config_names(classes)
        return new_id

    def _write_config_names(self, names: dict[int, str]) -> None:
        config: dict = {}
        if self.config_path.exists():
            with open(self.config_path) as f:
                config = yaml.safe_load(f) or {}
        config["names"] = {int(k): v for k, v in names.items()}
        with open(self.config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=True)

    # ── Image scanning & import ───────────────────────────────────

    def sync_images(self) -> dict:
        """Scan dataset/images/ for new files and import existing labels."""
        added = imported = 0
        for fp in sorted(self.images_dir.iterdir()):
            if fp.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if self.db.get_image_by_filename(fp.name):
                continue

            label_file = self.labels_dir / f"{fp.stem}.txt"
            has_label = label_file.exists() and label_file.stat().st_size > 0
            status = "draft" if has_label else ("no_object" if label_file.exists() else "pending")

            w, h = self._read_dimensions(fp)
            img_id = self.db.upsert_image(fp.name, width=w, height=h, status=status)

            if has_label and w and h:
                bboxes = self._parse_yolo_label(label_file, w, h)
                if bboxes:
                    self.db.save_annotations(img_id, bboxes)
                    imported += 1
            added += 1
        return {"added": added, "imported_labels": imported}

    @staticmethod
    def _read_dimensions(path: Path) -> tuple[int | None, int | None]:
        try:
            with Image.open(path) as img:
                return img.size
        except Exception:
            return None, None

    @staticmethod
    def _parse_yolo_label(label_path: Path, img_w: int, img_h: int) -> list[dict]:
        bboxes = []
        for line in label_path.read_text().strip().splitlines():
            parts = line.strip().split()
            if len(parts) != 5:
                continue
            try:
                cid = int(parts[0])
                cx, cy, bw, bh = (float(v) for v in parts[1:])
            except ValueError:
                continue
            bboxes.append({
                "class_id": cid,
                "x": round((cx - bw / 2) * img_w, 2),
                "y": round((cy - bh / 2) * img_h, 2),
                "width": round(bw * img_w, 2),
                "height": round(bh * img_h, 2),
            })
        return bboxes

    def add_image(self, filename: str, content: bytes) -> dict:
        """Save a new image file and register it in the database."""
        safe_name = Path(filename).name
        suffix = Path(safe_name).suffix.lower()
        if suffix not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported image format: {suffix}. Supported: {', '.join(SUPPORTED_EXTENSIONS)}")

        # Handle filename collisions
        target = self.images_dir / safe_name
        stem = Path(safe_name).stem
        counter = 1
        while target.exists():
            target = self.images_dir / f"{stem}_{counter}{suffix}"
            counter += 1
        safe_name = target.name

        target.write_bytes(content)
        w, h = self._read_dimensions(target)
        img_id = self.db.upsert_image(safe_name, width=w, height=h, status="pending")
        return {"id": img_id, "filename": safe_name, "width": w, "height": h, "status": "pending"}

    def delete_image(self, image_id: int) -> dict:
        """Delete an image, its YOLO labels, completed/skipped files, and DB records."""
        rec = self.db.get_image(image_id)
        if not rec:
            raise ValueError("Image not found")

        filename = rec["filename"]
        self._remove_dataset_copies(filename)

        self.db.delete_image(image_id)
        return {"status": "deleted", "image_id": image_id, "filename": filename}

    def delete_images(self, image_ids: list[int]) -> dict:
        """Delete multiple images, their YOLO labels, completed/skipped files, and DB records."""
        deleted_count = 0
        for image_id in image_ids:
            rec = self.db.get_image(image_id)
            if not rec:
                continue
            filename = rec["filename"]
            self._remove_dataset_copies(filename)
            deleted_count += 1
        self.db.delete_images(image_ids)
        return {"deleted_count": deleted_count, "image_ids": image_ids}

    def get_image_path(self, filename: str) -> Path:
        safe_name = Path(filename).name
        primary = self.images_dir / safe_name
        if primary.is_file():
            return primary
        # Training/export jobs can leave a copy outside the annotation inbox.
        for candidate in self.dataset_path.rglob(safe_name):
            if candidate.is_file() and candidate.parent.name == "images":
                return candidate
        return primary

    def _remove_dataset_copies(self, filename: str) -> None:
        """Remove every image/label copy for a database record."""
        safe_name = Path(filename).name
        stem = Path(safe_name).stem
        candidates = list(self.dataset_path.rglob(safe_name))
        candidates.extend(self.dataset_path.rglob(f"{stem}.txt"))
        for candidate in candidates:
            if not candidate.is_file():
                continue
            if candidate.name != safe_name and candidate.name != f"{stem}.txt":
                continue
            try:
                candidate.unlink()
            except OSError:
                pass

    def ensure_dimensions(self, image_id: int, filename: str) -> tuple[int, int]:
        rec = self.db.get_image(image_id)
        if rec and rec["width"] and rec["height"]:
            return rec["width"], rec["height"]
        w, h = self._read_dimensions(self.images_dir / filename)
        if w and h:
            self.db.upsert_image(filename, width=w, height=h)
        return w or 0, h or 0

    # ── Complete / Skip / Reopen ──────────────────────────────────

    def complete_image(self, image_id: int, bboxes: list | None = None) -> dict:
        rec = self.db.get_image(image_id)
        if not rec:
            raise ValueError("Image not found")

        filename = rec["filename"]
        w, h = self.ensure_dimensions(image_id, filename)
        if not w or not h:
            raise ValueError("Cannot determine image dimensions")

        if bboxes is None:
            bboxes = self.db.get_annotations(image_id)

        # Convert to YOLO format
        yolo_lines = []
        for bb in bboxes:
            cx = (bb["x"] + bb["width"] / 2) / w
            cy = (bb["y"] + bb["height"] / 2) / h
            bw = bb["width"] / w
            bh = bb["height"] / h
            yolo_lines.append(f"{bb['class_id']} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")

        stem = Path(filename).stem
        content = "\n".join(yolo_lines) + ("\n" if yolo_lines else "")

        # Write label to labels/ (overwrite existing)
        (self.labels_dir / f"{stem}.txt").write_text(content)
        # Copy image to completed/images/
        shutil.copy2(self.images_dir / filename, self.completed_images_dir / filename)
        # Write label to completed/labels/
        (self.completed_labels_dir / f"{stem}.txt").write_text(content)

        # Update database
        self.db.save_annotations(image_id, bboxes)
        status = "completed" if bboxes else "no_object"
        self.db.update_status(image_id, status)

        return {"status": status, "label_lines": len(yolo_lines), "filename": filename}

    def skip_image(self, image_id: int) -> None:
        self.db.update_status(image_id, "skipped")

    def reopen_image(self, image_id: int) -> None:
        rec = self.db.get_image(image_id)
        if not rec:
            raise ValueError("Image not found")
        annots = self.db.get_annotations(image_id)
        self.db.update_status(image_id, "draft" if annots else "pending")

    # ── Export dataset with train/val/test split ──────────────────

    def export_dataset(self, train_ratio: float = 0.8, val_ratio: float = 0.1,
                       test_ratio: float = 0.1, seed: int = 42) -> dict:
        export_dir = self.dataset_path / "dataset_export"
        for split in ("train", "val", "test"):
            (export_dir / "images" / split).mkdir(parents=True, exist_ok=True)
            (export_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

        # Gather all completed and no_object images
        all_images: list[dict] = []
        for st in ("completed", "no_object"):
            page = 1
            while True:
                result = self.db.get_images(status=st, page=page, per_page=1000)
                all_images.extend(result["images"])
                if page >= result["total_pages"]:
                    break
                page += 1

        if not all_images:
            return {"error": "No completed images to export", "total": 0}

        rng = random.Random(seed)
        rng.shuffle(all_images)

        n = len(all_images)
        n_train = int(n * train_ratio)
        n_val = int(n * val_ratio)
        splits = {
            "train": all_images[:n_train],
            "val": all_images[n_train:n_train + n_val],
            "test": all_images[n_train + n_val:],
        }

        counts = {}
        for split_name, images in splits.items():
            for img in images:
                stem = Path(img["filename"]).stem
                src_img = self.images_dir / img["filename"]
                src_lbl = self.labels_dir / f"{stem}.txt"
                if src_img.exists():
                    shutil.copy2(src_img, export_dir / "images" / split_name / img["filename"])
                if src_lbl.exists():
                    shutil.copy2(src_lbl, export_dir / "labels" / split_name / f"{stem}.txt")
            counts[split_name] = len(images)

        # Generate data.yaml
        classes = self.get_classes()
        data_yaml = {
            "path": str(export_dir),
            "train": "images/train",
            "val": "images/val",
            "test": "images/test",
            "names": classes,
        }
        with open(export_dir / "data.yaml", "w") as f:
            yaml.dump(data_yaml, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

        return {"export_path": str(export_dir), "counts": counts, "total": n}

    def close(self) -> None:
        self.db.close()
