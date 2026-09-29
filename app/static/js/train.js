import { $, api, esc, guard, toast } from "./api.js";

const STATE = {
  idle: ["대기", ""], running: ["● 학습 중", "run"], finished: ["✓ 완료", "ok"],
  failed: ["✕ 실패", "need"], stopped: ["■ 중지됨", ""],
};
const SERIES = [
  { key: "map50", name: "mAP50", color: "var(--series-1)" },
  { key: "map50_95", name: "mAP50-95", color: "var(--series-2)" },
];
let timer = null;
let lastState = null;

export function init() {
  $("#tr-start").onclick = guard(start);
  $("#tr-stop").onclick = guard(stop);
  document.addEventListener("visibilitychange", () => { if (!document.hidden && isActive()) poll(); });
}

const isActive = () => $("#tab-train").classList.contains("active");

export async function onShow() {
  await Promise.all([loadStats(), loadModel()]);
  await poll();
}

async function loadStats() {
  const s = await api("/api/dataset/stats");
  $("#tr-stats").innerHTML = `
    <div class="kpi"><div class="v">${s.trainable}</div><div class="k">학습 가능 이미지</div></div>
    <div class="kpi"><div class="v">${s.unlabeled}</div><div class="k">미라벨 이미지</div></div>
    <div class="kpi"><div class="v">${s.boxes}</div><div class="k">라벨 박스</div></div>`;
  const classes = await api("/api/classes");
  $("#tr-classes").innerHTML = s.per_class.map((c) => `<tr><td>${c.cls}</td>
    <td><span class="swatch sm" style="background:${esc(classes[c.cls]?.color ?? "#8a8a86")}"></span>${esc(c.name)}</td>
    <td class="num">${c.boxes}</td></tr>`).join("");
  const warns = [];
  if (s.raw) warns.push(`ROI가 적용되지 않은 이미지 ${s.raw}장은 학습에서 제외됩니다. [라벨링] 탭에서 [ROI 적용]을 누르세요.`);
  if (s.stale) warns.push(`랙 ROI가 바뀐 이미지 ${s.stale}장이 있습니다. [라벨링] 탭에서 다시 적용하는 것을 권장합니다.`);
  if (s.trainable < 2) warns.push("학습 가능(ROI 적용 + 라벨 완료) 이미지가 2장 이상 있어야 학습할 수 있습니다.");
  else if (s.trainable < 100) warns.push(`학습 가능 이미지가 ${s.trainable}장입니다. 실사용 정확도를 위해 200장 이상을 권장합니다.`);
  const warn = warns.join("\n");
  $("#tr-warn").textContent = warn;
  $("#tr-warn").hidden = !warn;
}

async function loadModel() {
  const m = await api("/api/model");
  const sel = $("#tr-base");
  const cur = sel.value;
  sel.innerHTML = (m.exists ? `<option value="current">현재 모델에서 이어서 학습</option>` : "")
    + m.base_models.map((b) => `<option value="${b}">${b}${b === "yolo11s.pt" ? " (권장)" : ""}</option>`).join("");
  sel.value = [...sel.options].some((o) => o.value === cur) ? cur : "yolo11s.pt";
  if (!m.exists) {
    $("#tr-model").innerHTML = `<p class="muted">아직 학습된 모델이 없습니다.</p>`;
    return;
  }
  const mt = m.metrics || {};
  const fmt = (k) => (mt[k] != null ? mt[k].toFixed(3) : "-");
  $("#tr-model").innerHTML = `
    <table><tbody>
      <tr><th>학습 시각</th><td>${esc((m.trained_at || m.updated_at).replace("T", " "))}</td></tr>
      <tr><th>기본 모델</th><td>${esc(m.base_model ?? "-")} · ${m.epochs ?? "-"} epoch · imgsz ${m.imgsz ?? "-"}</td></tr>
      <tr><th>클래스</th><td>${esc((m.classes || []).join(", ") || "-")}${m.stale ? ` <span class="badge need">현재 클래스와 다름 — 재학습 필요</span>` : ""}</td></tr>
      <tr><th>mAP50 / mAP50-95</th><td>${fmt("metrics/mAP50(B)")} / ${fmt("metrics/mAP50-95(B)")}</td></tr>
      <tr><th>Precision / Recall</th><td>${fmt("metrics/precision(B)")} / ${fmt("metrics/recall(B)")}</td></tr>
    </tbody></table>`;
}

async function start() {
  const body = {
    base_model: $("#tr-base").value,
    epochs: parseInt($("#tr-epochs").value, 10),
    imgsz: parseInt($("#tr-imgsz").value, 10),
    batch: parseInt($("#tr-batch").value, 10),
  };
  await api("/api/train/start", { method: "POST", json: body });
  toast("학습을 시작했습니다. 이 페이지를 닫아도 학습은 계속됩니다.");
  poll();
}

async function stop() {
  if (!confirm("학습을 중지할까요? 중지하면 모델은 교체되지 않습니다.")) return;
  await api("/api/train/stop", { method: "POST" });
  poll();
}

async function poll() {
  clearTimeout(timer);
  const s = await api("/api/train/status");
  render(s);
  if (lastState === "running" && s.state !== "running") {
    toast(s.state === "finished" ? "학습이 끝나 새 모델이 적용되었습니다." : "학습이 종료되었습니다. 로그를 확인하세요.", s.state === "finished" ? "ok" : "error");
    loadModel();
  }
  lastState = s.state;
  if (s.state === "running" && isActive() && !document.hidden) timer = setTimeout(poll, 2000);
}

