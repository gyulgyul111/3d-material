"""랙 ROI(네 모서리) -> 정면 사각형 이미지로 원근 보정.

학습/라벨링/추론 모두 보정된 이미지를 쓰므로 ROI 밖(옆 랙, 바닥, 천장 등)은 아예 보이지 않는다.
"""

from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image

from .config import Point, Rack


def corners_px(corners: list[Point], w: int, h: int) -> np.ndarray:
    return np.array([[x * w, y * h] for x, y in corners], dtype=np.float32)


def output_size(rack: Rack, w: int, h: int, out_w: int) -> tuple[int, int]:
    """보정 이미지 크기. 랙 실제 폭/높이가 있으면 그 비율, 없으면 네 변의 길이로 추정한다."""
    if rack.width_mm and rack.height_mm:
        aspect = rack.height_mm / rack.width_mm
    else:
        p = corners_px(rack.corners, w, h)
        dist = lambda a, b: math.dist(p[a], p[b])
        aspect = ((dist(0, 3) + dist(1, 2)) / 2) / max((dist(0, 1) + dist(3, 2)) / 2, 1)
    out_h = int(round(out_w * min(max(aspect, 0.1), 10)))
    return out_w, out_h


def homography(rack: Rack, w: int, h: int, out_w: int) -> tuple[np.ndarray, tuple[int, int]]:
    size = output_size(rack, w, h, out_w)
    W, H = size
    dst = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], dtype=np.float32)
    return cv2.getPerspectiveTransform(corners_px(rack.corners, w, h), dst), size


def warp_image(img: Image.Image, rack: Rack, out_w: int) -> Image.Image:
    if not rack.corners:
        return img
    M, size = homography(rack, img.width, img.height, out_w)
    arr = cv2.warpPerspective(np.asarray(img), M, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    return Image.fromarray(arr)


def warp_depth(depth: np.ndarray, rack: Rack, out_w: int) -> np.ndarray:
    if not rack.corners:
        return depth
    h, w = depth.shape
    M, size = homography(rack, w, h, out_w)
    return cv2.warpPerspective(depth.astype(np.float32), M, size, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def roi_mask(rack: Rack, w: int, h: int) -> np.ndarray:
    """원본 이미지 좌표에서 ROI 안쪽이면 True."""
    mask = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(mask, [corners_px(rack.corners, w, h).astype(np.int32)], 1)
    return mask.astype(bool)


def homography_for(corners: list[Point], src_w: int, src_h: int, size: tuple[int, int]) -> np.ndarray:
    """원본 크기(src_w, src_h)의 모서리 비율 좌표 -> 보정 이미지 size 로 가는 변환."""
    W, H = size
    dst = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], dtype=np.float32)
    return cv2.getPerspectiveTransform(corners_px(corners, src_w, src_h), dst)


def transform_boxes(boxes: list[tuple[float, float, float, float]], M: np.ndarray) -> list[tuple[float, float, float, float]]:
    """픽셀 박스를 변환한다. 네 꼭짓점을 옮긴 뒤 감싸는 사각형을 쓴다."""
    if not boxes:
        return []
    pts = np.array([[(x1, y1), (x2, y1), (x2, y2), (x1, y2)] for x1, y1, x2, y2 in boxes], dtype=np.float32)
    out = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), M).reshape(-1, 4, 2)
    return [(float(p[:, 0].min()), float(p[:, 1].min()), float(p[:, 0].max()), float(p[:, 1].max())) for p in out]
