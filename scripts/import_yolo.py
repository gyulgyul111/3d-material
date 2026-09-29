"""다른 라벨링 툴(CVAT, Label Studio, Roboflow 등)에서 YOLO 포맷으로 내보낸 데이터를 웹 라벨링 데이터셋으로 가져온다.

ROI 가 적용되지 않은 원본으로 들어가므로, 가져온 뒤 웹에서 [ROI 적용]을 누르세요 (라벨 좌표도 함께 보정됨).
입력 폴더:  SRC/images/*.jpg|png, SRC/labels/*.txt (파일명 동일), 선택: SRC/classes.txt
사용법:     python scripts/import_yolo.py SRC
가져온 뒤 웹의 [라벨링] 탭에서 확인/수정하고 [학습] 탭에서 학습한다.
"""

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import dataset  # noqa: E402
from app.config import PALETTE, load_raw, save_raw  # noqa: E402

IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", type=Path)
    args = ap.parse_args()

    src_classes = args.src / "classes.txt"
    if src_classes.exists():
        names = [l.strip() for l in src_classes.read_text(encoding="utf-8").splitlines() if l.strip()]
        raw = load_raw()
        current = [c["name"] for c in raw.get("classes") or []]
        if dataset.class_counts() and current[: len(names)] != names[: len(current)]:
            sys.exit(f"기존 라벨의 클래스 {current} 와 가져올 클래스 {names} 의 순서가 다릅니다. 웹에서 클래스를 맞춘 뒤 다시 실행하세요.")
        old = {c["name"]: c for c in raw.get("classes") or []}
        raw["classes"] = [old.get(n, {"name": n, "color": PALETTE[i % len(PALETTE)]}) for i, n in enumerate(names)]
        save_raw(raw)
        print(f"클래스: {names}")

    n = unlabeled = 0
    for img in sorted(p for p in (args.src / "images").iterdir() if p.suffix.lower() in IMG_EXT):
        name = dataset.add_raw_image(img.read_bytes(), img.name)
        label = args.src / "labels" / f"{img.stem}.txt"
        if label.exists():
            shutil.copy2(label, dataset.LABELS / f"{Path(name).stem}.txt")
        else:
            unlabeled += 1
        n += 1
    print(f"{n}장 가져옴 (라벨 없는 이미지 {unlabeled}장은 '미라벨'로 표시됨)")
    print("가져온 이미지는 ROI 가 적용되지 않은 상태입니다. 웹의 [라벨링] 탭에서 [ROI 적용]을 누르면 라벨과 함께 보정됩니다.")
    print("주의: 원본 사진에 EXIF 회전 정보가 있었다면 라벨 좌표가 어긋날 수 있으니 [라벨링] 탭에서 확인하세요.")


if __name__ == "__main__":
    main()
