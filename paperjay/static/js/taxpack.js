// Tax pack: pick a tax year and what belongs in it, check the list, download one zip for the accountant.
import { $$, api, qs, esc, icon, fmtDate, money, plural, labels, toast, me } from "./core.js";

export const nav = "settings";

const STARTS = { "01-01": "January 1", "04-06": "April 6 (UK)", "07-01": "July 1 (Australia)", "04-01": "April 1",
  "10-01": "October 1" };
const title = (s) => (s || "other").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const yearLabel = (y, start) => start === "01-01" ? String(y) : `${y}–${String(y + 1).slice(2)}`;

export async function show(view, { query }) {
  const saved = JSON.parse(localStorage.getItem("pj-taxpack") || "null");
  let data = null, pick = null, year = query.get("year") ? +query.get("year") : undefined;
  const tags = await labels("tag");
  view.innerHTML = `<div class="page narrow">
    <div class="head mobile-only" style="margin-bottom:6px"><a class="btn ghost sm" href="/settings">← Settings</a></div>
    <div class="head"><h1>Tax pack</h1></div>
    <p class="muted small" style="margin-top:-8px">Everything for one tax year in one zip: the documents in a folder
      per category, and a spreadsheet with each one's date, company and amount. Give it to your accountant or
      use it to fill in your return.</p>
    <div class="card pad" style="display:grid;gap:12px">
      <label class="field"><span class="lab">Tax year</span><select class="input" id="year"></select></label>
      <div class="help" id="startLine"></div>
    </div>
    <div class="section"><div class="h"><b>What to put in</b><span class="aside" id="pickCount"></span></div>
      <div class="card pad" id="picks"></div></div>
    <div class="section"><div class="h"><b>Totals</b></div><div class="card pad"><div class="breakdown" id="sums" style="margin:0"></div>
      <p class="help" style="margin-top:10px">Only payments are added up. Copies, price lists, pay and tax forms are listed but not added.
      Check each amount against its document.</p></div></div>
    <div class="section"><div class="h"><b>Documents</b></div><div class="card list" id="list"></div></div>
    <div style="display:flex;gap:10px;margin:18px 0 30px;flex-wrap:wrap">
      <a class="btn primary" id="dl" download>${icon("download")} Download the tax pack</a>
    </div></div>`;
  const els = Object.fromEntries($$("[id]", view).map((e) => [e.id, e]));

  async function load() {
    data = await api("/api/taxpack" + qs({ year }));
    year = data.year;
    history.replaceState(null, "", "/taxpack?year=" + year);
    els.year.innerHTML = data.years.map((y) => `<option value="${y}">${esc(yearLabel(y, data.tax_year_start))}</option>`).join("");
    els.year.value = year;
    const startName = STARTS[data.tax_year_start] || data.tax_year_start;
    els.startLine.innerHTML = `${esc(data.label)} runs from ${esc(fmtDate(data.from))} to ${esc(fmtDate(data.to))}. ` +
      (me?.role === "admin"
        ? `Your tax year starts on <select class="input" id="start" style="height:28px;width:auto;display:inline-block">
            ${Object.entries({ ...STARTS, [data.tax_year_start]: startName }).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("")}</select>.`
        : `Your tax year starts on ${esc(startName)}.`);
    const start = els.startLine.querySelector("#start");
    if (start) {
      start.value = data.tax_year_start;
      start.onchange = async () => { await api("/api/settings", { method: "PUT", body: { tax_year_start: start.value } }); toast("Saved"); year = undefined; load(); };
    }
    pick = pick || {
      categories: new Set(saved?.categories || data.defaults.categories),
      topics: new Set(saved?.topics || data.defaults.topics),
      tags: new Set((saved?.tags || []).map(Number)),
    };
    draw();
  }

  function counts(key) {
    const n = new Map();
    for (const d of data.documents) for (const v of key === "categories" ? [d.category] : d[key]) n.set(v, (n.get(v) || 0) + 1);
    return [...n.entries()].sort((a, b) => b[1] - a[1]);
  }
  function chips(key, name) {
    const list = counts(key);
    if (!list.length) return "";
    return `<div class="field"><span class="lab">${name}</span><div class="chips" style="margin-top:6px">${list.map(([v, n]) => {
      const label = key === "tags" ? (tags.find((t) => t.id === v)?.name || "?") : title(v);
      return `<button type="button" class="chip ${pick[key].has(v) ? "accent" : ""}" data-k="${key}" data-v="${esc(v)}">${esc(label)} · ${n}</button>`;
    }).join("")}</div></div>`;
  }
  // the same as the server's taxpack.choose: a document in "other" is grouped under its chosen topic
  const chosen = () => data.documents.flatMap((d) => {
    const mine = [...pick.topics].filter((t) => d.topics.includes(t));
    if (!(pick.categories.has(d.category) || mine.length || d.tags.some((t) => pick.tags.has(t)))) return [];
    return [{ ...d, group: d.category !== "other" || !mine.length ? d.category : mine[0] }];
  });

  function draw() {
    els.picks.innerHTML = data.documents.length
      ? chips("categories", "Categories") + chips("topics", "Topics") + chips("tags", "Tags")
      : `<div class="empty" style="padding:8px">No documents are dated in ${esc(data.label)}.</div>`;
    els.picks.style.display = "grid"; els.picks.style.gap = "14px";
    const docs = chosen();
    els.pickCount.textContent = `${plural(docs.length, "document")} of ${data.documents.length}`;
    const sums = new Map();
    for (const d of docs) {
      const s = sums.get(d.group) || { n: 0, total: 0, counted: 0 };
      s.n += 1; if (d.counted) { s.total += d.amount; s.counted += 1; }
      sums.set(d.group, s);
    }
    const all = [...sums.values()].reduce((a, s) => a + s.total, 0);
    els.sums.innerHTML = [...sums.entries()].sort().map(([c, s]) =>
      `<div class="r"><span class="v">${esc(title(c))} <span class="faint">· ${plural(s.n, "document")}</span></span><span class="num">${s.counted ? money(s.total) : `<span class="faint" title="Nothing here is a payment">—</span>`}</span></div>`).join("") +
      (sums.size ? `<div class="r sum"><span class="v">Payments altogether</span><span class="num">${money(all)}</span></div>` : `<div class="faint">Nothing chosen yet.</div>`);
    els.list.innerHTML = docs.map((d) => `<div class="item"><a class="main" href="/documents/${d.id}">
        <div class="t">${esc(d.title)}</div>
        <div class="sub">${esc([fmtDate(d.date), d.company, title(d.group), d.paper_no ? "#" + d.paper_no : "", d.note].filter(Boolean).join(" · "))}</div></a>
        ${d.amount != null ? `<span class="num ${d.counted ? "" : "faint"}">${money(d.amount)}</span>` : ""}</div>`).join("")
      || `<div class="empty">Choose a category, topic or tag above.</div>`;
    const params = { year, categories: [...pick.categories], topics: [...pick.topics], tags: [...pick.tags] };
    els.dl.href = "/api/taxpack.zip" + qs(params);
    els.dl.classList.toggle("disabled", !docs.length);
    els.dl.toggleAttribute("aria-disabled", !docs.length);
    localStorage.setItem("pj-taxpack", JSON.stringify({ categories: params.categories, topics: params.topics, tags: params.tags }));
  }

  els.picks.onclick = (e) => {
    const b = e.target.closest("[data-k]"); if (!b) return;
    const set = pick[b.dataset.k], v = b.dataset.k === "tags" ? +b.dataset.v : b.dataset.v;
    set.has(v) ? set.delete(v) : set.add(v);
    draw();
  };
  els.dl.onclick = (e) => { if (els.dl.classList.contains("disabled")) { e.preventDefault(); toast("Choose at least one document first"); } else toast("Making the tax pack…"); };
  els.year.onchange = () => { year = +els.year.value; load(); };
  await load();
}
