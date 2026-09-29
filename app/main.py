from __future__ import annotations

import io
import re
from dataclasses import asdict, replace
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field

from . import dataset, training
from .config import DEFAULT_CONFIG, ROOT, Rack, _corners, load_config, load_raw, save_raw
from .pipeline import Analyzer, load_history
from .rectify import warp_image

STATIC = ROOT / "app" / "static"
RACK_IMAGES = ROOT / "data" / "racks"
RACK_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,40}$")


class RackIn(BaseModel):
    name: str
    corners: list[list[float]] | None = None
    rack_depth_mm: float = Field(default=1000, gt=0)
    width_mm: float | None = None
    height_mm: float | None = None
    camera_to_front_m: float | None = None
    classes: list[str] = []


class NewRack(BaseModel):
    id: str
    name: str = ""


class ClassIn(BaseModel):
    orig: int | None = None  # 기존 클래스 번호. 새로 추가한 클래스면 None
    name: str
    color: str
    safety_stock: int = Field(default=0, ge=0)
    target_stock: int = Field(default=0, ge=0)
    order_lot: int = Field(default=1, ge=1)
    units_per_box: int = Field(default=1, ge=1)
    box_depth_mm: float = Field(default=300, gt=0)
    description: str = ""


class SettingsIn(BaseModel):
    detector_conf: float = Field(ge=0.01, le=0.99)
    background_margin_mm: float = Field(ge=0)
    depth_tolerance_ratio: float = Field(ge=0, le=0.9)
    default_box_depth_mm: float = Field(default=300, gt=0)


class BoxIn(BaseModel):
    cls: int
    x1: float
    y1: float
    x2: float
    y2: float


class LabelsIn(BaseModel):
    boxes: list[BoxIn]


class RoiApplyIn(BaseModel):
    names: list[str] | None = None  # None 이면 ROI 미적용/변경된 이미지 전부
    rack_id: str | None = None  # ROI 미적용 이미지에 쓸 랙 (이미 보정된 이미지는 원래 랙을 씀)


class TrainIn(BaseModel):
    base_model: str = "yolo11s.pt"
    epochs: int = 100
    imgsz: int = 960
    batch: int = 4


