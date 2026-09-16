import pytest
from fastapi.testclient import TestClient
from app import app
from services.training_service import default_training_service


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
