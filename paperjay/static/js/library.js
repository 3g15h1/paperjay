// Library: search and filter every document, saved searches, grid or list, select several and act on them.
import { $, $$, api, qs, esc, icon, labels, tagChip, docCard, go, toast, promptBox, confirmBox, plural, refreshCounts, combo, ensureLabel, modal, closeModal, libSwitch } from "./core.js";
import { swipeWrap, swipeToDelete } from "./swipe.js";

export const nav = "library";
const PAGE = 60;
const SORTS = { relevance: "Best match", date_desc: "Newest first", date_asc: "Oldest first", added_desc: "Recently added", title: "Title A–Z" };

export async function show(view, { query }) {
  const f = {
    q: query.get("q") || "", tags: (query.get("tags") || "").split(",").filter(Boolean).map(Number),
    correspondent_id: query.get("correspondent_id") || "", document_type_id: query.get("document_type_id") || "",
    date_from: query.get("date_from") || "", date_to: query.get("date_to") || "",
    sort: query.get("sort") || "", needs_review: query.get("needs_review") || "",
    category: query.get("category") || "", topic: query.get("topic") || "", series: query.get("series") || "",
    people: query.get("people") || "", term: query.get("term") || "",
    paper_no: query.get("paper_no") || "", has_paper: query.get("has_paper") || "",
  };
  const CATEGORIES = ["medical", "insurance", "utilities", "shopping", "home", "vehicle", "tax", "income",
    "travel", "food", "finance", "other"];
  let layout = localStorage.getItem("pj-layout") || "grid";
  let results = [], total = 0, selecting = false;
  const picked = new Set();
  const [tags, corrs, types] = await Promise.all([labels("tag"), labels("correspondent"), labels("document_type")]);

  view.innerHTML = `<div class="page">
    <div class="head libhead"><h1>Library</h1><span class="faint num" id="count"></span><span class="spacer"></span>
      <div class="seg" id="layout"><button data-l="grid" title="Grid">${icon("grid")}</button><button data-l="rows" title="List">${icon("rows")}</button></div>
      <button class="btn" id="selBtn">Select</button></div>
    ${libSwitch("library")}
    <div class="filters searchrow">
      <label class="search">${icon("search")}<input class="input" id="q" type="search" placeholder="Search text, titles, companies, tags, notes" value="${esc(f.q)}" autocomplete="off"></label>
      <button type="button" class="btn" id="filtBtn" aria-expanded="false">Filters</button>
    </div>
    <div class="filters more" id="filtPanel">
      <span class="chips" id="tagChips"></span>
      <span class="combo tagcombo"><input class="input" id="tagIn" placeholder="+ Tag"></span>
      <select class="input" id="corr"><option value="">Any company</option>${corrs.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select>
      <select class="input" id="type"><option value="">Any type</option>${types.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("")}</select>
      <select class="input" id="category"><option value="">Any category</option>${CATEGORIES.map((c) => `<option value="${c}">${c[0].toUpperCase() + c.slice(1)}</option>`).join("")}</select>
      <label class="check" style="align-self:center"><input type="checkbox" id="hasPaper"> On the paper shelf</label>
      <label class="datef"><span>From</span><input class="input" type="date" id="from" title="From" value="${esc(f.date_from)}"></label>
      <label class="datef"><span>To</span><input class="input" type="date" id="to" title="To" value="${esc(f.date_to)}"></label>
      <select class="input" id="sort">${Object.entries(SORTS).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select>
      <button class="btn ghost sm" id="reset" hidden>Clear filters</button>
      <button class="btn ghost sm" id="saveView">Save search</button>
    </div>
    <div class="chips" id="termChips" style="margin-bottom:8px"></div>
    <div class="chips" id="views" style="margin-bottom:14px"></div>
    <div class="bulk" id="bulk" hidden>
      <span id="bulkN" class="num"></span><span class="spacer" style="flex:1"></span>
      <span class="combo"><input class="input" id="bulkTag" placeholder="Add tag" style="width:120px;height:30px"></span>
      <span class="combo"><input class="input" id="bulkUntag" placeholder="Remove tag" style="width:130px;height:30px"></span>
      <span class="combo"><input class="input" id="bulkCorr" placeholder="Set company" style="width:130px;height:30px"></span>
      <button class="btn sm" data-b="mark_reviewed">Mark reviewed</button>
      <button class="btn sm" data-b="number_in_order">Number in order</button>
      <button class="btn sm" id="printLabels">Print labels</button>
      <button class="btn sm" data-b="analyze">Re-file with AI</button>
      <button class="btn sm danger" data-b="trash">Move to trash</button>
      <button class="btn ghost sm" id="selAll">Select all</button>
      <button class="btn ghost sm" id="selDone">Done</button>
    </div>
    <div id="list"></div>
    <div style="text-align:center;margin-top:16px"><button class="btn" id="more" hidden>Show more</button></div>
  </div>`;
  const els = Object.fromEntries($$("[id]", view).map((e) => [e.id, e]));
  if (matchMedia("(max-width: 560px)").matches) els.q.placeholder = "Search everything";
  els.corr.value = f.correspondent_id; els.type.value = f.document_type_id;
  els.category.value = f.category; els.hasPaper.checked = f.has_paper === "true";
  els.sort.value = f.sort || (f.q ? "relevance" : "date_desc");
  const anyFilter = () => f.tags.length + [f.correspondent_id, f.document_type_id, f.date_from, f.date_to,
    f.needs_review, f.category, f.topic, f.series, f.people, f.term, f.paper_no, f.has_paper].filter(Boolean).length;

  function params() {
    return { q: f.q, tags: f.tags, correspondent_id: f.correspondent_id, document_type_id: f.document_type_id,
      date_from: f.date_from, date_to: f.date_to, needs_review: f.needs_review, sort: f.sort || (f.q ? "relevance" : "date_desc"),
      category: f.category, topic: f.topic, series: f.series, people: f.people, term: f.term,
      paper_no: f.paper_no, has_paper: f.has_paper };
  }
  function syncUrl() {
    const p = params(); if (p.sort === (f.q ? "relevance" : "date_desc")) delete p.sort;
    history.replaceState(null, "", "/library" + qs(p));
    const active = anyFilter();
    els.reset.hidden = !active;
    els.filtBtn.textContent = active ? `Filters · ${active}` : "Filters";
    els.filtBtn.classList.toggle("on", !!active);
    renderTermChips();
  }
  // filters that arrive by link (from Browse, the A–Z index, suggestions) rather than a
  // control of their own: shown as chips that can be lifted off again
  function renderTermChips() {
    const chips = [];
    if (f.topic) chips.push(["topic", `Topic: ${f.topic}`]);
    if (f.people) chips.push(["people", `Person: ${f.people}`]);
    if (f.series) chips.push(["series", "One series"]);
    if (f.paper_no) chips.push(["paper_no", `Paper #${f.paper_no}`]);
    if (f.term) {
      const kind = f.term.split(":")[0];
      chips.push(["term", { company: "A company in the index", number: "A number in the index",
        person: "A person in the index", topic: "A topic in the index" }[kind] || "Index term"]);
    }
    els.termChips.innerHTML = chips.map(([k, label]) =>
      `<button type="button" class="chip" data-clear="${k}">${esc(label)} ✕</button>`).join("");
  }
  els.termChips.onclick = (e) => { const b = e.target.closest("[data-clear]"); if (b) { f[b.dataset.clear] = ""; load(); } };
  // phones: the filters fold away behind a button (open by themselves when one is in use)
  const setPanel = (open) => { els.filtPanel.classList.toggle("open", open); els.filtBtn.setAttribute("aria-expanded", open); };
  els.filtBtn.onclick = () => setPanel(!els.filtPanel.classList.contains("open"));
  setPanel(!!anyFilter());
  function renderTagChips() {
    els.tagChips.innerHTML = f.tags.map((id) => { const t = tags.find((x) => x.id === id); return t ? `<button type="button" class="chip tag" data-untag="${id}" style="--c:${esc(t.color)}">${esc(t.name)} ✕</button>` : ""; }).join("");
  }
  els.tagChips.onclick = (e) => { const b = e.target.closest("[data-untag]"); if (b) { f.tags = f.tags.filter((t) => t !== +b.dataset.untag); renderTagChips(); load(); } };
  combo(els.tagIn, "tag", (t) => { if (!t.create && !f.tags.includes(t.id)) { f.tags.push(t.id); renderTagChips(); load(); } }, { allowCreate: false, exclude: () => f.tags });

  async function load(append = false) {
    syncUrl();
    if (!append) els.list.innerHTML = `<div class="docs">${'<div class="doc"><div class="thumb skeleton"></div><div class="body"><div class="skeleton" style="height:14px"></div></div></div>'.repeat(6)}</div>`;
    const data = await api("/api/documents" + qs({ ...params(), limit: PAGE, offset: append ? results.length : 0 }));
    results = append ? results.concat(data.results) : data.results;
    total = data.total;
    els.count.textContent = f.q ? `${plural(total, "match", "matches")}${data.matched_all === false ? " (some words)" : ""}` : plural(total, "document");
    draw();
  }
  function draw() {
    els.layout.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.l === layout));
    if (!results.length) {
      els.list.innerHTML = `<div class="card empty">${f.q || els.reset.hidden === false ? "No documents match. Try fewer words or clear the filters." : `Nothing here yet. <a href="/upload" style="color:var(--accent)">Add your first document</a>.`}</div>`;
    } else {
      els.list.innerHTML = `<div class="docs ${layout === "rows" ? "rows" : ""} ${selecting ? "selecting" : ""}">${results.map((d) => swipeWrap(d.id, docCard(d, { picked: picked.has(d.id) }))).join("")}</div>`;
    }
    els.more.hidden = results.length >= total;
    els.bulk.hidden = !selecting;
    els.bulkN.textContent = picked.size ? `${picked.size} selected` : "Pick documents";
    els.selBtn.hidden = selecting;
  }
  els.list.addEventListener("click", (e) => {
    const a = e.target.closest(".doc"); if (!a || !selecting) return;
    e.preventDefault();
    const id = +a.dataset.id;
    picked.has(id) ? picked.delete(id) : picked.add(id);
    a.classList.toggle("picked", picked.has(id));
    els.bulkN.textContent = picked.size ? `${picked.size} selected` : "Pick documents";
  });
  // swipe a document left, then tap Delete: it goes to the trash (with Undo), like "Move to trash"
  const swiper = swipeToDelete(els.list, {
    enabled: () => !selecting && !document.body.classList.contains("readonly"),
    onDelete: async (id, row) => {
      try { await api("/api/documents/bulk", { method: "POST", body: { ids: [id], action: "trash" } }); }
      catch { swiper.close(row); return; }  // api() has already said what went wrong; slide the card back
      row.classList.add("gone");
      setTimeout(() => {
        results = results.filter((d) => d.id !== id); total = Math.max(0, total - 1);
        els.count.textContent = plural(total, "document"); draw();
      }, 200);
      refreshCounts();
      toast("Moved to the trash", { label: "Undo", run: async () => {
        await api("/api/documents/bulk", { method: "POST", body: { ids: [id], action: "restore" } }); load(); refreshCounts(); } });
    },
  });
  let timer;
  els.q.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => { f.q = els.q.value.trim(); if (!f.q && f.sort === "relevance") f.sort = ""; load(); }, 250); });
  els.corr.onchange = () => { f.correspondent_id = els.corr.value; load(); };
  els.type.onchange = () => { f.document_type_id = els.type.value; load(); };
  els.category.onchange = () => { f.category = els.category.value; load(); };
  els.hasPaper.onchange = () => { f.has_paper = els.hasPaper.checked ? "true" : ""; load(); };
  els.from.onchange = () => { f.date_from = els.from.value; load(); };
  els.to.onchange = () => { f.date_to = els.to.value; load(); };
  els.sort.onchange = () => { f.sort = els.sort.value; load(); };
  els.reset.onclick = () => { Object.assign(f, { tags: [], correspondent_id: "", document_type_id: "", date_from: "", date_to: "", needs_review: "",
      category: "", topic: "", series: "", people: "", term: "", paper_no: "", has_paper: "" });
    els.corr.value = els.type.value = els.category.value = els.from.value = els.to.value = ""; els.hasPaper.checked = false; renderTagChips(); load(); };
  els.more.onclick = () => load(true);
  els.layout.onclick = (e) => { const b = e.target.closest("[data-l]"); if (b) { layout = b.dataset.l; localStorage.setItem("pj-layout", layout); draw(); } };
  els.selBtn.onclick = () => { selecting = true; draw(); };
  els.selDone.onclick = () => { selecting = false; picked.clear(); draw(); };
  els.selAll.onclick = () => { results.forEach((d) => picked.add(d.id)); draw(); };

  // ---------- bulk actions ----------
  async function bulk(action, value, message, orderedIds) {
    if (!picked.size) { toast("Pick some documents first"); return; }
    const ids = orderedIds || [...picked];
    await api("/api/documents/bulk", { method: "POST", body: { ids, action, value } });
    toast(message(ids.length), action === "trash" ? { label: "Undo", run: async () => { await api("/api/documents/bulk", { method: "POST", body: { ids, action: "restore" } }); load(); refreshCounts(); } } : null);
    picked.clear(); selecting = false; refreshCounts(); load();
  }
  els.bulk.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-b]"); if (!b) return;
    const a = b.dataset.b;
    if (a === "trash") bulk("trash", null, (n) => `${plural(n, "document")} moved to the trash`);
    if (a === "mark_reviewed") bulk("mark_reviewed", null, (n) => `${plural(n, "document")} marked reviewed`);
    if (a === "number_in_order") {  // in the order they appear in the list, top to bottom
      const ordered = results.map((d) => d.id).filter((id) => picked.has(id));
      bulk("number_in_order", null, () => "Paper numbers assigned in order",
           [...ordered, ...[...picked].filter((id) => !ordered.includes(id))]);
    }
    if (a === "analyze" && await confirmBox("Re-file with AI?", "The AI reads the selected documents again and re-files anything a person hasn't set.", { ok: "Re-file" }))
      bulk("analyze", null, (n) => `${plural(n, "document")} queued for the AI`);
  });
  // ---------- paper labels (Phase D) ----------
  els.printLabels.onclick = () => {
    const box = modal(`<h1 style="font-size:17px">Print paper labels</h1>
      <div style="display:grid;gap:12px">
      <div class="muted">Each label carries the paper number, the title and date, and a code that opens the document.</div>
      <label class="field">Sheet<select class="input" id="plLayout">
        <option value="5160">Avery 5160 — 30 per sheet (Letter)</option>
        <option value="7160">L7160 style — 21 per sheet (A4)</option>
        <option value="single62">Single 62 mm labels (label printer)</option></select></label>
      <button class="btn primary" id="plDocs" ${picked.size ? "" : "disabled"}>Print labels for the ${picked.size} selected</button>
      <div class="field" style="margin:0">Blank labels, ahead of time
        <div class="help">Print numbers before the paper exists and stick one on each sheet as you file it.
        Printed numbers are reserved: the next upload number steps past them, and each can be claimed once.</div>
        <div class="row"><input class="input" id="plCount" type="number" value="30" min="1" max="1000" style="width:90px">
        <button class="btn" id="plBlank">Print blank labels</button><span class="help" id="plNext"></span></div></div>
      <p class="help" style="margin:0">The codes hold PaperJay's secure address when one is set up, otherwise the address
      this page was opened with — those codes then only open PaperJay from devices on your home network.
      To make codes that work from anywhere, open PaperJay at its secure address and print from there.</p>
      <div class="acts"><button class="btn ghost" id="plClose">Close</button></div></div>`);
    const layout = () => box.querySelector("#plLayout").value;
    box.querySelector("#plDocs").onclick = () => window.open(`/api/paper-labels.pdf?ids=${[...picked].join(",")}&layout=${layout()}`, "_blank");
    box.querySelector("#plClose").onclick = () => closeModal();
    api("/api/paper/next", { quiet: true }).then((p) => {
      box.querySelector("#plNext").textContent = `Next free number: ${p.next}`;
      box.querySelector("#plBlank").onclick = () => {
        const c = Math.min(1000, Math.max(1, +box.querySelector("#plCount").value || 30));
        window.open(`/api/paper-labels.pdf?from=${p.next}&to=${p.next + c - 1}&layout=${layout()}`, "_blank");
        closeModal();
      };
    }).catch(() => {});
  };
  combo(els.bulkTag, "tag", async (t) => { t = await ensureLabel("tag", t); bulk("add_tags", [t.id], (n) => `Tagged ${plural(n, "document")} “${t.name}”`); });
  combo(els.bulkUntag, "tag", (t) => bulk("remove_tags", [t.id], (n) => `Removed “${t.name}” from ${plural(n, "document")}`), { allowCreate: false });
  combo(els.bulkCorr, "correspondent", async (c) => { c = await ensureLabel("correspondent", c); bulk("set_correspondent", c.id, (n) => `${plural(n, "document")} now from ${c.name}`); });

  // ---------- saved searches ----------
  async function loadViews() {
    const vs = await api("/api/views", { quiet: true }).catch(() => []);
    els.views.innerHTML = vs.map((v) => `<span class="chip" style="padding-right:4px"><a href="/library${qs(v.params)}">${esc(v.name)}</a>
      <button type="button" class="btn ghost sm" data-delview="${v.id}" style="height:18px;padding:0 4px" title="Delete">✕</button></span>`).join("");
  }
  els.views.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-delview]"); if (!b) return;
    e.preventDefault(); await api(`/api/views/${b.dataset.delview}`, { method: "DELETE" }); loadViews();
  });
  els.saveView.onclick = async () => {
    const name = await promptBox("Save this search", { label: "Name", placeholder: "e.g. Medical 2025", ok: "Save" });
    if (!name) return;
    await api("/api/views", { method: "POST", body: { name, params: params() } }); loadViews(); toast("Saved");
  };

  renderTagChips(); loadViews(); load();
  if (matchMedia("(hover: hover)").matches && !f.q) els.q.focus();
  return () => swiper.detach();
}
