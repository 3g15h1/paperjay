// Review: "Needs review" (documents PaperJay wasn't sure about, and ones that couldn't be read)
// and "Tidy up" (spec E): every gap or doubt in the filing, each with a one-tap fix. Every fix is
// a person's own filing, so the library learns from it.
import { $$, api, esc, icon, fmtDate, plural, refreshCounts, toast, combo, ensureLabel, promptBox } from "./core.js";

export const nav = "review";

const LISTS = {
  failed: ["Couldn't be read", "Try reading it again, or open it to see the whole story."],
  no_company: ["No company", "Pick who sent it. PaperJay files the next ones from them the same way."],
  no_date: ["No date", "Tap the document's own date. Every date PaperJay found is listed; ★ is the one it would pick."],
  no_type: ["No type", "Say what kind of document it is."],
  file_name: ["File-name titles", "The title is just the name of the file. Use the suggested title or type a better one."],
  copies: ["Possible copies", "These look like a second copy of another document. Move the copy to the trash, or say it isn't one."],
  disagree: ["Sources disagree", "The text, the AI or your other papers point different ways. Open it to fix, or say it's right."],
  low_evidence: ["Low evidence", "Filed from one clue that nothing else backs up. Say it's right, or open it to fix."],
  confirm_import: ["Confirm imported filing", "Filing that came with the import. PaperJay only learns from it once you say it's right."],
};
const FIELD_NAMES = { vendor: "Company", kind: "Type", doc_date: "Date" };

const thumb = (d, q = "") => `<a href="/documents/${d.id}${q}" style="flex:none"><img src="/api/documents/${d.id}/thumb" alt="" style="width:44px;height:58px;object-fit:cover;object-position:top;border-radius:5px;border:1px solid var(--line)" onerror="this.style.visibility='hidden'"></a>`;
const filed = (d) => esc([d.correspondent, d.document_type, fmtDate(d.doc_date)].filter(Boolean).join(" · ") || `Added ${fmtDate(d.added_at)}`);
const cap = (s) => s ? s[0].toUpperCase() + s.slice(1) : "Date";
const dateChips = (d) => (d.dates || []).map((x) => `<button type="button" class="chip ${x.picked ? "on" : ""}" data-date="${esc(x.date)}">${x.picked ? "★ " : ""}${esc(cap(x.label))} ${esc(fmtDate(x.date))}</button>`).join("");

