"""라벨링 데이터셋 (YOLO 포맷).

datasets/box_label/
  images/<name>.jpg     # 랙 ROI 로 원근 보정된 이미지 (라벨링/학습에 쓰는 이미지)
  labels/<name>.txt     # 없으면 미라벨, 비어 있으면 '박스 없음'으로 확인된 이미지(배경 학습용)
  originals/<name>.jpg  # 보정 전 원본. ROI 가 바뀌면 여기서 다시 보정하고 라벨 좌표도 함께 옮긴다
  roi.json              # 이미지별로 적용한 ROI {name: {rack_id, corners, size, orig_size}}

roi.json 에 없는 이미지는 ROI 가 적용되지 않은 원본(예전 방식)이며 학습에서 제외된다.

클래스 목록(이름, 색상, 재고 기준)은 config/racks.yaml 의 classes 에 있고, 그 순서가 클래스 번호다.
"""

from __future__ import annotations

import io
import json
import re
import uuid
import zlib
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml
from PIL import Image, ImageOps

from .config import ROOT, Rack
from .rectify import homography_for, output_size, transform_boxes

DATASET_DIR = ROOT / "datasets" / "box_label"
IMAGES = DATASET_DIR / "images"
LABELS = DATASET_DIR / "labels"
ORIGINALS = DATASET_DIR / "originals"
ROI_FILE = DATASET_DIR / "roi.json"
NAME_RE = re.compile(r"^[\w\-]+\.jpg$")
MIN_BOX = 0.002  # 이보다 작은(정규화 기준) 박스는 실수로 클릭한 것으로 보고 버림


def _ensure_dirs():
    IMAGES.mkdir(parents=True, exist_ok=True)
    LABELS.mkdir(parents=True, exist_ok=True)
    ORIGINALS.mkdir(parents=True, exist_ok=True)


def load_roi() -> dict:
    return json.loads(ROI_FILE.read_text(encoding="utf-8")) if ROI_FILE.exists() else {}


def _save_roi(meta: dict):
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    tmp = ROI_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ROI_FILE)


def remap_labels(mapping: dict[int, int | None]) -> int:
    """클래스 삭제/순서 변경 시 모든 라벨 파일의 클래스 번호를 바꾼다. None 으로 매핑된 클래스의 박스는 지운다.

    반환값: 지운 박스 수
    """
    removed = 0
    if not LABELS.exists():
        return 0
    for lp in LABELS.glob("*.txt"):
        out = []
        for line in lp.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) != 5:
                continue
            new = mapping.get(int(parts[0]))
            if new is None:
                removed += 1
                continue
            out.append(" ".join([str(new), *parts[1:]]))
        lp.write_text("\n".join(out) + ("\n" if out else ""), encoding="utf-8")
    (DATASET_DIR / "labels.cache").unlink(missing_ok=True)  # 학습 도구의 라벨 캐시 무효화
    return removed


def check_name(name: str) -> str:
    if not NAME_RE.match(name) or not (IMAGES / name).exists():
        raise FileNotFoundError(f"이미지가 없습니다: {name}")
    return name


def image_path(name: str) -> Path:
    return IMAGES / check_name(name)


def _label_path(name: str) -> Path:
    return LABELS / f"{Path(name).stem}.txt"


def normalize_image(data: bytes) -> Image.Image:
    """휴대폰 사진의 EXIF 회전을 픽셀에 반영한다. (학습 도구가 EXIF 회전을 무시하면 라벨 좌표가 어긋남)"""
    return ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")


def _new_name(original_name: str) -> str:
    stem = re.sub(r"[^\w\-]", "_", Path(original_name).stem)[:40] or "img"
    return f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}_{stem}.jpg"


def add_image(original: Image.Image, original_name: str, rack: Rack, out_w: int) -> str:
    """원본을 보관하고 랙 ROI 로 보정한 이미지를 라벨링용으로 추가한다."""
    if not rack.corners:
        raise ValueError(f"{rack.id} 랙의 ROI 가 설정되지 않았습니다.")
    _ensure_dirs()
    name = _new_name(original_name)
    original.save(ORIGINALS / name, format="JPEG", quality=95)
    meta = load_roi()
    meta[name] = _warp_and_save(original, name, rack, out_w)
    _save_roi(meta)
    return name


def add_raw_image(data: bytes | Image.Image, original_name: str = "") -> str:
    """ROI 없이 원본 그대로 추가한다 (외부 라벨 데이터 가져오기용). 나중에 [ROI 적용]으로 보정해야 학습에 쓰인다."""
    _ensure_dirs()
    img = data if isinstance(data, Image.Image) else normalize_image(data)
    name = _new_name(original_name)
    img.save(IMAGES / name, format="JPEG", quality=95)
    return name


