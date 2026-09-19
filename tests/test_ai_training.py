import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import app
from annotation.database import AnnotationDatabase
from annotation.service import AnnotationService
from services.training_service import AITrainingService, default_training_service


def test_dataset_status_endpoint():
    client = TestClient(app)
    response = client.get("/api/ai/dataset/status")
    assert response.status_code == 200
    data = response.json()
    assert "total_images" in data
    assert "total_labels" in data
    assert "class_counts" in data


def test_model_status_endpoint():
    client = TestClient(app)
    response = client.get("/api/ai/model/status")
    assert response.status_code == 200
    data = response.json()
    assert "ready" in data
    assert "model_path" in data


def test_auto_label_endpoint():
    client = TestClient(app)
    response = client.post("/api/ai/dataset/auto-label")
    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert "labeled_images" in data
    assert "total_boxes" in data


def test_training_status_endpoint():
    client = TestClient(app)
    response = client.get("/api/ai/train/status")
    assert response.status_code == 200
    data = response.json()
    assert "stage" in data
    assert "logs" in data


def test_training_dataset_uses_selected_label_source(tmp_path):
    dataset = tmp_path / "dataset"
    for relative_dir in (
        "images",
        "labels",
        "completed/images",
        "completed/labels",
    ):
        (dataset / relative_dir).mkdir(parents=True)

    for index in range(2):
        filename = f"frame_{index}.png"
        Image.new("RGB", (20, 20)).save(dataset / "images" / filename)
        Image.new("RGB", (20, 20)).save(dataset / "completed" / "images" / filename)
        (dataset / "labels" / f"frame_{index}.txt").write_text("0 0.5 0.5 0.2 0.2\n")
        (dataset / "completed" / "labels" / f"frame_{index}.txt").write_text("3 0.5 0.5 0.2 0.2\n")

    service = AITrainingService(tmp_path)
    auto_yaml = service._prepare_training_dataset("auto")
    manual_yaml = service._prepare_training_dataset("manual")

    assert auto_yaml.parent.name == "auto"
    assert manual_yaml.parent.name == "manual"
    assert (auto_yaml.parent / "labels/train/frame_0.txt").read_text().startswith("0 ")
    assert (manual_yaml.parent / "labels/train/frame_0.txt").read_text().startswith("3 ")


def test_manual_training_clips_only_staged_labels(tmp_path):
    label_path = tmp_path / "completed.txt"
    label_path.write_text("2 0.984200 0.367028 0.034467 0.059719\n")

    normalized = AITrainingService._normalize_training_labels(label_path)

    assert normalized == "2 0.983483 0.367028 0.033034 0.059719\n"
    assert label_path.read_text() == "2 0.984200 0.367028 0.034467 0.059719\n"


def test_annotation_finds_and_deletes_copied_image(tmp_path):
    dataset = tmp_path / "dataset"
    source = dataset / "train" / "images"
    source.mkdir(parents=True)
    (dataset / "train" / "labels").mkdir()
    filename = "pogo_20260916_165036_056093.png"
    (source / filename).write_bytes(b"image")
    (dataset / "train" / "labels" / "pogo_20260916_165036_056093.txt").write_text("0 0.5 0.5 0.2 0.2\n")

    database = AnnotationDatabase(str(tmp_path / "annotation.db"))
    service = AnnotationService(str(dataset), database)
    image_id = database.upsert_image(filename)

    assert service.get_image_path(filename) == source / filename
    service.delete_image(image_id)
    assert not (source / filename).exists()
    assert not (dataset / "train" / "labels" / "pogo_20260916_165036_056093.txt").exists()
    database.close()
