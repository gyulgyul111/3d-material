import { $, api, esc, guard, on, toast } from "./api.js";
import { BoxCanvas } from "./boxcanvas.js";

let images = [];
let classes = []; // [{ name, color, ... }] — 순서 = 클래스 번호
let curClass = 0;
let current = null; // 현재 이미지 이름
let dirty = false;
let canvas;
let roiRacks = []; // ROI 가 설정된 랙
const imageVersion = {}; // ROI 를 다시 적용해 이미지가 바뀌면 올려서 캐시된 예전 이미지를 쓰지 않게 한다
const ROI_BADGE = { ok: "", raw: "ROI 미적용", stale: "ROI 변경됨" };

export function init() {
  canvas = new BoxCanvas($("#lb-canvas"), {
    canDraw: () => !!current,
    drawColor: () => classes[curClass]?.color ?? "#fff",
    onCreate: (r) => {
      canvas.boxes.push(decorate({ ...r, cls: curClass }));
      canvas.select(canvas.boxes.length - 1);
      markDirty();
    },
    onChange: markDirty,
    onSelect: (i) => { if (i >= 0) setClass(canvas.boxes[i].cls, false); },
  });
  canvas.placeholder = "왼쪽 목록에서 이미지를 선택하거나 사진을 추가하세요";

  $("#lb-upload").onchange = guard(upload);
  $("#lb-rack").onchange = () => ($("#lb-rack").dataset.chosen = "1");
  $("#lb-filter").onchange = renderList;
  $("#lb-list").onclick = guard(async (e) => {
    const li = e.target.closest("[data-name]");
    if (li) await open(li.dataset.name);
  });
  $("#lb-classes").onclick = (e) => {
    const b = e.target.closest("[data-cls]");
    if (b) setClass(+b.dataset.cls, true);
  };
  $("#lb-prev").onclick = guard(() => step(-1));
  $("#lb-next").onclick = guard(() => step(1));
  $("#lb-save").onclick = guard(save);
  $("#lb-predict").onclick = guard(predict);
  $("#lb-clear").onclick = () => { canvas.setBoxes([]); markDirty(); };
  $("#lb-del-img").onclick = guard(deleteImage);
  $("#lb-roi-apply").onclick = guard(() => applyRoi([current]));
  $("#lb-roi-all").onclick = guard(() => applyRoi(null));
  on("racks-changed", () => { if ($("#tab-label").classList.contains("active")) onShow(); });
  on("classes-changed", async () => {
    classes = await api("/api/classes");
    curClass = Math.min(curClass, classes.length - 1);
    renderClasses();
    if (current) {
      // 클래스 삭제/순서 변경으로 라벨 파일 번호가 바뀌었을 수 있으니 다시 읽는다
      const { boxes } = await api(`/api/dataset/images/${encodeURIComponent(current)}/labels`);
      canvas.setBoxes(boxes.map(decorate));
      dirty = false;
      updateInfo();
    }
  });
  window.addEventListener("keydown", guard(keydown));
}

export async function onShow() {
  // 라벨링 이미지는 항상 랙 ROI 로 보정한다 (추론 때 보는 이미지와 같아야 하므로)
  roiRacks = (await api("/api/racks")).filter((r) => r.corners);
  const cur = $("#lb-rack").value;
  $("#lb-rack").innerHTML = roiRacks.map((r) => `<option value="${esc(r.id)}">${esc(r.id)} · ${esc(r.name)}</option>`).join("")
    || `<option value="">ROI가 설정된 랙이 없음</option>`;
  if (roiRacks.some((r) => r.id === cur)) $("#lb-rack").value = cur;
  $("#lb-upload").disabled = !roiRacks.length;
  $("#lb-no-roi").hidden = !!roiRacks.length;
  classes = await api("/api/classes");
  renderClasses();
  await refreshList();
}

async function refreshList() {
  images = await api("/api/dataset/images");
  renderList();
}

function filtered() {
  const f = $("#lb-filter").value;
  return images.filter((i) => f === "all" || (f === "roi" ? i.roi !== "ok" : f === "unlabeled" ? !i.labeled : i.labeled));
}

