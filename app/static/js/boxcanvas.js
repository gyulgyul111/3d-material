// 이미지 위에 사각형(정규화 좌표 0~1)을 그리고/선택/이동/크기조절하는 캔버스.
// ROI 설정과 라벨링 화면이 같이 쓴다.
//   좌클릭 드래그(빈 곳): 새 박스   좌클릭: 선택   선택된 박스 드래그: 이동   가장자리 드래그: 크기조절
//   휠: 확대/축소   우클릭 드래그 또는 Space+드래그: 화면 이동   더블클릭: 화면 맞춤

const HANDLE_PX = 7;
const MIN_PX = 4;

export class BoxCanvas {
  constructor(container, opts = {}) {
    this.opts = opts; // { canDraw(), onCreate(rect), onChange(), onSelect(index) }
    this.el = document.createElement("canvas");
    this.el.className = "boxcanvas";
    this.el.tabIndex = 0;
    container.appendChild(this.el);
    this.ctx = this.el.getContext("2d");
    this.img = null;
    this.boxes = [];
    this.selected = -1;
    this.view = { s: 1, ox: 0, oy: 0 };
    this.drag = null;
    this.space = false;
    this.placeholder = "";

    new ResizeObserver(() => this.resize()).observe(container);
    this.el.addEventListener("pointerdown", (e) => this.down(e));
    this.el.addEventListener("pointermove", (e) => this.move(e));
    this.el.addEventListener("pointerup", (e) => this.up(e));
    this.el.addEventListener("wheel", (e) => this.wheel(e), { passive: false });
    this.el.addEventListener("dblclick", () => { this.fit(); this.render(); });
    this.el.addEventListener("contextmenu", (e) => e.preventDefault());
    window.addEventListener("keydown", (e) => { if (e.code === "Space" && e.target === this.el) { this.space = true; e.preventDefault(); } });
    window.addEventListener("keyup", (e) => { if (e.code === "Space") this.space = false; });
  }

  setImage(url) {
    return new Promise((resolve, reject) => {
      if (!url) { this.img = null; this.render(); return resolve(); }
      const img = new Image();
      img.onload = () => { this.img = img; this.fit(); this.render(); resolve(); };
      img.onerror = reject;
      img.src = url;
    });
  }

  setBoxes(boxes, selected = -1) {
    this.boxes = boxes;
    this.selected = selected;
    this.render();
  }

  select(i) {
    this.selected = i;
    this.render();
    this.opts.onSelect?.(i);
  }

  resize() {
    const r = this.el.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    this.el.width = Math.max(1, Math.round(r.width * dpr));
    this.el.height = Math.max(1, Math.round(r.height * dpr));
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.fit();
    this.render();
  }

  get cw() { return this.el.width / (window.devicePixelRatio || 1); }
  get ch() { return this.el.height / (window.devicePixelRatio || 1); }

  fit() {
    if (!this.img) return;
    const s = Math.min(this.cw / this.img.width, this.ch / this.img.height);
    this.view = { s, ox: (this.cw - this.img.width * s) / 2, oy: (this.ch - this.img.height * s) / 2 };
  }

  // 화면 좌표 <-> 정규화 이미지 좌표
  toNorm(px, py) {
    const { s, ox, oy } = this.view;
    return [(px - ox) / (s * this.img.width), (py - oy) / (s * this.img.height)];
  }
  toPx(u, v) {
    const { s, ox, oy } = this.view;
    return [ox + u * s * this.img.width, oy + v * s * this.img.height];
  }
  pos(e) {
    const r = this.el.getBoundingClientRect();
    return [e.clientX - r.left, e.clientY - r.top];
  }

  hitEdges(i, px, py) {
    const b = this.boxes[i];
    const [x1, y1] = this.toPx(b.x1, b.y1);
    const [x2, y2] = this.toPx(b.x2, b.y2);
    const inY = py > y1 - HANDLE_PX && py < y2 + HANDLE_PX;
    const inX = px > x1 - HANDLE_PX && px < x2 + HANDLE_PX;
    const edges = {
      l: inY && Math.abs(px - x1) < HANDLE_PX,
      r: inY && Math.abs(px - x2) < HANDLE_PX,
      t: inX && Math.abs(py - y1) < HANDLE_PX,
      b: inX && Math.abs(py - y2) < HANDLE_PX,
    };
    return edges.l || edges.r || edges.t || edges.b ? edges : null;
  }

