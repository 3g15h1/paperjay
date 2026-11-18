// Browse: the filing cabinet (spec C.1) — drill Category → Company → Year, or start from
// Type, Tag, Topic or Year → Month.
import { $, $$, api, qs, esc, plural, docCard, libSwitch } from "./core.js";
import { loadAz } from "./az.js";

export const nav = "library";   // Browse lives under the Library tab
const TABS = [
  { by: "category", label: "Cabinet" },
  { by: "type", label: "Type" },
  { by: "tag", label: "Tag" },
  { by: "topic", label: "Topic" },
  { by: "year", label: "Year" },
  { by: "az", label: "A–Z" },
];
const PARAM = { category: "category", correspondent: "correspondent_id", year: "year",
                type: "document_type_id", tag: "tag_id", topic: "topic", month: "month" };
const DIM_LABEL = { category: "Category", correspondent: "Company", year: "Year", type: "Type",
                    tag: "Tag", topic: "Topic", month: "Month" };
const PAGE = 50;

const pretty = (dim, name) => dim === "category" && name !== "Unfiled"
  ? name.charAt(0).toUpperCase() + name.slice(1) : name;

export async function show(view, { query }) {
  let by = query.get("by") || (location.pathname === "/index" ? "az" : "category");
  if (!TABS.some((t) => t.by === by)) by = "category";
  const sel = {};   // query-param name -> chosen value ("" key "__unfiled__" allowed)
  for (const p of Object.values(PARAM)) if (query.get(p)) sel[p] = query.get(p);
  let docs = [], total = 0;

  view.innerHTML = `<div class="page">
    <div class="head"><h1>Library</h1><span class="faint num" id="count"></span></div>
    ${libSwitch("browse")}
    <div class="seg browse-tabs" id="tabs">${TABS.map((t) =>
      `<button data-by="${t.by}" class="${t.by === by ? "on" : ""}">${t.label}</button>`).join("")}</div>
    <div class="crumbs" id="crumbs"></div>
    <div id="body"></div>
    <div style="text-align:center;margin-top:16px"><button class="btn" id="more" hidden>Show more</button></div>
  </div>`;
  const els = { count: $("#count", view), crumbs: $("#crumbs", view), body: $("#body", view), more: $("#more", view) };

  function syncUrl() {
    history.replaceState(null, "", "/browse" + qs({ by: by === "category" ? "" : by, ...sel }));
  }

  async function load(append = false) {
    if (by === "az") {   // the A–Z index (C.2) keeps its own URL state (/index?kind=…)
      els.count.textContent = "";
      els.more.hidden = true;
      await loadAz(els.body, els.crumbs);
      return;
    }
    syncUrl();
    const data = await api("/api/browse" + qs({ by, ...sel, limit: PAGE, offset: append ? docs.length : 0 }));
    total = data.total;
    els.count.textContent = plural(total, "document");
    // breadcrumb: one crumb per chosen level, taken from the path the server walked
    const chosen = [];
    for (const dim of data.path) {
      const p = PARAM[dim];
      if (sel[p] === undefined) break;
      chosen.push({ dim, param: p, value: sel[p] });
    }
    els.crumbs.innerHTML = chosen.length ? `<button class="crumb" data-up="-1">All</button>` +
      chosen.map((c, i) => `<span class="crumb-sep">›</span><button class="crumb" data-up="${i}">${esc(pretty(c.dim, labelFor(c)))}</button>`).join("") +
      (data.level !== "documents" ? `<span class="crumb-sep">›</span><span class="crumb here">${esc(DIM_LABEL[data.level])}</span>` : "")
      : "";
    function labelFor(c) {
      if (c.value === "__unfiled__") return "Unfiled";
      if (c.dim === "month") return ["", "January", "February", "March", "April", "May", "June", "July",
        "August", "September", "October", "November", "December"][+c.value] || c.value;
      return c.value;
    }
    els.crumbs.querySelectorAll("[data-up]").forEach((b) => b.onclick = () => {
      const up = +b.dataset.up;
      chosen.slice(up + 1).forEach((c) => delete sel[c.param]);
      load();
    });
    if (data.level === "documents") {
      docs = append ? docs.concat(data.documents) : data.documents;
      els.body.innerHTML = docs.length
        ? `<div class="docs">${docs.map((d) => docCard(d)).join("")}</div>`
        : `<div class="card empty">No documents here.</div>`;
      els.more.hidden = docs.length >= total;
    } else {
      docs = [];
      els.more.hidden = true;
      els.body.innerHTML = `<div class="card list">${data.items.map((i) =>
        `<button class="item browse-item" data-key="${esc(i.key)}"><span class="main"><span class="t">${esc(pretty(data.level, i.name))}</span></span><span class="faint num">${i.count}</span></button>`).join("") ||
        `<div class="empty">Nothing filed yet.</div>`}</div>`;
      els.body.querySelectorAll("[data-key]").forEach((b) => b.onclick = () => {
        sel[PARAM[data.level]] = b.dataset.key;
        load();
      });
    }
  }

  $("#tabs", view).addEventListener("click", (e) => {
    const b = e.target.closest("[data-by]");
    if (!b || b.dataset.by === by) return;
    by = b.dataset.by;
    for (const k of Object.keys(sel)) delete sel[k];
    docs = [];
    $$("#tabs [data-by]", view).forEach((x) => x.classList.toggle("on", x.dataset.by === by));
    load();
  });
  els.more.onclick = () => load(true);

  load();
}