function renderList() {
  const list = filtered();
  const done = images.filter((i) => i.labeled).length;
  $("#lb-count").textContent = `${done} / ${images.length} 라벨 완료`;
  const needRoi = images.filter((i) => i.roi !== "ok").length;
  $("#lb-roi-all").hidden = !needRoi;
  $("#lb-roi-all").textContent = `ROI 미적용·변경 ${needRoi}장 모두 적용`;
  $("#lb-list").innerHTML = list.map((i) => `
    <li data-name="${esc(i.name)}" class="${i.name === current ? "active" : ""}">
      <span class="dot ${i.labeled ? (i.boxes ? "done" : "bg") : ""}" title="${i.labeled ? (i.boxes ? "라벨 완료" : "박스 없음(배경)") : "미라벨"}"></span>
      <span class="fname">${esc(i.name)}</span>${i.roi !== "ok" ? `<span class="badge warn">${ROI_BADGE[i.roi]}</span>` : ""}<span class="muted">${i.labeled ? i.boxes : ""}</span>
    </li>`).join("") || `<li class="muted">이미지가 없습니다</li>`;
  $("#lb-list li.active")?.scrollIntoView({ block: "nearest" });
}

function renderClasses() {
  $("#lb-classes").innerHTML = classes.map((c, i) => `
    <button data-cls="${i}" class="cls ${i === curClass ? "active" : ""}" style="--c:${esc(c.color)}" title="${esc(c.description)}">
      <i></i>${i < 9 ? `<kbd>${i + 1}</kbd>` : ""}${esc(c.name)}
    </button>`).join("");
}

function setClass(i, applyToSelected) {
  if (i < 0 || i >= classes.length) return;
  curClass = i;
  if (applyToSelected && canvas.selected >= 0) {
    Object.assign(canvas.boxes[canvas.selected], decorate({ ...canvas.boxes[canvas.selected], cls: i }));
    canvas.render();
    markDirty();
  }
  renderClasses();
}

function decorate(b) {
  const c = classes[b.cls];
  return { ...b, color: c?.color ?? "#8a8a86", label: c?.name ?? `#${b.cls}` };
}

async function open(name) {
  if (name === current) return;
  if (dirty) await save();
  current = name;
  const { boxes } = await api(`/api/dataset/images/${encodeURIComponent(name)}/labels`);
  await canvas.setImage(`/api/dataset/images/${encodeURIComponent(name)}?v=${imageVersion[name] ?? 0}`);
  canvas.setBoxes(boxes.map(decorate));
  dirty = false;
  updateInfo();
  renderList();
  canvas.el.focus();
}

// ROI 미적용 이미지는 선택한 랙 ROI 로, ROI 가 바뀐 이미지는 원래 랙의 새 ROI 로 원본에서 다시 보정한다.
// 라벨 박스도 같은 변환으로 옮겨지고, ROI 밖으로 나간 박스는 지워진다.
async function applyRoi(names) {
  if (dirty) await save();
  const targets = names ?? images.filter((i) => i.roi !== "ok").map((i) => i.name);
  if (!targets.length) return;
  const rack = $("#lb-rack").value;
  const hasRaw = images.some((i) => targets.includes(i.name) && i.roi === "raw");
  const msg = `${targets.length}장에 ROI를 적용합니다.${hasRaw ? `\nROI 미적용 이미지에는 [${rack}] 랙의 ROI를 씁니다.` : ""}\n라벨 박스도 함께 옮겨지며, ROI 밖으로 나가는 박스는 지워집니다.`;
  if (!confirm(msg)) return;
  const r = await api("/api/dataset/roi", { method: "POST", json: { names, rack_id: rack || null } });
  for (const n of targets) imageVersion[n] = Date.now();
  await refreshList();
  if (current && targets.includes(current)) {
    const name = current;
    current = null;
    await open(name);
  }
  const extra = r.dropped_boxes ? ` · ROI 밖 박스 ${r.dropped_boxes}개 삭제` : "";
  toast(r.errors.length ? `${r.done}장 적용${extra}\n${r.errors.join("\n")}` : `${r.done}장에 ROI를 적용했습니다${extra}.`, r.errors.length ? "error" : "ok");
}

async function step(d) {
  const list = filtered();
  if (!list.length) return;
  let i = list.findIndex((x) => x.name === current);
  i = i < 0 ? 0 : Math.min(Math.max(i + d, 0), list.length - 1);
  // '미라벨' 필터에서 저장하면 목록에서 빠지므로 인덱스를 저장 전에 결정한다
  const target = list[i].name;
  if (dirty) await save();
  await open(target);
}

