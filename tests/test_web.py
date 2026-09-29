import base64
import io

import numpy as np
import pytest
import yaml
from fastapi.testclient import TestClient
from PIL import Image

import app.dataset as dataset
import app.main as main
import app.pipeline as pipeline
from app.config import DEFAULT_CONFIG, Rack, load_config
from app.counting import Detection
from app.pipeline import Analyzer


def jpeg(w=400, h=300, color="gray") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg_path = tmp_path / "racks.yaml"
    # 실제 설정의 settings 만 가져오고 랙/클래스는 고정값으로 둬서, 사용자가 웹에서 바꾼 값과 무관하게 테스트한다
    raw = yaml.safe_load(DEFAULT_CONFIG.read_text(encoding="utf-8"))
    raw.pop("items", None)
    raw["racks"] = {"RACK-A01": {"name": "A동 1번 랙", "rack_depth_mm": 1000, "classes": ["볼트"]}}
    raw["classes"] = [
        {"name": "볼트", "color": "#2a78d6", "safety_stock": 20, "target_stock": 40, "order_lot": 10,
         "units_per_box": 50, "box_depth_mm": 300},
        {"name": "너트", "color": "#eb6834", "safety_stock": 15, "target_stock": 30, "order_lot": 5,
         "units_per_box": 100, "box_depth_mm": 240},
    ]
    cfg_path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    ds = tmp_path / "ds"
    monkeypatch.setattr(dataset, "DATASET_DIR", ds)
    monkeypatch.setattr(dataset, "IMAGES", ds / "images")
    monkeypatch.setattr(dataset, "LABELS", ds / "labels")
    monkeypatch.setattr(dataset, "ORIGINALS", ds / "originals")
    monkeypatch.setattr(dataset, "ROI_FILE", ds / "roi.json")
    monkeypatch.setattr(main, "RACK_IMAGES", tmp_path / "rack_images")
    monkeypatch.setattr(pipeline, "HISTORY_FILE", tmp_path / "history.jsonl")

    detections = []
    analyzer = Analyzer(
        load_config(cfg_path),
        detector=lambda img: list(detections),
        depth=lambda img: np.full((img.height, img.width), 1.8, dtype=np.float32),
    )
    client = TestClient(main.create_app(analyzer, config_path=cfg_path))
    return client, cfg_path, detections, tmp_path


QUAD = [[0.1, 0.1], [0.9, 0.12], [0.88, 0.9], [0.12, 0.88]]


def test_rack_crud_and_roi_save(env):
    client, cfg_path, *_ = env
    assert client.post("/api/racks", json={"id": "RACK-B02", "name": "B동"}).status_code == 200
    assert client.post("/api/racks", json={"id": "RACK-B02"}).status_code == 400  # 중복
    assert client.post("/api/racks", json={"id": "bad id!"}).status_code == 400

    # 찍은 순서가 뒤섞여도 좌상/우상/우하/좌하로 정렬되어 저장
    body = {"name": "B동 2번", "corners": [QUAD[2], QUAD[0], QUAD[3], QUAD[1]], "rack_depth_mm": 800,
            "width_mm": 2000, "height_mm": 1500, "camera_to_front_m": 2.0, "classes": ["너트"]}
    r = client.put("/api/racks/RACK-B02", json=body)
    assert r.status_code == 200, r.text
    saved = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))["racks"]["RACK-B02"]
    assert saved["corners"] == QUAD and saved["rack_depth_mm"] == 800 and saved["classes"] == ["너트"]
    rack = next(r for r in client.get("/api/racks").json() if r["id"] == "RACK-B02")
    assert rack["name"] == "B동 2번" and not rack["has_image"]

    # 꼬인 사각형은 저장되지 않는다
    bad = {**body, "corners": [[0.1, 0.1], [0.9, 0.9], [0.9, 0.1], [0.1, 0.9]][:2] + [[0.5, 0.5], [0.3, 0.3]]}
    assert client.put("/api/racks/RACK-B02", json=bad).status_code == 400
    assert yaml.safe_load(cfg_path.read_text(encoding="utf-8"))["racks"]["RACK-B02"]["name"] == "B동 2번"

    c = ",".join(str(v) for p in QUAD for v in p)
    assert client.get(f"/api/racks/RACK-B02/preview?c={c}").status_code == 404  # 기준 사진 없음
    assert client.post("/api/racks/RACK-B02/image", files={"image": ("r.jpg", jpeg(), "image/jpeg")}).status_code == 200
    assert client.get("/api/racks/RACK-B02/image").status_code == 200
    prev = client.get(f"/api/racks/RACK-B02/preview?c={c}&width_mm=2000&height_mm=1000")
    assert prev.status_code == 200 and Image.open(io.BytesIO(prev.content)).size == (960, 480)

    assert client.delete("/api/racks/RACK-B02").status_code == 200
    assert "RACK-B02" not in {r["id"] for r in client.get("/api/racks").json()}
    assert client.put("/api/racks/NOPE", json={"name": "x"}).status_code == 404


