"""Audit YOLO labels and train/validation separation without modifying annotations."""

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

import yaml

CLASS_NAMES = {
    0: "pokestop_active",
    1: "pokestop_cooldown",
    2: "pokestop_distant",
    3: "gym",
    4: "pokemon",
}
EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def images_for(root: Path, source) -> list[Path]:
    sources = source if isinstance(source, list) else [source]
    images = set()
    for item in sources:
        if not isinstance(item, str):
            raise ValueError("Each dataset split must be an image directory or image-list file")
        path = Path(item)
        path = path if path.is_absolute() else root / path
        if path.is_dir():
            images.update(p.resolve() for p in path.rglob("*") if p.suffix.lower() in EXTENSIONS)
        elif path.suffix == ".txt" and path.is_file():
            for line in path.read_text().splitlines():
                if line.strip():
                    candidate = Path(line.strip())
                    images.add((candidate if candidate.is_absolute() else path.parent / candidate).resolve())
        else:
            raise ValueError(f"Missing split path: {path}")
    return sorted(images)


def label_for(image: Path) -> Path:
    parts = list(image.parts)
    if "images" not in parts:
        raise ValueError(f"Image path has no images directory: {image}")
    parts[len(parts) - 1 - parts[::-1].index("images")] = "labels"
    return Path(*parts).with_suffix(".txt")


def audit(data_yaml: Path) -> dict:
    config = yaml.safe_load(data_yaml.read_text())
    names = config.get("names", {})
    if isinstance(names, list):
        names = dict(enumerate(names))
    root = Path(config.get("path", data_yaml.resolve().parent))
    if not root.is_absolute():
        root = data_yaml.resolve().parent / root
    issues = []
    if names != CLASS_NAMES:
        issues.append("Class mapping must be exactly 0 active, 1 cooldown, 2 distant, 3 gym.")
    splits = {}
    members = {}
    hashes = {}
    for split in ["train", "val"] + (["test"] if config.get("test") else []):
        paths = images_for(root, config.get(split))
        members[split] = set(paths)
        counts, frames = Counter(), Counter()
        missing, invalid = [], []
        hashes[split] = set()
        for image in paths:
            if not image.is_file():
                invalid.append(f"Missing image: {image}")
                continue
            digest = hashlib.sha256(image.read_bytes()).hexdigest()
            hashes[split].add(digest)
            label = label_for(image)
            if not label.exists():
                missing.append(str(label))
                continue
            seen = set()
            for line_no, line in enumerate(label.read_text().splitlines(), 1):
                try:
                    parts = line.split()
                    if len(parts) != 5:
                        raise ValueError()
                    cid = int(parts[0])
                    x, y, w, h = map(float, parts[1:])
                    if cid not in CLASS_NAMES or not (
                        0 < w <= 1.0001
                        and 0 < h <= 1.0001
                        and -0.0001 <= x - w / 2
                        and x + w / 2 <= 1.0001
                        and -0.0001 <= y - h / 2
                        and y + h / 2 <= 1.0001
                    ):
                        raise ValueError()
                    counts[cid] += 1
                    seen.add(cid)
                except ValueError:
                    invalid.append(f"{label}:{line_no}")
            frames.update(seen)
        splits[split] = {
            "images": len(paths),
            "unique_image_hashes": len(hashes[split]),
            "boxes": {name: counts[cid] for cid, name in CLASS_NAMES.items()},
            "images_per_class": {name: frames[cid] for cid, name in CLASS_NAMES.items()},
            "missing_labels": missing,
            "invalid_labels": invalid,
        }
        if not paths or missing or invalid:
            issues.append(f"{split}: empty split, missing labels or invalid annotations.")
        for cid, name in CLASS_NAMES.items():
            if not frames[cid]:
                issues.append(f"{split}: no labeled examples for {name}.")
    for i, first in enumerate(splits):
        for second in list(splits)[i + 1 :]:
            shared_paths = len(members[first] & members[second])
            shared_content = len(hashes[first] & hashes[second])
            if shared_paths or shared_content:
                issues.append(
                    f"{first}/{second}: {shared_paths} shared image paths, {shared_content} shared image contents. Split by capture session, not adjacent frames."
                )
    all_images = members["train"] | members["val"]
    for cid, name in CLASS_NAMES.items():
        annotated = sum(
            any(
                line.split() and line.split()[0] == str(cid) for line in label_for(p).read_text().splitlines()
            )
            for p in all_images
            if label_for(p).is_file()
        )
        if annotated < 2:
            issues.append(
                f"{name}: only {annotated} image(s); cannot provide independent train/validation examples."
            )
    return {
        "data_yaml": str(data_yaml.resolve()),
        "splits": splits,
        "ready_for_training": not issues,
        "blocking_issues": issues,
        "notes": [
            "Valid label syntax does not prove annotation correctness. Review every object, including Gym examples mislabeled as active.",
            "Do not treat adjacent frames of the same scene as independent validation.",
            "Four-class coverage is a minimum check, not a guarantee of model quality.",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("dataset/data.yaml"))
    parser.add_argument("--output", type=Path, default=Path("dataset/audit.json"))
    args = parser.parse_args()
    report = audit(args.data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