async function save() {
  if (!current) return;
  const boxes = canvas.boxes.map(({ cls, x1, y1, x2, y2 }) => ({ cls, x1, y1, x2, y2 }));
  const res = await api(`/api/dataset/images/${encodeURIComponent(current)}/labels`, { method: "PUT", json: { boxes } });
  const img = images.find((i) => i.name === current);
  if (img) Object.assign(img, { labeled: true, boxes: res.boxes });
  dirty = false;
  updateInfo();
  renderList();
  toast(res.boxes ? `저장: 박스 ${res.boxes}개` : "저장: 박스 없음(배경 이미지로 학습)");
}

async function predict() {
  if (!current) return;
  if (canvas.boxes.length && !confirm("현재 박스를 모델 예측 결과로 바꿀까요?")) return;
  const { boxes } = await api(`/api/dataset/images/${encodeURIComponent(current)}/predict`, { method: "POST" });
  canvas.setBoxes(boxes.map(decorate));
  markDirty();
  toast(`모델이 ${boxes.length}개를 찾았습니다. 확인 후 저장하세요.`);
}

async function deleteImage() {
  if (!current || !confirm(`${current} 이미지와 라벨을 삭제할까요?`)) return;
  const list = filtered();
  const i = list.findIndex((x) => x.name === current);
  await api(`/api/dataset/images/${encodeURIComponent(current)}`, { method: "DELETE" });
  const next = list[i + 1]?.name ?? list[i - 1]?.name;
  current = null;
  dirty = false;
  await refreshList();
  if (next) await open(next);
  else { await canvas.setImage(null); updateInfo(); }
}

async function upload() {
  const files = [...$("#lb-upload").files];
  if (!files.length) return;
  $("#lb-count").textContent = `${files.length}장 업로드 중...`;
  // 한 번에 너무 많이 보내지 않도록 나눠서 올린다
  let added = 0;
  const errors = [];
  for (let i = 0; i < files.length; i += 10) {
    const fd = new FormData();
    files.slice(i, i + 10).forEach((f) => fd.append("images", f));
    fd.append("rack_id", $("#lb-rack").value);
    const r = await api("/api/dataset/images", { method: "POST", form: fd });
    added += r.added.length;
    errors.push(...r.errors);
  }
  $("#lb-upload").value = "";
  await refreshList();
  toast(errors.length ? `${added}장 추가, 읽지 못한 파일 ${errors.length}개` : `${added}장 추가했습니다.`, errors.length ? "error" : "ok");
}

function updateInfo() {
  const img = images.find((i) => i.name === current);
  $("#lb-info").textContent = current
    ? `${current} · ${img?.labeled ? "라벨 완료" : "미라벨"}${dirty ? " · 저장 안 됨" : ""}`
    : "";
  const roi = current ? img?.roi : "ok";
  $("#lb-roi-note").hidden = !roi || roi === "ok";
  if (roi === "raw") {
    $("#lb-roi-msg").textContent = "이 이미지는 ROI가 적용되지 않은 원본이라 학습에서 제외됩니다. 선택한 랙의 ROI로 보정하면 라벨도 함께 옮겨집니다.";
    $("#lb-roi-apply").textContent = `[${$("#lb-rack").value || "랙 없음"}] ROI 적용`;
  } else if (roi === "stale") {
    $("#lb-roi-msg").textContent = `[${img.rack_id}] 랙의 ROI가 바뀌었습니다. 원본에서 새 ROI로 다시 보정하면 라벨도 함께 옮겨집니다.`;
    $("#lb-roi-apply").textContent = "새 ROI로 다시 적용";
  }
  $("#lb-save").classList.toggle("primary", dirty);
}

function markDirty() {
  dirty = true;
  updateInfo();
}

function keydown(e) {
  if (!$("#tab-label").classList.contains("active")) return;
  if (e.target.closest("input, select, textarea")) return;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
    e.preventDefault();
    return save();
  }
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (/^[1-9]$/.test(k)) return setClass(+k - 1, true);
  if (k === "delete" || k === "backspace") {
    if (canvas.selected >= 0) {
      canvas.boxes.splice(canvas.selected, 1);
      canvas.select(-1);
      markDirty();
    }
    e.preventDefault();
  } else if (k === "a" || k === "arrowleft") {
    e.preventDefault();
    return step(-1);
  } else if (k === "d" || k === "arrowright") {
    e.preventDefault();
    return step(1);
  } else if (k === "s") {
    return save();
  } else if (k === "escape") {
    canvas.select(-1);
  }
}
