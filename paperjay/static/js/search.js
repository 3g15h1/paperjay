// Search (spec C.3): type two letters and the library's vocabulary answers —
// companies, people, topics, types, tags, numbers, paper numbers, years and matching
// documents — from /api/suggest. Choosing one opens the library already filtered (or
// the document itself). The same picker sits under the Ask box (home.js), where Enter
// asks the question; in the desktop sidebar Enter runs a plain word search and a
// question-shaped input offers "Ask PaperJay" first. ↑/↓ move, Enter picks, Esc closes.
// "/" focuses the box (on a phone it opens Ask, the one search box there).
import { $, el, esc, api, go } from "./core.js";

const KIND_LABEL = { company: "Companies", person: "People", topic: "Topics", type: "Types",
                     tag: "Tags", number: "Numbers", paper: "Paper numbers", year: "Years",
                     document: "Documents" };
const QUESTION = /^(who|what|when|where|why|which|how|can|could|does|do|did|is|are|was|were)\b/i;

function target(item, kind) {
  if (kind === "company") return "/library?correspondent_id=" + item.id;
  if (kind === "type") return "/library?document_type_id=" + item.id;
  if (kind === "tag") return "/library?tags=" + item.id;
  if (kind === "topic") return "/library?topic=" + encodeURIComponent(item.id);
  if (kind === "paper") return "/library?paper_no=" + item.id;
  if (kind === "year") return `/library?date_from=${item.id}-01-01&date_to=${item.id}-12-31`;
  if (kind === "document") return "/documents/" + item.id;
  return "/library?term=" + encodeURIComponent(kind + ":" + item.id);  // number / person
}

// opts.onEnter: called for Enter when no suggestion is picked (default: word search in the
// library). opts.enabled: return false to show nothing (e.g. while asking about one document).
// With onEnter set, the box is quiet when nothing matches: Enter does the work there.
export function attach(input, box, opts = {}) {
  let seq = 0, timer, rows = [], active = -1;
  const inAsk = !!opts.onEnter;
  function close() { box.hidden = true; box.innerHTML = ""; rows = []; active = -1; }
  function mark() { rows.forEach((r, i) => r.classList.toggle("on", i === active)); }
  async function update() {
    const q = input.value.trim();
    const mine = ++seq;
    if (q.length < 2 || (opts.enabled && !opts.enabled())) { close(); return; }
    const data = await api("/api/suggest?q=" + encodeURIComponent(q)).catch(() => null);
    if (!data || mine !== seq) return;
    box.innerHTML = "";
    rows = []; active = -1;
    const addRow = (text, href, count) => {
      const b = el(`<button type="button" class="sg-item"><span class="grow"></span>${count != null ? `<span class="faint num">${count}</span>` : ""}</button>`);
      b.querySelector(".grow").textContent = text;
      b.onclick = () => { close(); location.href = href; };
      rows.push(b);
      box.append(b);
    };
    if (inAsk && !data.groups.length) { close(); return; }
    if (inAsk) box.append(el(`<div class="sg-none">Pick one to open it, or press Enter to ask.</div>`));
    if (!inAsk && (QUESTION.test(q) || q.endsWith("?"))) addRow(`Ask PaperJay: “${q}”`, "/?q=" + encodeURIComponent(q), null);
    for (const g of data.groups) {
      box.append(el(`<div class="sg-kind">${KIND_LABEL[g.kind] || g.kind}</div>`));
      for (const item of g.items) addRow(item.name, target(item, g.kind), item.count);
    }
    if (!data.groups.length && !rows.length) {
      box.append(el(`<div class="sg-none">Nothing in the library matches — press Enter to search the words themselves.</div>`));
    }
    box.hidden = false;
  }
  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(update, 150); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!rows.length) return;
      e.preventDefault();
      active = (active + (e.key === "ArrowDown" ? 1 : -1) + rows.length) % rows.length;
      mark();
    } else if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      if (active >= 0 && rows[active]) { rows[active].click(); return; }
      clearTimeout(timer); seq++; close();
      if (opts.onEnter) opts.onEnter();
      else location.href = "/library?q=" + encodeURIComponent(input.value.trim());
    } else if (e.key === "Escape") close();
  });
  const outside = (e) => { if (!box.contains(e.target) && e.target !== input) close(); };
  document.addEventListener("click", outside);
  return { close: () => { clearTimeout(timer); seq++; close(); }, focus: () => input.focus(),
           detach: () => document.removeEventListener("click", outside) };
}

export function initSearch() {
  const desktop = attach($("#search-input"), $("#search-results"));
  document.addEventListener("keydown", (e) => {
    if (e.key !== "/" || /input|textarea|select/i.test(document.activeElement.tagName)) return;
    e.preventDefault();
    const ask = document.querySelector("[data-searchbox]");
    if (ask) ask.focus();
    else if (innerWidth <= 820) go("/");
    else desktop.focus();
  });
}