def set_roi(client, rack_id="RACK-A01", **extra):
    body = {"name": "A", "corners": [[0, 0], [1, 0], [1, 1], [0, 1]], "rack_depth_mm": 1000,
            "camera_to_front_m": 1.8, "classes": ["볼트"], **extra}
    assert client.put(f"/api/racks/{rack_id}", json=body).status_code == 200


FULL = [[0, 0], [1, 0], [1, 1], [0, 1]]


def upload(client, *files, rack_id="RACK-A01"):
    return client.post("/api/dataset/images", data={"rack_id": rack_id}, files=[("images", f) for f in files])


def test_analysis_requires_roi_then_counts(env):
    client, cfg_path, detections, _ = env
    img = ("a.jpg", jpeg(1000, 1000), "image/jpeg")
    r = client.post("/api/analyze", data={"rack_id": "RACK-A01"}, files={"image": img})
    assert r.status_code == 400 and "ROI" in r.json()["detail"]

    set_roi(client, width_mm=1000, height_mm=1000)
    classes = client.get("/api/classes").json()
    body = [{**c, "orig": c["index"]} for c in classes]
    body[0].update(safety_stock=2, target_stock=5)
    assert client.put("/api/classes", json=body).status_code == 200
    assert client.put("/api/classes", json=body + [{**body[0], "orig": None}]).status_code == 400  # 이름 중복

    s = client.put("/api/settings", json={"detector_conf": 0.5, "background_margin_mm": 100,
                                          "depth_tolerance_ratio": 0.2, "default_box_depth_mm": 250})
    assert s.json()["detector_conf"] == 0.5 and s.json()["rectified_width"] == 1280

    # 깊이 = 랙 앞면인 볼트 라벨 하나 -> 박스 300mm 라서 3박스. 적정재고 2 보다 많아 구매 불필요
    detections.append(Detection((200, 150, 300, 220), 0.9, "볼트"))
    r = client.post("/api/analyze", data={"rack_id": "RACK-A01"}, files={"image": img})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["classes"] == [{"class_name": "볼트", "visible_labels": 1, "boxes": 3, "color": "#2a78d6"}]
    line = next(p for p in body["purchase"] if p["class_name"] == "볼트")
    assert line["stock_boxes"] == 3 and not line["need_purchase"]
    assert client.get("/api/history?rack_id=RACK-A01").json()[0]["classes"][0]["boxes"] == 3


def test_dataset_upload_requires_rack_roi_and_keeps_original(env):
    client, *_ = env
    f = ("a.jpg", jpeg(800, 600), "image/jpeg")
    assert client.post("/api/dataset/images", files=[("images", f)]).status_code == 400  # 랙 미선택
    assert upload(client, f).status_code == 400  # ROI 없음
    assert upload(client, f, rack_id="NOPE").status_code == 404
    set_roi(client, corners=QUAD, width_mm=2000, height_mm=1000)
    name = upload(client, f).json()["added"][0]
    assert Image.open(dataset.IMAGES / name).size == (1280, 640)
    assert Image.open(dataset.ORIGINALS / name).size == (800, 600)
    img = client.get("/api/dataset/images").json()[0]
    assert (img["roi"], img["rack_id"]) == ("ok", "RACK-A01")
    client.delete(f"/api/dataset/images/{name}")
    assert not (dataset.ORIGINALS / name).exists() and dataset.load_roi() == {}


