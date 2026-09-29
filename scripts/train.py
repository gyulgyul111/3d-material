"""박스라벨 검출 YOLO 학습. 끝나면 best.pt 를 models/box_label.pt 로, 학습 정보를 models/box_label.json 으로 저장한다.

웹 화면의 [학습] 탭이 이 스크립트를 실행한다. 직접 실행할 수도 있다.
  python scripts/train.py --data runs/data.yaml --epochs 100

RTX 2050(4GB) 기준 yolo11s, imgsz 960, batch 4~8 정도가 적당하다. 메모리가 부족하면 batch 를 줄인다.
라벨이 작게 찍히는 경우가 많아 imgsz 를 기본 640보다 크게 잡는다.
"""

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

import yaml
from ultralytics import YOLO

ROOT = Path(__file__).resolve().parent.parent
RUN_DIR = ROOT / "runs" / "box_label"
MODEL_OUT = ROOT / "models" / "box_label.pt"


def resolve_data_yaml(path: Path) -> str:
    """ultralytics 는 상대 path 를 yaml 위치가 아니라 자체 datasets 폴더 기준으로 해석하므로 절대경로로 바꾼다."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if Path(data["path"]).is_absolute():
        return str(path)
    data["path"] = str((path.parent / data["path"]).resolve())
    out = ROOT / "runs" / "data.resolved.yaml"
    out.parent.mkdir(exist_ok=True)
    out.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return str(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "runs" / "data.yaml"))
    ap.add_argument("--model", default="yolo11s.pt")
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--workers", type=int, default=2)
    args = ap.parse_args()

    model = YOLO(args.model)
    model.train(
        data=resolve_data_yaml(Path(args.data)),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        workers=args.workers,
        project=str(RUN_DIR.parent),
        name=RUN_DIR.name,
        exist_ok=True,
        patience=30,
        # 라벨은 글자가 있어 좌우반전하면 안 되고, 랙 사진은 원근 변화가 크다
        fliplr=0.0,
        degrees=5,
        perspective=0.0005,
    )

    trainer = model.trainer
    MODEL_OUT.parent.mkdir(exist_ok=True)
    shutil.copy2(trainer.best, MODEL_OUT)
    meta = {
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "base_model": Path(args.model).name,
        "epochs": trainer.epoch + 1,
        "imgsz": args.imgsz,
        "classes": list(trainer.data["names"].values()),
        "metrics": {k: round(float(v), 4) for k, v in (trainer.metrics or {}).items()},
    }
    MODEL_OUT.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"모델 저장: {MODEL_OUT}")


if __name__ == "__main__":
    main()
