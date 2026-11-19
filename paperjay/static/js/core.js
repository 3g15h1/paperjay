// Shared helpers: the API, routing, formatting, labels, and small UI pieces (toast, dialogs, pickers).

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export function el(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; }
export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const icon = (name, cls = "i") => `<svg class="${cls}"><use href="#i-${name}"/></svg>`;

// ---------- API ----------
export class ApiError extends Error {
  constructor(status, detail) { super(detail); this.status = status; }
}
export async function api(path, { method = "GET", body, form, quiet = false } = {}) {
  const opts = { method, headers: {} };
  if (form) opts.body = form;
  else if (body !== undefined) { opts.body = JSON.stringify(body); opts.headers["Content-Type"] = "application/json"; }
  let r;
  try { r = await fetch(path, opts); } catch (e) {
    if (!quiet) toast("Can't reach PaperJay. Check your connection.");
    throw new ApiError(0, "offline");
  }
  const data = (r.headers.get("content-type") || "").includes("json") ? await r.json() : await r.text();
  if (r.status === 401 && !path.startsWith("/api/auth/")) { location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search); throw new ApiError(401, "sign in"); }
  if (r.status === 403 && data && data.setup) { location.href = "/setup"; throw new ApiError(403, "setup"); }
  if (!r.ok) {
    const detail = typeof data === "object" ? (data.detail || data.error || JSON.stringify(data)) : data;
    if (!quiet) toast(typeof detail === "string" ? detail : "Something went wrong");
    throw new ApiError(r.status, detail);
  }
  return data;
}
export const qs = (obj) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) if (v !== undefined && v !== null && v !== "" && !(Array.isArray(v) && !v.length)) p.set(k, Array.isArray(v) ? v.join(",") : v);
  const s = p.toString();
  return s ? "?" + s : "";
};

// ---------- formatting ----------
export function fmtDate(iso, opts = { month: "short", day: "numeric", year: "numeric" }) {
  if (!iso) return "";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00" : iso);
  return isNaN(d) ? iso : d.toLocaleDateString(undefined, opts);
}
export function ago(iso) {
  const t = typeof iso === "number" ? iso : Date.parse(iso);
  const s = Math.max(1, Math.round((Date.now() - t) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60); if (m < 60) return m + " min ago";
  const h = Math.round(m / 60); if (h < 24) return h + " h ago";
  const d = Math.round(h / 24); if (d < 30) return d === 1 ? "yesterday" : d + " days ago";
  return fmtDate(new Date(t).toISOString().slice(0, 10));
}
export const money = (v) => "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
export const plural = (n, word, many = word + "s") => `${n} ${n === 1 ? word : many}`;
// Search snippets mark matches with \x02 … \x03 (control characters never in OCR text): escape, then mark.
export const snippet = (s) => esc(s).replace(/\x02/g, "<mark>").replace(/\x03/g, "</mark>");

// ---------- labels (tags, companies, types), cached ----------
const labelCache = {};
export async function labels(kind, fresh = false) {
  if (fresh || !labelCache[kind]) labelCache[kind] = api(`/api/labels/${kind}`, { quiet: true }).catch(() => []);
  return labelCache[kind];
}
export function forgetLabels() { for (const k of Object.keys(labelCache)) delete labelCache[k]; }
export const tagChip = (t, extra = "") => `<span class="chip tag ${extra}" style="--c:${esc(t.color || "#8b8f99")}">${esc(t.name)}</span>`;
export const KIND_NAMES = { tag: "Tag", correspondent: "Company", document_type: "Type" };

// ---------- routing ----------
const routes = [];
let cleanup = null, rendered = null;
export function route(pattern, load) { routes.push({ re: new RegExp("^" + pattern.replace(/:(\w+)/g, "(?<$1>[^/]+)") + "/?$"), load }); }
export function go(url, { replace = false } = {}) {
  if (replace) history.replaceState(null, "", url); else history.pushState(null, "", url);
  render();
}
export async function render() {
  const path = location.pathname;
  rendered = path + location.search;
  if (cleanup) { try { cleanup(); } catch {} cleanup = null; }
  closeModal();
  const view = $("#view");
  for (const r of routes) {
    const m = path.match(r.re);
    if (!m) continue;
    const mod = await r.load();
    const params = { ...(m.groups || {}), query: new URLSearchParams(location.search) };
    const nav = mod.nav || "";
    $$("[data-nav]").forEach((a) => a.classList.toggle("on", a.dataset.nav.split(" ").includes(nav)));
    view.innerHTML = "";
    window.scrollTo(0, 0);
    cleanup = (await mod.show(view, params)) || null;
    drawUpdateNotice();
    return;
  }
  view.innerHTML = `<div class="page narrow"><div class="empty">This page doesn't exist. <a href="/">Go home</a></div></div>`;
}
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href]");
  if (!a || a.target || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || a.hasAttribute("download")) return;
  const url = new URL(a.href, location.href);
  if (url.origin !== location.origin || url.pathname.startsWith("/api/") || url.pathname.startsWith("/static/") || url.pathname.endsWith(".ics")) return;
  e.preventDefault();
  if (url.pathname + url.search !== location.pathname + location.search) go(url.pathname + url.search + url.hash);
});
// Back within one page (e.g. closing the camera, which adds a history entry for the back gesture) stays on it
addEventListener("popstate", () => { if (location.pathname + location.search !== rendered) render(); });