def test_apply_roi_to_raw_image_moves_labels_and_reapply_after_roi_change(env):
    client, *_ = env
    # 원본 800x600 에 ROI 미적용 이미지 + 라벨 3개 (원본 비율 좌표)
    name = dataset.add_raw_image(jpeg(800, 600), "raw")
    inside = {"cls": 0, "x1": 0.4, "y1": 0.4, "x2": 0.5, "y2": 0.5}      # 원본 (320,240)-(400,300)
    edge = {"cls": 0, "x1": 0.05, "y1": 0.4, "x2": 0.25, "y2": 0.5}     # ROI 경계에 걸침: 원본 x 40~200
    outside = {"cls": 0, "x1": 0.01, "y1": 0.01, "x2": 0.05, "y2": 0.05}  # ROI 밖
    client.put(f"/api/dataset/images/{name}/labels", json={"boxes": [inside, edge, outside]})
    assert client.get("/api/dataset/images").json()[0]["roi"] == "raw"
    stats = client.get("/api/dataset/stats").json()
    assert (stats["labeled"], stats["trainable"], stats["raw"]) == (1, 0, 1)

    # ROI1: 원본 (200,150)-(600,450) 직사각형 -> 1280x960 으로 확대 (변환이 정확히 계산됨)
    set_roi(client, corners=[[0.25, 0.25], [0.75, 0.25], [0.75, 0.75], [0.25, 0.75]])
    assert client.post("/api/dataset/roi", json={"names": [name]}).json()["errors"]  # 랙 미지정
    r = client.post("/api/dataset/roi", json={"names": [name], "rack_id": "RACK-A01"}).json()
    assert (r["done"], r["dropped_boxes"]) == (1, 2)  # edge(대부분 밖), outside 삭제
    boxes = client.get(f"/api/dataset/images/{name}/labels").json()["boxes"]
    assert len(boxes) == 1
    # 원본 (320,240)-(400,300) -> ROI 안 비율 (0.3,0.3)-(0.5,0.5)
    assert [round(boxes[0][k], 2) for k in ("x1", "y1", "x2", "y2")] == [0.3, 0.3, 0.5, 0.5]
    assert Image.open(dataset.IMAGES / name).size == (1280, 960)
    assert Image.open(dataset.ORIGINALS / name).size == (800, 600)
    assert client.get("/api/dataset/images").json()[0]["roi"] == "ok"

    # 랙 ROI 변경 -> stale. 다시 적용하면 원본에서 새로 보정하고 라벨도 새 ROI 기준으로 옮겨짐
    set_roi(client, corners=[[0.3, 0.3], [0.6, 0.3], [0.6, 0.6], [0.3, 0.6]])
    assert client.get("/api/dataset/images").json()[0]["roi"] == "stale"
    assert client.get("/api/dataset/stats").json()["stale"] == 1
    r = client.post("/api/dataset/roi", json={}).json()  # 전체(미적용+변경) 대상
    assert (r["done"], r["dropped_boxes"]) == (1, 0)
    b = client.get(f"/api/dataset/images/{name}/labels").json()["boxes"][0]
    # 원본 (320,240)-(400,300), ROI2 = 원본 (240,180)-(480,360) -> (0.333,0.333)-(0.667,0.667)
    assert [round(b[k], 2) for k in ("x1", "y1", "x2", "y2")] == [0.33, 0.33, 0.67, 0.67]
    assert client.get("/api/dataset/images").json()[0]["roi"] == "ok"


def test_depth_view_endpoint(env):
    client, *_ = env
    r = client.post("/api/depth", files={"image": ("a.jpg", jpeg(640, 480), "image/jpeg")})
    body = r.json()
    assert r.status_code == 200 and not body["rectified"] and body["rack"] is None
    assert body["cloud"]["w"] == 320 and body["cloud"]["h"] == 240
    assert len(base64.b64decode(body["cloud"]["depth"])) == 320 * 240 * 4
    assert len(base64.b64decode(body["cloud"]["rgb"])) == 320 * 240 * 3

    # 랙 기준 사진 + ROI 보정 -> 앞면 기준 깊이 맵 포함
    set_roi(client, corners=QUAD)
    client.post("/api/racks/RACK-A01/image", files={"image": ("r.jpg", jpeg(640, 480), "image/jpeg")})
    body = client.post("/api/depth", data={"rack_id": "RACK-A01", "use_reference": "true"}).json()
    assert body["rectified"] and body["view"]["recess_color"].startswith("data:image/png")
    g = body["view"]["grid"]
    assert len(base64.b64decode(g["recess"])) == g["w"] * g["h"] * 4
    assert body["rack"]["front_depth_m"] == [1.8] * 4
    assert client.post("/api/depth", data={"rack_id": "NOPE"}).status_code == 404


