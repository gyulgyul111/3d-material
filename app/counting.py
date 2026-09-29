"""라벨 검출 결과 + 깊이맵(둘 다 ROI 보정 좌표) -> 클래스(부자재)별 박스 수량.

ROI 네 모서리는 랙 앞면 프레임이므로, 보정 이미지의 네 모서리 깊이로 '랙 앞면'을 만든다.
선반은 앞에서부터 꺼내 쓰고 남은 박스는 뒤쪽에 붙어 있다고 가정하면, 보이는 라벨(맨 앞 박스의 앞면)이
랙 앞면보다 얼마나 안쪽에 있는지(recess)로 그 레인에 남은 박스 수를 알 수 있다.

    레인 수량 = floor((랙깊이 - recess + 허용오차) / 박스깊이)   (1 ~ 최대 적재열 사이로 제한)
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .config import ClassInfo, Rack, Settings

CORNER_PATCH = 0.04  # 모서리 깊이를 잴 영역 크기 (이미지 짧은 변 대비)


@dataclass
class Detection:
    xyxy: tuple[float, float, float, float]  # 픽셀 좌표 (보정 이미지 기준)
    conf: float
    class_name: str = "box_label"


@dataclass
class LabelCount:
    detection: Detection
    recess_mm: float
    rows: int  # 이 라벨 뒤쪽까지 포함한 박스 수
    status: str  # counted | background


@dataclass
class ClassCount:
    class_name: str
    visible_labels: int
    boxes: int


@dataclass
class FrontPlane:
    corner_depths_m: list[float]  # 좌상, 우상, 우하, 좌하
    scale: float  # 카메라~랙 앞 실측 거리로 구한 깊이 보정 배율

    def at(self, u: np.ndarray | float, v: np.ndarray | float):
        """보정 이미지의 정규화 좌표(u, v)에서 랙 앞면 깊이(m)."""
        tl, tr, br, bl = self.corner_depths_m
        return (1 - u) * (1 - v) * tl + u * (1 - v) * tr + u * v * br + (1 - u) * v * bl

    def recess_map_mm(self, depth: np.ndarray) -> np.ndarray:
        h, w = depth.shape
        u = (np.arange(w) + 0.5) / w
        v = (np.arange(h) + 0.5) / h
        return (depth - self.at(u[None, :], v[:, None])) * self.scale * 1000


@dataclass
class RackCount:
    rack_id: str
    labels: list[LabelCount]
    classes: list[ClassCount]
    front: FrontPlane
    class_boxes: dict[str, int] = field(default_factory=dict)


def sample_depth(depth: np.ndarray, xyxy, shrink: float = 0.25, pct: float = 50) -> float:
    """bbox 가장자리(배경이 섞이는 부분)를 잘라내고 깊이의 백분위수를 구한다."""
    h, w = depth.shape
    x1, y1, x2, y2 = xyxy
    dx, dy = (x2 - x1) * shrink, (y2 - y1) * shrink
    xa, xb = int(np.clip(x1 + dx, 0, w - 1)), int(np.clip(x2 - dx, 1, w))
    ya, yb = int(np.clip(y1 + dy, 0, h - 1)), int(np.clip(y2 - dy, 1, h))
    patch = depth[ya:max(yb, ya + 1), xa:max(xb, xa + 1)]
    return float(np.percentile(patch, pct))


def front_plane(depth: np.ndarray, rack: Rack) -> FrontPlane:
    h, w = depth.shape
    p = max(3, int(min(w, h) * CORNER_PATCH))
    boxes = [(0, 0, p, p), (w - p, 0, w, p), (w - p, h - p, w, h), (0, h - p, p, h)]
    # 모서리 영역에서 가장 가까운 쪽(랙 프레임)을 잡기 위해 하위 20% 사용
    corner = [sample_depth(depth, b, shrink=0, pct=20) for b in boxes]
    mean = float(np.mean(corner))
    scale = rack.camera_to_front_m / mean if rack.camera_to_front_m and mean > 0 else 1.0
    return FrontPlane(corner, scale)


def rows_from_recess(recess_mm: float, rack_depth_mm: float, box_depth_mm: float, tolerance_ratio: float) -> int:
    max_rows = max(1, int(rack_depth_mm // box_depth_mm))
    remaining = rack_depth_mm - max(recess_mm, 0.0)
    n = int((remaining + tolerance_ratio * box_depth_mm) // box_depth_mm)
    return int(np.clip(n, 1, max_rows))


def count_rack(
    detections: list[Detection],
    depth_m: np.ndarray,
    rack: Rack,
    settings: Settings,
    classes: dict[str, ClassInfo],
) -> RackCount:
    h, w = depth_m.shape
    front = front_plane(depth_m, rack)

    labels: list[LabelCount] = []
    for det in detections:
        x1, y1, x2, y2 = det.xyxy
        c = classes.get(det.class_name)
        box_depth = c.box_depth_mm if c else settings.default_box_depth_mm
        front_d = front.at((x1 + x2) / 2 / w, (y1 + y2) / 2 / h)
        recess_mm = (sample_depth(depth_m, det.xyxy) - front_d) * front.scale * 1000

        if recess_mm > rack.rack_depth_mm + settings.background_margin_mm:
            labels.append(LabelCount(det, recess_mm, 0, "background"))
            continue
        rows = rows_from_recess(recess_mm, rack.rack_depth_mm, box_depth, settings.depth_tolerance_ratio)
        labels.append(LabelCount(det, recess_mm, rows, "counted"))

    boxes: dict[str, int] = defaultdict(int)
    visible: dict[str, int] = defaultdict(int)
    for name in rack.classes:
        boxes[name] += 0  # 이 랙의 부자재는 라벨이 하나도 안 보여도 재고 0으로 표시
    for lc in labels:
        if lc.status == "counted":
            boxes[lc.detection.class_name] += lc.rows
            visible[lc.detection.class_name] += 1
    order = {n: i for i, n in enumerate(classes)}
    names = sorted(boxes, key=lambda n: order.get(n, 1_000_000))
    counts = [ClassCount(n, visible[n], boxes[n]) for n in names]
    return RackCount(rack.id, labels, counts, front, {n: boxes[n] for n in names})
