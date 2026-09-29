"""웹에서 학습을 시작/중지하고 진행상황을 읽는다. 학습은 별도 프로세스(scripts/train.py)로 돌린다."""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime

from . import dataset
from .config import ROOT

RUNS = ROOT / "runs"
RUN_DIR = RUNS / "box_label"
LOG_FILE = RUNS / "train.log"
MODEL = ROOT / "models" / "box_label.pt"
BASE_MODELS = ["yolo11n.pt", "yolo11s.pt", "yolo11m.pt"]
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class TrainManager:
    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._params: dict = {}
        self._started_at: str | None = None
        self._stopped = False

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, base_model: str, epochs: int, imgsz: int, batch: int, class_names: list[str]) -> dict:
        with self._lock:
            if self.running():
                raise RuntimeError("이미 학습이 진행 중입니다.")
            if base_model == "current":
                if not MODEL.exists():
                    raise ValueError("이어서 학습할 현재 모델이 없습니다.")
                model_arg = str(MODEL)
            elif base_model in BASE_MODELS:
                model_arg = base_model
            else:
                raise ValueError(f"지원하지 않는 기본 모델입니다: {base_model}")
            if not (1 <= epochs <= 1000 and 320 <= imgsz <= 1920 and 1 <= batch <= 64):
                raise ValueError("epochs 1~1000, imgsz 320~1920, batch 1~64 범위로 입력하세요.")

            data_yaml = dataset.write_data_yaml(RUNS, class_names)
            shutil.rmtree(RUN_DIR, ignore_errors=True)
            log = LOG_FILE.open("w", encoding="utf-8")
            cmd = [
                sys.executable, "-u", str(ROOT / "scripts" / "train.py"),
                "--data", str(data_yaml), "--model", model_arg,
                "--epochs", str(epochs), "--imgsz", str(imgsz), "--batch", str(batch),
            ]
            # cwd=runs: ultralytics 가 기본 가중치를 현재 폴더에 내려받으므로 프로젝트 루트를 어지럽히지 않게 한다
            self._proc = subprocess.Popen(
                cmd, cwd=RUNS, stdout=log, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            log.close()
            self._params = {"base_model": base_model, "epochs": epochs, "imgsz": imgsz, "batch": batch}
            self._started_at = datetime.now().isoformat(timespec="seconds")
            self._stopped = False
            return self.status()

    def stop(self):
        if not self.running():
            return
        self._stopped = True
        if os.name == "nt":
            # 데이터로더 워커까지 함께 종료
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(self._proc.pid)], capture_output=True)
        else:
            self._proc.terminate()
        self._proc.wait(timeout=30)

    def status(self) -> dict:
        if self._proc is None:
            state = "idle"
        elif self._proc.poll() is None:
            state = "running"
        elif self._stopped:
            state = "stopped"
        else:
            state = "finished" if self._proc.returncode == 0 else "failed"
        return {
            "state": state,
            "params": self._params,
            "started_at": self._started_at,
            "epochs": read_results(),
            "log": tail_log(),
        }


def read_results() -> list[dict]:
    path = RUN_DIR / "results.csv"
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except (OSError, csv.Error):
        return []  # 학습 프로세스가 쓰는 중
    keys = {
        "epoch": "epoch",
        "box_loss": "train/box_loss",
        "val_box_loss": "val/box_loss",
        "precision": "metrics/precision(B)",
        "recall": "metrics/recall(B)",
        "map50": "metrics/mAP50(B)",
        "map50_95": "metrics/mAP50-95(B)",
    }
    out = []
    for r in rows:
        r = {k.strip(): v.strip() for k, v in r.items() if k}
        try:
            out.append({k: float(r[src]) for k, src in keys.items()})
        except (KeyError, ValueError):
            continue
    return out


def tail_log(n_lines: int = 40) -> str:
    if not LOG_FILE.exists():
        return ""
    with LOG_FILE.open("rb") as f:
        f.seek(max(0, LOG_FILE.stat().st_size - 32_000))
        text = f.read().decode("utf-8", errors="replace").replace("\r\n", "\n")
    # 진행바는 \r 로 같은 줄을 덮어쓰므로 마지막 상태만 남긴다
    lines = [ANSI.sub("", l.rstrip("\r").split("\r")[-1]).rstrip() for l in text.split("\n")]
    return "\n".join([l for l in lines if l][-n_lines:])


def model_info(class_names: list[str] | None = None) -> dict:
    if not MODEL.exists():
        return {"exists": False}
    meta_path = MODEL.with_suffix(".json")
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    info = {
        "exists": True,
        "updated_at": datetime.fromtimestamp(MODEL.stat().st_mtime).isoformat(timespec="seconds"),
        **meta,
    }
    # 모델이 학습한 클래스 목록이 현재 클래스와 다르면(삭제/이름변경/추가) 재학습이 필요하다
    if class_names is not None and "classes" in meta:
        info["stale"] = meta["classes"] != class_names
    return info
