// One document: its pages, its fields (editable), what the AI suggested, its text, notes and history.
import { $, $$, api, esc, icon, fmtDate, ago, money, labels, tagChip, combo, ensureLabel, forgetLabels, go, toast,
  confirmBox, refreshCounts, plural } from "./core.js";

export const nav = "library";
const FIELD_NAMES = { title: "title", doc_date: "date", correspondent_id: "company", document_type_id: "type", tags: "tags",
  paper_no: "paper number", content: "text", status: "status", needs_review: "review", error: "note" };
const ACTIONS = { add: "Added", update: "Edited", ai_update: "Filed by the AI", auto_update: "Read by OCR", note: "Note added",
  trash: "Moved to the trash", restore: "Restored", purge: "Deleted" };

export async function show(view, { id }) {
  let doc;
  try { doc = await api(`/api/documents/${id}`, { quiet: true }); }
  catch { view.innerHTML = `<div class="page narrow"><div class="card empty">This document doesn't exist (any more). <a href="/library">Back to the library</a></div></div>`; return; }
  let poll = null, tab = "details";

  view.innerHTML = `<div class="page">
    <div class="head" style="margin-bottom:10px"><a class="btn ghost sm" href="/library">← Library</a><span class="spacer"></span>
      <a class="btn sm" href="/?doc=${doc.id}" title="Ask about this">${icon("ask")}<span class="lbl"> Ask about this</span></a>
      <a class="btn sm" href="/api/documents/${doc.id}/original" download title="Download the original">${icon("download")}<span class="lbl"> Original</span></a>
      <button class="btn sm icon" id="moreBtn" title="More">${icon("more")}</button></div>
    <div class="docpage">
      <div><input class="title-edit" id="title" value="${esc(doc.title)}" aria-label="Title"><div class="faint small" id="sub" style="margin:2px 0 14px"></div>
        <div id="banner"></div><div class="viewer" id="viewer"></div></div>
      <div class="panel">
        <div class="seg" id="tabs"><button data-t="details">Details</button><button data-t="text">Text</button><button data-t="notes">Notes</button><button data-t="history">History</button></div>
        <div class="card" id="pane"></div>
      </div>
    </div></div>`;
  const els = Object.fromEntries($$("[id]", view).map((e) => [e.id, e]));

  // ---------- header, banners and pages ----------
  function drawTop() {
    els.title.value = doc.title;
    els.sub.innerHTML = esc([doc.original_name, doc.page_count ? plural(doc.page_count, "page") : "", `added ${ago(doc.added_at)}`].filter(Boolean).join(" · "))
      + (doc.paper_no ? ` <span class="paperno">Paper no. ${doc.paper_no}</span>` : "");
    const b = [];
    if (doc.deleted_at) b.push(`<div class="notice warn"><div class="t">In the trash</div><div>It will be deleted for good 30 days after ${esc(fmtDate(doc.deleted_at.slice(0, 10)))}.</div><div><button class="btn sm" data-act="restore">Restore</button></div></div>`);
    if (doc.status === "pending" || doc.status === "processing") b.push(`<div class="notice info"><div class="t">Reading this document…</div><div>OCR is running${doc.error ? `: ${esc(doc.error)}` : ""}. This page updates by itself.</div></div>`);
    if (doc.status === "failed") b.push(`<div class="notice bad"><div class="t">This document couldn't be read</div><div>${esc(doc.error || "Unknown error")}</div><div class="faint small">The original file is kept safe.</div><div><button class="btn sm" data-act="reprocess">Try again</button></div></div>`);
    else if (doc.status === "ready" && doc.error) b.push(`<div class="notice warn"><div>${esc(doc.error)}</div></div>`);
    els.banner.innerHTML = b.join("");
  }
  function drawPages() {
    const n = doc.page_count || (doc.status === "ready" || doc.mime_type !== "application/pdf" ? 1 : 0);
    if (!n) { els.viewer.innerHTML = `<div class="card skeleton" style="height:420px"></div>`; return; }
    const w = Math.min(1800, Math.round((els.viewer.clientWidth || 800) * (devicePixelRatio || 1) / 600) * 600 || 1200);
    els.viewer.innerHTML = Array.from({ length: n }, (_, i) =>
      `<img loading="${i < 2 ? "eager" : "lazy"}" alt="Page ${i + 1}" src="/api/documents/${doc.id}/pages/${i + 1}?w=${w}&r=${doc.rev}">`).join("") +
      (doc.archive_checksum ? `<a class="btn ghost sm" href="/api/documents/${doc.id}/archive" target="_blank" rel="noopener">${icon("file")} Open the searchable PDF</a>` : "");
  }

  // ---------- details ----------
  async function drawDetails() {
    const ai = new Set(doc.ai_fields);
    const [corrs, types] = await Promise.all([labels("correspondent"), labels("document_type")]);
    const sug = doc.suggestions || {};
    const review = sug.review || [];
    const card = doc.card;
    const aiMark = (f) => ai.has(f) ? `<span class="chip accent mini" title="Set by the AI. Change it and it's yours.">AI</span>` : "";
    els.pane.innerHTML = `
      ${doc.needs_review ? `<div class="notice warn"><div class="t">Check this one</div>${review.map((r) => `<div>• ${esc(r)}</div>`).join("")}
        ${(sug.new_tags || []).map((t) => `<div><button class="btn sm" data-accept-tag="${esc(t)}">${icon("plus")} Add tag “${esc(t)}”</button></div>`).join("")}
        ${sug.correspondent ? `<div><button class="btn sm" data-accept-corr="${esc(sug.correspondent)}">${icon("plus")} Add company “${esc(sug.correspondent)}”</button></div>` : ""}
        <div><button class="btn sm primary" data-act="reviewed">${icon("check")} Looks right</button></div></div>` : ""}
      <label class="field"><span class="lab">Date${aiMark("doc_date")}</span><input class="input" type="date" id="f-date" value="${esc(doc.doc_date || "")}"></label>
      <label class="field"><span class="lab">Paper no.${aiMark("paper_no")}</span><input class="input" type="number" min="1" step="1" id="f-paper" value="${doc.paper_no ?? ""}" placeholder="Not numbered"><span class="help">The number written on the paper original. Empty clears it.</span></label>
      <label class="field"><span class="lab">Company${aiMark("correspondent_id")}</span><select class="input" id="f-corr"><option value="">—</option>${corrs.map((c) => `<option value="${c.id}" ${c.id === doc.correspondent_id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}<option value="new">+ New company…</option></select></label>
      <label class="field"><span class="lab">Type${aiMark("document_type_id")}</span><select class="input" id="f-type"><option value="">—</option>${types.map((c) => `<option value="${c.id}" ${c.id === doc.document_type_id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}<option value="new">+ New type…</option></select></label>
      <div class="field"><span class="lab">Tags${aiMark("tags")}</span><div class="tagpick">${doc.tags.map((t) => `<span class="chip tag" style="--c:${esc(t.color)}">${esc(t.name)}<button type="button" data-untag="${t.id}" aria-label="Remove">✕</button></span>`).join("")}
        <span class="combo" style="flex:1;min-width:120px"><input class="input" id="f-tag" placeholder="Add a tag" style="height:30px"></span></div></div>
      ${card ? `<div class="field">What the AI read<dl class="kv">
        ${card.summary ? `<dt>Summary</dt><dd>${esc(card.summary)}</dd>` : ""}
        ${card.total != null ? `<dt>Total</dt><dd class="num">${money(card.total)}</dd>` : ""}
        ${card.dates.map((d) => `<dt>${esc(d.label[0].toUpperCase() + d.label.slice(1))}</dt><dd>${esc(fmtDate(d.date))}</dd>`).join("")}
        ${card.ids.map((i) => `<dt>${esc(i.label)}</dt><dd class="num">${esc(i.value)}</dd>`).join("")}</dl></div>` : ""}
      <dl class="kv faint small"><dt>File</dt><dd>${esc(doc.mime_type)} · ${(doc.size / 1024).toFixed(0)} KB</dd><dt>Added</dt><dd>${esc(fmtDate(doc.added_at.slice(0, 10)))}</dd></dl>`;
    const save = (fields) => patch(fields);
    $("#f-date", els.pane).onchange = (e) => save({ doc_date: e.target.value || null });
    $("#f-paper", els.pane).onchange = (e) => { const v = e.target.value.trim(); save({ paper_no: v ? parseInt(v, 10) : null }); };
    const pickSelect = (sel, kind, field) => sel.onchange = async () => {
      if (sel.value === "new") {
        const name = prompt(kind === "correspondent" ? "New company name" : "New type name");
        if (!name) { sel.value = doc[field] ?? ""; return; }
        const made = await ensureLabel(kind, { create: name });
        return save({ [field]: made.id });
      }
      save({ [field]: sel.value ? +sel.value : null });
    };
    pickSelect($("#f-corr", els.pane), "correspondent", "correspondent_id");
    pickSelect($("#f-type", els.pane), "document_type", "document_type_id");
    combo($("#f-tag", els.pane), "tag", async (t) => { t = await ensureLabel("tag", t); save({ tags: [...doc.tags.map((x) => x.id), t.id] }); },
      { exclude: () => doc.tags.map((t) => t.id) });
  }
  async function patch(fields) {
    doc = { ...doc, ...(await api(`/api/documents/${doc.id}`, { method: "PATCH", body: fields })) };
    doc = await api(`/api/documents/${doc.id}`);
    forgetLabels(); drawTop(); drawTab(); refreshCounts();
  }

  // ---------- other tabs ----------
  async function drawTab() {
    els.tabs.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.t === tab));
    if (tab === "details") return drawDetails();
    if (tab === "text") {
      els.pane.innerHTML = doc.content ? `<div class="ocrtext">${esc(doc.content.replace(/\f/g, "\n\n— next page —\n\n"))}</div>
        <div><button class="btn sm ghost" id="copyText">${icon("copy")} Copy text</button></div>` : `<div class="faint">No text was found${doc.status === "ready" ? "" : " yet"}.</div>`;
      const c = $("#copyText", els.pane); if (c) c.onclick = () => navigator.clipboard.writeText(doc.content).then(() => toast("Copied"));
      return;
    }
    if (tab === "notes") {
      const notes = await api(`/api/documents/${doc.id}/notes`);
      els.pane.innerHTML = `${notes.map((n) => `<div><div>${esc(n.body)}</div><div class="faint small">${esc(n.user || "")} ${ago(n.created_at)}</div></div>`).join("") || '<div class="faint">No notes yet. Notes are searchable.</div>'}
        <form id="noteForm" style="display:grid;gap:8px"><textarea class="input" rows="3" id="noteText" placeholder="Add a note"></textarea><div><button class="btn sm primary">Add note</button></div></form>`;
      $("#noteForm", els.pane).onsubmit = async (e) => {
        e.preventDefault(); const body = $("#noteText", els.pane).value.trim(); if (!body) return;
        await api(`/api/documents/${doc.id}/notes`, { method: "POST", body: { body } }); drawTab();
      };
      return;
    }
    const hist = await api(`/api/documents/${doc.id}/history`);
    const names = { tag: Object.fromEntries((await labels("tag")).map((t) => [t.id, t.name])),
      correspondent_id: Object.fromEntries((await labels("correspondent")).map((t) => [t.id, t.name])),
      document_type_id: Object.fromEntries((await labels("document_type")).map((t) => [t.id, t.name])) };
    const val = (k, v) => v == null || v === "" ? "—" : k === "tags" ? v.map((t) => names.tag[t] || "#" + t).join(", ") || "—"
      : names[k] ? names[k][v] || "#" + v : k === "content" ? `${v} characters` : String(v);
    els.pane.innerHTML = `<div class="timeline">${hist.slice().reverse().map((h) => {
      const changes = Object.entries(h.detail).filter(([k]) => FIELD_NAMES[k] && k !== "given").map(([k, v]) =>
        Array.isArray(v) && v.length === 2 ? `<div class="faint">${FIELD_NAMES[k]}: ${esc(val(k, v[0]))} → ${esc(val(k, v[1]))}</div>` : "").join("");
      return `<div class="ev ${h.action === "ai_update" ? "ai" : ""}"><div>${ACTIONS[h.action] || esc(h.action)}${h.user ? ` by ${esc(h.user)}` : ""} <span class="faint">· ${ago(h.at)}</span></div>${changes}</div>`;
    }).join("")}</div>`;
  }
  els.tabs.onclick = (e) => { const b = e.target.closest("[data-t]"); if (b) { tab = b.dataset.t; drawTab(); } };

  // ---------- actions ----------
  els.title.addEventListener("keydown", (e) => { if (e.key === "Enter") els.title.blur(); if (e.key === "Escape") { els.title.value = doc.title; els.title.blur(); } });
  els.title.addEventListener("blur", () => { const t = els.title.value.trim(); if (t && t !== doc.title) patch({ title: t }); else els.title.value = doc.title; });
  view.addEventListener("click", async (e) => {
    const a = e.target.closest("[data-act], [data-untag], [data-accept-tag], [data-accept-corr]"); if (!a) return;
    if (a.dataset.untag) return patch({ tags: doc.tags.map((t) => t.id).filter((t) => t !== +a.dataset.untag) });
    if (a.dataset.acceptTag) { await api(`/api/documents/${doc.id}/suggestions/accept`, { method: "POST", body: { tag: a.dataset.acceptTag } }); forgetLabels(); return reload(); }
    if (a.dataset.acceptCorr) { await api(`/api/documents/${doc.id}/suggestions/accept`, { method: "POST", body: { correspondent: a.dataset.acceptCorr } }); forgetLabels(); return reload(); }
    const act = a.dataset.act;
    if (act === "reviewed") { await api(`/api/documents/${doc.id}/reviewed`, { method: "POST" }); refreshCounts(); return reload(); }
    if (act === "restore") { await api(`/api/documents/${doc.id}/restore`, { method: "POST" }); toast("Restored"); refreshCounts(); return reload(); }
    if (act === "reprocess") { await api(`/api/documents/${doc.id}/reprocess`, { method: "POST" }); toast("Reading it again…"); return reload(); }
  });
  els.moreBtn.onclick = () => {
    const menu = document.createElement("div");
    menu.className = "card"; menu.style.cssText = "position:absolute;z-index:30;padding:4px;display:grid;min-width:200px";
    const r = els.moreBtn.getBoundingClientRect();
    menu.style.top = (r.bottom + scrollY + 4) + "px"; menu.style.right = (innerWidth - r.right) + "px";
    menu.innerHTML = `<button class="btn ghost" data-m="analyze" style="justify-content:flex-start">${icon("sparkle")} File again with AI</button>
      <button class="btn ghost" data-m="reprocess" style="justify-content:flex-start">${icon("redo")} Read again (OCR)</button>
      ${doc.paper_no ? `<button class="btn ghost" data-m="label" style="justify-content:flex-start">${icon("tag")} Print paper label</button>` : ""}
      ${doc.deleted_at ? "" : `<button class="btn ghost danger" data-m="trash" style="justify-content:flex-start">${icon("trash")} Move to trash</button>`}`;
    document.body.append(menu);
    const close = () => { menu.remove(); removeEventListener("mousedown", out); };
    const out = (e) => { if (!menu.contains(e.target)) close(); };
    setTimeout(() => addEventListener("mousedown", out));
    menu.onclick = async (e) => {
      const b = e.target.closest("[data-m]"); if (!b) return; close();
      if (b.dataset.m === "analyze") { await api(`/api/documents/${doc.id}/analyze`, { method: "POST" }); toast("The AI will file it again shortly"); }
      if (b.dataset.m === "label") window.open(`/api/paper-labels.pdf?ids=${doc.id}&layout=single62`, "_blank");
      if (b.dataset.m === "reprocess" && await confirmBox("Read it again?", "OCR runs again on the original file. Fields you've set stay as they are.", { ok: "Read again" })) {
        await api(`/api/documents/${doc.id}/reprocess`, { method: "POST" }); reload();
      }
      if (b.dataset.m === "trash") {
        await api(`/api/documents/${doc.id}/trash`, { method: "POST" }); refreshCounts();
        toast("Moved to the trash", { label: "Undo", run: async () => { await api(`/api/documents/${doc.id}/restore`, { method: "POST" }); refreshCounts(); go(`/documents/${doc.id}`); } });
        go("/library");
      }
    };
  };

  async function reload() {
    const before = doc.status + doc.rev;
    doc = await api(`/api/documents/${doc.id}`);
    drawTop(); drawTab();
    if (before !== doc.status + doc.rev) drawPages();
    const busy = doc.status === "pending" || doc.status === "processing";
    if (busy && !poll) poll = setInterval(reload, 2500);
    if (!busy && poll) { clearInterval(poll); poll = null; refreshCounts(); }
  }
  drawTop(); drawPages(); drawTab();
  if (doc.status === "pending" || doc.status === "processing") poll = setInterval(reload, 2500);
  return () => clearInterval(poll);
}
