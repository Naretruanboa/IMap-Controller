"""Service for managing AI dataset collection, auto-labeling, and YOLO training workflows."""

import asyncio
import io
import json
import logging
import os
import re
import sqlite3
import shutil
import subprocess
import sys
import threading
import time
from uuid import uuid4
from datetime import datetime
from pathlib import Path
from typing import Any
from PIL import Image

from services.ai_detector import default_ai_detector
from services.screen_capture import capture_screen, screen_devices
from scripts.auto_label import detect_boxes
from models.class_config import load_class_names

logger = logging.getLogger(__name__)


class AITrainingService:
    """Manages dataset collection, auto-labeling, and YOLO training in the background."""

    def __init__(self, base_dir: Path | str = "."):
        self.base_dir = Path(base_dir).resolve()
        self.dataset_dir = self.base_dir / "dataset"
        self.images_dir = self.dataset_dir / "images"
        self.labels_dir = self.dataset_dir / "labels"
        self.models_dir = self.base_dir / "models"
        self.registry_db_path = self.models_dir / "model_registry.db"
        
        # State tracking
        self.collection_task: asyncio.Task | None = None
        self.collection_status: dict[str, Any] = {
            "running": False,
            "current": 0,
            "total": 0,
            "last_image": "",
            "error": None
        }
        
        self.training_process: subprocess.Popen | None = None
        self.training_status: dict[str, Any] = {
            "running": False,
            "stage": "idle",
            "progress": 0,
            "epoch": 0,
            "total_epochs": 50,
            "error": None,
            "logs": []
        }
        self.log_buffer: list[str] = []
        self._log_lock = threading.Lock()

        self._init_model_registry()
        self._load_active_model()

    def _init_model_registry(self) -> None:
        self.models_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.registry_db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS models (
                    id TEXT PRIMARY KEY,
                    version TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    label_source TEXT NOT NULL,
                    epochs INTEGER,
                    imgsz INTEGER,
                    size_bytes INTEGER NOT NULL,
                    metrics_json TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT NOT NULL DEFAULT 'training'
                );
                CREATE TABLE IF NOT EXISTS model_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_id TEXT NOT NULL REFERENCES models(id) ON DELETE CASCADE,
                    selected_at TEXT NOT NULL,
                    action TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS registry_state (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );
            """)
            count = conn.execute("SELECT COUNT(*) FROM models").fetchone()[0]
            legacy_registry_path = self.models_dir / "registry.json"
            if count == 0 and legacy_registry_path.exists():
                try:
                    legacy = json.loads(legacy_registry_path.read_text())
                except (OSError, json.JSONDecodeError):
                    legacy = {"active_id": None, "models": []}
                for item in legacy.get("models", []):
                    conn.execute(
                        """INSERT OR IGNORE INTO models
                           (id, version, path, created_at, label_source, epochs, imgsz, size_bytes, metrics_json, created_by)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (item["id"], item.get("version", item["id"]), item["path"], item["created_at"],
                         item.get("label_source", "legacy"), item.get("epochs"), item.get("imgsz"),
                         item.get("size_bytes", 0), json.dumps(item.get("metrics", {})), "json-migration"),
                    )
                if legacy.get("active_id"):
                    conn.execute("INSERT OR REPLACE INTO registry_state(key, value) VALUES ('active_id', ?)", (legacy["active_id"],))
            legacy_path = self.models_dir / "pokestop_yolov8n.onnx"
            existing = conn.execute("SELECT 1 FROM models WHERE path = ?", (str(legacy_path.resolve()),)).fetchone()
            if legacy_path.is_file() and not existing:
                conn.execute(
                    """INSERT INTO models (id, version, path, created_at, label_source, imgsz, size_bytes, created_by)
                       VALUES (?, ?, ?, ?, 'legacy', 640, ?, 'legacy-migration')""",
                    ("legacy-yolov8n", "legacy-yolov8n", str(legacy_path.resolve()),
                     datetime.fromtimestamp(legacy_path.stat().st_mtime).isoformat(timespec="seconds"), legacy_path.stat().st_size),
                )
                active = conn.execute("SELECT value FROM registry_state WHERE key = 'active_id'").fetchone()
                if not active:
                    conn.execute("INSERT INTO registry_state(key, value) VALUES ('active_id', 'legacy-yolov8n')")

    @staticmethod
    def _row_to_model(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["metrics"] = json.loads(item.pop("metrics_json") or "{}")
        return item

    def _load_active_model(self) -> None:
        with sqlite3.connect(self.registry_db_path) as conn:
            row = conn.execute(
                "SELECT path FROM models WHERE id = (SELECT value FROM registry_state WHERE key = 'active_id')"
            ).fetchone()
        if row and Path(row[0]).is_file():
            default_ai_detector.model_path = Path(row[0])
            default_ai_detector._load_model()

    def list_models(self) -> dict[str, Any]:
        with sqlite3.connect(self.registry_db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM models ORDER BY created_at DESC").fetchall()
            models = [self._row_to_model(row) for row in rows if Path(row["path"]).is_file()]
            active = conn.execute("SELECT value FROM registry_state WHERE key = 'active_id'").fetchone()
        return {"active_id": active[0] if active else None, "models": models}

    def get_model_detail(self, model_id: str) -> dict[str, Any]:
        with sqlite3.connect(self.registry_db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()
            if not row:
                raise ValueError("Model version not found")
            model = self._row_to_model(row)
            model["usage_history"] = [dict(item) for item in conn.execute(
                "SELECT selected_at, action FROM model_usage WHERE model_id = ? ORDER BY selected_at DESC", (model_id,)
            ).fetchall()]
        return model

    def compare_models(self) -> dict[str, Any]:
        models = self.list_models()["models"]
        comparison = []
        for model in models:
            classes = model.get("metrics", {}).get("classes", {})
            values = [item.get("map50", 0.0) for item in classes.values()]
            comparison.append({
                "id": model["id"],
                "version": model["version"],
                "created_at": model["created_at"],
                "label_source": model["label_source"],
                "mean_map50": round(sum(values) / len(values), 4) if values else None,
                "mean_precision": round(sum(item.get("precision", 0.0) for item in classes.values()) / len(classes), 4) if classes else None,
                "mean_recall": round(sum(item.get("recall", 0.0) for item in classes.values()) / len(classes), 4) if classes else None,
                "classes": classes,
            })
        return {"active_id": self.list_models()["active_id"], "models": comparison}

    def register_model(self, source_path: Path, label_source: str, epochs: int, imgsz: int, metrics: dict[str, Any] | None = None) -> dict[str, Any]:
        now = datetime.now()
        model_id = f"v{now.strftime('%Y%m%d-%H%M%S')}-{uuid4().hex[:6]}"
        version_path = self.models_dir / f"pokestop_yolov8n-{model_id}.onnx"
        shutil.copyfile(source_path, version_path)
        metadata = {
            "id": model_id,
            "version": model_id,
            "path": str(version_path.resolve()),
            "created_at": now.isoformat(timespec="seconds"),
            "label_source": label_source,
            "epochs": epochs,
            "imgsz": imgsz,
            "size_bytes": version_path.stat().st_size,
            "metrics": metrics or {},
        }
        with sqlite3.connect(self.registry_db_path) as conn:
            conn.execute("""INSERT INTO models
                (id, version, path, created_at, label_source, epochs, imgsz, size_bytes, metrics_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (metadata["id"], metadata["version"], metadata["path"], metadata["created_at"], label_source,
                 epochs, imgsz, metadata["size_bytes"], json.dumps(metadata["metrics"])))
            self._activate_in_connection(conn, model_id)
        return metadata

    def _activate_in_connection(self, conn: sqlite3.Connection, model_id: str) -> None:
        selected_at = datetime.now().isoformat(timespec="seconds")
        conn.execute("INSERT OR REPLACE INTO registry_state(key, value) VALUES ('active_id', ?)", (model_id,))
        conn.execute("INSERT INTO model_usage(model_id, selected_at, action) VALUES (?, ?, 'activate')", (model_id, selected_at))

    def use_model(self, model_id: str) -> dict[str, Any]:
        with sqlite3.connect(self.registry_db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()
            model = self._row_to_model(row) if row else None
        if not model or not Path(model["path"]).is_file():
            raise ValueError("Model version not found")
        default_ai_detector.model_path = Path(model["path"])
        default_ai_detector._load_model()
        with sqlite3.connect(self.registry_db_path) as conn:
            self._activate_in_connection(conn, model_id)
        return model

    def delete_model(self, model_id: str) -> dict[str, Any]:
        registry = self.list_models()
        if registry.get("active_id") == model_id:
            raise ValueError("Cannot delete the active model; select another version first")
        model = next((item for item in registry["models"] if item["id"] == model_id), None)
        if not model:
            raise ValueError("Model version not found")
        Path(model["path"]).unlink(missing_ok=True)
        with sqlite3.connect(self.registry_db_path) as conn:
            conn.execute("DELETE FROM models WHERE id = ?", (model_id,))
        return {"deleted": model_id}

    def get_dataset_stats(self) -> dict[str, Any]:
        """Return counts of images, labels, and class distribution."""
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.labels_dir.mkdir(parents=True, exist_ok=True)
        
        images = list(self.images_dir.glob("*.png")) + list(self.images_dir.glob("*.jpg"))
        labels = list(self.labels_dir.glob("*.txt"))
        
        class_names = load_class_names(self.dataset_dir / "data.yaml")
        class_counts = {cid: 0 for cid in class_names}
        total_boxes = 0
        
        for lbl in labels:
            try:
                for line in lbl.read_text().splitlines():
                    parts = line.strip().split()
                    if parts:
                        cid = int(parts[0])
                        class_counts[cid] = class_counts.get(cid, 0) + 1
                        total_boxes += 1
            except Exception:
                pass

        return {
            "total_images": len(images),
            "total_labels": len(labels),
            "total_boxes": total_boxes,
            "class_counts": {
                name: class_counts.get(class_id, 0)
                for class_id, name in class_names.items()
            },
            "collection": self.collection_status
        }

    async def start_collection(self, count: int = 30, interval: float = 2.0, serial: str | None = None) -> dict[str, Any]:
        """Start background screenshot collection from ADB."""
        if self.collection_status["running"]:
            raise ValueError("Collection task is already running")

        if not serial:
            devices = await screen_devices()
            if not devices:
                raise ValueError("No Android/ADB devices connected")
            serial = devices[0]

        self.collection_status = {
            "running": True,
            "current": 0,
            "total": count,
            "last_image": "",
            "error": None
        }

        self.collection_task = asyncio.create_task(self._run_collection(count, interval, serial))
        return {"ok": True, "message": f"Started capturing {count} images from {serial}"}

    async def stop_collection(self) -> dict[str, Any]:
        """Stop running collection task."""
        if self.collection_task and not self.collection_task.done():
            self.collection_task.cancel()
        self.collection_status["running"] = False
        return {"ok": True, "message": "Collection stopped"}

    async def _run_collection(self, count: int, interval: float, serial: str):
        self.images_dir.mkdir(parents=True, exist_ok=True)
        try:
            for i in range(count):
                png_bytes = await capture_screen(serial)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                filename = self.images_dir / f"pogo_{timestamp}.png"
                filename.write_bytes(png_bytes)
                
                self.collection_status["current"] = i + 1
                self.collection_status["last_image"] = filename.name
                
                if i < count - 1:
                    await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.collection_status["error"] = str(exc)
        finally:
            self.collection_status["running"] = False

    def auto_label(self) -> dict[str, Any]:
        """Auto-generate YOLO annotations for all images in dataset/images/."""
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.labels_dir.mkdir(parents=True, exist_ok=True)
        
        images = list(self.images_dir.glob("*.png")) + list(self.images_dir.glob("*.jpg"))
        if not images:
            raise ValueError("No images found in dataset/images/. Collect images first.")

        labeled_count = 0
        skipped_count = 0
        total_boxes = 0

        for img_path in images:
            # Preserve reviewed annotations, including manually corrected Gym labels.
            if (self.labels_dir / f"{img_path.stem}.txt").exists():
                skipped_count += 1
                continue
            try:
                with Image.open(img_path) as img:
                    boxes = detect_boxes(img)
                
                label_path = self.labels_dir / f"{img_path.stem}.txt"
                lines = [
                    f"{b['class_id']} {b['bbox_norm'][0]:.6f} {b['bbox_norm'][1]:.6f} {b['bbox_norm'][2]:.6f} {b['bbox_norm'][3]:.6f}\n"
                    for b in boxes
                ]
                label_path.write_text("".join(lines))
                labeled_count += 1
                total_boxes += len(boxes)
            except Exception as exc:
                logger.error("Failed to auto-label %s: %s", img_path.name, exc)

        # Write data.yaml
        data_yaml = self.dataset_dir / "data.yaml"
        yaml_content = f"""# Pokémon GO PokéStop Object Detection Dataset
path: {self.dataset_dir.resolve()}
train: images
val: images

names:
{chr(10).join(f"  {class_id}: {name}" for class_id, name in load_class_names(self.dataset_dir / 'data.yaml').items())}
"""
        if not data_yaml.exists():
            data_yaml.write_text(yaml_content)

        return {
            "ok": True,
            "labeled_images": labeled_count,
            "skipped_images": skipped_count,
            "total_boxes": total_boxes,
            "data_yaml": str(data_yaml)
        }

    def _prepare_training_dataset(self, label_source: str) -> Path:
        """Build an isolated YOLO dataset from auto or completed annotations."""
        if label_source not in {"auto", "manual"}:
            raise ValueError("label_source must be 'auto' or 'manual'")

        if label_source == "manual":
            source_images = self.dataset_dir / "completed" / "images"
            source_labels = self.dataset_dir / "completed" / "labels"
        else:
            source_images = self.images_dir
            source_labels = self.labels_dir

        images = sorted(
            path for path in source_images.iterdir()
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
            and (source_labels / f"{path.stem}.txt").is_file()
        ) if source_images.exists() else []
        if len(images) < 2:
            raise ValueError(f"At least 2 labeled images are required for {label_source} training")

        output_dir = self.dataset_dir / "training" / label_source
        if output_dir.exists():
            shutil.rmtree(output_dir)
        for split in ("train", "val"):
            (output_dir / "images" / split).mkdir(parents=True, exist_ok=True)
            (output_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

        # Keep the validation set independent and stable between training runs.
        split_at = max(1, int(len(images) * 0.8))
        if len(images) > 1:
            split_at = min(split_at, len(images) - 1)
        splits = {"train": images[:split_at], "val": images[split_at:]}
        for split, split_images in splits.items():
            for image in split_images:
                shutil.copy2(image, output_dir / "images" / split / image.name)
                label_path = source_labels / f"{image.stem}.txt"
                staged_label = output_dir / "labels" / split / f"{image.stem}.txt"
                staged_label.write_text(self._normalize_training_labels(label_path))

        data_yaml = output_dir / "data.yaml"
        data_yaml.write_text(f"""path: {output_dir.resolve()}
train: images/train
val: images/val

names:
{chr(10).join(f"  {class_id}: {name}" for class_id, name in load_class_names(self.dataset_dir / 'data.yaml').items())}
""")
        return data_yaml

    def _normalize_training_labels(self, label_path: Path) -> str:
        """Keep completed labels intact while clipping staged boxes to image bounds."""
        normalized = []
        for line_number, line in enumerate(label_path.read_text().splitlines(), 1):
            parts = line.split()
            if len(parts) != 5:
                raise ValueError(f"Invalid label format: {label_path}:{line_number}")
            try:
                class_id = int(parts[0])
                center_x, center_y, width, height = map(float, parts[1:])
            except ValueError as exc:
                raise ValueError(f"Invalid label values: {label_path}:{line_number}") from exc
            if class_id not in load_class_names(self.dataset_dir / "data.yaml") or width <= 0 or height <= 0:
                raise ValueError(f"Invalid label bounds: {label_path}:{line_number}")

            left = max(0.0, center_x - width / 2)
            top = max(0.0, center_y - height / 2)
            right = min(1.0, center_x + width / 2)
            bottom = min(1.0, center_y + height / 2)
            clipped_width = right - left
            clipped_height = bottom - top
            if clipped_width <= 0 or clipped_height <= 0:
                raise ValueError(f"Label is outside image: {label_path}:{line_number}")
            normalized.append(
                f"{class_id} {(left + right) / 2:.6f} {(top + bottom) / 2:.6f} "
                f"{clipped_width:.6f} {clipped_height:.6f}"
            )
        return "\n".join(normalized) + ("\n" if normalized else "")

    def start_training(self, epochs: int = 50, imgsz: int = 640, label_source: str = "auto") -> dict[str, Any]:
        """Start YOLOv8n model training from auto or manually completed labels."""
        if self.training_status["running"]:
            raise ValueError("Training is already running")

        if label_source == "auto":
            self.auto_label()
        data_yaml = self._prepare_training_dataset(label_source)

        self.training_status = {
            "running": True,
            "stage": "training",
            "progress": 0,
            "epoch": 0,
            "total_epochs": epochs,
            "label_source": label_source,
            "data_yaml": str(data_yaml),
            "error": None,
            "logs": []
        }
        with self._log_lock:
            self.log_buffer = [f"[{datetime.now().strftime('%H:%M:%S')}] Initializing YOLO training ({epochs} epochs, imgsz={imgsz})...\n"]

        thread = threading.Thread(target=self._run_training_thread, args=(epochs, imgsz, data_yaml), daemon=True)
        thread.start()
        return {"ok": True, "message": f"Training started ({epochs} epochs, {label_source} labels)", "label_source": label_source}

    def _append_log(self, text: str):
        # Print to terminal/server log stream (make logs-follow)
        sys.stdout.write(text)
        sys.stdout.flush()

        with self._log_lock:
            # Clean carriage returns from tqdm/progress lines if needed
            lines = text.replace("\r", "\n").splitlines(keepends=True)
            for line in lines:
                if not line:
                    continue
                self.log_buffer.append(line)
            if len(self.log_buffer) > 400:
                self.log_buffer = self.log_buffer[-400:]

    def _run_training_thread(self, epochs: int, imgsz: int, data_yaml: Path):
        target_onnx = self.models_dir / "pokestop_yolov8n.onnx"
        self.models_dir.mkdir(parents=True, exist_ok=True)
        train_script = self.base_dir / "scripts" / "train_yolo.py"

        try:
            self._append_log(f"Spawning YOLOv8 training subprocess ({epochs} epochs, {imgsz}px)...\n")
            
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            
            cmd = [
                sys.executable,
                "-u",
                str(train_script),
                "--data",
                str(data_yaml.resolve()),
                "--epochs",
                str(epochs),
                "--imgsz",
                str(imgsz),
                "--min-precision",
                "0.0",
                "--min-recall",
                "0.0",
                "--output",
                str(target_onnx.resolve())
            ]
            
            self.training_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env
            )

            # Stream stdout line by line
            metrics_path: Path | None = None
            if self.training_process.stdout:
                for line in iter(self.training_process.stdout.readline, ""):
                    if not line:
                        break
                    self._append_log(line)
                    metrics_match = re.search(r"Metrics:\s+(.+)", line)
                    if metrics_match:
                        metrics_path = Path(metrics_match.group(1).strip())
                    if "Epoch " in line or "epoch " in line:
                        self.training_status["stage"] = "training"

            self.training_process.wait()
            ret = self.training_process.returncode

            if ret == 0 and target_onnx.exists():
                metrics = {}
                if metrics_path and metrics_path.is_file():
                    try:
                        metrics = json.loads(metrics_path.read_text())
                    except (OSError, json.JSONDecodeError):
                        pass
                model_metadata = self.register_model(target_onnx, self.training_status["label_source"], epochs, imgsz, metrics)
                self.training_status["stage"] = "completed"
                self.training_status["progress"] = 100
                self.training_status["model_id"] = model_metadata["id"]
                self._append_log(f"\n[SUCCESS] Training finished! Reloading AI detector from {target_onnx.name}...\n")
                
                # Reload detector
                default_ai_detector.model_path = Path(model_metadata["path"])
                default_ai_detector._load_model()
                self._append_log("Active AI detector successfully reloaded with new model weights!\n")
            elif self.training_status["stage"] == "stopped":
                self._append_log("\n[STOPPED] Training was terminated by user.\n")
            else:
                self.training_status["stage"] = "error"
                self.training_status["error"] = f"Subprocess exited with code {ret}"
                self._append_log(f"\n[ERROR] Training failed with exit code {ret}\n")
        except Exception as exc:
            logger.error("Training error: %s", exc)
            self.training_status["stage"] = "error"
            self.training_status["error"] = str(exc)
            self._append_log(f"\n[ERROR] Training exception: {exc}\n")
        finally:
            self.training_status["running"] = False
            self.training_process = None

    def get_training_status(self) -> dict[str, Any]:
        """Return current training state and recent log output."""
        with self._log_lock:
            logs = list(self.log_buffer)
        
        return {
            **self.training_status,
            "is_training": self.training_status["running"],
            "status": self.training_status["stage"],
            "logs": logs,
            "model_ready": default_ai_detector.is_ai_ready,
            "model_path": str(default_ai_detector.model_path) if default_ai_detector.model_path else None
        }

    def stop_training(self) -> dict[str, Any]:
        """Cancel training if running."""
        self.training_status["running"] = False
        self.training_status["stage"] = "stopped"
        if self.training_process and self.training_process.poll() is None:
            try:
                self.training_process.terminate()
                self.training_process.wait(timeout=2)
            except Exception:
                try:
                    self.training_process.kill()
                except Exception:
                    pass
        self._append_log("[CANCELLED] Training process terminated.\n")
        return {"ok": True, "message": "Training stopped"}


# Singleton instance
default_training_service = AITrainingService()
