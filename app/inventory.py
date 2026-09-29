from __future__ import annotations

import math
from dataclasses import dataclass

from .config import ClassInfo


@dataclass
class PurchaseLine:
    class_name: str
    description: str
    color: str
    stock_boxes: int
    safety_stock: int
    target_stock: int
    order_lot: int
    managed: bool  # 재고 기준이 설정된 클래스인지
    need_purchase: bool
    order_boxes: int
    order_units: int


def purchase_line(c: ClassInfo, stock_boxes: int) -> PurchaseLine:
    """현재고가 적정재고 이하이면 목표재고까지 채울 수량을 발주 단위로 올림해서 계산한다."""
    need = c.managed and stock_boxes <= c.safety_stock
    order = 0
    if need:
        shortfall = max(c.target_stock - stock_boxes, 0)
        # 목표재고 = 적정재고이고 현재고가 딱 적정재고인 경우에도 최소 1 lot은 발주
        order = max(math.ceil(shortfall / c.order_lot), 1) * c.order_lot
    return PurchaseLine(
        class_name=c.name,
        description=c.description,
        color=c.color,
        stock_boxes=stock_boxes,
        safety_stock=c.safety_stock,
        target_stock=c.target_stock,
        order_lot=c.order_lot,
        managed=c.managed,
        need_purchase=need,
        order_boxes=order,
        order_units=order * c.units_per_box,
    )


def purchase_plan(class_boxes: dict[str, int], classes: dict[str, ClassInfo]) -> list[PurchaseLine]:
    lines = []
    for name, boxes in class_boxes.items():
        c = classes.get(name)
        if c is None:
            # 모델이 예전 클래스 이름을 낸 경우(클래스 삭제/이름 변경 후 재학습 전): 수량만 보고
            lines.append(PurchaseLine(name, "(삭제되었거나 이름이 바뀐 클래스 — 재학습 필요)", "#8a8a86",
                                      boxes, 0, 0, 1, False, False, 0, 0))
        else:
            lines.append(purchase_line(c, boxes))
    order = {n: i for i, n in enumerate(classes)}
    # 구매 필요 → 기준 있음 → 클래스 순서
    return sorted(lines, key=lambda l: (not l.need_purchase, not l.managed, order.get(l.class_name, 1_000_000)))