function render(s) {
  const [label, cls] = STATE[s.state];
  $("#tr-state").textContent = label;
  $("#tr-state").className = `badge ${cls}`;
  const running = s.state === "running";
  $("#tr-start").disabled = running;
  $("#tr-stop").disabled = !running;
  $("#tr-params").querySelectorAll("input, select").forEach((el) => (el.disabled = running));

  const total = s.params?.epochs || 0;
  const done = s.epochs.length;
  $("#tr-progress").style.width = total ? `${(100 * done) / total}%` : "0";
  $("#tr-progress-text").textContent = total ? `${done} / ${total} epoch${s.started_at ? ` · 시작 ${s.started_at.replace("T", " ")}` : ""}` : "";

  const lastEp = s.epochs.at(-1);
  $("#tr-latest").innerHTML = lastEp
    ? ["precision", "recall", "map50", "map50_95", "box_loss", "val_box_loss"].map((k) =>
      `<div><span class="muted">${{ precision: "Precision", recall: "Recall", map50: "mAP50", map50_95: "mAP50-95", box_loss: "train box loss", val_box_loss: "val box loss" }[k]}</span><b>${lastEp[k].toFixed(3)}</b></div>`).join("")
    : "";
  chart($("#tr-chart"), s.epochs);
  const log = $("#tr-log");
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 10;
  log.textContent = s.log || "로그 없음";
  if (atBottom) log.scrollTop = log.scrollHeight;
}

// mAP 추이 선 그래프 (두 지표 모두 0~1 이라 축 하나)
function chart(el, rows) {
  if (!rows.length) {
    el.innerHTML = `<div class="chart-empty muted">학습이 시작되면 epoch마다 검증 성능이 표시됩니다.</div>`;
    return;
  }
  const W = el.clientWidth || 600, H = 220, m = { l: 36, r: 100, t: 12, b: 26 };
  const maxEp = Math.max(rows.at(-1).epoch, 2);
  const x = (e) => m.l + ((e - 1) / (maxEp - 1)) * (W - m.l - m.r);
  const y = (v) => m.t + (1 - v) * (H - m.t - m.b);
  const grid = [0, 0.25, 0.5, 0.75, 1].map((v) => `
    <line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" class="grid"/>
    <text x="${m.l - 6}" y="${y(v) + 4}" class="tick" text-anchor="end">${v}</text>`).join("");
  const xt = [...new Set([1, Math.round(maxEp / 2), maxEp])].map((e) => `<text x="${x(e)}" y="${H - 6}" class="tick" text-anchor="middle">${e}</text>`).join("");
  const lastR = rows.at(-1);
  // 끝점 라벨이 겹치지 않게 세로로 벌린다
  const ly = SERIES.map((s) => y(lastR[s.key]) + 4);
  if (Math.abs(ly[0] - ly[1]) < 14) {
    const mid = (ly[0] + ly[1]) / 2, up = ly[0] <= ly[1] ? 0 : 1;
    ly[up] = mid - 7;
    ly[1 - up] = mid + 7;
  }
  const lines = SERIES.map((s, i) => {
    const pts = rows.map((r) => `${x(r.epoch)},${y(r[s.key])}`).join(" ");
    return `<polyline points="${pts}" style="fill:none;stroke:${s.color};stroke-width:2;stroke-linejoin:round"/>
      <circle cx="${x(lastR.epoch)}" cy="${y(lastR[s.key])}" r="4" style="fill:${s.color};stroke:var(--panel);stroke-width:2"/>
      <text x="${x(lastR.epoch) + 8}" y="${ly[i]}" class="dlabel">${s.name} ${lastR[s.key].toFixed(2)}</text>`;
  }).join("");
  el.innerHTML = `
    <div class="legend">${SERIES.map((s) => `<span><i style="background:${s.color}"></i>${s.name}</span>`).join("")}<span class="muted">x축: epoch</span></div>
    <div class="chart-wrap">
      <svg width="${W}" height="${H}" role="img" aria-label="epoch별 mAP50, mAP50-95 추이">
        ${grid}${xt}${lines}
        <line class="xhair" y1="${m.t}" y2="${H - m.b}" style="display:none"/>
        <rect x="${m.l}" y="0" width="${W - m.l - m.r}" height="${H}" fill="transparent" class="hit"/>
      </svg>
      <div class="tip" hidden></div>
    </div>`;
  const svg = el.querySelector("svg"), tip = el.querySelector(".tip"), xh = el.querySelector(".xhair");
  el.querySelector(".hit").addEventListener("pointermove", (ev) => {
    const px = ev.clientX - svg.getBoundingClientRect().left;
    const r = rows.reduce((a, b) => (Math.abs(x(b.epoch) - px) < Math.abs(x(a.epoch) - px) ? b : a));
    xh.setAttribute("x1", x(r.epoch)); xh.setAttribute("x2", x(r.epoch)); xh.style.display = "";
    tip.hidden = false;
    tip.innerHTML = `<b>epoch ${r.epoch}</b>` + SERIES.map((s) => `<div><i style="background:${s.color}"></i>${s.name} <b>${r[s.key].toFixed(3)}</b></div>`).join("");
    tip.style.left = `${Math.min(x(r.epoch) + 12, W - 150)}px`;
    tip.style.top = "8px";
  });
  el.querySelector(".hit").addEventListener("pointerleave", () => { tip.hidden = true; xh.style.display = "none"; });
}
