// "Fix pages": turn, remove, move and split the pages of one document. Saving makes new document(s) with
// the same filing; the old one goes to the trash (pageedit.py).
import { $, api, esc, plural, toast, modal, closeModal, go, refreshCounts } from "./core.js";

export function openPageEditor(doc) {
  const n = doc.page_count || 1;
  let pages = Array.from({ length: n }, (_, i) => ({ n: i + 1, rotate: 0, keep: true }));
  const splits = new Set();
  const box = modal(`<div class="row" style="align-items:baseline"><b style="font-size:16px">Fix pages</b><span class="spacer" style="flex:1"></span>
      <span class="faint small" id="peSum"></span></div>
    <p class="help" style="margin:0">Turn pages, take out the ones you don't want, move them, or split the document where a new
      one starts. PaperJay makes the new document(s) with the same filing and paper number, and moves this one to the Trash
      (you can restore it for 30 days).</p>
    <div class="pe-grid" id="peGrid"></div>
    <div class="acts"><button class="btn ghost" id="peCancel">Cancel</button><button class="btn primary" id="peSave">Save</button></div>`);
  box.style.width = "min(900px, 100%)";
  const grid = $("#peGrid", box);

  function parts() {
    const out = []; let cur = [];
    for (const p of pages) {
      if (p.keep) cur.push(p.n);
      if (splits.has(p.n) && cur.length) { out.push(cur); cur = []; }
    }
    if (cur.length) out.push(cur);
    return out;
  }
  function draw() {
    grid.innerHTML = pages.map((p, i) => `<div class="pe-page ${p.keep ? "" : "off"}">
        <div class="pe-img"><img src="/api/documents/${doc.id}/pages/${p.n}?w=600&r=${doc.rev}" alt="Page ${p.n}" style="transform:rotate(${p.rotate}deg)" loading="lazy"></div>
        <div class="pe-bar"><span class="num small">${p.n}</span><span style="flex:1"></span>
          <button type="button" class="btn ghost sm icon" data-left="${i}" title="Move earlier" ${i ? "" : "disabled"}>←</button>
          <button type="button" class="btn ghost sm icon" data-turn="${i}" data-by="270" title="Turn left">↺</button>
          <button type="button" class="btn ghost sm icon" data-turn="${i}" data-by="90" title="Turn right">↻</button>
          <button type="button" class="btn ghost sm icon" data-right="${i}" title="Move later" ${i < pages.length - 1 ? "" : "disabled"}>→</button></div>
        <button type="button" class="btn sm ${p.keep ? "ghost" : "danger"}" data-keep="${i}">${p.keep ? "Take out" : "Taken out · put back"}</button>
      </div>${i < pages.length - 1 ? `<button type="button" class="pe-split ${splits.has(p.n) ? "on" : ""}" data-split="${p.n}" title="Start a new document after this page">${splits.has(p.n) ? "✂ New document starts here" : "✂"}</button>` : ""}`).join("");
    const ps = parts();
    const kept = ps.reduce((a, p) => a + p.length, 0);
    $("#peSum", box).textContent = !kept ? "Keep at least one page" : ps.length > 1 ? `Makes ${ps.length} documents` : `${plural(kept, "page")}`;
    $("#peSave", box).disabled = !kept;
  }
  grid.onclick = (e) => {
    const t = e.target.closest("button"); if (!t) return;
    const d = t.dataset;
    if (d.turn) { const p = pages[+d.turn]; p.rotate = (p.rotate + +d.by) % 360; }
    if (d.keep) pages[+d.keep].keep = !pages[+d.keep].keep;
    if (d.left) { const i = +d.left; [pages[i - 1], pages[i]] = [pages[i], pages[i - 1]]; }
    if (d.right) { const i = +d.right; [pages[i + 1], pages[i]] = [pages[i], pages[i + 1]]; }
    if (d.split) { const k = +d.split; splits.has(k) ? splits.delete(k) : splits.add(k); }
    draw();
  };
  $("#peCancel", box).onclick = closeModal;
  $("#peSave", box).onclick = async () => {
    const save = $("#peSave", box);
    save.disabled = true; save.textContent = "Saving…";
    try {
      const r = await api(`/api/documents/${doc.id}/edit-pages`, { method: "POST", body: { pages, split_after: [...splits] } });
      closeModal(); refreshCounts();
      toast(r.documents.length > 1 ? `Made ${r.documents.length} documents. They're being read now.` : "Saved. It's being read again now.");
      go(r.documents.length > 1 ? `/library?sort=added_desc` : `/documents/${r.documents[0]}`);
    } catch { save.disabled = false; save.textContent = "Save"; }
  };
  draw();
}

// Library: join the selected documents into one, in the order they were picked.
export async function joinDocuments(ids, titles) {
  return new Promise((resolve) => {
    const box = modal(`<b style="font-size:16px">Join into one document?</b>
      <p class="help" style="margin:0">The pages follow each other in this order. The new document takes the filing and paper number
        of the first one and every tag and note; the ${plural(ids.length, "document")} move to the Trash (you can restore them for 30 days).</p>
      <ol style="margin:0;padding-left:20px">${titles.map((t) => `<li>${esc(t)}</li>`).join("")}</ol>
      <div class="acts"><button class="btn ghost" id="jCancel">Cancel</button><button class="btn primary" id="jOk">Join</button></div>`);
    $("#jCancel", box).onclick = () => { closeModal(); resolve(null); };
    $("#jOk", box).onclick = async () => {
      $("#jOk", box).disabled = true;
      try {
        const r = await api("/api/documents/merge", { method: "POST", body: { ids } });
        closeModal(); refreshCounts(); toast("Joined. The new document is being read now.");
        resolve(r.document); go(`/documents/${r.document}`);
      } catch { $("#jOk", box).disabled = false; }
    };
  });
}