// ---------- toast ----------
let toastTimer = null;
export function toast(text, action) {
  let el = $(".toast");
  if (!el) { el = document.createElement("div"); el.className = "toast"; el.setAttribute("role", "status"); document.body.append(el); }
  el.innerHTML = `<span>${esc(text)}</span>` + (action ? `<button type="button">${esc(action.label)}</button>` : "");
  if (action) el.querySelector("button").onclick = () => { el.remove(); action.run(); };
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), action ? 6000 : 3500);
}

// ---------- dialogs ----------
export function closeModal() { $("#overlay").innerHTML = ""; }
export function modal(html, { onMount } = {}) {
  const ov = $("#overlay");
  ov.innerHTML = `<div class="modal-bg"><div class="modal card" role="dialog" aria-modal="true">${html}</div></div>`;
  const bg = ov.firstElementChild, box = bg.firstElementChild;
  bg.addEventListener("mousedown", (e) => { if (e.target === bg) closeModal(); });
  const key = (e) => { if (e.key === "Escape") { closeModal(); removeEventListener("keydown", key); } };
  addEventListener("keydown", key);
  if (onMount) onMount(box);
  const f = box.querySelector("input, select, textarea, button.primary");
  if (f) f.focus();
  return box;
}
export function confirmBox(title, text, { ok = "OK", danger = false } = {}) {
  return new Promise((resolve) => {
    const box = modal(`<h1 style="font-size:17px">${esc(title)}</h1><div class="muted">${esc(text)}</div>
      <div class="acts"><button class="btn" data-a="no">Cancel</button><button class="btn ${danger ? "danger" : "primary"}" data-a="yes">${esc(ok)}</button></div>`);
    box.addEventListener("click", (e) => {
      const b = e.target.closest("[data-a]"); if (!b) return;
      closeModal(); resolve(b.dataset.a === "yes");
    });
  });
}
export function promptBox(title, { value = "", label = "", ok = "Save", placeholder = "" } = {}) {
  return new Promise((resolve) => {
    const box = modal(`<h1 style="font-size:17px">${esc(title)}</h1><form class="grid" style="display:grid;gap:14px">
      <label class="field">${esc(label)}<input class="input" name="v" value="${esc(value)}" placeholder="${esc(placeholder)}" autocomplete="off"></label>
      <div class="acts"><button type="button" class="btn" data-a="no">Cancel</button><button class="btn primary">${esc(ok)}</button></div></form>`);
    const input = box.querySelector("input"); input.select();
    box.querySelector("[data-a=no]").onclick = () => { closeModal(); resolve(null); };
    box.querySelector("form").onsubmit = (e) => { e.preventDefault(); const v = input.value.trim(); closeModal(); resolve(v || null); };
  });
}

// ---------- pickers ----------
// A searchable menu over a label kind. onPick(label) with an existing label, or with {create: name}.
export function combo(input, kind, onPick, { allowCreate = true, exclude = () => [] } = {}) {
  const wrap = input.closest(".combo");
  let menu = null, items = [], hi = 0;
  const close = () => { if (menu) menu.remove(); menu = null; };
  async function open() {
    const all = await labels(kind);
    const q = input.value.trim().toLowerCase();
    const skip = new Set(exclude());
    items = all.filter((l) => !skip.has(l.id) && (!q || l.name.toLowerCase().includes(q))).slice(0, 50);
    const exact = all.some((l) => l.name.toLowerCase() === q);
    if (allowCreate && q && !exact) items.push({ create: input.value.trim() });
    close();
    if (!items.length) return;
    menu = document.createElement("div"); menu.className = "menu";
    menu.innerHTML = items.map((l, i) => `<button type="button" data-i="${i}" class="${i === hi ? "hi" : ""}">${l.create
      ? `${icon("plus")} Add “${esc(l.create)}”` : (kind === "tag" ? tagChip(l) : esc(l.name)) + `<span class="faint small" style="margin-left:auto">${l.document_count ?? ""}</span>`}</button>`).join("");
    menu.addEventListener("mousedown", (e) => { e.preventDefault(); const b = e.target.closest("button"); if (b) pick(+b.dataset.i); });
    wrap.append(menu);
  }
  function pick(i) { const l = items[i]; if (!l) return; close(); input.value = ""; hi = 0; onPick(l); }
  input.addEventListener("focus", open);
  input.addEventListener("input", () => { hi = 0; open(); });
  input.addEventListener("blur", () => setTimeout(close, 120));
  input.addEventListener("keydown", (e) => {
    if (!menu) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); hi = (hi + (e.key === "ArrowDown" ? 1 : -1) + items.length) % items.length; open(); }
    else if (e.key === "Enter") { e.preventDefault(); pick(hi); }
    else if (e.key === "Escape") close();
  });
}
export async function ensureLabel(kind, picked) {
  if (!picked.create) return picked;
  const made = await api(`/api/labels/${kind}`, { method: "POST", body: { name: picked.create } });
  forgetLabels();
  return made;
}

