// 깊이맵 -> 3D 재구성 뷰어 (three.js)
// 핀홀 카메라 가정: 시야각(FOV)으로 초점거리를 정하고 각 픽셀을 깊이만큼 역투영한다.
// 처음 시점은 사진을 찍은 카메라 위치이며, 드래그로 돌려보면 입체 구조를 확인할 수 있다.
import * as THREE from "three";
import { OrbitControls } from "/static/vendor/OrbitControls.js";
import { insidePoly } from "./boxcanvas.js";

const RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"].map((h) => new THREE.Color(h));

export function rampColor(t, out = new THREE.Color()) {
  t = Math.min(Math.max(t, 0), 1) * (RAMP.length - 1);
  const i = Math.min(Math.floor(t), RAMP.length - 2);
  return out.copy(RAMP[i]).lerp(RAMP[i + 1], t - i);
}

export class CloudViewer {
  constructor(container) {
    this.container = container;
    this.renderer = new THREE.WebGLRenderer({ antialias: true });
    this.renderer.setPixelRatio(window.devicePixelRatio || 1);
    container.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(60, 1, 0.01, 200);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.addEventListener("change", () => this.render());
    this.object = null;
    this.data = null;
    this.home = null;
    new ResizeObserver(() => this.resize()).observe(container);
    this.updateTheme();
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { this.updateTheme(); this.render(); });
    const loop = () => { requestAnimationFrame(loop); if (this.controls.update()) this.render(); };
    loop();
  }

  updateTheme() {
    const bg = getComputedStyle(document.documentElement).getPropertyValue("--canvas-bg").trim() || "#e9ebef";
    this.scene.background = new THREE.Color(bg);
  }

  resize() {
    const w = this.container.clientWidth, h = this.container.clientHeight;
    if (!w || !h) return;
    this.renderer.setSize(w, h, false);
    this.renderer.domElement.style.width = "100%";
    this.renderer.domElement.style.height = "100%";
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    this.render();
  }

  render() {
    this.renderer.render(this.scene, this.camera);
  }

  // data: { w, h, depth: Float32Array, rgb: Uint8Array, range: [lo, hi], corners: [[u,v]x4] | null }
  setData(data) {
    this.data = data;
  }

  // opts: { mode: "points"|"mesh", color: "rgb"|"depth", fov, size, cropRoi }
  build(opts) {
    const d = this.data;
    if (!d) return;
    if (this.object) {
      this.scene.remove(this.object);
      this.object.traverse((o) => { o.geometry?.dispose(); o.material?.dispose(); });
    }
    const { w, h, depth, rgb } = d;
    const f = (w / 2) / Math.tan((opts.fov * Math.PI) / 360);
    const [lo, hi] = d.range;
    const n = w * h;
    const pos = new Float32Array(n * 3);
    const col = new Float32Array(n * 3);
    const keep = new Uint8Array(n);
    const c = new THREE.Color();
    const crop = opts.cropRoi && d.corners;

    for (let j = 0; j < h; j++) {
      for (let i = 0; i < w; i++) {
        const k = j * w + i;
        const z = depth[k];
        if (!(z > 0)) continue;
        if (crop && !insidePoly(d.corners, (i + 0.5) / w, (j + 0.5) / h)) continue;
        keep[k] = 1;
        pos[3 * k] = ((i + 0.5 - w / 2) * z) / f;
        pos[3 * k + 1] = (-(j + 0.5 - h / 2) * z) / f;
        pos[3 * k + 2] = -z;
        if (opts.color === "depth") rampColor((z - lo) / (hi - lo || 1), c);
        else c.setRGB(rgb[3 * k] / 255, rgb[3 * k + 1] / 255, rgb[3 * k + 2] / 255, THREE.SRGBColorSpace);
        col[3 * k] = c.r; col[3 * k + 1] = c.g; col[3 * k + 2] = c.b;
      }
    }

    const geom = new THREE.BufferGeometry();
    geom.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    geom.setAttribute("color", new THREE.BufferAttribute(col, 3));
    const group = new THREE.Group();

    if (opts.mode === "mesh") {
      // 이웃 픽셀끼리 삼각형을 만들되, 깊이 차이가 큰 곳(물체 경계)은 잇지 않는다
      const idx = [];
      const ok = (a, b, e) => {
        if (!keep[a] || !keep[b] || !keep[e]) return false;
        const za = depth[a], zb = depth[b], ze = depth[e];
        return Math.max(za, zb, ze) - Math.min(za, zb, ze) < 0.04 * Math.min(za, zb, ze) + 0.02;
      };
      for (let j = 0; j < h - 1; j++) {
        for (let i = 0; i < w - 1; i++) {
          const a = j * w + i, b = a + 1, e = a + w, g = e + 1;
          if (ok(a, e, b)) idx.push(a, e, b);
          if (ok(b, e, g)) idx.push(b, e, g);
        }
      }
      geom.setIndex(idx);
      group.add(new THREE.Mesh(geom, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.DoubleSide })));
    } else {
      const zmid = (lo + hi) / 2;
      const size = (opts.size * zmid) / f * 1.6; // 픽셀 간격에 맞춘 점 크기
      group.add(new THREE.Points(geom, new THREE.PointsMaterial({ size, vertexColors: true, sizeAttenuation: true })));
    }

    // 랙 ROI(앞면) 윤곽을 3D 로 표시
    if (d.corners) {
      const pts = d.corners.map(([u, v]) => {
        const i = Math.min(w - 1, Math.max(0, Math.round(u * w - 0.5)));
        const j = Math.min(h - 1, Math.max(0, Math.round(v * h - 0.5)));
        const z = localDepth(depth, w, h, i, j);
        return new THREE.Vector3(((i + 0.5 - w / 2) * z) / f, (-(j + 0.5 - h / 2) * z) / f, -z);
      });
      const line = new THREE.LineLoop(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0xeda100 }));
      group.add(line);
    }

    this.object = group;
    this.scene.add(group);

    // 처음 시점: 원래 카메라 위치에서 장면 중앙을 바라봄
    const zc = (lo + hi) / 2;
    this.home = { target: new THREE.Vector3(0, 0, -zc), pos: new THREE.Vector3(0, 0, 0.001) };
    this.camera.fov = opts.fov;
    this.camera.near = Math.max(0.01, lo * 0.05);
    this.camera.far = hi * 20;
    this.camera.updateProjectionMatrix();
    if (opts.resetView) this.resetView();
    this.render();
  }

  resetView() {
    if (!this.home) return;
    this.controls.target.copy(this.home.target);
    this.camera.position.copy(this.home.pos);
    this.controls.update();
    this.render();
  }
}

// 모서리 근처 5x5 에서 가장 가까운 쪽 깊이 (랙 프레임)
function localDepth(depth, w, h, i, j) {
  const vals = [];
  for (let y = Math.max(0, j - 2); y <= Math.min(h - 1, j + 2); y++)
    for (let x = Math.max(0, i - 2); x <= Math.min(w - 1, i + 2); x++) vals.push(depth[y * w + x]);
  vals.sort((a, b) => a - b);
  return vals[Math.floor(vals.length * 0.2)];
}
