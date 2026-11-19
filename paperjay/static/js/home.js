// Home: one box to find or ask (as you type it suggests companies, people, numbers and
// documents from search.js; Enter asks), what's coming up, what needs attention, recent documents.
import { $, $$, api, esc, icon, fmtDate, ago, money, plural, docCard, refreshCounts, me } from "./core.js";
import { attach } from "./search.js";

export const nav = "home";
const HKEY = "pj-ask-history";
const PLACEHOLDERS = ["When does my car insurance expire?", "How much did I spend on medical this year?",
  "What's my policy number?", "Show me my receipts from March", "When is my water bill due?"];

// "Hi Sam" and today's date at the top of the start page; hidden while an answer is shown
function greeting() {
  const first = (me?.display_name || me?.username || "").trim().split(/\s+/)[0];
  const today = new Date().toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  return `<div class="greet" id="greet"><div><h1>${first ? `Hi ${esc(first)}` : "Hi there"}</h1><div class="date">${esc(today)}</div></div>
    <a class="btn icon round mobile-only" href="/settings" aria-label="Settings" title="Settings">${icon("settings")}</a></div>`;
}

export async function show(view, { query }) {
  view.innerHTML = `<div class="page narrow">
    ${greeting()}
    <div class="askwrap"><form class="askbox" id="askForm"><textarea id="q" rows="1" data-searchbox placeholder="${esc(PLACEHOLDERS[0])}" aria-label="Search or ask your documents"></textarea>
      <button class="btn primary" id="go" aria-label="Ask">${icon("send")}</button></form>
      <div class="suggest" id="qSuggest" hidden></div></div>
    <div class="context" id="ctx" hidden><span id="ctxText"></span><button type="button" class="btn ghost sm" id="ctxClear">New topic</button></div>
    <div class="chips scroll" id="suggest" style="margin-top:10px"></div>
    <div id="result" hidden>
      <div class="card answer">
        <div class="top"><span class="chip" id="mode" hidden></span><span class="faint small num" id="timer"></span>
          <span class="acts">
            <button class="btn ghost sm" id="aiBtn" hidden>${icon("sparkle")} Ask the AI</button>
            <button class="btn ghost sm icon" id="upBtn" title="Good answer" hidden>${icon("up")}</button>
            <button class="btn ghost sm icon" id="downBtn" title="Wrong answer" hidden>${icon("down")}</button>
            <button class="btn ghost sm icon" id="copyBtn" title="Copy" hidden>${icon("copy")}</button>
          </span></div>
        <div class="text" id="answer"></div>
        <div class="facts" id="facts"></div>
        <div class="breakdown" id="breakdown"></div>
        <div class="note-line" id="note" hidden></div>
        <form class="row" id="fbBox" hidden style="margin-top:10px"><input class="input" id="fbText" placeholder="What was wrong? (optional)" style="flex:1"><button class="btn sm">Send</button></form>
      </div>
      <div class="section" id="sourcesWrap" hidden><div class="h"><h2>Sources</h2><span class="aside" id="meta"></span></div><div class="sources" id="sources"></div>
        <a class="btn ghost sm" id="allLink" hidden style="margin-top:10px">See every match in the Library</a></div>
    </div>
    <div id="home">
      <div class="section" id="upWrap" hidden><div class="h"><h2>Coming up</h2><a class="aside" href="/settings/account#calendar">${icon("calendar", "i")} Add to calendar</a></div>
        <div class="card list upcoming" id="upcoming"></div></div>
      <div class="section" id="attnWrap" hidden><div class="h"><h2>Needs attention</h2></div><div class="card list" id="attn"></div></div>
      <div class="section" id="recentWrap" hidden><div class="h"><h2>Recent questions</h2><button class="btn ghost sm aside" id="clearBtn">Clear</button></div><div class="card list" id="recent"></div></div>
      <div class="section" id="newWrap" hidden><div class="h"><h2>Recently added</h2><a class="aside" href="/library?sort=added_desc">See all</a></div><div class="docs" id="newDocs"></div></div>
    </div>
  </div>`;

  const els = Object.fromEntries($$("[id]", view).map((e) => [e.id, e]));
  let es = null, tick = null, current = null, followCtx = null, docScope = null;
  const settings = await api("/api/settings", { quiet: true }).catch(() => ({}));

  // ---------- input ----------
  const sugg = (settings.ask_suggestions || []).length ? settings.ask_suggestions : [];
  els.suggest.innerHTML = sugg.map((s) => `<button type="button" class="chip" data-q="${esc(s)}">${esc(s)}</button>`).join("");
  els.suggest.hidden = !sugg.length;
  els.suggest.onclick = (e) => { const c = e.target.closest("[data-q]"); if (c) { newTopic(); els.q.value = c.dataset.q; ask(c.dataset.q); } };
  const autosize = () => { els.q.style.height = "auto"; els.q.style.height = Math.min(els.q.scrollHeight + 2, 160) + "px"; els.go.disabled = !els.q.value.trim() || !!es; };
  els.q.addEventListener("input", autosize);
  // Enter (with no suggestion picked) asks; the picker owns the key so ↑/↓/Enter can choose a suggestion
  const picker = attach(els.q, els.qSuggest, { onEnter: () => els.askForm.requestSubmit(), enabled: () => !docScope && !es });
  document.body.classList.add("on-home");
  els.askForm.onsubmit = (e) => { e.preventDefault(); const q = els.q.value.trim(); if (q && !es) ask(q); };
  let ph = 0;
  const phTimer = setInterval(() => { if (document.activeElement !== els.q && !els.q.value && !docScope) els.q.placeholder = PLACEHOLDERS[ph = (ph + 1) % PLACEHOLDERS.length]; }, 4000);

  // ---------- follow-ups / one document ----------
  function renderCtx() {
    const parts = [];
    if (docScope) parts.push(`About “${docScope.title}”`);
    if (followCtx) parts.push(`Your next question follows on from “${followCtx.q}”`);
    els.ctx.hidden = !parts.length; els.ctxText.textContent = parts.join(" · ");
  }
  function newTopic() { followCtx = null; docScope = null; renderCtx(); }
  els.ctxClear.onclick = () => { newTopic(); els.q.focus(); };

  // ---------- rendering ----------
  function renderAnswer(text, sources) {
    let h = esc(text.trim()).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    const pill = (n) => sources && sources[n - 1] ? `<a class="cite" data-n="${n}" title="${esc(sources[n - 1].title)}">${n}</a>` : "";
    h = h.replace(/\[(?:(?:source|doc(?:ument)?)\s*)?(\d+(?:\s*(?:,|and)\s*\d+)*)\]/gi, (_, nums) => nums.split(/\s*(?:,|and)\s*/).map((n) => pill(+n)).join(""));
    return "<p>" + h.replace(/\n{2,}/g, "</p><p>").replace(/\n/g, "<br>") + "</p>";
  }
  function highlight(text, terms) {
    let h = esc(text);
    const t = (terms || []).filter((x) => x && x.length > 1).map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    return t.length ? h.replace(new RegExp("\\b(" + t.join("|") + ")[a-z0-9]*", "gi"), "<mark>$&</mark>") : h;
  }
  function renderSources(list, terms) {
    els.sourcesWrap.hidden = !list.length;
    els.sources.innerHTML = list.map((s) => {
      const meta = [s.correspondent, fmtDate(s.created), s.type, s.category].filter(Boolean).join(" · ");
      return `<a class="src" id="src-${s.n}" href="${esc(s.url)}"><div class="thumb"><span class="n">${s.n}</span><img loading="lazy" alt="" src="${esc(s.thumb)}" onerror="this.remove()"></div>
        <div class="b"><div class="t">${esc(s.title)}</div><div class="faint small">${esc(meta)}</div><div class="s">${highlight(s.snippet, terms)}</div>
        <div><button type="button" class="btn ghost sm" data-askdoc="${s.id}" data-title="${esc(s.title)}">Ask about this</button></div></div></a>`;
    }).join("");
  }
  els.sources.addEventListener("click", (e) => {
    const b = e.target.closest("[data-askdoc]"); if (!b) return;
    e.preventDefault();
    docScope = { id: +b.dataset.askdoc, title: b.dataset.title }; followCtx = null; renderCtx();
    els.q.value = ""; els.q.placeholder = "Ask about this document…"; autosize();
    window.scrollTo({ top: 0, behavior: "smooth" }); els.q.focus({ preventScroll: true });
  });
  view.addEventListener("click", (e) => {
    const c = e.target.closest(".cite"); if (!c) return;
    e.preventDefault();
    const t = document.getElementById("src-" + c.dataset.n); if (!t) return;
    t.scrollIntoView({ behavior: "smooth", block: "center" });
    t.classList.add("flash"); setTimeout(() => t.classList.remove("flash"), 1200);
  });
  const renderFacts = (facts) => { els.facts.innerHTML = (facts || []).map((f) => `<span class="fact"><span class="k">${({ amount: "Amount", date: "Date", number: "Number" })[f.kind] || ""}</span>${esc(f.value)}</span>`).join(""); };
  function renderBreakdown(t) {
    if (!t) { els.breakdown.innerHTML = ""; return; }
    const rows = t.items.map((i) => `<div class="r"><a class="cite" data-n="${i.n}">${i.n}</a><span class="v">${esc(i.vendor)}</span><span class="faint small">${esc(fmtDate(i.date))}</span><span>${money(i.amount)}</span></div>`);
    if (t.items.length > 1) rows.push(`<div class="r sum"><span class="v">Total</span><span>${money(t.total)}</span></div>`);
    for (const x of t.excluded || []) rows.push(`<div class="faint small">Not counted: <a href="${esc(x.url)}">${esc(x.title)}</a> ${money(x.amount)}, ${esc(x.reason)}</div>`);
    els.breakdown.innerHTML = rows.join("");
  }
  const setMode = (cls, text) => { els.mode.className = "chip " + (cls || ""); els.mode.textContent = text || ""; els.mode.hidden = !text; };

  // ---------- history (this browser) ----------
  const loadHist = () => { try { return JSON.parse(localStorage.getItem(HKEY)) || []; } catch { return []; } };
  const saveHist = (h) => { try { localStorage.setItem(HKEY, JSON.stringify(h.slice(0, 25))); } catch {} };
  function remember(entry) { saveHist([entry, ...loadHist().filter((x) => x.q.toLowerCase() !== entry.q.toLowerCase())]); renderRecent(); }
  function renderRecent() {
    const h = loadHist();
    els.recentWrap.hidden = !h.length;
    els.recent.innerHTML = h.slice(0, 6).map((x, i) => `<button type="button" class="item" data-i="${i}" style="border:0;background:none;text-align:left;width:100%;cursor:pointer">
      <span class="main"><div class="t">${esc(x.q)}</div><div class="sub">${ago(x.ts)}${x.a ? " · " + esc(x.a.replace(/\[\d+\]/g, "").slice(0, 80)) : ""}</div></span></button>`).join("");
  }
  els.recent.onclick = (e) => { const b = e.target.closest("[data-i]"); if (b) { const x = loadHist()[+b.dataset.i]; if (x) showSaved(x); } };
  els.clearBtn.onclick = () => { saveHist([]); renderRecent(); };

  // ---------- feedback ----------
  const newId = () => (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));
  function sendFeedback(vote, comment = "") {
    if (!current) return;
    current.fid = current.fid || newId(); current.vote = vote;
    api("/api/feedback", { method: "POST", quiet: true, body: { id: current.fid, vote, comment, q: current.q, context_q: current.context_q || "",
      doc: current.doc || null, mode: current.mode, answer: current.a, missing: current.missing || [],
      sources: (current.sources || []).map(({ id, title }) => ({ id, title })) } }).catch(() => {});
    showVote();
  }
  function showVote() {
    els.upBtn.hidden = els.downBtn.hidden = !current;
    els.upBtn.classList.toggle("on", current?.vote === "up"); els.downBtn.classList.toggle("on", current?.vote === "down");
    els.upBtn.style.color = current?.vote === "up" ? "var(--good)" : ""; els.downBtn.style.color = current?.vote === "down" ? "var(--warn)" : "";
  }
  els.upBtn.onclick = () => { sendFeedback("up"); els.fbBox.hidden = true; };
  els.downBtn.onclick = () => { sendFeedback("down"); els.fbBox.hidden = false; els.fbText.value = ""; els.fbText.focus(); };
  els.fbBox.onsubmit = (e) => { e.preventDefault(); sendFeedback("down", els.fbText.value.trim()); els.fbBox.hidden = true; els.note.hidden = false; els.note.textContent = "Thanks, that's saved for review."; };
  els.copyBtn.onclick = async () => {
    if (!current) return;
    const txt = current.a + "\n\n" + current.sources.map((s) => `[${s.n}] ${s.title} — ${location.origin}${s.url}`).join("\n");
    try { await navigator.clipboard.writeText(txt); els.copyBtn.style.color = "var(--good)"; setTimeout(() => (els.copyBtn.style.color = ""), 1200); } catch {}
  };
  els.aiBtn.onclick = () => { if (current) ask(current.q, true); };

  // ---------- asking ----------
  function stop() { if (es) { es.close(); es = null; } if (tick) { clearInterval(tick); tick = null; } els.go.classList.remove("busy"); autosize(); }
  function resetView(q) {
    els.home.hidden = true; els.greet.hidden = true; els.result.hidden = false;
    els.answer.className = "text"; els.answer.innerHTML = ""; els.facts.innerHTML = ""; els.breakdown.innerHTML = "";
    els.note.hidden = true; els.meta.textContent = ""; els.sources.innerHTML = ""; els.sourcesWrap.hidden = true; els.allLink.hidden = true;
    els.copyBtn.hidden = els.aiBtn.hidden = els.upBtn.hidden = els.downBtn.hidden = true; els.fbBox.hidden = true;
    setMode("", ""); els.q.value = q; autosize();
  }
  function showSaved(x) {
    stop(); newTopic(); resetView(x.q); current = x;
    els.answer.innerHTML = renderAnswer(x.a, x.sources);
    if (!["llm", "facts", "total"].includes(x.mode)) els.answer.classList.add("quick");
    renderFacts(x.facts); renderBreakdown(x.total); renderSources(x.sources, x.terms);
    setMode("", "Saved answer · " + ago(x.ts));
    els.copyBtn.hidden = false; showVote();
  }
  function ask(q, full = false) {
    stop(); picker.close(); resetView(q); els.q.blur();
    const t0 = performance.now();
    tick = setInterval(() => { els.timer.textContent = ((performance.now() - t0) / 1000).toFixed(0) + "s"; }, 250);
    els.go.classList.add("busy"); setMode("", "Searching…");
    els.answer.innerHTML = '<span class="caret"></span>';
    history.replaceState(null, "", "/?q=" + encodeURIComponent(q));
    let buf = "", r = null, done = false;
    const params = new URLSearchParams({ q });
    if (full) params.set("full", "1");
    const sentCtx = followCtx && followCtx.q !== q ? followCtx : null;
    if (sentCtx) { params.set("prev_q", sentCtx.q); params.set("prev_a", (sentCtx.a || "").slice(0, 600)); }
    if (docScope) params.set("doc", String(docScope.id));
    es = new EventSource("/api/ask/stream?" + params);
    autosize();
    const finish = (mode, d = {}) => {
      done = true;
      const secs = ((performance.now() - t0) / 1000).toFixed(1);
      stop();
      const sources = r ? r.sources : [];
      let a = (d.answer || buf).trim();
      if (mode === "llm" && a) {
        els.answer.innerHTML = renderAnswer(a, sources);
        if (d.unverified && d.unverified.length) {
          setMode("warn", "Check the source");
          els.note.hidden = false;
          els.note.textContent = "I couldn't find " + d.unverified.join(", ") + " in the documents; the AI may have misread it. Double-check the source.";
        } else { setMode("good", "AI · " + secs + "s"); renderFacts([]); }
      } else if ((mode === "facts" || mode === "total") && r) {
        a = r.extractive.text; els.answer.className = "text"; els.answer.innerHTML = renderAnswer(a, sources); renderFacts([]);
        if (mode === "total") { renderBreakdown(r.total); setMode("good", "Added up · " + secs + "s"); }
        else { setMode("good", "Instant · from saved facts"); els.aiBtn.hidden = false; }
      } else {
        a = r ? r.extractive.text : "Something went wrong.";
        els.answer.className = "text quick"; els.answer.innerHTML = renderAnswer(a, sources);
        if (mode === "list") { els.answer.className = "text"; setMode("good", `Found ${sources.length}`); }
        else if (mode === "none") { setMode("", sources.length ? "No exact match" : "No matches"); if (r && r.refused && sources.length) els.aiBtn.hidden = false; }
        else setMode("", d.ai === "off" ? "Best passages · AI is off" : "Best passages");
      }
      els.timer.textContent = secs + "s";
      if (r && sources.length) { els.meta.textContent = `Searched ${plural(r.library, "document")}`; els.copyBtn.hidden = false; }
      current = { q, a, mode, ts: Date.now(), unverified: d.unverified || [], facts: r && els.facts.innerHTML ? r.extractive.facts : [], terms: r ? r.highlight : [],
        total: mode === "total" && r ? r.total : null, context_q: r ? r.context_q : q, doc: r ? r.scoped : null, missing: r ? r.missing : [],
        sources: sources.map(({ n, id, title, created, type, correspondent, category, snippet, url, thumb }) => ({ n, id, title, created, type, correspondent, category, snippet, url, thumb })) };
      if (r) remember(current);
      showVote();
      if (r && mode !== "none") { followCtx = { q: r.context_q || q, a }; renderCtx(); }
    };
    es.addEventListener("sources", (e) => {
      r = JSON.parse(e.data);
      setMode("", r.sources.length ? `Reading ${Math.min(r.sources.length, 3)} of ${r.library}` : "");
      renderSources(r.sources, r.highlight);
      // the Library's word search, for when the answer's sources aren't everything you're after
      if (r.sources.length && !docScope) { els.allLink.href = "/library?q=" + encodeURIComponent(q); els.allLink.hidden = false; }
      if (r.sources.length) { els.answer.className = "text quick"; els.answer.innerHTML = renderAnswer(r.extractive.text, r.sources) + ' <span class="caret"></span>'; renderFacts(r.extractive.facts); }
    });
    es.addEventListener("stage", (e) => { setMode("", JSON.parse(e.data).busy ? "Waiting for the AI…" : "Writing…"); });
    es.addEventListener("token", (e) => {
      if (!buf) { els.answer.className = "text"; setMode("", "Writing…"); }
      buf += JSON.parse(e.data).t;
      els.answer.innerHTML = renderAnswer(buf, r ? r.sources : []).replace(/<\/p>$/, ' <span class="caret"></span></p>');
    });
    es.addEventListener("llm_error", (e) => { buf = ""; els.note.hidden = false; els.note.textContent = JSON.parse(e.data).message; });
    es.addEventListener("fatal", (e) => { done = true; stop(); els.answer.className = "text quick"; els.answer.textContent = JSON.parse(e.data).message; setMode("warn", "Error"); });
    es.addEventListener("done", (e) => { const d = JSON.parse(e.data); finish(d.mode, d); });
    es.onerror = () => {
      if (done) return;
      if (r) finish(buf ? "llm" : "extractive");
      else { stop(); els.answer.className = "text quick"; els.answer.textContent = "Couldn't reach PaperJay. Check your connection and try again."; setMode("warn", "Connection lost"); }
    };
  }

  // ---------- start page ----------
  async function loadHome() {
    const [up, sum, recent, tidy] = await Promise.all([
      api("/api/upcoming", { quiet: true }).catch(() => ({ items: [] })),
      api("/api/summary", { quiet: true }).catch(() => null),
      api("/api/documents?sort=added_desc&limit=6", { quiet: true }).catch(() => ({ results: [] })),
      api("/api/health/filing", { quiet: true }).catch(() => null),
    ]);
    els.upWrap.hidden = !up.items.length;
    els.upcoming.innerHTML = up.items.slice(0, 6).map((i) => `<a class="item ${i.days_left <= 3 ? "soon" : ""}" href="${esc(i.url)}">
      <span class="days">${i.days_left === 0 ? "Today" : `${i.days_left}<small>day${i.days_left === 1 ? "" : "s"}</small>`}</span>
      <span class="main"><div class="t">${esc(i.vendor)} ${i.label === "due" ? "due" : "expires"}</div><div class="sub">${esc(fmtDate(i.date))} · ${esc(i.title)}</div></span>
      ${i.amount ? `<span class="num">${money(i.amount)}</span>` : ""}</a>`).join("");
    const attn = [];
    if (sum && sum.needs_review) attn.push(`<a class="item" href="/review"><span class="main"><div class="t">${plural(sum.needs_review, "document")} to check</div><div class="sub">The AI filed them but wasn't sure about something</div></span><span class="chip warn">Review</span></a>`);
    if (sum && sum.failed) attn.push(`<a class="item" href="/review"><span class="main"><div class="t">${plural(sum.failed, "document")} couldn't be read</div><div class="sub">See why, then fix or try again</div></span><span class="chip bad">Failed</span></a>`);
    if (tidy && tidy.documents) {  // Tidy up (spec E): gaps in the filing, failures counted above
      const c = tidy.counts, gaps = [["no_company", "no company"], ["no_date", "no date"], ["no_type", "no type"],
        ["copies", "possible copies"], ["confirm_import", "imported filing to confirm"]].filter(([k]) => c[k]).map(([k, w]) => `${c[k]} ${w}`);
      const n = tidy.documents - (c.failed || 0);
      if (n > 0) attn.push(`<a class="item" href="/review?tab=tidy"><span class="main"><div class="t">${plural(n, "document")} to tidy up</div><div class="sub">${esc(gaps.slice(0, 3).join(" · ") || "Filing that needs a second look")}</div></span><span class="chip accent">Tidy up</span></a>`);
    }
    if (sum && sum.processing) attn.push(`<a class="item" href="/upload"><span class="main"><div class="t">${plural(sum.processing, "document")} being read</div><div class="sub">OCR is running</div></span><span class="chip accent">Reading…</span></a>`);
    els.attnWrap.hidden = !attn.length; els.attn.innerHTML = attn.join("");
    els.newWrap.hidden = !recent.results.length;
    els.newDocs.innerHTML = recent.results.map((d) => docCard(d)).join("");
  }
  renderRecent(); loadHome(); refreshCounts(); autosize();
  const docParam = query.get("doc");
  if (docParam) { const d = await api(`/api/documents/${docParam}`, { quiet: true }).catch(() => null); if (d) { docScope = { id: d.id, title: d.title }; renderCtx(); els.q.placeholder = "Ask about this document…"; els.q.focus(); } }
  const initial = query.get("q");
  if (initial) ask(initial); else if (!docParam && matchMedia("(hover: hover)").matches) els.q.focus();
  return () => { stop(); clearInterval(phTimer); picker.detach(); document.body.classList.remove("on-home"); };
}