// ---------- document cards ----------
export function statusChip(d) {
  if (d.status === "failed") return `<span class="chip bad">Failed</span>`;
  if (d.status === "pending" || d.status === "processing") return `<span class="chip accent">Reading…</span>`;
  if (d.needs_review) return `<span class="chip warn">Review</span>`;
  return "";
}
export function docCard(d, { picked = false, hl = "" } = {}) {
  const meta = [d.correspondent, fmtDate(d.doc_date || (d.added_at || "").slice(0, 10)), d.document_type].filter(Boolean).join(" · ");
  // hl: the words searched for, marked on the document's pages when it opens
  return `<a class="doc${picked ? " picked" : ""}" href="/documents/${d.id}${hl ? "?q=" + encodeURIComponent(hl) : ""}" data-id="${d.id}">
    <span class="sel"></span>
    <div class="thumb"><img loading="lazy" alt="" src="/api/documents/${d.id}/thumb" onerror="this.style.visibility='hidden'"></div>
    <div class="body"><div class="title">${esc(d.title)}</div><div class="meta">${esc(meta)}</div>
      ${d.snippet ? `<div class="snippet">${snippet(d.snippet)}</div>` : ""}
      ${d.tags && d.tags.length ? `<div class="chips">${d.tags.slice(0, 4).map((t) => tagChip(t)).join("")}</div>` : ""}</div>
    <span class="state">${statusChip(d)}</span></a>`;
}

// ---------- who's signed in ----------
export let me = null;
export async function loadMe() {
  const s = await api("/api/auth/status", { quiet: true }).catch(() => ({}));
  me = s.user || null;
  document.body.classList.toggle("readonly", me?.role === "readonly");
  document.body.classList.toggle("admin", me?.role === "admin");
  const el = $("#me");
  if (el) el.textContent = me ? me.display_name : "";
  return s;
}
export async function signOut() { await api("/api/auth/logout", { method: "POST", quiet: true }).catch(() => {}); location.href = "/login"; }

// ---------- navigation counts and AI status ----------
export async function refreshCounts() {
  try {
    const s = await api("/api/summary", { quiet: true });
    const set = (id, n) => { const el = $(id); if (el) el.textContent = n ? n : ""; };
    set("#n-docs", s.documents); set("#n-review", s.needs_review + s.failed); set("#n-trash", s.trash); set("#n-processing", s.processing);
    $("#t-review").hidden = !(s.needs_review + s.failed);
  } catch {}
}
export async function refreshAI() {
  try {
    const a = await api("/api/ai/status", { quiet: true });
    $("#ai-dot").className = "dot " + (a.on ? "on" : "");
    const pending = a.on ? a.documents - a.analyzed : 0;
    $("#ai-text").textContent = !a.on ? "AI off" : a.busy ? "AI working…" : pending > 0 ? `AI: ${pending} to read` : `AI: ${a.model}`;
    return a;
  } catch { return null; }
}

// ---------- "a new version is out" (admins) ----------
// The server checks GitHub every few hours; this only reads its answer. Shown on every page except Updates
// itself, until someone updates or says "Not now" (which hides it until the next version).
let updateNotice = null;
export async function refreshUpdateNotice() {
  if (me?.role !== "admin") return;
  const n = await api("/api/update/notice", { quiet: true }).catch(() => null);
  updateNotice = n && n.show ? n : null;
  const side = $("#n-update"), tab = $("#t-update");
  if (side) side.textContent = updateNotice ? "New" : "";
  if (tab) tab.hidden = !updateNotice;
  drawUpdateNotice();
}
function drawUpdateNotice() {
  $("#update-notice")?.remove();
  if (!updateNotice || location.pathname.startsWith("/settings/updates") || document.body.classList.contains("bare")) return;
  const n = updateNotice;
  const box = el(`<div class="notice card" id="update-notice" role="status">${icon("download")}
    <span class="grow">PaperJay <b>${esc(n.latest)}</b> is available. You have ${esc(n.current)}.</span>
    <span class="acts"><a class="btn primary sm" href="/settings/updates">${n.install === "lxc" ? "Update" : "See how to update"}</a>
    <button type="button" class="btn ghost sm" data-a="later">Not now</button></span></div>`);
  box.querySelector("[data-a=later]").onclick = async () => {
    box.remove(); updateNotice = null;
    await api("/api/update/dismiss", { method: "POST", body: { tag: n.latest }, quiet: true }).catch(() => {});
    refreshUpdateNotice();
  };
  $("#view")?.prepend(box);
}

// Library and Browse are one place (the Library tab): this switch sits at the top of both.
export function libSwitch(current) {
  const a = (href, key, text) => `<a href="${href}" class="${key === current ? "on" : ""}"${key === current ? ' aria-current="page"' : ""}>${text}</a>`;
  return `<nav class="seg lib-switch" aria-label="Library view">${a("/library", "library", "All documents")}${a("/browse", "browse", "Browse")}${a("/warranties", "warranties", "Warranties")}</nav>`;
}