export async function show(view, params) {
  const query = params.query;
  let tab = query.get("tab") === "tidy" ? "tidy" : "review";
  let list = query.get("list") || null;
  view.innerHTML = `<div class="page narrow">
    <div class="head"><h1>Review</h1><span class="spacer"></span><button class="btn sm" id="allOk" hidden>${icon("check")} Mark all reviewed</button></div>
    <div class="seg" id="tabs" style="margin-bottom:14px"><button type="button" data-tab="review">Needs review <span class="faint" id="nReview"></span></button><button type="button" data-tab="tidy">Tidy up <span class="faint" id="nTidy"></span></button></div>
    <div id="reviewPane">
      <div class="section" id="failWrap" hidden style="margin-top:0"><div class="h"><h2>Couldn't be read</h2></div><div class="card list" id="failed"></div></div>
      <div class="section" id="revWrap" style="margin-top:0"><div class="h"><h2>Check the filing</h2><span class="aside">PaperJay filed these but wasn't sure about something</span></div><div class="card list" id="review"></div></div>
    </div>
    <div id="tidyPane" hidden>
      <div class="chips scroll" id="lists"></div>
      <div class="row" style="margin:12px 0 8px;align-items:flex-start;gap:10px"><p class="sec-desc" id="listDesc" style="margin:0;flex:1"></p><button class="btn sm" id="bulkBtn" hidden></button></div>
      <div class="card list" id="tidy"></div>
    </div>
  </div>`;
  const els = Object.fromEntries($$("[id]", view).map((e) => [e.id, e]));
  let review = [], items = [];

  function setTab(t) {
    tab = t;
    $$("[data-tab]", els.tabs).forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
    els.reviewPane.hidden = t !== "review"; els.tidyPane.hidden = t !== "tidy";
    els.allOk.hidden = t !== "review" || !review.length;
    if (t === "tidy") loadTidy(); else history.replaceState(null, "", "/review");
  }
  els.tabs.onclick = (e) => { const b = e.target.closest("[data-tab]"); if (b && b.dataset.tab !== tab) setTab(b.dataset.tab); };

  // ---------- needs review ----------
  async function loadReview() {
    const [rev, fail] = await Promise.all([api("/api/documents?needs_review=true&sort=added_desc&limit=200"), api("/api/documents?status=failed&sort=added_desc&limit=200")]);
    review = rev.results;
    els.nReview.textContent = review.length + fail.results.length || "";
    els.failWrap.hidden = !fail.results.length;
    els.failed.innerHTML = fail.results.map((d) => `<div class="item"><a class="main" href="/documents/${d.id}"><div class="t">${esc(d.title)}</div><div class="sub">Open it to see why</div></a>
      <button class="btn sm" data-retry="${d.id}">${icon("redo")} Try again</button></div>`).join("");
    const docs = await Promise.all(review.map((d) => api(`/api/documents/${d.id}`, { quiet: true }).catch(() => null)));
    // opened from here, a document page walks through the list: "Looks right & next" (document.js)
    els.review.innerHTML = docs.filter(Boolean).map((d) => `<div class="item" style="align-items:flex-start">${thumb(d, "?from=review")}
      <a class="main" href="/documents/${d.id}?from=review"><div class="t">${esc(d.title)}</div>
        <div class="sub">${esc([d.correspondent, d.document_type, fmtDate(d.doc_date)].filter(Boolean).join(" · "))}</div>
        <div class="small" style="color:var(--warn);margin-top:4px">${(d.suggestions.review || []).map(esc).join(" · ")}</div></a>
      <button class="btn sm" data-ok="${d.id}">${icon("check")} Looks right</button></div>`).join("") || `<div class="empty">Nothing to check. New documents PaperJay isn't sure about show up here.</div>`;
    els.allOk.hidden = tab !== "review" || !review.length;
  }
  els.allOk.onclick = async () => {
    await api("/api/documents/bulk", { method: "POST", body: { ids: review.map((d) => d.id), action: "mark_reviewed" } });
    toast(`${plural(review.length, "document")} marked reviewed`); loadReview(); refreshCounts();
  };
  els.reviewPane.addEventListener("click", async (e) => {
    const ok = e.target.closest("[data-ok]"), retry = e.target.closest("[data-retry]");
    if (ok) { await api(`/api/documents/${ok.dataset.ok}/reviewed`, { method: "POST" }); refreshCounts(); loadReview(); }
    if (retry) { await api(`/api/documents/${retry.dataset.retry}/reprocess`, { method: "POST" }); toast("Reading it again…"); loadReview(); refreshCounts(); }
  });

  // ---------- tidy up ----------
  async function loadCounts() {
    const h = await api("/api/health/filing", { quiet: true }).catch(() => null);
    els.nTidy.textContent = h && h.documents ? h.documents : "";
    return h;
  }
  async function loadTidy() {
    const h = await loadCounts();
    if (!h) return;
    const open = Object.keys(LISTS).filter((k) => h.counts[k]);
    if (!list || !h.counts[list]) list = open[0] || null;
    els.lists.innerHTML = open.map((k) => `<button type="button" class="chip ${k === list ? "on" : ""}" data-list="${k}">${esc(LISTS[k][0])} <b>${h.counts[k]}</b></button>`).join("");
    history.replaceState(null, "", `/review?tab=tidy${list ? "&list=" + list : ""}`);
    if (!list) {
      els.listDesc.textContent = ""; els.bulkBtn.hidden = true; items = [];
      els.tidy.innerHTML = `<div class="empty">${icon("check")} All tidy: every document has a company, a date and a type, and nothing needs a second look.</div>`;
      return;
    }
    els.listDesc.textContent = LISTS[list][1];
    items = (await api(`/api/health/filing?list=${list}&limit=200`)).items;
    els.bulkBtn.hidden = !["confirm_import", "low_evidence"].includes(list) || !items.length;
    els.bulkBtn.innerHTML = `${icon("check")} ${list === "confirm_import" ? "Confirm all" : "All look right"}`;
    els.tidy.innerHTML = items.map(row).join("") || `<div class="empty">Nothing here now.</div>`;
    wire();
  }
  function row(d) {
    let detail = "", acts = "";
    if (list === "failed") {
      detail = `<div class="small" style="color:var(--bad);margin-top:4px">${esc(d.error)}</div>`;
      acts = `<button class="btn sm" data-act="retry">${icon("redo")} Try again</button>`;
    } else if (list === "no_company" || list === "no_type") {
      const kind = list === "no_company" ? "correspondent" : "document_type";
      detail = `<div class="row" style="gap:6px;margin-top:6px;flex-wrap:wrap">
        ${d.suggest ? `<button type="button" class="chip on" data-act="accept" data-val="${esc(d.suggest)}">${icon("check")} ${esc(d.suggest)}</button>` : ""}
        <span class="combo" style="flex:1;min-width:150px"><input class="input" data-pick="${kind}" placeholder="${kind === "correspondent" ? "Pick a company…" : "Pick a type…"}" style="height:30px"></span></div>`;
    } else if (list === "no_date") {
      detail = `<div class="chips" style="margin-top:6px;align-items:center">${dateChips(d)}<input class="input" type="date" data-dateinput aria-label="Another date" style="height:26px;width:auto"></div>`;
    } else if (list === "file_name") {
      detail = `<div class="row" style="gap:6px;margin-top:6px;flex-wrap:wrap">${(d.options || []).map((t, i) => `<button type="button" class="chip ${i ? "" : "on"}" data-act="accept" data-val="${esc(t)}">${i ? "" : icon("check") + " "}${esc(t)}</button>`).join("")}
        <button type="button" class="chip" data-act="rename">Type a title</button></div>`;
    } else if (list === "copies") {
      detail = `<div class="small muted" style="margin-top:4px">Looks like a copy of <a href="/documents/${d.copy_of.id}" style="color:var(--accent)">${esc(d.copy_of.title)}</a></div>`;
      acts = `<button class="btn sm" data-act="trash">${icon("trash")} Trash copy</button><button class="btn ghost sm" data-act="notcopy">Not a copy</button>`;
    } else if (list === "disagree") {
      detail = `<div class="small" style="color:var(--warn);margin-top:4px">${d.reasons.map(esc).join(" · ")}</div>`;
      acts = `<button class="btn sm" data-act="reviewed">${icon("check")} Looks right</button>`;
    } else if (list === "low_evidence") {
      const val = { vendor: d.correspondent, kind: d.document_type, doc_date: fmtDate(d.doc_date) };
      detail = `<div class="small muted" style="margin-top:4px">${d.fields.map((f) => `${FIELD_NAMES[f]}: <b>${esc(val[f])}</b>`).join(" · ")}</div>
        ${d.dates && d.dates.length > 1 ? `<div class="chips" style="margin-top:6px">${dateChips(d)}</div>` : ""}`;
      acts = `<button class="btn sm" data-act="confirm-fields">${icon("check")} Looks right</button>`;
    } else if (list === "confirm_import") {
      acts = `<button class="btn sm" data-act="confirm">${icon("check")} Confirm</button>`;
    }
    return `<div class="item" data-id="${d.id}" style="align-items:flex-start;flex-wrap:wrap">${thumb(d)}
      <div class="main"><a href="/documents/${d.id}" style="color:inherit"><div class="t">${esc(d.title)}</div><div class="sub">${filed(d)}</div></a>${detail}
      ${acts ? `<div class="row" style="gap:6px;margin-top:8px;flex-wrap:wrap">${acts}</div>` : ""}</div></div>`;
  }
  async function done(message) { toast(message); refreshCounts(); await loadTidy(); }
  async function act(id, name, value) {
    const post = (path, body) => api(`/api/documents/${id}${path}`, { method: "POST", body });
    if (name === "retry") { await post("/reprocess"); return done("Reading it again…"); }
    if (name === "accept") {
      const field = { no_company: "correspondent", no_type: "document_type", file_name: "title" }[list];
      await post("/suggestions/accept", { [field]: value }); return done("Saved");
    }
    if (name === "date") { await post("/suggestions/accept", { doc_date: value }); return done("Date saved"); }
    if (name === "rename") {
      const d = items.find((x) => x.id === id);
      const title = await promptBox("Title", { value: d.suggest || "", placeholder: "What is this document?" });
      if (title) { await api(`/api/documents/${id}`, { method: "PATCH", body: { title } }); return done("Saved"); }
      return;
    }
    if (name === "trash") { await post("/trash"); return done("Moved to the trash"); }
    if (name === "notcopy") { await post("/not-a-copy"); return done("Kept: it won't be flagged as that copy again"); }
    if (name === "reviewed") { await post("/reviewed"); return done("Marked as right"); }
    if (name === "confirm-fields") { await post("/confirm-fields", {}); return done("Confirmed"); }
    if (name === "confirm") { await post("/confirm-filing"); return done("Filing confirmed"); }
  }
  function wire() {
    for (const input of $$("[data-pick]", els.tidy)) {
      const kind = input.dataset.pick, id = +input.closest("[data-id]").dataset.id;
      combo(input, kind, async (l) => {
        l = await ensureLabel(kind, l);
        await api(`/api/documents/${id}`, { method: "PATCH", body: { [kind === "correspondent" ? "correspondent_id" : "document_type_id"]: l.id } });
        done("Saved");
      });
    }
    for (const input of $$("[data-dateinput]", els.tidy)) {
      input.onchange = () => { if (input.value) act(+input.closest("[data-id]").dataset.id, "date", input.value); };
    }
  }
  els.lists.onclick = (e) => { const b = e.target.closest("[data-list]"); if (b) { list = b.dataset.list; loadTidy(); } };
  els.tidy.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-act], button[data-date]");
    if (!b) return;
    const id = +b.closest("[data-id]").dataset.id;
    if (b.dataset.date) act(id, "date", b.dataset.date);
    else act(id, b.dataset.act, b.dataset.val);
  });
  els.bulkBtn.onclick = async () => {
    const action = list === "confirm_import" ? "confirm_filing" : "confirm_fields";
    const r = await api("/api/documents/bulk", { method: "POST", body: { ids: items.map((d) => d.id), action } });
    done(`${plural(r.done, "document")} confirmed`);
  };

  await loadReview();
  loadCounts();
  setTab(tab);
}