def _warp_and_save(original: Image.Image, name: str, rack: Rack, out_w: int) -> dict:
    import cv2
    import numpy as np

    w, h = original.size
    size = output_size(rack, w, h, out_w)
    M = homography_for(rack.corners, w, h, size)
    arr = cv2.warpPerspective(np.asarray(original), M, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    Image.fromarray(arr).save(IMAGES / name, format="JPEG", quality=95)
    return {"rack_id": rack.id, "corners": [list(p) for p in rack.corners], "size": list(size), "orig_size": [w, h]}


def apply_roi(name: str, rack: Rack, out_w: int) -> int:
    """이미 있는 이미지에 랙 ROI 를 (다시) 적용한다. 라벨 박스 좌표도 같은 변환으로 옮긴다.

    - ROI 미적용 이미지: 지금 이미지를 원본으로 보관하고 보정한다.
    - 이미 보정된 이미지: 보관한 원본에서 새 ROI 로 다시 보정한다 (예전 보정을 되돌린 뒤 새로 적용).
    반환값: ROI 밖으로 나가서 지운 박스 수
    """
    import numpy as np

    check_name(name)
    if not rack.corners:
        raise ValueError(f"{rack.id} 랙의 ROI 가 설정되지 않았습니다.")
    _ensure_dirs()
    meta = load_roi()
    info = meta.get(name)
    orig_path = ORIGINALS / name
    labels = read_labels(name)
    if info is None:
        (IMAGES / name).replace(orig_path)  # ROI 미적용 이미지는 지금 이미지가 곧 원본
    elif not orig_path.exists():
        raise FileNotFoundError(f"{name} 의 원본이 없어 ROI 를 다시 적용할 수 없습니다.")
    with Image.open(orig_path) as im:
        original = im.convert("RGB")
    w, h = original.size

    # 현재 라벨 좌표(지금 이미지 기준 비율) -> 원본 픽셀
    if info is None:
        boxes_orig = [(b["x1"] * w, b["y1"] * h, b["x2"] * w, b["y2"] * h) for b in labels["boxes"]]
    else:
        W0, H0 = info["size"]
        M0 = homography_for([tuple(p) for p in info["corners"]], *info["orig_size"], (W0, H0))
        boxes_orig = transform_boxes([(b["x1"] * W0, b["y1"] * H0, b["x2"] * W0, b["y2"] * H0) for b in labels["boxes"]],
                                     np.linalg.inv(M0))

    meta[name] = _warp_and_save(original, name, rack, out_w)
    W, H = meta[name]["size"]
    M = homography_for(rack.corners, w, h, (W, H))
    moved = transform_boxes(boxes_orig, M)

    dropped = 0
    new_boxes = []
    for b, (x1, y1, x2, y2) in zip(labels["boxes"], moved):
        area = max(x2 - x1, 0) * max(y2 - y1, 0)
        cx1, cy1, cx2, cy2 = max(x1, 0), max(y1, 0), min(x2, W), min(y2, H)
        visible = max(cx2 - cx1, 0) * max(cy2 - cy1, 0)
        # 절반 이상이 ROI 밖으로 나간 박스는 버린다 (보정 이미지에 라벨이 거의 안 보임)
        if area <= 0 or visible / area < 0.5:
            dropped += 1
            continue
        new_boxes.append({"cls": b["cls"], "x1": cx1 / W, "y1": cy1 / H, "x2": cx2 / W, "y2": cy2 / H})
    if labels["labeled"]:
        _write_label_file(name, new_boxes)
    _save_roi(meta)
    (DATASET_DIR / "labels.cache").unlink(missing_ok=True)
    return dropped


def roi_state(info: dict | None, racks: dict[str, Rack], out_w: int) -> str:
    """raw: ROI 미적용 / ok: 현재 랙 ROI 와 같음 / stale: 랙 ROI 가 바뀌었거나 랙이 삭제됨"""
    if info is None:
        return "raw"
    rack = racks.get(info["rack_id"])
    if rack is None or not rack.corners:
        return "stale"
    same_corners = all(abs(a - b) < 1e-4 for p, q in zip(rack.corners, info["corners"]) for a, b in zip(p, q))
    same_size = list(output_size(rack, *info["orig_size"], out_w)) == list(info["size"])
    return "ok" if same_corners and same_size else "stale"


def delete_image(name: str):
    image_path(name).unlink()
    _label_path(name).unlink(missing_ok=True)
    (ORIGINALS / name).unlink(missing_ok=True)
    meta = load_roi()
    if meta.pop(name, None) is not None:
        _save_roi(meta)


def read_labels(name: str) -> dict:
    check_name(name)
    return _read_label_file(name)


def _read_label_file(name: str) -> dict:
    path = _label_path(name)
    if not path.exists():
        return {"labeled": False, "boxes": []}
    boxes = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        c, cx, cy, w, h = int(parts[0]), *map(float, parts[1:])
        boxes.append({"cls": c, "x1": cx - w / 2, "y1": cy - h / 2, "x2": cx + w / 2, "y2": cy + h / 2})
    return {"labeled": True, "boxes": boxes}


def write_labels(name: str, boxes: list[dict], n_cls: int) -> int:
    check_name(name)
    lines = []
    for b in boxes:
        c = int(b["cls"])
        if not 0 <= c < n_cls:
            raise ValueError(f"없는 클래스 인덱스입니다: {c}")
        x1, x2 = sorted(min(max(float(b[k]), 0.0), 1.0) for k in ("x1", "x2"))
        y1, y2 = sorted(min(max(float(b[k]), 0.0), 1.0) for k in ("y1", "y2"))
        if x2 - x1 < MIN_BOX or y2 - y1 < MIN_BOX:
            continue
        lines.append(f"{c} {(x1 + x2) / 2:.6f} {(y1 + y2) / 2:.6f} {x2 - x1:.6f} {y2 - y1:.6f}")
    _ensure_dirs()
    _label_path(name).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)


