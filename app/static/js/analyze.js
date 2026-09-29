import { $, api, esc, guard, on, toast } from "./api.js";

const STATUS = { counted: "카운트", background: "배경 제외" };
let last = null;
let racks = [];

export function init() {
  $("#an-file").onchange = () => {
    const f = $("#an-file").files[0];
    $("#an-run").disabled = !f;
    $("#an-to-dataset").disabled = !f;
    if (f) $("#an-imgbox").innerHTML = `<img class="result-img" src="${URL.createObjectURL(f)}">`;
  };
  $("#an-run").onclick = analyze;
  $("#an-csv").onclick = downloadCsv;
  $("#an-rack").onchange = () => { rackWarning(); loadHistory(); };
  $("#an-to-dataset").onclick = guard(async () => {
    const fd = new FormData();
    fd.append("images", $("#an-file").files[0]);
    fd.append("rack_id", $("#an-rack").value);
    await api("/api/dataset/images", { method: "POST", form: fd });
    toast("ROI 보정 후 라벨링 데이터에 추가했습니다.");
  });
  on("racks-changed", loadRacks);
  loadRacks();
}

export function onShow() {
  loadRacks();
  modelStatus();
}

async function loadRacks() {
  racks = await api("/api/racks");
  const cur = $("#an-rack").value;
  $("#an-rack").innerHTML = racks.map((r) => `<option value="${esc(r.id)}">${esc(r.id)} · ${esc(r.name)}</option>`).join("")
    || `<option value="">등록된 랙 없음</option>`;
  if (racks.some((r) => r.id === cur)) $("#an-rack").value = cur;
  rackWarning();
  loadHistory();
}

function rackWarning() {
  const r = racks.find((x) => x.id === $("#an-rack").value);
  $("#an-roi-warn").hidden = !r || !!r.corners;
}

async function modelStatus() {
  const m = await api("/api/model");
  $("#an-model").innerHTML = m.exists
    ? `검출 모델: ${esc(m.updated_at?.replace("T", " "))} 학습${m.metrics?.["metrics/mAP50(B)"] != null ? ` · mAP50 ${m.metrics["metrics/mAP50(B)"].toFixed(3)}` : ""}`
    : `<span class="warn-text">검출 모델 없음 — [라벨링] → [학습] 탭에서 먼저 학습하세요.</span>`;
  if (m.stale) $("#an-model").innerHTML += ` <span class="warn-text">· 클래스가 바뀌어 재학습이 필요합니다</span>`;
}

async function analyze() {
  const fd = new FormData();
  fd.append("rack_id", $("#an-rack").value);
  fd.append("image", $("#an-file").files[0]);
  $("#an-run").disabled = true;
  $("#an-status").textContent = "분석 중... (첫 실행은 모델 로딩으로 오래 걸립니다)";
  try {
    last = await api("/api/analyze", { method: "POST", form: fd });
    render(last);
    $("#an-status").textContent = `완료 · ${last.analyzed_at.replace("T", " ")}`;
    loadHistory();
  } catch (e) {
    toast(e.message, "error");
    $("#an-status").textContent = "";
  } finally {
    $("#an-run").disabled = false;
  }
}

function render(r) {
  $("#an-imgbox").innerHTML = `<img class="result-img" src="${r.annotated_image}">`;
  $("#k-boxes").textContent = r.classes.reduce((a, s) => a + s.boxes, 0);
  $("#k-labels").textContent = r.labels.filter((l) => l.status === "counted").length;
  $("#k-need").textContent = r.purchase.filter((p) => p.need_purchase).length;
  $("#an-csv").disabled = false;

  $("#an-purchase").innerHTML = r.purchase.map((p) => `
    <tr class="${p.need_purchase ? "need" : ""}">
      <td><span class="swatch sm" style="background:${esc(p.color)}"></span>${esc(p.class_name)}<div class="muted">${esc(p.description)}</div></td>
      <td class="num">${p.stock_boxes}</td><td class="num">${p.managed ? p.safety_stock : "-"}</td><td class="num">${p.managed ? p.target_stock : "-"}</td>
      <td>${p.managed ? `<span class="badge ${p.need_purchase ? "need" : "ok"}">${p.need_purchase ? "▲ 구매 필요" : "✓ 적정"}</span>` : `<span class="badge" title="[클래스(부자재)] 탭에서 적정재고를 입력하세요">기준 없음</span>`}</td>
      <td class="num"><b>${p.order_boxes || "-"}</b></td><td class="num">${p.order_units ? p.order_units.toLocaleString() : "-"}</td>
    </tr>`).join("");

  $("#an-items").innerHTML = r.classes.map((s) => `
    <tr><td><span class="swatch sm" style="background:${esc(s.color)}"></span>${esc(s.class_name)}</td><td class="num">${s.visible_labels}</td><td class="num">${s.boxes}</td></tr>`).join("")
    || `<tr><td colspan="3" class="muted">-</td></tr>`;
  $("#an-front").textContent = `랙 앞면 깊이(좌상·우상·우하·좌하): ${r.front_depth_m.map((d) => d.toFixed(2)).join(" · ")} m`;

  $("#an-labels").innerHTML = r.labels.map((l, i) => `
    <tr><td>${i + 1}</td><td>${esc(l.class_name)}</td>
    <td class="num">${l.conf.toFixed(2)}</td><td class="num">${l.recess_mm ?? "-"}</td>
    <td class="num">${l.rows || "-"}</td><td>${STATUS[l.status]}</td></tr>`).join("")
    || `<tr><td colspan="6" class="muted">검출된 라벨 없음</td></tr>`;
}

async function loadHistory() {
  const rack = $("#an-rack").value;
  if (!rack) return;
  const rows = await api(`/api/history?rack_id=${encodeURIComponent(rack)}&limit=10`);
  $("#an-history").innerHTML = rows.map((h) => {
    const total = h.classes.reduce((a, s) => a + s.boxes, 0);
    const need = h.purchase.filter((p) => p.need_purchase).map((p) => `${esc(p.class_name)} ${p.order_boxes}`).join(", ");
    return `<tr><td>${esc(h.analyzed_at.replace("T", " "))}</td><td class="num">${total}</td><td>${need || "-"}</td></tr>`;
  }).join("") || `<tr><td colspan="3" class="muted">이력 없음</td></tr>`;
}

function downloadCsv() {
  const head = ["랙", "분석시각", "부자재(클래스)", "설명", "현재고(박스)", "적정재고", "목표재고", "발주단위", "구매필요", "구매수량(박스)", "구매수량(EA)"];
  const lines = last.purchase.map((p) => [last.rack_id, last.analyzed_at, p.class_name, p.description, p.stock_boxes, p.safety_stock,
    p.target_stock, p.order_lot, p.need_purchase ? "Y" : "N", p.order_boxes, p.order_units]);
  const csv = [head, ...lines].map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(",")).join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob(["﻿" + csv], { type: "text/csv" }));
  a.download = `purchase_${last.rack_id}_${last.analyzed_at.replace(/[:T-]/g, "")}.csv`;
  a.click();
}