  hitBox(px, py) {
    // 겹치면 작은 박스 우선 (선반 ROI 안의 앞 기준선 띠 같은 경우)
    const [u, v] = this.toNorm(px, py);
    let best = -1, bestArea = Infinity;
    this.boxes.forEach((b, i) => {
      if (b.locked) return;
      if (u >= b.x1 && u <= b.x2 && v >= b.y1 && v <= b.y2) {
        const a = (b.x2 - b.x1) * (b.y2 - b.y1);
        if (a < bestArea) { best = i; bestArea = a; }
      }
    });
    return best;
  }

  down(e) {
    if (!this.img) return;
    this.el.focus();
    this.el.setPointerCapture(e.pointerId);
    const [px, py] = this.pos(e);
    if (e.button === 2 || e.button === 1 || this.space) {
      this.drag = { mode: "pan", px, py, ox: this.view.ox, oy: this.view.oy };
      return;
    }
    if (e.button !== 0) return;
    const [u, v] = this.toNorm(px, py);
    if (this.selected >= 0) {
      const edges = this.hitEdges(this.selected, px, py);
      if (edges) {
        this.drag = { mode: "resize", edges, i: this.selected };
        return;
      }
    }
    const hit = this.hitBox(px, py);
    if (hit >= 0) {
      this.select(hit);
      const b = this.boxes[hit];
      this.drag = { mode: "move", i: hit, u, v, orig: { ...b } };
      return;
    }
    if (this.opts.canDraw?.() ?? true) {
      this.drag = { mode: "draw", u0: clamp01(u), v0: clamp01(v), u1: clamp01(u), v1: clamp01(v) };
    } else {
      this.select(-1);
    }
  }

  move(e) {
    if (!this.img) return;
    const [px, py] = this.pos(e);
    const d = this.drag;
    if (!d) {
      this.el.style.cursor = this.cursorAt(px, py);
      return;
    }
    const [u, v] = this.toNorm(px, py);
    if (d.mode === "pan") {
      this.view.ox = d.ox + px - d.px;
      this.view.oy = d.oy + py - d.py;
    } else if (d.mode === "draw") {
      d.u1 = clamp01(u); d.v1 = clamp01(v);
    } else if (d.mode === "move") {
      const b = this.boxes[d.i], o = d.orig;
      const w = o.x2 - o.x1, h = o.y2 - o.y1;
      const x1 = Math.min(Math.max(o.x1 + u - d.u, 0), 1 - w);
      const y1 = Math.min(Math.max(o.y1 + v - d.v, 0), 1 - h);
      Object.assign(b, { x1, y1, x2: x1 + w, y2: y1 + h });
      d.moved = true;
    } else if (d.mode === "resize") {
      const b = this.boxes[d.i];
      if (d.edges.l) b.x1 = clamp01(u);
      if (d.edges.r) b.x2 = clamp01(u);
      if (d.edges.t) b.y1 = clamp01(v);
      if (d.edges.b) b.y2 = clamp01(v);
      d.moved = true;
    }
    this.render();
  }

  up() {
    const d = this.drag;
    this.drag = null;
    if (!d) return;
    if (d.mode === "draw") {
      const r = { x1: Math.min(d.u0, d.u1), y1: Math.min(d.v0, d.v1), x2: Math.max(d.u0, d.u1), y2: Math.max(d.v0, d.v1) };
      const [ax, ay] = this.toPx(r.x1, r.y1), [bx, by] = this.toPx(r.x2, r.y2);
      if (bx - ax >= MIN_PX && by - ay >= MIN_PX) this.opts.onCreate?.(r);
    } else if ((d.mode === "move" || d.mode === "resize") && d.moved) {
      const b = this.boxes[d.i];
      [b.x1, b.x2] = [Math.min(b.x1, b.x2), Math.max(b.x1, b.x2)];
      [b.y1, b.y2] = [Math.min(b.y1, b.y2), Math.max(b.y1, b.y2)];
      this.opts.onChange?.();
    }
    this.render();
  }

