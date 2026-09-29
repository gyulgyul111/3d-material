"""모델 래퍼. 무거운 import(torch 등)는 처음 사용할 때 로드한다."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from .config import ROOT
from .counting import Detection


def _device() -> str:
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def resolve_weights(weights: str) -> Path:
    path = Path(weights)
    return path if path.is_absolute() else ROOT / path


class LabelDetector:
    def __init__(self, weights: str, conf: float = 0.35):
        path = resolve_weights(weights)
        if not path.exists():
            raise FileNotFoundError(
                f"검출 모델이 아직 없습니다: {path}\n"
                "[라벨링] 탭에서 사진에 라벨을 그리고 [학습] 탭에서 모델을 학습하세요."
            )
        from ultralytics import YOLO

        self.model = YOLO(str(path))
        self.conf = conf

    def __call__(self, image: Image.Image) -> list[Detection]:
        result = self.model.predict(image, conf=self.conf, device=_device(), verbose=False)[0]
        names = result.names
        return [
            Detection(tuple(map(float, xyxy)), float(c), names[int(k)])
            for xyxy, c, k in zip(result.boxes.xyxy.tolist(), result.boxes.conf.tolist(), result.boxes.cls.tolist())
        ]


class DepthEstimator:
    """단안 metric 깊이추정. 입력 이미지와 같은 크기의 깊이맵(m)을 반환한다."""

    def __init__(self, model_id: str):
        import torch
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation

        self.torch = torch
        self.device = _device()
        self.processor = AutoImageProcessor.from_pretrained(model_id)
        self.model = AutoModelForDepthEstimation.from_pretrained(model_id).to(self.device).eval()

    def __call__(self, image: Image.Image) -> np.ndarray:
        torch = self.torch
        inputs = self.processor(images=image, return_tensors="pt").to(self.device)
        with torch.no_grad():
            pred = self.model(**inputs).predicted_depth  # (1, h, w)
        depth = torch.nn.functional.interpolate(
            pred.unsqueeze(1), size=(image.height, image.width), mode="bicubic", align_corners=False
        )
        return depth.squeeze().float().cpu().numpy()
