import numpy as np
import pytest
from PIL import Image

from app.config import ClassInfo, Rack, Settings, load_config, order_corners, parse_config
from app.counting import Detection, count_rack, front_plane, rows_from_recess
from app.inventory import purchase_line, purchase_plan
from app.rectify import output_size, roi_mask, warp_depth, warp_image

W, H = 1000, 800
FRONT_M = 1.8
CLASSES = {
    "볼트": ClassInfo("볼트", "#2a78d6", 20, 40, 10, 50, box_depth_mm=300),
    "너트": ClassInfo("너트", "#eb6834", 15, 30, 5, 100, box_depth_mm=250),
}
RACK = Rack("R1", "테스트 랙", corners=[(0, 0), (1, 0), (1, 1), (0, 1)], rack_depth_mm=1000,
            camera_to_front_m=FRONT_M, classes=["볼트"])


def depth_with_labels(labels, base=FRONT_M):
    """모든 곳이 랙 앞면 깊이(base)이고 라벨 위치만 base + recess 인 합성 깊이맵 (보정 이미지 좌표)."""
    d = np.full((H, W), base, dtype=np.float32)
    for (x1, y1, x2, y2), recess_m in labels:
        d[y1:y2, x1:x2] = base + recess_m
    return d


@pytest.mark.parametrize("recess_mm,expected", [
    (0, 3),      # 맨 앞까지 꽉 참 -> 1000/300 = 3열
    (80, 3),     # 깊이 오차 수준
    (320, 2),    # 한 열 빠짐
    (650, 1),    # 두 열 빠짐
    (950, 1),    # 라벨이 보이면 최소 1
    (-120, 3),   # 앞면보다 앞으로 나온 측정값은 0으로 처리
])
def test_rows_from_recess(recess_mm, expected):
    assert rows_from_recess(recess_mm, 1000, 300, 0.3) == expected


def test_count_uses_class_box_depth_and_filters_background():
    boxes = {
        "full": ((100, 200, 200, 300), 0, "볼트"),          # 박스 300mm -> 3
        "one_gone": ((300, 200, 400, 300), 0.32, "볼트"),   # -> 2
        "behind": ((500, 200, 600, 300), 2.0, "볼트"),      # 뒤쪽 랙 -> 제외
        "nut": ((100, 500, 200, 600), 0.26, "너트"),        # 박스 250mm -> 740+75 -> 3
        "unknown": ((700, 500, 800, 600), 0, "옛클래스"),   # 설정에 없는 클래스 -> 기본 박스깊이 300 -> 3
    }
    depth = depth_with_labels([(b, r) for b, r, _ in boxes.values()])
    dets = [Detection(b, 0.9, c) for b, _, c in boxes.values()]
    rc = count_rack(dets, depth, RACK, Settings(), CLASSES)

    assert [lc.status for lc in rc.labels] == ["counted", "counted", "background", "counted", "counted"]
    assert [lc.rows for lc in rc.labels] == [3, 2, 0, 3, 3]
    # 클래스 순서대로, 설정에 없는 클래스는 뒤에
    assert list(rc.class_boxes.items()) == [("볼트", 5), ("너트", 3), ("옛클래스", 3)]
    bolt = rc.classes[0]
    assert (bolt.class_name, bolt.visible_labels, bolt.boxes) == ("볼트", 2, 5)


def test_rack_classes_reported_with_zero_stock():
    rack = Rack("R", "r", corners=RACK.corners, classes=["볼트", "너트"])
    rc = count_rack([], depth_with_labels([]), rack, Settings(), CLASSES)
    assert rc.class_boxes == {"볼트": 0, "너트": 0}


def test_front_plane_from_tilted_rack_and_scale():
    # 랙 앞면이 기울어 오른쪽이 0.4m 더 멀고, 모델이 전체를 0.9배로 작게 추정하는 경우
    u = np.linspace(0, 1, W)[None, :].repeat(H, 0)
    depth = (FRONT_M + 0.4 * u).astype(np.float32)
    box = (700, 300, 800, 400)
    depth[300:400, 700:800] += 0.32
    rc = count_rack([Detection(box, 0.9, "볼트")], depth * 0.9, RACK, Settings(), CLASSES)
    assert rc.labels[0].recess_mm == pytest.approx(320 * FRONT_M / (FRONT_M + 0.2), abs=15)
    assert rc.labels[0].rows == 2
    fp = front_plane(depth, RACK)
    assert fp.corner_depths_m[0] < fp.corner_depths_m[1]