  wheel(e) {
    if (!this.img) return;
    e.preventDefault();
    const [px, py] = this.pos(e);
    const k = Math.exp(-e.deltaY * 0.0015);
    const s = Math.min(Math.max(this.view.s * k, 0.02), 40);
    const f = s / this.view.s;
    this.view = { s, ox: px - (px - this.view.ox) * f, oy: py - (py - this.view.oy) * f };
    this.render();
  }

  cursorAt(px, py) {
    if (this.space) return "grab";
    if (this.selected >= 0) {
      const e = this.hitEdges(this.selected, px, py);
      if (e) {
        if ((e.l && e.t) || (e.r && e.b)) return "nwse-resize";
        if ((e.r && e.t) || (e.l && e.b)) return "nesw-resize";
        return e.l || e.r ? "ew-resize" : "ns-resize";
      }
    }
    if (this.hitBox(px, py) >= 0) return "move";
    return (this.opts.canDraw?.() ?? true) ? "crosshair" : "default";
  }

  render() {
    const c = this.ctx;
    c.clearRect(0, 0, this.cw, this.ch);
    if (!this.img) {
      c.fillStyle = getComputedStyle(this.el).color;
      c.font = "14px sans-serif";
      c.textAlign = "center";
      c.fillText(this.placeholder, this.cw / 2, this.ch / 2);
      c.textAlign = "left";
      return;
    }
    const { s, ox, oy } = this.view;
    c.imageSmoothingEnabled = s < 2;
    c.drawImage(this.img, ox, oy, this.img.width * s, this.img.height * s);

    this.boxes.forEach((b, i) => this.drawBox(b, i === this.selected));
    const d = this.drag;
    if (d?.mode === "draw") {
      const [x1, y1] = this.toPx(Math.min(d.u0, d.u1), Math.min(d.v0, d.v1));
      const [x2, y2] = this.toPx(Math.max(d.u0, d.u1), Math.max(d.v0, d.v1));
      c.setLineDash([6, 4]);
      c.strokeStyle = this.opts.drawColor?.() || "#fff";
      c.lineWidth = 2;
      c.strokeRect(x1, y1, x2 - x1, y2 - y1);
      c.setLineDash([]);
    }
  }

  drawBox(b, sel) {
    const c = this.ctx;
    const [x1, y1] = this.toPx(b.x1, b.y1);
    const [x2, y2] = this.toPx(b.x2, b.y2);
    const color = b.color || "#2a78d6";
    if (sel) {
      c.fillStyle = color + "33";
      c.fillRect(x1, y1, x2 - x1, y2 - y1);
    }
    c.strokeStyle = color;
    c.lineWidth = sel ? 3 : 2;
    if (b.dashed) c.setLineDash([5, 3]);
    c.strokeRect(x1, y1, x2 - x1, y2 - y1);
    c.setLineDash([]);
    if (sel) {
      c.fillStyle = "#fff";
      for (const [hx, hy] of [[x1, y1], [x2, y1], [x1, y2], [x2, y2], [(x1 + x2) / 2, y1], [(x1 + x2) / 2, y2], [x1, (y1 + y2) / 2], [x2, (y1 + y2) / 2]]) {
        c.fillRect(hx - 3, hy - 3, 6, 6);
        c.strokeRect(hx - 3, hy - 3, 6, 6);
      }
    }
    if (b.label) {
      c.font = "600 12px sans-serif";
      const tw = c.measureText(b.label).width + 8;
      const ty = y1 - 18 >= 0 ? y1 - 18 : y1;
      c.fillStyle = color;
      c.fillRect(x1, ty, tw, 18);
      c.fillStyle = "#fff";
      c.fillText(b.label, x1 + 4, ty + 13);
    }
  }
}

function clamp01(x) { return Math.min(Math.max(x, 0), 1); }

// 랙 ROI: 네 모서리(좌상, 우상, 우하, 좌하)를 찍고 끌어서 맞춘다.
//   '다시 찍기' 모드에서 네 번 클릭 → 모서리 드래그로 미세조정(돋보기 표시) → 안쪽 드래그로 전체 이동
export class QuadCanvas extends BoxCanvas {
  constructor(container, opts = {}) {
    super(container, { ...opts, canDraw: () => false });
    this.points = null;
    this.clicking = false;
    this.pending = [];
    this.mouse = null;
  }