def create_app(analyzer: Analyzer | None = None, config_path: Path = DEFAULT_CONFIG) -> FastAPI:
    app = FastAPI(title="랙 재고 카운팅")
    state = {"analyzer": analyzer}
    trainer = training.TrainManager()

    def get_analyzer() -> Analyzer:
        if state["analyzer"] is None:
            state["analyzer"] = Analyzer(load_config(config_path))
        return state["analyzer"]

    def edit_config(fn: Callable[[dict], None]):
        raw = load_raw(config_path)
        raw.setdefault("racks", {})
        raw.pop("items", None)  # 예전 품목 기준(클래스로 대체됨)
        raw.setdefault("settings", {})
        fn(raw)
        get_analyzer().config = save_raw(raw, config_path)

    def rack_or_404(rack_id: str) -> Rack:
        rack = get_analyzer().config.racks.get(rack_id)
        if rack is None:
            raise HTTPException(404, f"등록되지 않은 랙입니다: {rack_id}")
        return rack

    def rack_image_path(rack_id: str) -> Path:
        return RACK_IMAGES / f"{rack_id}.jpg"

    @app.exception_handler(ValueError)
    async def bad_request(_: Request, e: ValueError):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.exception_handler(FileNotFoundError)
    async def not_found(_: Request, e: FileNotFoundError):
        return JSONResponse({"detail": str(e)}, status_code=404)

    @app.exception_handler(UnidentifiedImageError)
    async def bad_image(_: Request, e: UnidentifiedImageError):
        return JSONResponse({"detail": "이미지 파일을 읽을 수 없습니다."}, status_code=400)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    # ---------- 랙 / ROI ----------
    @app.get("/api/racks")
    def racks():
        return [{**asdict(r), "has_image": rack_image_path(r.id).exists()} for r in get_analyzer().config.racks.values()]

    @app.post("/api/racks")
    def create_rack(body: NewRack):
        if not RACK_ID_RE.match(body.id):
            raise ValueError("랙 ID는 영문, 숫자, -, _ 로 40자 이내여야 합니다.")
        if body.id in get_analyzer().config.racks:
            raise ValueError(f"이미 있는 랙 ID입니다: {body.id}")
        edit_config(lambda raw: raw["racks"].__setitem__(body.id, {"name": body.name or body.id, "rack_depth_mm": 1000}))
        return {"id": body.id}

    @app.put("/api/racks/{rack_id}")
    def update_rack(rack_id: str, body: RackIn):
        rack_or_404(rack_id)
        data = body.model_dump()
        data["corners"] = None if body.corners is None else [[round(x, 4), round(y, 4)] for x, y in _corners(body.corners)]
        data = {k: v for k, v in data.items() if v not in (None, [])}
        edit_config(lambda raw: raw["racks"].__setitem__(rack_id, data))
        return {"ok": True}

    @app.delete("/api/racks/{rack_id}")
    def delete_rack(rack_id: str):
        rack_or_404(rack_id)
        edit_config(lambda raw: raw["racks"].pop(rack_id))
        rack_image_path(rack_id).unlink(missing_ok=True)
        return {"ok": True}

    @app.get("/api/racks/{rack_id}/image")
    def rack_image(rack_id: str):
        rack_or_404(rack_id)
        if not rack_image_path(rack_id).exists():
            raise HTTPException(404, "기준 사진이 없습니다.")
        return FileResponse(rack_image_path(rack_id), headers={"Cache-Control": "no-cache"})

    @app.post("/api/racks/{rack_id}/image")
    async def upload_rack_image(rack_id: str, image: UploadFile = File(...)):
        rack_or_404(rack_id)
        RACK_IMAGES.mkdir(parents=True, exist_ok=True)
        dataset.normalize_image(await image.read()).save(rack_image_path(rack_id), quality=92)
        return {"ok": True}

    @app.get("/api/racks/{rack_id}/preview")
    def rack_preview(rack_id: str, c: str, width_mm: float | None = None, height_mm: float | None = None):
        """저장 전 ROI(c = x1,y1,...,x4,y4)로 기준 사진을 보정한 미리보기."""
        rack = rack_or_404(rack_id)
        if not rack_image_path(rack_id).exists():
            raise HTTPException(404, "기준 사진이 없습니다.")
        v = [float(x) for x in c.split(",")]
        if len(v) != 8:
            raise ValueError("모서리 좌표 8개가 필요합니다.")
        tmp = replace(rack, corners=_corners([v[i:i + 2] for i in range(0, 8, 2)]), width_mm=width_mm, height_mm=height_mm)
        with Image.open(rack_image_path(rack_id)) as img:
            out = warp_image(img.convert("RGB"), tmp, 960)
        buf = io.BytesIO()
        out.save(buf, format="JPEG", quality=85)
        return Response(buf.getvalue(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    # ---------- 클래스(부자재) / 분석 파라미터 ----------
    @app.get("/api/classes")
    def classes():
        counts = dataset.class_counts()
        return [{"index": i, **asdict(c), "boxes": counts.get(i, 0)} for i, c in enumerate(get_analyzer().config.classes)]

    @app.put("/api/classes")
    def save_classes(body: list[ClassIn]):
        """클래스 추가/수정/순서변경/삭제. 삭제된 클래스의 박스는 라벨 파일에서 지우고 나머지 번호를 당긴다."""
        old = get_analyzer().config.classes
        if not body:
            raise ValueError("클래스가 하나 이상 있어야 합니다.")
        origs = [c.orig for c in body if c.orig is not None]
        if len(set(origs)) != len(origs) or any(not 0 <= o < len(old) for o in origs):
            raise ValueError("클래스 목록이 서버와 맞지 않습니다. 새로고침 후 다시 시도하세요.")
        mapping = {i: None for i in range(len(old))}
        for new_i, c in enumerate(body):
            if c.orig is not None:
                mapping[c.orig] = new_i
        renamed = {old[c.orig].name: c.name.strip() for c in body if c.orig is not None}
        new_classes = [c.model_dump(exclude={"orig"}) | {"name": c.name.strip()} for c in body]

        def apply(raw):
            raw["classes"] = new_classes
            for r in raw["racks"].values():  # 랙의 부자재 목록도 이름 변경/삭제 반영
                r["classes"] = [renamed[n] for n in r.get("classes") or [] if n in renamed]

        edit_config(apply)  # 검증에 실패하면 여기서 오류가 나고 라벨은 그대로
        removed = 0
        if any(mapping[i] != i for i in mapping):
            removed = dataset.remap_labels(mapping)
        return {"removed_boxes": removed, "model": training.model_info(get_analyzer().config.class_names)}

    @app.get("/api/settings")
    def get_settings():
        return asdict(get_analyzer().config.settings)

    @app.put("/api/settings")
    def save_settings(body: SettingsIn):
        edit_config(lambda raw: raw["settings"].update(body.model_dump()))
        return asdict(get_analyzer().config.settings)

    # ---------- 분석 ----------
    def load_rack_or_upload(image: bytes | None, rack_id: str | None, use_reference: bool) -> Image.Image:
        if use_reference:
            if not rack_id or not rack_image_path(rack_id).exists():
                raise HTTPException(404, "선택한 랙의 기준 사진이 없습니다.")
            return dataset.normalize_image(rack_image_path(rack_id).read_bytes())
        if not image:
            raise HTTPException(400, "이미지를 올려주세요.")
        return dataset.normalize_image(image)

    @app.post("/api/analyze")
    async def analyze(rack_id: str = Form(...), image: UploadFile = File(...)):
        rack_or_404(rack_id)
        img = dataset.normalize_image(await image.read())
        try:
            return get_analyzer().analyze(img, rack_id)
        except FileNotFoundError as e:
            raise HTTPException(503, str(e))

    @app.post("/api/depth")
    async def depth(
        image: UploadFile | None = File(None),
        rack_id: str = Form(""),
        use_reference: bool = Form(False),
        rectify: bool = Form(True),
    ):
        if rack_id:
            rack_or_404(rack_id)
        img = load_rack_or_upload(await image.read() if image else None, rack_id, use_reference)
        return get_analyzer().depth_view(img, rack_id or None, rectify)

    @app.get("/api/history")
    def history(rack_id: str | None = None, limit: int = 50):
        return load_history(rack_id, limit)

    # ---------- 라벨링 데이터셋 ----------
    def out_w() -> int:
        return get_analyzer().config.settings.rectified_width

    @app.get("/api/dataset/images")
    def dataset_images():
        return dataset.list_images(get_analyzer().config.racks, out_w())

    @app.post("/api/dataset/images")
    async def dataset_upload(images: list[UploadFile] = File(...), rack_id: str = Form("")):
        """선택한 랙의 ROI 로 보정해서 저장한다 (추론 때와 같은 이미지가 되도록). 원본은 따로 보관한다."""
        if not rack_id:
            raise ValueError("ROI 를 적용할 랙을 선택하세요.")
        try:
            rack = get_analyzer().rack(rack_id)  # ROI 미설정이면 여기서 오류
        except KeyError as e:
            raise HTTPException(404, str(e.args[0]))
        names, errors = [], []
        for f in images:
            try:
                img = dataset.normalize_image(await f.read())
            except UnidentifiedImageError:
                errors.append(f.filename)
                continue
            names.append(dataset.add_image(img, f.filename or "", rack, out_w()))
        return {"added": names, "errors": errors}

    @app.post("/api/dataset/roi")
    def dataset_apply_roi(body: RoiApplyIn):
        """ROI 미적용 이미지에 ROI 를 적용하거나, 랙 ROI 가 바뀐 이미지를 원본에서 다시 보정한다. 라벨도 함께 옮긴다."""
        cfg = get_analyzer().config
        meta = dataset.load_roi()
        imgs = {i["name"]: i for i in dataset.list_images(cfg.racks, out_w())}
        targets = body.names if body.names is not None else [n for n, i in imgs.items() if i["roi"] != "ok"]
        done, dropped, errors = 0, 0, []
        for name in targets:
            if name not in imgs:
                errors.append(f"{name}: 이미지가 없습니다")
                continue
            info = meta.get(name)
            rack = cfg.racks.get(info["rack_id"]) if info else None
            if rack is None or not rack.corners:
                rack = cfg.racks.get(body.rack_id or "")
            if rack is None or not rack.corners:
                errors.append(f"{name}: ROI 가 설정된 랙을 선택하세요")
                continue
            try:
                dropped += dataset.apply_roi(name, rack, out_w())
                done += 1
            except (FileNotFoundError, ValueError) as e:
                errors.append(f"{name}: {e}")
        return {"done": done, "dropped_boxes": dropped, "errors": errors}

    @app.get("/api/dataset/images/{name}")
    def dataset_image(name: str):
        # ROI 를 다시 적용하면 같은 이름으로 이미지가 바뀌므로 브라우저 캐시를 쓰지 않게 한다
        return FileResponse(dataset.image_path(name), headers={"Cache-Control": "no-cache"})

    @app.delete("/api/dataset/images/{name}")
    def dataset_delete(name: str):
        dataset.delete_image(name)
        return {"ok": True}

    @app.get("/api/dataset/images/{name}/labels")
    def get_labels(name: str):
        return dataset.read_labels(name)

    @app.put("/api/dataset/images/{name}/labels")
    def put_labels(name: str, body: LabelsIn):
        n = len(get_analyzer().config.classes)
        return {"boxes": dataset.write_labels(name, [b.model_dump() for b in body.boxes], n)}

    @app.post("/api/dataset/images/{name}/predict")
    def predict_labels(name: str):
        """현재 모델로 초벌 라벨을 만든다. 저장하지 않고 돌려주기만 한다."""
        path = dataset.image_path(name)
        try:
            detector = get_analyzer().detector
        except FileNotFoundError as e:
            raise HTTPException(503, str(e))
        with Image.open(path) as img:
            img = img.convert("RGB")
            w, h = img.size
            dets = detector(img)
        classes = get_analyzer().config.class_names
        return {"boxes": [
            {
                "cls": classes.index(d.class_name) if d.class_name in classes else 0,
                "x1": d.xyxy[0] / w, "y1": d.xyxy[1] / h, "x2": d.xyxy[2] / w, "y2": d.xyxy[3] / h,
                "conf": d.conf,
            }
            for d in dets
        ]}

    @app.get("/api/dataset/stats")
    def dataset_stats():
        cfg = get_analyzer().config
        return dataset.stats(cfg.class_names, cfg.racks, out_w())

    # ---------- 학습 ----------
    @app.get("/api/train/status")
    def train_status():
        return trainer.status()

    @app.post("/api/train/start")
    def train_start(body: TrainIn):
        try:
            return trainer.start(body.base_model, body.epochs, body.imgsz, body.batch, get_analyzer().config.class_names)
        except RuntimeError as e:
            raise HTTPException(409, str(e))

    @app.post("/api/train/stop")
    def train_stop():
        trainer.stop()
        return trainer.status()

    @app.get("/api/model")
    def model():
        return {**training.model_info(get_analyzer().config.class_names), "base_models": training.BASE_MODELS}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