def _write_label_file(name: str, boxes: list[dict]):
    lines = [f"{b['cls']} {(b['x1'] + b['x2']) / 2:.6f} {(b['y1'] + b['y2']) / 2:.6f} {b['x2'] - b['x1']:.6f} {b['y2'] - b['y1']:.6f}"
             for b in boxes if b["x2"] - b["x1"] >= MIN_BOX and b["y2"] - b["y1"] >= MIN_BOX]
    _label_path(name).write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def list_images(racks: dict[str, Rack] | None = None, out_w: int = 1280) -> list[dict]:
    """racks 를 주면 ROI 상태(ok/stale/raw)까지 판단한다. 없으면 적용 여부(ok/raw)만."""
    if not IMAGES.exists():
        return []
    meta = load_roi()
    out = []
    for p in sorted(IMAGES.glob("*.jpg")):
        lp = _label_path(p.name)
        n = len([l for l in lp.read_text(encoding="utf-8").splitlines() if l.strip()]) if lp.exists() else 0
        info = meta.get(p.name)
        roi = roi_state(info, racks, out_w) if racks is not None else ("ok" if info else "raw")
        out.append({"name": p.name, "labeled": lp.exists(), "boxes": n, "roi": roi, "rack_id": info and info["rack_id"]})
    return out


def class_counts() -> Counter:
    counts: Counter = Counter()
    if LABELS.exists():
        for lp in LABELS.glob("*.txt"):
            for line in lp.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    counts[int(line.split()[0])] += 1
    return counts


def stats(names: list[str], racks: dict[str, Rack] | None = None, out_w: int = 1280) -> dict:
    imgs = list_images(racks, out_w)
    labeled = [i for i in imgs if i["labeled"]]
    counts = class_counts()
    return {
        "images": len(imgs),
        "labeled": len(labeled),
        "trainable": sum(1 for i in labeled if i["roi"] != "raw"),
        "raw": sum(1 for i in imgs if i["roi"] == "raw"),
        "stale": sum(1 for i in imgs if i["roi"] == "stale"),
        "unlabeled": len(imgs) - len(labeled),
        "background": sum(1 for i in labeled if i["boxes"] == 0),
        "boxes": sum(counts.values()),
        "per_class": [{"cls": i, "name": n, "boxes": counts.get(i, 0)} for i, n in enumerate(names)],
    }


def _is_val(name: str, val_ratio: float) -> bool:
    # 파일명 해시로 나눠서 이미지가 추가돼도 기존 이미지의 train/val 소속이 바뀌지 않게 한다
    return (zlib.crc32(name.encode()) % 1000) < val_ratio * 1000


def write_data_yaml(out_dir: Path, names: list[str], val_ratio: float = 0.2) -> Path:
    # ROI 가 적용되지 않은 원본 이미지는 추론 때 보는 이미지와 모양이 달라 학습에서 뺀다
    labeled = [i["name"] for i in list_images() if i["labeled"] and i["roi"] != "raw"]
    if len(labeled) < 2:
        raise ValueError(f"학습에 쓸 수 있는(ROI 적용 + 라벨링된) 이미지가 {len(labeled)}장입니다. "
                         "최소 2장(권장 200장 이상) 필요합니다.")
    val = [n for n in labeled if _is_val(n, val_ratio)]
    train = [n for n in labeled if n not in val]
    if not val:
        val, train = train[-1:], train[:-1]
    if not train:
        train, val = val[:-1], val[-1:]

    out_dir.mkdir(parents=True, exist_ok=True)
    for split, files in (("train", train), ("val", val)):
        (out_dir / f"{split}.txt").write_text("\n".join(str(IMAGES / n) for n in files) + "\n", encoding="utf-8")
    data = {
        "path": str(DATASET_DIR),
        "train": str(out_dir / "train.txt"),
        "val": str(out_dir / "val.txt"),
        "names": dict(enumerate(names)),
    }
    path = out_dir / "data.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path