  setPoints(points) {
    this.points = points ? points.map((p) => [...p]) : null;
    this.render();
  }

  startClicking() {
    this.clicking = true;
    this.pending = [];
    this.render();
    this.el.focus();
  }

  cancelClicking() {
    this.clicking = false;
    this.pending = [];
    this.render();
  }

  hitPoint(px, py) {
    if (!this.points) return -1;
    return this.points.findIndex(([u, v]) => {
      const [x, y] = this.toPx(u, v);
      return Math.hypot(x - px, y - py) < 12;
    });
  }

  down(e) {
    if (!this.img) return;
    if (e.button !== 0 || this.space) return super.down(e);
    this.el.setPointerCapture(e.pointerId);
    this.el.focus();
    const [px, py] = this.pos(e);
    const [u, v] = this.toNorm(px, py);
    if (this.clicking) {
      this.pending.push([clamp01(u), clamp01(v)]);
      if (this.pending.length === 4) {
        this.points = orderCorners(this.pending);
        this.clicking = false;
        this.pending = [];
        this.opts.onChange?.();
        this.opts.onMode?.(false);
      }
      this.render();
      return;
    }
    const i = this.hitPoint(px, py);
    if (i >= 0) {
      this.drag = { mode: "point", i };
    } else if (this.points && insidePoly(this.points, u, v)) {
      this.drag = { mode: "quad", u, v, orig: this.points.map((p) => [...p]) };
    }
  }

  move(e) {
    if (!this.img) return;
    const [px, py] = this.pos(e);
    this.mouse = [px, py];
    const d = this.drag;
    if (d?.mode === "point") {
      const [u, v] = this.toNorm(px, py);
      this.points[d.i] = [clamp01(u), clamp01(v)];
      this.render();
    } else if (d?.mode === "quad") {
      const [u, v] = this.toNorm(px, py);
      let du = u - d.u, dv = v - d.v;
      // 네 점이 모두 이미지 안에 남도록 이동량 제한
      du = Math.min(Math.max(du, -Math.min(...d.orig.map((p) => p[0]))), 1 - Math.max(...d.orig.map((p) => p[0])));
      dv = Math.min(Math.max(dv, -Math.min(...d.orig.map((p) => p[1]))), 1 - Math.max(...d.orig.map((p) => p[1])));
      this.points = d.orig.map(([a, b]) => [a + du, b + dv]);
      this.render();
    } else if (d) {
      super.move(e);
    } else {
      this.el.style.cursor = this.cursorAt(px, py);
      if (this.clicking) this.render();
    }
  }

  up(e) {
    const d = this.drag;
    if (d?.mode === "point" || d?.mode === "quad") {
      this.drag = null;
      this.points = orderCorners(this.points);
      this.opts.onChange?.();
      this.render();
      return;
    }
    super.up(e);
  }

  cursorAt(px, py) {
    if (this.space) return "grab";
    if (this.clicking) return "crosshair";
    if (this.hitPoint(px, py) >= 0) return "grab";
    if (this.points && insidePoly(this.points, ...this.toNorm(px, py))) return "move";
    return "default";
  }

