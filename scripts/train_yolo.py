#!/usr/bin/env python3
"""Train four classes on independent splits, evaluate best weights, then export a candidate."""

import argparse
import json
import shutil
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.audit_dataset import CLASS_NAMES, audit


def train_yolo(
    data_yaml: Path,
    epochs: int = 50,
    imgsz: int = 640,
    output_onnx: Path | None = None,
    min_precision: float = 0.80,
    min_recall: float = 0.70,
):
    report = audit(data_yaml)
    report_path = data_yaml.parent / "audit.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    if not report["ready_for_training"]:
        raise ValueError("Dataset is not ready:\n- " + "\n- ".join(report["blocking_issues"]))
    if epochs < 1 or imgsz != 640:
        raise ValueError("Use epochs >= 1 and imgsz=640 (the installed detector expects 640×640 input).")
    from ultralytics import YOLO

    model = YOLO("yolov8n.pt")
    results = model.train(
        data=str(data_yaml.resolve()),
        epochs=epochs,
        imgsz=imgsz,
        device="cpu",
        seed=42,
        deterministic=True,
        workers=0,
        patience=15,
    )
    best_pt = Path(results.save_dir) / "weights" / "best.pt"
    if not best_pt.is_file():
        raise RuntimeError("Training did not produce best.pt")
    best = YOLO(str(best_pt))
    metrics = best.val(data=str(data_yaml.resolve()), split="val", imgsz=imgsz, device="cpu", workers=0)
    per_class = {
        name: {"precision": 0.0, "recall": 0.0, "map50": 0.0, "map50_95": 0.0}
        for name in CLASS_NAMES.values()
    }
    for index, cid in enumerate(metrics.box.ap_class_index):
        precision, recall, ap50, ap = metrics.box.class_result(index)
        per_class[CLASS_NAMES[int(cid)]] = dict(
            zip(["precision", "recall", "map50", "map50_95"], map(float, [precision, recall, ap50, ap]))
        )
    passed = all(
        item["precision"] >= min_precision and item["recall"] >= min_recall for item in per_class.values()
    )
    evaluation = {
        "weights": str(best_pt),
        "classes": per_class,
        "passed": passed,
        "minimum_precision": min_precision,
        "minimum_recall": min_recall,
        "note": "Validation metrics are not a guarantee of performance on new maps or conditions.",
    }
    metrics_path = Path(results.save_dir) / "four_class_metrics.json"
    metrics_path.write_text(json.dumps(evaluation, indent=2) + "\n")
    print(json.dumps(evaluation, indent=2), flush=True)
    # Always export a candidate beside the run; never overwrite the active model on failed evaluation.
    exported = Path(best.export(format="onnx", imgsz=imgsz))
    print(f"Candidate model: {exported}\nMetrics: {metrics_path}", flush=True)
    if not passed:
        raise ValueError("Candidate did not meet all four class thresholds; active model was not replaced.")
    if output_onnx:
        output_onnx.parent.mkdir(parents=True, exist_ok=True)
        temporary = output_onnx.with_suffix(".onnx.pending")
        shutil.copyfile(exported, temporary)
        temporary.replace(output_onnx)
        print(f"Model installed to: {output_onnx.resolve()}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", "-d", type=Path, default=Path("dataset/data.yaml"))
    parser.add_argument("--epochs", "-e", type=int, default=50)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument(
        "--output", "-o", type=Path, default=None, help="Install only after every class passes evaluation"
    )
    parser.add_argument("--min-precision", type=float, default=0.80)
    parser.add_argument("--min-recall", type=float, default=0.70)
    args = parser.parse_args()
    if not (0 <= args.min_precision <= 1 and 0 <= args.min_recall <= 1):
        parser.error("Metric thresholds must be between 0 and 1")
    try:
        train_yolo(args.data, args.epochs, args.imgsz, args.output, args.min_precision, args.min_recall)
    except (ValueError, ImportError) as exc:
        print(f"Training blocked: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
