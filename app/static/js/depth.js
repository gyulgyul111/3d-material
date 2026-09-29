import { $, api, esc, guard, on, toast } from "./api.js";

let racks = [];
let result = null;
let grid = null; // { w, h, depth: Float32Array, recess: Float32Array|null }
let viewer = null;
let layer = "depth";

const f32 = (b64) => new Float32Array(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0)).buffer);
const u8 = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));

export function init() {
  $("#dp-run").onclick = guard(run);
  $("#dp-rack").onchange = syncSource;
  document.querySelectorAll("input[name=dp-src]").forEach((r) => (r.onchange = syncSource));
  $("#dp-layers").onclick = (e) => {
    const b = e.target.closest("[data-layer]");
    if (b && !b.disabled) showLayer(b.dataset.layer);
  };
  const img = $("#dp-img");
  img.addEventListener("mousemove", hover);
  img.addEventListener("mouseleave", () => ($("#dp-tip").hidden = true));
  for (const id of ["#dp-mode", "#dp-color", "#dp-fov", "#dp-size", "#dp-crop"]) $(id).oninput = () => rebuild(false);
  $("#dp-reset").onclick = () => viewer?.resetView();
  on("racks-changed", loadRacks);
}

export async function onShow() {
  await loadRacks();
}

async function loadRacks() {
  racks = await api("/api/racks");
  const cur = $("#dp-rack").value;
  $("#dp-rack").innerHTML = `<option value="">랙 선택 안 함</option>`
    + racks.map((r) => `<option value="${esc(r.id)}">${esc(r.id)} · ${esc(r.name)}${r.corners ? "" : " (ROI 없음)"}</option>`).join("");
  $("#dp-rack").value = racks.some((r) => r.id === cur) ? cur : racks.find((r) => r.has_image)?.id ?? "";
  syncSource();
}

function syncSource() {
  const rack = racks.find((r) => r.id === $("#dp-rack").value);
  const refOpt = $("input[name=dp-src][value=ref]");
  refOpt.disabled = !rack?.has_image;
  if (refOpt.disabled && refOpt.checked) $("input[name=dp-src][value=file]").checked = true;
  if (!refOpt.disabled && !$("#dp-file").files.length) refOpt.checked = true;
  $("#dp-file").disabled = $("input[name=dp-src]:checked").value !== "file";
  $("#dp-rectify").disabled = !rack?.corners;
}

async function run() {
  const fd = new FormData();
  const src = $("input[name=dp-src]:checked").value;
  fd.append("rack_id", $("#dp-rack").value);
  fd.append("rectify", String($("#dp-rectify").checked && !$("#dp-rectify").disabled));
  if (src === "ref") fd.append("use_reference", "true");
  else {
    if (!$("#dp-file").files[0]) throw new Error("이미지 파일을 선택하세요.");
    fd.append("image", $("#dp-file").files[0]);
  }
  $("#dp-run").disabled = true;
  $("#dp-status").textContent = "깊이 추정 중... (첫 실행은 모델 로딩으로 오래 걸립니다)";
  try {
    result = await api("/api/depth", { method: "POST", form: fd });
  } finally {
    $("#dp-run").disabled = false;
    $("#dp-status").textContent = "";
  }
  const g = result.view.grid;
  grid = { w: g.w, h: g.h, depth: f32(g.depth), recess: g.recess ? f32(g.recess) : null };
  $("#dp-empty").hidden = true;
  $("#dp-content").hidden = false;
  document.querySelector("[data-layer=recess]").disabled = !result.rectified;
  showLayer(result.rectified ? "recess" : "depth");

  const rk = result.rack;
  $("#dp-info").innerHTML = rk?.front_depth_m
    ? `랙 앞면 깊이(좌상·우상·우하·좌하): <b>${rk.front_depth_m.map((d) => d.toFixed(2)).join(" · ")} m</b>${rk.scale !== 1 ? ` · 실측 거리 보정 배율 ×${rk.scale.toFixed(3)}` : ""}`
    : "랙 ROI를 적용하면 랙 앞면 기준 깊이(가려진 박스 추정에 쓰는 값)를 볼 수 있습니다.";

  $("#dp-crop").disabled = !rk?.corners;
  if (!rk?.corners) $("#dp-crop").checked = false;
  await ensureViewer();
  const c = result.cloud;
  viewer.setData({ w: c.w, h: c.h, depth: f32(c.depth), rgb: u8(c.rgb), range: c.range, corners: rk?.corners ?? null });
  rebuild(true);
  toast("깊이 추정을 마쳤습니다.");
}

async function ensureViewer() {
  if (viewer) return;
  try {
    const { CloudViewer } = await import("./depth3d.js");
    viewer = new CloudViewer($("#dp-3d"));
  } catch (e) {
    $("#dp-3d").innerHTML = `<div class="placeholder">3D 뷰어를 불러오지 못했습니다: ${esc(e.message)}</div>`;
    throw e;
  }
}

function rebuild(resetView) {
  if (!viewer?.data) return;
  $("#dp-fov-v").textContent = `${$("#dp-fov").value}°`;
  viewer.build({
    mode: $("#dp-mode").value,
    color: $("#dp-color").value,
    fov: parseFloat($("#dp-fov").value),
    size: parseFloat($("#dp-size").value),
    cropRoi: $("#dp-crop").checked,
    resetView,
  });
}

function showLayer(name) {
  layer = name;
  document.querySelectorAll("#dp-layers [data-layer]").forEach((b) => b.classList.toggle("active", b.dataset.layer === name));
  const v = result.view;
  $("#dp-img").src = { rgb: v.rgb, depth: v.depth_color, recess: v.recess_color }[name];
  const bar = $("#dp-bar");
  bar.hidden = name === "rgb";
  if (name === "depth") {
    $("#dp-bar-lo").textContent = `${v.depth_range[0].toFixed(2)} m (가까움)`;
    $("#dp-bar-hi").textContent = `${v.depth_range[1].toFixed(2)} m (멂)`;
    $("#dp-bar-title").textContent = "카메라로부터의 거리";
  } else if (name === "recess") {
    $("#dp-bar-lo").textContent = "0 mm (랙 앞면)";
    $("#dp-bar-hi").textContent = `${v.recess_range[1]} mm (랙 안쪽 끝)`;
    $("#dp-bar-title").textContent = "랙 앞면에서 안쪽으로 들어간 거리";
  }
}

function hover(e) {
  if (!grid) return;
  const r = e.currentTarget.getBoundingClientRect();
  const u = (e.clientX - r.left) / r.width, v = (e.clientY - r.top) / r.height;
  const i = Math.min(grid.w - 1, Math.floor(u * grid.w)), j = Math.min(grid.h - 1, Math.floor(v * grid.h));
  const k = j * grid.w + i;
  const tip = $("#dp-tip");
  tip.hidden = false;
  tip.innerHTML = `거리 <b>${grid.depth[k].toFixed(3)} m</b>`
    + (grid.recess ? `<br>앞면 기준 <b>${Math.round(grid.recess[k])} mm</b>` : "");
  const box = $("#dp-imgwrap").getBoundingClientRect();
  tip.style.left = `${Math.min(e.clientX - box.left + 14, box.width - 150)}px`;
  tip.style.top = `${e.clientY - box.top + 14}px`;
}