  render() {
    super.render();
    if (!this.img) return;
    const c = this.ctx;
    const pts = this.clicking ? this.pending : this.points;
    if (this.points && !this.clicking) {
      // ROI 밖 어둡게
      c.save();
      c.beginPath();
      c.rect(0, 0, this.cw, this.ch);
      this.points.forEach(([u, v], i) => { const [x, y] = this.toPx(u, v); i ? c.lineTo(x, y) : c.moveTo(x, y); });
      c.closePath();
      c.fillStyle = "rgba(0,0,0,0.55)";
      c.fill("evenodd");
      c.restore();
      // 보정 후 격자가 어떻게 펴질지 보여주는 원근 격자
      c.strokeStyle = "rgba(255,255,255,0.35)";
      c.lineWidth = 1;
      for (let k = 1; k < 4; k++) {
        const t = k / 4;
        this.line(bilerp(this.points, t, 0), bilerp(this.points, t, 1));
        this.line(bilerp(this.points, 0, t), bilerp(this.points, 1, t));
      }
    }
    if (pts?.length) {
      c.strokeStyle = "#eda100";
      c.lineWidth = 2;
      c.beginPath();
      pts.forEach(([u, v], i) => { const [x, y] = this.toPx(u, v); i ? c.lineTo(x, y) : c.moveTo(x, y); });
      if (!this.clicking) c.closePath();
      else if (this.mouse) c.lineTo(...this.mouse);
      c.stroke();
      const names = ["좌상", "우상", "우하", "좌하"];
      pts.forEach(([u, v], i) => {
        const [x, y] = this.toPx(u, v);
        c.beginPath();
        c.arc(x, y, 7, 0, Math.PI * 2);
        c.fillStyle = "#eda100";
        c.fill();
        c.strokeStyle = "#fff";
        c.lineWidth = 2;
        c.stroke();
        c.font = "600 12px sans-serif";
        c.fillStyle = "#fff";
        c.strokeStyle = "rgba(0,0,0,.7)";
        c.lineWidth = 3;
        const label = this.clicking ? `${i + 1}` : names[i];
        c.strokeText(label, x + 10, y - 10);
        c.fillText(label, x + 10, y - 10);
      });
    }
    if (this.drag?.mode === "point" && this.mouse) this.loupe(...this.toPx(...this.points[this.drag.i]));
  }

  line([u1, v1], [u2, v2]) {
    const c = this.ctx;
    c.beginPath();
    c.moveTo(...this.toPx(u1, v1));
    c.lineTo(...this.toPx(u2, v2));
    c.stroke();
  }

  // 모서리를 끌 때 주변을 4배 확대해서 보여준다
  loupe(x, y) {
    const c = this.ctx, R = 70, Z = 4;
    const [u, v] = this.toNorm(x, y);
    const sx = u * this.img.width, sy = v * this.img.height;
    const src = R / Z / this.view.s; // 화면 R 픽셀에 들어갈 원본 픽셀 반경
    const cx = x < this.cw / 2 ? this.cw - R - 12 : R + 12, cy = R + 12;
    c.save();
    c.beginPath();
    c.arc(cx, cy, R, 0, Math.PI * 2);
    c.clip();
    c.fillStyle = "#000";
    c.fillRect(cx - R, cy - R, 2 * R, 2 * R);
    c.imageSmoothingEnabled = false;
    c.drawImage(this.img, sx - src, sy - src, 2 * src, 2 * src, cx - R, cy - R, 2 * R, 2 * R);
    c.strokeStyle = "#eda100";
    c.lineWidth = 1;
    c.beginPath();
    c.moveTo(cx - R, cy); c.lineTo(cx + R, cy);
    c.moveTo(cx, cy - R); c.lineTo(cx, cy + R);
    c.stroke();
    c.restore();
    c.beginPath();
    c.arc(cx, cy, R, 0, Math.PI * 2);
    c.strokeStyle = "#fff";
    c.lineWidth = 2;
    c.stroke();
  }
}

// 좌상, 우상, 우하, 좌하 순서로 정렬 (서버 config.order_corners 와 같은 방식)
export function orderCorners(pts) {
  const cx = pts.reduce((a, p) => a + p[0], 0) / 4;
  const cy = pts.reduce((a, p) => a + p[1], 0) / 4;
  const s = [...pts].sort((a, b) => Math.atan2(a[1] - cy, a[0] - cx) - Math.atan2(b[1] - cy, b[0] - cx));
  let start = 0;
  s.forEach((p, i) => { if (p[0] + p[1] < s[start][0] + s[start][1]) start = i; });
  return [...s.slice(start), ...s.slice(0, start)];
}

export function insidePoly(pts, u, v) {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
    const [xi, yi] = pts[i], [xj, yj] = pts[j];
    if ((yi > v) !== (yj > v) && u < ((xj - xi) * (v - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

// 사각형 네 모서리 사이 (s, t) 위치
function bilerp([tl, tr, br, bl], s, t) {
  return [0, 1].map((k) => (1 - s) * (1 - t) * tl[k] + s * (1 - t) * tr[k] + s * t * br[k] + (1 - s) * t * bl[k]);
}
