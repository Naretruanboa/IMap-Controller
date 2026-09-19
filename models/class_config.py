"""Load the configured YOLO class mapping from dataset/data.yaml."""

from pathlib import Path
from typing import Mapping

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_CONFIG = PROJECT_ROOT / "dataset" / "data.yaml"


def load_class_names(config_path: Path | str = DEFAULT_DATASET_CONFIG) -> dict[int, str]:
    """Return the configured class IDs and names from a dataset manifest."""
    path = Path(config_path)
    config = yaml.safe_load(path.read_text()) or {}
    names = config.get("names", {})
    if isinstance(names, list):
        names = dict(enumerate(names))
    if not isinstance(names, Mapping) or not names:
        raise ValueError(f"Dataset config has no class names: {path}")
    return {int(class_id): str(name) for class_id, name in names.items()}
