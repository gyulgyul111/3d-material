import { $, api, emit, esc, guard, on, toast } from "./api.js";
import { QuadCanvas } from "./boxcanvas.js";

let racks = [];
let classes = [];
let rack = null; // 편집 중인 랙 (복사본)
let dirty = false;
let canvas;
let previewTimer;

export function init() {
  canvas = new QuadCanvas($("#rk-canvas"), {
    onChange: () => { rack.corners = canvas.points; markDirty(); updateRoiStatus(); schedulePreview(); },
    onMode: setClicking,
  });
  canvas.placeholder = "기준 사진을 올려주세요 (고정 카메라로 찍은 랙 사진)";

  $("#rk-select").onchange = () => {
    if (dirty && !confirm("저장하지 않은 변경이 있습니다. 버리고 이동할까요?")) {
      $("#rk-select").value = rack.id;
      return;
    }
    openRack($("#rk-select").value);
  };
  $("#rk-new").onclick = guard(newRack);
  $("#rk-delete").onclick = guard(deleteRack);
  $("#rk-save").onclick = guard(save);
  $("#rk-pick").onclick = () => (canvas.clicking ? (canvas.cancelClicking(), setClicking(false)) : (canvas.startClicking(), setClicking(true)));
  $("#rk-reset").onclick = () => {
    canvas.setPoints([[0.05, 0.05], [0.95, 0.05], [0.95, 0.95], [0.05, 0.95]]);
    rack.corners = canvas.points;
    markDirty(); updateRoiStatus(); schedulePreview();
  };
  $("#rk-image").onchange = guard(uploadImage);
  $("#rk-settings-save").onclick = guard(saveSettings);

  for (const [id, key, num] of [["#rk-name", "name"], ["#rk-depth", "rack_depth_mm", 1], ["#rk-w", "width_mm", 1], ["#rk-h", "height_mm", 1], ["#rk-cam", "camera_to_front_m", 1]]) {
    $(id).oninput = () => {
      rack[key] = num ? parseFloat($(id).value) || null : $(id).value;
      markDirty();
      if (key === "width_mm" || key === "height_mm") schedulePreview();
    };
  }
  $("#rk-classes").onchange = (e) => {
    const name = e.target.value;
    rack.classes = e.target.checked ? [...rack.classes, name] : rack.classes.filter((c) => c !== name);
    markDirty();
  };
  window.addEventListener("keydown", (e) => {
    if (!$("#tab-racks").classList.contains("active")) return;
    if (e.key === "Escape" && canvas.clicking) { canvas.cancelClicking(); setClicking(false); }
  });
  window.addEventListener("beforeunload", (e) => { if (dirty) e.preventDefault(); });
  on("classes-changed", async () => {
    classes = await api("/api/classes");
    await loadRacks(rack?.id); // 클래스 이름 변경/삭제가 랙 설정에도 반영됨
  });
}

export async function onShow() {
  classes = await api("/api/classes");
  await loadRacks(rack?.id);
  loadSettings();
}

async function loadRacks(keep) {
  racks = await api("/api/racks");
  $("#rk-select").innerHTML = racks.map((r) => `<option value="${esc(r.id)}">${esc(r.id)} · ${esc(r.name)}</option>`).join("");
  const id = racks.some((r) => r.id === keep) ? keep : racks[0]?.id;
  if (!dirty || !rack) await openRack(id);
  else $("#rk-select").value = rack.id;
}

async function openRack(id) {
  $("#rk-editor").hidden = !id;
  if (!id) { rack = null; return; }
  rack = structuredClone(racks.find((x) => x.id === id));
  $("#rk-select").value = id;
  $("#rk-name").value = rack.name;
  $("#rk-depth").value = rack.rack_depth_mm ?? 1000;
  $("#rk-w").value = rack.width_mm ?? "";
  $("#rk-h").value = rack.height_mm ?? "";
  $("#rk-cam").value = rack.camera_to_front_m ?? "";
  dirty = false;
  updateDirty();
  setClicking(false);
  renderClasses();
  await canvas.setImage(rack.has_image ? `/api/racks/${encodeURIComponent(id)}/image?t=${Date.now()}` : null);
  canvas.setPoints(rack.corners);
  updateRoiStatus();
  schedulePreview(0);
}

function renderClasses() {
  $("#rk-classes").innerHTML = classes.map((c) => `
    <label class="check"><input type="checkbox" value="${esc(c.name)}" ${rack.classes.includes(c.name) ? "checked" : ""}>
      <span class="swatch sm" style="background:${esc(c.color)}"></span>${esc(c.name)}
      <span class="muted">${esc(c.description)}</span></label>`).join("")
    || `<p class="muted">[클래스(부자재)] 탭에서 클래스를 먼저 등록하세요.</p>`;
}

