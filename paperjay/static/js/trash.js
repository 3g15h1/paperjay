// Trash: documents stay 30 days, then they're deleted for good.
import { $$, api, esc, icon, fmtDate, plural, refreshCounts, toast, confirmBox } from "./core.js";

export const nav = "trash";

export async function show(view) {
  view.innerHTML = `<div class="page narrow">
    <div class="head mobile-only" style="margin-bottom:6px"><a class="btn ghost sm" href="/settings">← Settings</a></div>
    <div class="head"><h1>Trash</h1><span class="spacer"></span><button class="btn sm danger" id="empty" hidden>${icon("trash")} Empty trash</button></div>
    <p class="muted small" style="margin-top:-8px">Documents here are deleted for good 30 days after they were moved here.</p>
    <div class="card list" id="list"></div></div>`;
  const els = Object.fromEntries($$("[id]", view).map((e) => [e.id, e]));
  async function load() {
    const r = await api("/api/documents?trash=true&sort=added_desc&limit=500");
    els.empty.hidden = !r.results.length;
    els.list.innerHTML = r.results.map((d) => {
      const left = d.deleted_at ? Math.max(0, 30 - Math.floor((Date.now() - Date.parse(d.deleted_at)) / 86400000)) : 30;
      return `<div class="item"><a class="main" href="/documents/${d.id}"><div class="t">${esc(d.title)}</div>
        <div class="sub">${esc([d.correspondent, fmtDate(d.doc_date)].filter(Boolean).join(" · "))} · deleted for good in ${plural(left, "day")}</div></a>
        <button class="btn sm" data-restore="${d.id}">Restore</button><button class="btn sm ghost danger" data-purge="${d.id}">Delete now</button></div>`;
    }).join("") || `<div class="empty">The trash is empty.</div>`;
  }
  view.addEventListener("click", async (e) => {
    const r = e.target.closest("[data-restore]"), p = e.target.closest("[data-purge]");
    if (r) { await api(`/api/documents/${r.dataset.restore}/restore`, { method: "POST" }); toast("Restored"); }
    if (p) {
      if (!await confirmBox("Delete for good?", "The document and its files are deleted. This can't be undone.", { ok: "Delete", danger: true })) return;
      await api(`/api/documents/${p.dataset.purge}`, { method: "DELETE" }); toast("Deleted");
    }
    if (r || p) { load(); refreshCounts(); }
  });
  els.empty.onclick = async () => {
    if (!await confirmBox("Empty the trash?", "Every document in the trash is deleted for good. This can't be undone.", { ok: "Empty trash", danger: true })) return;
    const res = await api("/api/trash/empty", { method: "POST" }); toast(`${plural(res.deleted, "document")} deleted`); load(); refreshCounts();
  };
  load();
}
