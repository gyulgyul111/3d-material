export const $ = (sel, root = document) => root.querySelector(sel);
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// 새 클래스의 기본 색: 검증된 범주형 팔레트에서 아직 안 쓴 색을 고정 순서로 배정 (사용자가 바꿀 수 있음)
export const PALETTE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];

export async function api(path, { method = "GET", json, form } = {}) {
  const opts = { method };
  if (json !== undefined) {
    opts.headers = { "Content-Type": "application/json" };
    opts.body = JSON.stringify(json);
  } else if (form) {
    opts.body = form;
  }
  const res = await fetch(path, opts);
  const body = res.headers.get("content-type")?.includes("json") ? await res.json() : null;
  if (!res.ok) {
    let msg = body?.detail ?? res.statusText;
    if (Array.isArray(msg)) msg = msg.map((d) => `${d.loc?.slice(1).join(".")}: ${d.msg}`).join("\n");
    throw new Error(msg);
  }
  return body;
}

let toastTimer;
export function toast(msg, kind = "ok") {
  const el = $("#toast");
  el.textContent = msg;
  el.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = "toast"), kind === "error" ? 6000 : 2500);
}

// 비동기 핸들러의 오류를 알림으로 보여준다
export const guard = (fn) => async (...args) => {
  try {
    return await fn(...args);
  } catch (e) {
    toast(e.message, "error");
  }
};

// 탭 간 공유 이벤트 (예: 랙/클래스 설정이 바뀌면 다른 탭 목록 갱신)
export const bus = new EventTarget();
export const emit = (name) => bus.dispatchEvent(new Event(name));
export const on = (name, fn) => bus.addEventListener(name, fn);
