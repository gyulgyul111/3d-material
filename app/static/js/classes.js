// 클래스(부자재) 관리: 라벨링 클래스 = 부자재. 색상, 재고 기준, 박스 깊이를 클래스별로 관리한다.
import { $, PALETTE, api, emit, esc, guard, toast } from "./api.js";

const NUM = [
  ["safety_stock", 0, 1], ["target_stock", 0, 1], ["order_lot", 1, 1], ["units_per_box", 1, 1], ["box_depth_mm", 1, 10],
];
let rows = []; // { orig, name, color, description, ...재고기준, boxes }
let dirty = false;

export function init() {
  $("#cl-add").onclick = () => {
    const used = new Set(rows.map((r) => r.color.toLowerCase()));
    rows.push({
      orig: null, name: "", description: "", color: PALETTE.find((c) => !used.has(c)) ?? "#8a8a86",
      safety_stock: 0, target_stock: 0, order_lot: 1, units_per_box: 1, box_depth_mm: 300, boxes: 0,
    });
    markDirty(true);
    render();
    $("#cl-rows tr:last-child input[data-f=name]").focus();
  };
  $("#cl-save").onclick = guard(save);
  $("#cl-reload").onclick = guard(async () => {
    if (dirty && !confirm("저장하지 않은 변경을 버릴까요?")) return;
    markDirty(false);
    await onShow();
  });
  $("#cl-rows").addEventListener("input", (e) => {
    const tr = e.target.closest("tr[data-i]");
    if (!tr) return;
    const r = rows[+tr.dataset.i];
    const f = e.target.dataset.f;
    r[f] = e.target.type === "number" ? parseFloat(e.target.value) : e.target.value;
    if (f === "color") tr.querySelector(".swatch").style.background = e.target.value;
    markDirty(true);
  });
  $("#cl-rows").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-i]");
    const btn = e.target.closest("button[data-act]");
    if (!tr || !btn) return;
    const i = +tr.dataset.i;
    const act = btn.dataset.act;
    if (act === "up" && i > 0) [rows[i - 1], rows[i]] = [rows[i], rows[i - 1]];
    else if (act === "down" && i < rows.length - 1) [rows[i + 1], rows[i]] = [rows[i], rows[i + 1]];
    else if (act === "del") rows.splice(i, 1);
    else return;
    markDirty(true);
    render();
  });
  window.addEventListener("beforeunload", (e) => { if (dirty) e.preventDefault(); });
}

export async function onShow() {
  if (dirty) return;
  const list = await api("/api/classes");
  rows = list.map((c) => ({ ...c, orig: c.index }));
  render();
  modelNote();
}

async function modelNote() {
  const m = await api("/api/model");
  $("#cl-model").hidden = !m.stale;
}

function render() {
  $("#cl-rows").innerHTML = rows.map((r, i) => `
    <tr data-i="${i}">
      <td class="muted">${i}</td>
      <td><label class="color-pick"><span class="swatch" style="background:${esc(r.color)}"></span>
        <input type="color" data-f="color" value="${esc(r.color)}" aria-label="${esc(r.name)} 색상"></label></td>
      <td><input data-f="name" value="${esc(r.name)}" placeholder="예: 볼트M6" size="12"></td>
      <td><input data-f="description" value="${esc(r.description)}" placeholder="설명(선택)" size="14"></td>
      ${NUM.map(([f, min, step]) => `<td><input data-f="${f}" type="number" min="${min}" step="${step}" value="${r[f]}" class="w-num"></td>`).join("")}
      <td class="num">${r.orig == null ? `<span class="badge ok">새 클래스</span>` : r.boxes}</td>
      <td class="nowrap">
        <button class="icon" data-act="up" title="위로" ${i === 0 ? "disabled" : ""}>▲</button>
        <button class="icon" data-act="down" title="아래로" ${i === rows.length - 1 ? "disabled" : ""}>▼</button>
        <button class="icon danger" data-act="del" title="삭제">✕</button>
      </td>
    </tr>`).join("") || `<tr><td colspan="12" class="muted">클래스가 없습니다. [＋ 클래스 추가]를 누르세요.</td></tr>`;
}

function markDirty(v) {
  dirty = v;
  $("#cl-dirty").hidden = !v;
}

async function save() {
  const names = rows.map((r) => r.name.trim());
  if (!rows.length) throw new Error("클래스가 하나 이상 있어야 합니다.");
  if (names.some((n) => !n)) throw new Error("클래스 이름을 입력하세요.");
  if (new Set(names).size !== names.length) throw new Error("클래스 이름이 중복됩니다.");

  const low = rows.find((r) => r.target_stock < r.safety_stock);
  if (low && !confirm(`${low.name}: 목표재고(${low.target_stock})가 적정재고(${low.safety_stock})보다 작습니다. 그대로 저장할까요?`)) return;

  // 삭제되는 클래스와 지워질 박스 수를 확인
  const orig = await api("/api/classes");
  const kept = new Set(rows.filter((r) => r.orig != null).map((r) => r.orig));
  const deleted = orig.filter((c) => !kept.has(c.index));
  if (deleted.length) {
    const lines = deleted.map((c) => `· ${c.name}: 라벨 박스 ${c.boxes}개`).join("\n");
    if (!confirm(`다음 클래스를 삭제합니다. 라벨링한 박스도 함께 지워지며 되돌릴 수 없습니다.\n\n${lines}\n\n계속할까요?`)) return;
  }
  const reordered = rows.some((r, i) => r.orig != null && r.orig !== i);
  if (reordered && !deleted.length && !confirm("클래스 순서를 바꾸면 라벨 파일의 클래스 번호도 함께 바뀝니다. 계속할까요?")) return;

  const res = await api("/api/classes", {
    method: "PUT",
    json: rows.map((r) => ({
      orig: r.orig, name: r.name.trim(), color: r.color, description: r.description ?? "",
      safety_stock: r.safety_stock | 0, target_stock: r.target_stock | 0, order_lot: r.order_lot | 0 || 1,
      units_per_box: r.units_per_box | 0 || 1, box_depth_mm: r.box_depth_mm || 300,
    })),
  });
  markDirty(false);
  toast(res.removed_boxes ? `저장했습니다. 삭제된 클래스의 라벨 박스 ${res.removed_boxes}개를 지웠습니다.` : "클래스를 저장했습니다.");
  emit("classes-changed");
  await onShow();
}