def test_dataset_upload_label_roundtrip(env):
    client, *_ = env
    set_roi(client, corners=FULL)
    r = upload(client, ("a b.jpg", jpeg(), "image/jpeg"), ("x.txt", b"not image", "text/plain"))
    body = r.json()
    assert len(body["added"]) == 1 and body["errors"] == ["x.txt"]
    name = body["added"][0]
    assert " " not in name

    assert client.get(f"/api/dataset/images/{name}/labels").json() == {"labeled": False, "boxes": []}
    boxes = [{"cls": 0, "x1": 0.1, "y1": 0.2, "x2": 0.3, "y2": 0.5},
             {"cls": 0, "x1": 0.5, "y1": 0.5, "x2": 0.5005, "y2": 0.6}]  # 너무 작은 박스는 버림
    assert client.put(f"/api/dataset/images/{name}/labels", json={"boxes": boxes}).json() == {"boxes": 1}
    got = client.get(f"/api/dataset/images/{name}/labels").json()
    assert got["labeled"] and got["boxes"][0]["x1"] == pytest.approx(0.1) and got["boxes"][0]["y2"] == pytest.approx(0.5)
    txt = (dataset.LABELS / name.replace(".jpg", ".txt")).read_text()
    assert txt.split()[:3] == ["0", "0.200000", "0.350000"]

    assert client.put(f"/api/dataset/images/{name}/labels",
                      json={"boxes": [{**boxes[0], "cls": 5}]}).status_code == 400  # 없는 클래스

    assert client.get("/api/dataset/images/../../secret.jpg").status_code == 404
    assert client.get("/api/dataset/images/none.jpg/labels").status_code == 404

    assert client.delete(f"/api/dataset/images/{name}").status_code == 200
    assert client.get("/api/dataset/images").json() == []


def test_exif_rotation_is_applied_on_upload(env):
    client, *_ = env
    img = Image.new("RGB", (400, 300), "gray")
    exif = img.getexif()
    exif[0x0112] = 6  # 90도 회전
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    set_roi(client, corners=FULL)
    name = upload(client, ("p.jpg", buf.getvalue(), "image/jpeg")).json()["added"][0]
    assert Image.open(dataset.ORIGINALS / name).size == (300, 400)


def label(client, name, *classes):
    boxes = [{"cls": c, "x1": 0.1 * i, "y1": 0.1, "x2": 0.1 * i + 0.05, "y2": 0.2} for i, c in enumerate(classes, 1)]
    assert client.put(f"/api/dataset/images/{name}/labels", json={"boxes": boxes}).status_code == 200


def label_classes(name):
    return [int(l.split()[0]) for l in (dataset.LABELS / name.replace(".jpg", ".txt")).read_text().splitlines()]