function setClicking(v) {
  $("#rk-pick").classList.toggle("active", v);
  $("#rk-pick").textContent = v ? "찍기 취소 (Esc)" : "네 모서리 다시 찍기";
  $("#rk-roi-status").textContent = v ? "사진에서 랙 앞면 프레임의 모서리 네 곳을 차례로 클릭하세요." : $("#rk-roi-status").textContent;
}

function updateRoiStatus() {
  $("#rk-roi-status").innerHTML = rack.corners
    ? "노란 점을 끌어 모서리를 맞추세요. 어두운 영역은 학습과 추론에서 제외됩니다."
    : `<span class="warn-text">ROI가 없습니다. [네 모서리 다시 찍기]를 누르세요.</span>`;
}

function schedulePreview(delay = 300) {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(() => {
    const box = $("#rk-preview");
    if (!rack?.has_image || !rack.corners) {
      box.innerHTML = `<div class="placeholder">기준 사진과 ROI를 설정하면 보정된 이미지가 여기 표시됩니다.</div>`;
      return;
    }
    const c = rack.corners.flat().map((v) => v.toFixed(4)).join(",");
    const dims = rack.width_mm && rack.height_mm ? `&width_mm=${rack.width_mm}&height_mm=${rack.height_mm}` : "";
    const url = `/api/racks/${encodeURIComponent(rack.id)}/preview?c=${c}${dims}`;
    const img = new Image();
    img.className = "result-img";
    img.onload = () => { box.innerHTML = ""; box.appendChild(img); };
    img.onerror = () => { box.innerHTML = `<div class="placeholder warn-text">보정할 수 없는 모서리 배치입니다.</div>`; };
    img.src = url;
  }, delay);
}

function markDirty() { dirty = true; updateDirty(); }
function updateDirty() { $("#rk-dirty").hidden = !dirty; }

async function save() {
  if (!rack.corners) throw new Error("ROI(네 모서리)를 먼저 지정하세요.");
  await api(`/api/racks/${encodeURIComponent(rack.id)}`, {
    method: "PUT",
    json: {
      name: rack.name, corners: rack.corners, rack_depth_mm: rack.rack_depth_mm || 1000,
      width_mm: rack.width_mm || null, height_mm: rack.height_mm || null,
      camera_to_front_m: rack.camera_to_front_m || null, classes: rack.classes,
    },
  });
  dirty = false;
  updateDirty();
  toast("랙 설정을 저장했습니다.");
  emit("racks-changed");
  await loadRacks(rack.id);
}

async function newRack() {
  const id = prompt("새 랙 ID (영문/숫자/-/_)", "RACK-");
  if (!id) return;
  const name = prompt("랙 이름", id) ?? id;
  await api("/api/racks", { method: "POST", json: { id: id.trim(), name } });
  dirty = false;
  emit("racks-changed");
  await loadRacks(id.trim());
  toast("랙을 만들었습니다. 기준 사진을 올리고 네 모서리를 찍으세요.");
}

async function deleteRack() {
  if (!rack || !confirm(`${rack.id} 랙을 삭제할까요? 설정과 기준 사진이 삭제됩니다.`)) return;
  await api(`/api/racks/${encodeURIComponent(rack.id)}`, { method: "DELETE" });
  dirty = false;
  rack = null;
  emit("racks-changed");
  await loadRacks();
}

async function uploadImage() {
  const f = $("#rk-image").files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append("image", f);
  await api(`/api/racks/${encodeURIComponent(rack.id)}/image`, { method: "POST", form: fd });
  $("#rk-image").value = "";
  rack.has_image = true;
  racks.find((r) => r.id === rack.id).has_image = true;
  await canvas.setImage(`/api/racks/${encodeURIComponent(rack.id)}/image?t=${Date.now()}`);
  canvas.setPoints(rack.corners);
  if (!rack.corners) { canvas.startClicking(); setClicking(true); }
  schedulePreview(0);
  toast(rack.corners ? "기준 사진을 바꿨습니다. 모서리 위치를 확인하세요." : "기준 사진을 올렸습니다. 랙 모서리 네 곳을 클릭하세요.");
}

async function loadSettings() {
  const s = await api("/api/settings");
  $("#st-conf").value = s.detector_conf;
  $("#st-margin").value = s.background_margin_mm;
  $("#st-tol").value = s.depth_tolerance_ratio;
  $("#st-box").value = s.default_box_depth_mm;
}

async function saveSettings() {
  await api("/api/settings", {
    method: "PUT",
    json: {
      detector_conf: parseFloat($("#st-conf").value),
      background_margin_mm: parseFloat($("#st-margin").value),
      depth_tolerance_ratio: parseFloat($("#st-tol").value),
      default_box_depth_mm: parseFloat($("#st-box").value),
    },
  });
  toast("분석 파라미터를 저장했습니다.");
}
