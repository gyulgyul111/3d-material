from __future__ import annotations

import base64
import io
import json
from dataclasses import asdict
from datetime import datetime
from typing import Callable

import cv2
import numpy as np
from PIL import Image, ImageDraw

from .config import ROOT, Config, Rack
from .counting import Detection, RackCount, count_rack, front_plane
from .inventory import purchase_plan
from .rectify import warp_depth, warp_image

HISTORY_FILE = ROOT / "data" / "history.jsonl"

BACKGROUND_COLOR = (148, 163, 184)

# 깊이 색상: 단일 색상(파랑) 순차 램프. 밝을수록 가깝고(앞), 진할수록 멀다(안쪽).
RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]


def _lut() -> np.ndarray:
    stops = np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in RAMP], dtype=np.float32)
    x = np.linspace(0, len(RAMP) - 1, 256)
    return np.stack([np.interp(x, np.arange(len(RAMP)), stops[:, k]) for k in range(3)], axis=1).astype(np.uint8)


LUT = _lut()


class Analyzer:
    """검출기/깊이추정기는 주입 가능하게 해서 테스트에서 가짜로 바꿔 끼운다."""

    def __init__(
        self,
        config: Config,
        detector: Callable[[Image.Image], list[Detection]] | None = None,
        depth: Callable[[Image.Image], np.ndarray] | None = None,
    ):
        self.config = config
        self._detector = detector
        self._depth = depth
        self._injected = detector is not None
        self._detector_key = None

    @property
    def detector(self):
        if self._injected:
            return self._detector
        from .vision import LabelDetector, resolve_weights

        s = self.config.settings
        path = resolve_weights(s.detector_weights)
        # 웹에서 학습이 끝나 가중치 파일이 바뀌면 다시 불러온다
        key = (str(path), path.stat().st_mtime if path.exists() else None, s.detector_conf)
        if self._detector is None or key != self._detector_key:
            self._detector = LabelDetector(s.detector_weights, s.detector_conf)
            self._detector_key = key
        return self._detector

    @property
    def depth(self):
        if self._depth is None:
            from .vision import DepthEstimator

            self._depth = DepthEstimator(self.config.settings.depth_model)
        return self._depth

    def rack(self, rack_id: str, need_roi: bool = True) -> Rack:
        rack = self.config.racks.get(rack_id)
        if rack is None:
            raise KeyError(f"등록되지 않은 랙입니다: {rack_id}")
        if need_roi and not rack.corners:
            raise ValueError(f"{rack_id} 랙의 ROI(네 모서리)가 설정되지 않았습니다. [랙·ROI 설정] 탭에서 먼저 설정하세요.")
        return rack

    def rectify(self, image: Image.Image, rack_id: str) -> Image.Image:
        return warp_image(image, self.rack(rack_id), self.config.settings.rectified_width)

    def analyze(self, image: Image.Image, rack_id: str, save_history: bool = True) -> dict:
        """image 는 EXIF 회전이 반영된 RGB 원본 사진."""
        rack = self.rack(rack_id)
        out_w = self.config.settings.rectified_width

        # 깊이는 원본 사진에서 추정(모델이 자연스러운 원근의 사진에서 가장 정확)한 뒤 같은 변환으로 보정한다
        depth_m = warp_depth(self.depth(image), rack, out_w)
        rectified = warp_image(image, rack, out_w)
        detections = self.detector(rectified)
        classes = self.config.by_name
        counts = count_rack(detections, depth_m, rack, self.config.settings, classes)
        plan = purchase_plan(counts.class_boxes, classes)

        result = {
            "rack_id": rack.id,
            "rack_name": rack.name,
            "analyzed_at": datetime.now().isoformat(timespec="seconds"),
            "classes": [{**asdict(c), "color": classes[c.class_name].color if c.class_name in classes else "#8a8a86"}
                        for c in counts.classes],
            "front_depth_m": [round(d, 3) for d in counts.front.corner_depths_m],
            "labels": [
                {
                    "xyxy": [round(v, 1) for v in lc.detection.xyxy],
                    "conf": round(lc.detection.conf, 3),
                    "class_name": lc.detection.class_name,
                    "recess_mm": round(lc.recess_mm),
                    "rows": lc.rows,
                    "status": lc.status,
                }
                for lc in counts.labels
            ],
            "purchase": [asdict(p) for p in plan],
        }
        if save_history:
            _append_history(result)
        result["annotated_image"] = _encode(annotate(rectified, counts, {n: c.color for n, c in classes.items()}))
        return result

    def depth_view(self, image: Image.Image, rack_id: str | None = None, rectify: bool = True) -> dict:
        """깊이추정 화면용: 2D 깊이맵(보정 이미지 기준)과 3D 재구성용 데이터(원본 원근 기준)."""
        rack = self.rack(rack_id) if rack_id else None
        depth = self.depth(image)
        h, w = depth.shape

        # 3D: 원본 사진의 원근이 있어야 카메라 시야각으로 역투영할 수 있다
        cw = min(320, w)
        ch = max(1, round(h * cw / w))
        cloud_depth = cv2.resize(depth, (cw, ch), interpolation=cv2.INTER_NEAREST)
        cloud_rgb = np.asarray(image.resize((cw, ch), Image.BILINEAR), dtype=np.uint8)

        view_img, view_depth, recess, front = image, depth, None, None
        if rack and rack.corners and rectify:
            out_w = self.config.settings.rectified_width
            view_img = warp_image(image, rack, out_w)
            view_depth = warp_depth(depth, rack, out_w)
            front = front_plane(view_depth, rack)
            recess = front.recess_map_mm(view_depth)

        d_lo, d_hi = np.percentile(view_depth, [2, 98])
        gw = min(320, view_depth.shape[1])
        gh = max(1, round(view_depth.shape[0] * gw / view_depth.shape[1]))
        view = {
            "width": view_depth.shape[1],
            "height": view_depth.shape[0],
            "rgb": _encode(view_img),
            "depth_color": _encode(colorize(view_depth, d_lo, d_hi), fmt="PNG"),
            "depth_range": [round(float(d_lo), 3), round(float(d_hi), 3)],
            "grid": {"w": gw, "h": gh, "depth": _b64(cv2.resize(view_depth, (gw, gh), interpolation=cv2.INTER_NEAREST))},
        }
        if recess is not None:
            r_hi = rack.rack_depth_mm
            view["recess_color"] = _encode(colorize(recess, 0, r_hi), fmt="PNG")
            view["recess_range"] = [0, r_hi]
            view["grid"]["recess"] = _b64(cv2.resize(recess, (gw, gh), interpolation=cv2.INTER_NEAREST))

        return {
            "rectified": recess is not None,
            "view": view,
            "cloud": {
                "w": cw, "h": ch,
                "depth": _b64(cloud_depth),
                "rgb": base64.b64encode(cloud_rgb.tobytes()).decode(),
                "range": [float(np.percentile(cloud_depth, 1)), float(np.percentile(cloud_depth, 99))],
            },
            "rack": None if rack is None else {
                "id": rack.id,
                "corners": rack.corners,
                "rack_depth_mm": rack.rack_depth_mm,
                "front_depth_m": None if front is None else [round(d, 3) for d in front.corner_depths_m],
                "scale": None if front is None else round(front.scale, 4),
            },
        }