def test_classes_add_color_rename_delete_remaps_labels(env):
    client, cfg_path, *_ = env
    classes = client.get("/api/classes").json()
    assert [c["name"] for c in classes] == ["볼트", "너트"] and classes[1]["color"] == "#eb6834"

    # 와셔 추가 후 라벨: 볼트, 너트, 와셔, 너트
    body = [{**c, "orig": c["index"]} for c in classes] + [{"orig": None, "name": "와셔", "color": "#1baf7a"}]
    assert client.put("/api/classes", json=body).json()["removed_boxes"] == 0
    set_roi(client, corners=FULL)
    name = upload(client, ("a.jpg", jpeg(), "image/jpeg")).json()["added"][0]
    label(client, name, 0, 1, 2, 1)
    assert [c["boxes"] for c in client.get("/api/classes").json()] == [1, 2, 1]

    # 너트 삭제 + 볼트 이름·색 변경 -> 너트 박스 2개 삭제, 와셔 2 -> 1 로 당겨짐, 랙의 부자재 이름도 바뀜
    cur = client.get("/api/classes").json()
    body = [{**cur[0], "orig": 0, "name": "볼트M6", "color": "#123456"}, {**cur[2], "orig": 2}]
    r = client.put("/api/classes", json=body).json()
    assert r["removed_boxes"] == 2
    assert label_classes(name) == [0, 1]
    saved = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    assert [c["name"] for c in saved["classes"]] == ["볼트M6", "와셔"] and saved["classes"][0]["color"] == "#123456"
    assert saved["racks"]["RACK-A01"]["classes"] == ["볼트M6"]

    # 순서 바꾸기 -> 번호가 서로 바뀜
    cur = client.get("/api/classes").json()
    client.put("/api/classes", json=[{**cur[1], "orig": 1}, {**cur[0], "orig": 0}])
    assert label_classes(name) == [1, 0]

    # 잘못된 요청은 아무것도 바꾸지 않는다
    before = label_classes(name)
    assert client.put("/api/classes", json=[{**cur[0], "orig": 0, "color": "빨강"}]).status_code == 400
    assert client.put("/api/classes", json=[{**cur[0], "orig": 5}]).status_code == 400
    assert client.put("/api/classes", json=[]).status_code == 400
    assert label_classes(name) == before and len(client.get("/api/classes").json()) == 2

    stats = client.get("/api/dataset/stats").json()
    assert stats["labeled"] == 1 and [c["name"] for c in stats["per_class"]] == ["와셔", "볼트M6"]


def test_predict_uses_detector_and_maps_class_names(env):
    client, _, detections, _ = env
    set_roi(client, corners=FULL, width_mm=1280, height_mm=640)
    name = upload(client, ("a.jpg", jpeg(400, 200), "image/jpeg")).json()["added"][0]
    detections.extend([Detection((128, 64, 256, 192), 0.9, "너트"), Detection((0, 0, 40, 20), 0.7, "unknown")])
    boxes = client.post(f"/api/dataset/images/{name}/predict").json()["boxes"]
    assert [b["cls"] for b in boxes] == [1, 0]
    assert boxes[0]["x1"] == pytest.approx(0.1) and boxes[0]["y2"] == pytest.approx(0.3)


def test_data_yaml_split(env):
    _, _, _, tmp = env
    rack = Rack("R", "r", corners=[(0, 0), (1, 0), (1, 1), (0, 1)])
    names = [dataset.add_image(Image.new("RGB", (40, 30)), f"img{i}", rack, 64) for i in range(12)]
    raw = dataset.add_raw_image(jpeg(), "raw")
    dataset.write_labels(raw, [], 1)  # ROI 미적용 이미지는 라벨이 있어도 학습에서 제외
    for n in names[:10]:
        dataset.write_labels(n, [{"cls": 0, "x1": 0.1, "y1": 0.1, "x2": 0.2, "y2": 0.2}], 1)
    path = dataset.write_data_yaml(tmp / "runs", ["box_label"])
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    train = (tmp / "runs" / "train.txt").read_text().split()
    val = (tmp / "runs" / "val.txt").read_text().split()
    assert len(train) + len(val) == 10 and val and train and not set(train) & set(val)
    assert not any(raw in x for x in train + val)
    assert data["names"] == {0: "box_label"}
    # 이미지가 추가돼도 해시로 val 에 배정된 기존 이미지의 소속은 그대로
    hashed = {str(dataset.IMAGES / n) for n in names[:10] if dataset._is_val(n, 0.2)}
    dataset.write_labels(names[10], [], 1)
    dataset.write_data_yaml(tmp / "runs", ["box_label"])
    assert hashed <= set((tmp / "runs" / "val.txt").read_text().split())


def test_train_start_requires_labeled_data(env):
    client, *_ = env
    r = client.post("/api/train/start", json={"base_model": "yolo11n.pt", "epochs": 1, "imgsz": 320, "batch": 2})
    assert r.status_code == 400 and "최소 2장" in r.json()["detail"]
    assert client.post("/api/train/start", json={"base_model": "evil.pt"}).status_code == 400
    assert client.get("/api/train/status").json()["state"] == "idle"