def test_order_corners_any_click_order():
    pts = [(0.9, 0.85), (0.1, 0.1), (0.1, 0.9), (0.95, 0.12)]
    assert order_corners(pts) == [(0.1, 0.1), (0.95, 0.12), (0.9, 0.85), (0.1, 0.9)]


def test_invalid_corners_rejected():
    with pytest.raises(ValueError):
        parse_config({"racks": {"R": {"corners": [[0.1, 0.1], [0.2, 0.2], [0.3, 0.3], [0.4, 0.4]]}}})
    with pytest.raises(ValueError):
        parse_config({"racks": {"R": {"corners": [[0.1, 0.1], [1.2, 0.1], [0.9, 0.9], [0.1, 0.9]]}}})


def test_warp_maps_corners_to_rectangle():
    # 원본에서 비스듬한 사각형 안쪽만 흰색 -> 보정 결과는 전부 흰색
    import cv2

    img = np.zeros((600, 800, 3), dtype=np.uint8)
    quad = [(0.2, 0.1), (0.85, 0.2), (0.8, 0.9), (0.15, 0.8)]
    rack = Rack("R", "r", corners=quad, width_mm=2000, height_mm=1000)
    cv2.fillPoly(img, [np.array([[x * 800, y * 600] for x, y in quad], np.int32)], (255, 255, 255))
    out = warp_image(Image.fromarray(img), rack, 400)
    assert out.size == output_size(rack, 800, 600, 400) == (400, 200)
    assert np.asarray(out)[5:-5, 5:-5].mean() > 250
    assert warp_depth(np.ones((600, 800), np.float32), rack, 400).shape == (200, 400)
    mask = roi_mask(rack, 800, 600)
    assert mask[300, 400] and not mask[10, 10]


@pytest.mark.parametrize("stock,need,order", [
    (25, False, 0),
    (21, False, 0),
    (20, True, 20),   # 적정재고와 같으면(이하) 구매
    (13, True, 30),   # 27 부족 -> 10단위 올림 30
    (0, True, 40),
])
def test_purchase_line(stock, need, order):
    line = purchase_line(CLASSES["볼트"], stock)
    assert (line.need_purchase, line.order_boxes, line.order_units) == (need, order, order * 50)


def test_purchase_at_safety_when_target_equals_safety_orders_one_lot():
    c = ClassInfo("X", "#000000", safety_stock=10, target_stock=10, order_lot=4)
    assert purchase_line(c, 10).order_boxes == 4


def test_class_without_stock_criteria_is_not_ordered():
    line = purchase_line(ClassInfo("X", "#000000"), 0)
    assert not line.managed and not line.need_purchase and line.order_boxes == 0


def test_purchase_plan_order_and_unknown_class():
    classes = {"볼트": CLASSES["볼트"], "너트": ClassInfo("너트", "#000000", 5, 10), "기타": ClassInfo("기타", "#000000")}
    plan = purchase_plan({"볼트": 30, "옛클래스": 0, "기타": 0, "너트": 1}, classes)
    assert [p.class_name for p in plan] == ["너트", "볼트", "기타", "옛클래스"]
    assert not plan[-1].need_purchase and "재학습" in plan[-1].description


def test_class_config_validation():
    cfg = parse_config({})
    assert cfg.class_names == ["box_label"] and cfg.classes[0].color == "#2a78d6"
    with pytest.raises(ValueError, match="중복"):
        parse_config({"classes": [{"name": "a"}, {"name": "a"}]})
    with pytest.raises(ValueError, match="색상"):
        parse_config({"classes": [{"name": "a", "color": "red"}]})


def test_default_config_loads():
    cfg = load_config()
    assert "RACK-A01" in cfg.racks and cfg.classes
    for rack in cfg.racks.values():
        assert all(name in cfg.by_name for name in rack.classes)