def colorize(values: np.ndarray, lo: float, hi: float) -> Image.Image:
    t = np.clip((values - lo) / max(hi - lo, 1e-6), 0, 1)
    return Image.fromarray(LUT[(t * 255).astype(np.uint8)])


def annotate(image: Image.Image, counts: RackCount, colors: dict[str, str]) -> Image.Image:
    """클래스 색으로 라벨 박스를 그린다. 배경(랙 뒤쪽)으로 제외된 라벨은 회색."""
    img = image.copy()
    draw = ImageDraw.Draw(img)
    lw = max(2, img.width // 500)
    for lc in counts.labels:
        name = lc.detection.class_name
        color = BACKGROUND_COLOR if lc.status == "background" else _rgb(colors.get(name, "#8a8a86"))
        x1, y1, x2, y2 = map(int, lc.detection.xyxy)
        draw.rectangle((x1, y1, x2, y2), outline=color, width=lw)
        if lc.status == "counted":
            _tag(draw, (x1, y2 + 1), f"{name} x{lc.rows}", color)
    return img


def _rgb(hex_color: str) -> tuple[int, int, int]:
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def _tag(draw: ImageDraw.ImageDraw, xy, text: str, color):
    left, top, right, bottom = draw.textbbox(xy, text)
    draw.rectangle((left - 2, top - 2, right + 2, bottom + 2), fill=color)
    draw.text(xy, text, fill=(255, 255, 255))


def _encode(img: Image.Image, fmt: str = "JPEG") -> str:
    img = img.copy()
    img.thumbnail((1600, 1600))
    buf = io.BytesIO()
    img.save(buf, format=fmt, **({"quality": 85} if fmt == "JPEG" else {}))
    return f"data:image/{fmt.lower()};base64," + base64.b64encode(buf.getvalue()).decode()


def _b64(arr: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(arr, dtype="<f4").tobytes()).decode()


def _append_history(result: dict):
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    summary = {k: result[k] for k in ("rack_id", "analyzed_at", "classes", "purchase")}
    with HISTORY_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(summary, ensure_ascii=False) + "\n")


def load_history(rack_id: str | None = None, limit: int = 50) -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    rows = [json.loads(line) for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = [r for r in rows if "classes" in r]  # 예전 형식(선반별/품목별) 이력은 건너뜀
    if rack_id:
        rows = [r for r in rows if r["rack_id"] == rack_id]
    return rows[-limit:][::-1]
