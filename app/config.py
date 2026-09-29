from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config" / "racks.yaml"

Point = tuple[float, float]

# 클래스 기본 색상: 검증된 범주형 팔레트를 고정 순서로 배정 (사용자가 바꿀 수 있음)
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass
class Rack:
    id: str
    name: str
    # 랙 앞면 프레임의 네 모서리 (이미지 대비 비율). 좌상, 우상, 우하, 좌하 순서. None 이면 ROI 미설정.
    corners: list[Point] | None = None
    rack_depth_mm: float = 1000
    # 랙 앞면의 실제 폭/높이. 보정 이미지의 가로세로 비율에 쓴다. 없으면 모서리 길이로 추정.
    width_mm: float | None = None
    height_mm: float | None = None
    camera_to_front_m: float | None = None
    # 이 랙에 보관하는 부자재(클래스). 라벨이 하나도 안 보여도 재고 0으로 표시된다.
    classes: list[str] = field(default_factory=list)


@dataclass
class ClassInfo:
    """라벨링 클래스 = 부자재. 클래스 순서(인덱스)가 YOLO 라벨 파일의 클래스 번호다."""

    name: str
    color: str
    safety_stock: int = 0  # 적정재고(박스). 현재고가 이 값 이하이면 구매 필요
    target_stock: int = 0  # 발주 후 채울 목표재고
    order_lot: int = 1  # 발주 단위(배수)
    units_per_box: int = 1  # 박스당 입수(EA)
    box_depth_mm: float = 300  # 가려진 박스 수 추정용
    description: str = ""

    @property
    def managed(self) -> bool:
        """적정재고/목표재고가 둘 다 0이면 재고 기준이 없는 클래스로 보고 구매 판단을 하지 않는다."""
        return self.safety_stock > 0 or self.target_stock > 0


@dataclass
class Settings:
    detector_weights: str = "models/box_label.pt"
    detector_conf: float = 0.35
    depth_model: str = "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf"
    background_margin_mm: float = 150
    depth_tolerance_ratio: float = 0.3
    default_box_depth_mm: float = 300
    rectified_width: int = 1280


@dataclass
class Config:
    settings: Settings
    racks: dict[str, Rack]
    classes: list[ClassInfo] = field(default_factory=list)

    @property
    def class_names(self) -> list[str]:
        return [c.name for c in self.classes]

    @property
    def by_name(self) -> dict[str, ClassInfo]:
        return {c.name: c for c in self.classes}


def order_corners(pts: list[Point]) -> list[Point]:
    """네 점을 좌상, 우상, 우하, 좌하 순서로 정렬한다. (찍은 순서가 달라도 같은 결과)"""
    cx = sum(p[0] for p in pts) / 4
    cy = sum(p[1] for p in pts) / 4
    by_angle = sorted(pts, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))  # 화면 좌표: 좌상부터 시계방향이 되도록
    start = min(range(4), key=lambda i: by_angle[i][0] + by_angle[i][1])
    return by_angle[start:] + by_angle[:start]


def _corners(v) -> list[Point] | None:
    if v is None:
        return None
    if len(v) != 4 or any(len(p) != 2 for p in v):
        raise ValueError(f"ROI 모서리는 [x, y] 네 점이어야 합니다: {v}")
    pts = [(float(x), float(y)) for x, y in v]
    if any(not (0 <= c <= 1) for p in pts for c in p):
        raise ValueError("ROI 모서리 좌표는 0~1 비율이어야 합니다.")
    pts = order_corners(pts)
    # 볼록 사각형인지 확인 (꼬인 사각형이면 원근 보정이 뒤집힘)
    signs = []
    for i in range(4):
        (x1, y1), (x2, y2), (x3, y3) = pts[i], pts[(i + 1) % 4], pts[(i + 2) % 4]
        signs.append((x2 - x1) * (y3 - y2) - (y2 - y1) * (x3 - x2))
    if not (all(s > 1e-6 for s in signs) or all(s < -1e-6 for s in signs)):
        raise ValueError("ROI 네 점이 볼록한 사각형을 이루지 않습니다. 랙의 네 모서리를 다시 찍어주세요.")
    return pts


def _opt_float(v) -> float | None:
    return float(v) if v not in (None, "", 0) else None


def load_raw(path: str | Path = DEFAULT_CONFIG) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}


def save_raw(raw: dict, path: str | Path = DEFAULT_CONFIG) -> Config:
    """검증을 통과한 경우에만 저장한다. 웹에서 저장하면 YAML 주석은 사라진다."""
    cfg = parse_config(raw)
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        "# 웹 화면에서 저장된 설정입니다. 직접 수정해도 됩니다.\n"
        + yaml.safe_dump(raw, allow_unicode=True, sort_keys=False, default_flow_style=None),
        encoding="utf-8",
    )
    tmp.replace(path)
    return cfg


def load_config(path: str | Path = DEFAULT_CONFIG) -> Config:
    return parse_config(load_raw(path))


def parse_config(raw: dict) -> Config:
    known = Settings.__dataclass_fields__
    settings = Settings(**{k: v for k, v in (raw.get("settings") or {}).items() if k in known})

    racks = {}
    for rack_id, r in (raw.get("racks") or {}).items():
        rack = Rack(
            id=rack_id,
            name=r.get("name", rack_id),
            corners=_corners(r.get("corners")),
            rack_depth_mm=float(r.get("rack_depth_mm", 1000)),
            width_mm=_opt_float(r.get("width_mm")),
            height_mm=_opt_float(r.get("height_mm")),
            camera_to_front_m=_opt_float(r.get("camera_to_front_m")),
            classes=[str(i) for i in (r.get("classes") or [])],
        )
        if rack.rack_depth_mm <= 0:
            raise ValueError(f"{rack_id}: 랙 깊이는 0보다 커야 합니다.")
        racks[rack_id] = rack

    classes = []
    for i, c in enumerate(raw.get("classes") or [{"name": "box_label"}]):
        info = ClassInfo(
            name=str(c["name"]).strip(),
            color=str(c.get("color") or PALETTE[i % len(PALETTE)]),
            safety_stock=int(c.get("safety_stock", 0)),
            target_stock=int(c.get("target_stock", c.get("safety_stock", 0))),
            order_lot=max(1, int(c.get("order_lot", 1))),
            units_per_box=max(1, int(c.get("units_per_box", 1))),
            box_depth_mm=float(c.get("box_depth_mm", settings.default_box_depth_mm)),
            description=str(c.get("description") or ""),
        )
        if not info.name:
            raise ValueError("클래스 이름은 비어 있을 수 없습니다.")
        if not COLOR_RE.match(info.color):
            raise ValueError(f"{info.name}: 색상은 #RRGGBB 형식이어야 합니다.")
        if info.box_depth_mm <= 0:
            raise ValueError(f"{info.name}: 박스 깊이는 0보다 커야 합니다.")
        if min(info.safety_stock, info.target_stock) < 0:
            raise ValueError(f"{info.name}: 재고 기준은 0 이상이어야 합니다.")
        classes.append(info)
    names = [c.name for c in classes]
    if len(set(names)) != len(names):
        raise ValueError(f"클래스 이름이 중복됩니다: {names}")

    return Config(settings=settings, racks=racks, classes=classes)
