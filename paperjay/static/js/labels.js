// Tags, companies and document types: rename, recolour, merge, delete.
import { $, $$, api, esc, icon, labels, forgetLabels, tagChip, toast, confirmBox, promptBox, plural, modal, closeModal, KIND_NAMES } from "./core.js";

export const nav = "labels";
const KINDS = [["tag", "Tags"], ["correspondent", "Companies"], ["document_type", "Types"]];
const COLORS = ["#8b8f99", "#6ea8fe", "#7fd1a1", "#f2c14e", "#f07178", "#c792ea", "#5ccfe6", "#f78c6c", "#a3be8c", "#d4a373"];

export async function show(view, { query }) {
  let kind = query.get("kind") || "tag";
  const picked = new Set();
  view.innerHTML = `<div class="page narrow">
    <div class="head mobile-only" style="margin-bottom:6px"><a class="btn ghost sm" href="/settings">← Settings</a></div>
    <div class="head"><h1>Tags &amp; companies</h1><span class="spacer"></span><button class="btn sm primary" id="add">${icon("plus")} Add</button></div>
    <div class="row" style="margin-bottom:14px"><div class="seg" id="kinds">${KINDS.map(([k, n]) => `<button data-k="${k}">${n}</button>`).join("")}</div>
      <span style="flex:1"></span><button class="btn sm" id="merge" disabled>Merge selected</button></div>
    <div class="card list" id="list"></div></div>`;
  const els = Object.fromEntries($$("[id]", view).map((e) => [e.id, e]));
  let rows = [];

  async function load() {
    history.replaceState(null, "", "/labels?kind=" + kind);
    els.kinds.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.k === kind));
    forgetLabels(); rows = await labels(kind, true); picked.clear(); draw();
  }
  function draw() {
    const filter = kind === "tag" ? "tags" : kind === "correspondent" ? "correspondent_id" : "document_type_id";
    els.list.innerHTML = rows.map((l) => `<div class="item">
      <input type="checkbox" data-pick="${l.id}" ${picked.has(l.id) ? "checked" : ""} aria-label="Select" style="accent-color:var(--accent)">
      ${kind === "tag" ? `<button class="btn ghost sm icon" data-color="${l.id}" title="Colour"><span style="width:12px;height:12px;border-radius:50%;background:${esc(l.color)}"></span></button>` : ""}
      <a class="main" href="/library?${filter}=${l.id}"><div class="t">${kind === "tag" ? tagChip(l) : esc(l.name)}</div></a>
      <span class="faint small num">${plural(l.document_count, "document")}</span>
      <button class="btn ghost sm" data-rename="${l.id}">Rename</button>
      <button class="btn ghost sm icon danger" data-del="${l.id}" title="Delete">${icon("trash")}</button></div>`).join("")
      || `<div class="empty">No ${KINDS.find(([k]) => k === kind)[1].toLowerCase()} yet.</div>`;
    els.merge.disabled = picked.size < 2;
  }
  els.kinds.onclick = (e) => { const b = e.target.closest("[data-k]"); if (b) { kind = b.dataset.k; load(); } };
  els.list.addEventListener("change", (e) => { const c = e.target.closest("[data-pick]"); if (c) { c.checked ? picked.add(+c.dataset.pick) : picked.delete(+c.dataset.pick); els.merge.disabled = picked.size < 2; } });
  els.list.addEventListener("click", async (e) => {
    const r = e.target.closest("[data-rename]"), d = e.target.closest("[data-del]"), c = e.target.closest("[data-color]");
    if (r) {
      const l = rows.find((x) => x.id === +r.dataset.rename);
      const name = await promptBox(`Rename ${KIND_NAMES[kind].toLowerCase()}`, { value: l.name, label: "Name" });
      if (name && name !== l.name) { await api(`/api/labels/${kind}/${l.id}`, { method: "PATCH", body: { name } }); load(); }
    }
    if (d) {
      const l = rows.find((x) => x.id === +d.dataset.del);
      if (await confirmBox(`Delete “${l.name}”?`, `It's removed from ${plural(l.document_count, "document")}. The documents stay.`, { ok: "Delete", danger: true })) {
        await api(`/api/labels/${kind}/${l.id}`, { method: "DELETE" }); toast("Deleted"); load();
      }
    }
    if (c) {
      const l = rows.find((x) => x.id === +c.dataset.color);
      const box = modal(`<h1 style="font-size:17px">Colour for ${tagChip(l)}</h1><div class="row">${COLORS.map((col) => `<button class="btn icon" data-c="${col}" style="border-color:${col === l.color ? "var(--text)" : "var(--line-2)"}"><span style="width:16px;height:16px;border-radius:50%;background:${col}"></span></button>`).join("")}</div>`);
      box.onclick = async (ev) => { const b = ev.target.closest("[data-c]"); if (!b) return; closeModal(); await api(`/api/labels/tag/${l.id}`, { method: "PATCH", body: { color: b.dataset.c } }); load(); };
    }
  });
  els.merge.onclick = () => {
    const sel = rows.filter((l) => picked.has(l.id));
    const box = modal(`<h1 style="font-size:17px">Merge ${plural(sel.length, KIND_NAMES[kind].toLowerCase())}</h1>
      <div class="muted">Keep which name? The others are deleted and their documents move to it.</div>
      <div class="list">${sel.map((l, i) => `<label class="check item"><input type="radio" name="keep" value="${l.id}" ${i === 0 ? "checked" : ""}>${esc(l.name)} <span class="faint small">${plural(l.document_count, "document")}</span></label>`).join("")}</div>
      <div class="acts"><button class="btn" data-a="no">Cancel</button><button class="btn primary" data-a="yes">Merge</button></div>`);
    box.onclick = async (e) => {
      const b = e.target.closest("[data-a]"); if (!b) return;
      if (b.dataset.a === "yes") {
        const keep = +box.querySelector("input[name=keep]:checked").value;
        await api(`/api/labels/${kind}/merge`, { method: "POST", body: { target_id: keep, source_ids: sel.map((l) => l.id).filter((i) => i !== keep) } });
        toast("Merged");
      }
      closeModal(); load();
    };
  };
  els.add.onclick = async () => {
    const name = await promptBox(`New ${KIND_NAMES[kind].toLowerCase()}`, { label: "Name", ok: "Add" });
    if (name) { await api(`/api/labels/${kind}`, { method: "POST", body: { name } }); load(); }
  };
  load();
}
