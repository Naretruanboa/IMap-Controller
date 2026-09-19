from pathlib import Path

import pytest
import yaml
from PIL import Image

from scripts.audit_dataset import CLASS_NAMES, audit
from scripts.train_yolo import train_yolo


def make_dataset(root: Path, shared=False, missing_gym=False):
    for split, color in [("train", "blue"), ("val", "green")]:
        (root / "images" / split).mkdir(parents=True)
        (root / "labels" / split).mkdir(parents=True)
        Image.new("RGB", (20, 20), color).save(root / "images" / split / "example.png")
        ids = [cid for cid in CLASS_NAMES if not (missing_gym and CLASS_NAMES[cid] == "gym")]
        (root / "labels" / split / "example.txt").write_text("".join(f"{cid} .5 .5 .2 .2\n" for cid in ids))
    config = root / "data.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "path": str(root),
                "train": "images/train",
                "val": "images/train" if shared else "images/val",
                "names": CLASS_NAMES,
            }
        )
    )
    return config


def test_rejects_shared_train_validation_and_missing_gym(tmp_path):
    config = make_dataset(tmp_path, shared=True, missing_gym=True)
    report = audit(config)
    assert not report["ready_for_training"]
    assert any("shared image" in issue for issue in report["blocking_issues"])
    assert any("gym" in issue for issue in report["blocking_issues"])
    model = tmp_path / "active.onnx"
    model.write_bytes(b"original")
    with pytest.raises(ValueError, match="Dataset is not ready"):
        train_yolo(config, output_onnx=model)
    assert model.read_bytes() == b"original"


def test_independent_four_class_splits_pass(tmp_path):
    assert audit(make_dataset(tmp_path))["ready_for_training"]


def test_rejects_identical_image_content_under_different_names(tmp_path):
    config = make_dataset(tmp_path)
    (tmp_path / "images/val/example.png").write_bytes((tmp_path / "images/train/example.png").read_bytes())
    assert not audit(config)["ready_for_training"]
