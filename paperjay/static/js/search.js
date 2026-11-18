// Header search (spec C.3): type two letters and the library's vocabulary answers —
// companies, people, topics, types, tags, numbers, paper numbers, years and matching
// documents — from /api/suggest. Choosing one opens the library already filtered (or
// the document itself); Enter runs a plain word search; a question-shaped input
// offers "Ask PaperJay" first. ↑/↓ move, Enter picks, Esc closes. "/" focuses it.
import { $, el, esc, api } from "./core.js";

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

function attach(input, box) {
  let seq = 0, timer, rows = [], active = -1;
  function close() { box.hidden = true; box.innerHTML = ""; rows = []; active = -1; }
  function mark() { rows.forEach((r, i) => r.classList.toggle("on", i === active)); }
  async function update() {
    const q = input.value.trim();
    const mine = ++seq;
    if (q.length < 2) { close(); return; }
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
    if (QUESTION.test(q) || q.endsWith("?")) addRow(`Ask PaperJay: “${q}”`, "/?q=" + encodeURIComponent(q), null);
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
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (active >= 0 && rows[active]) { rows[active].click(); return; }
      close();
      location.href = "/library?q=" + encodeURIComponent(input.value.trim());
    } else if (e.key === "Escape") close();
  });
  document.addEventListener("click", (e) => { if (!box.contains(e.target) && e.target !== input) close(); });
  return { open: () => { box.hidden = false; }, focus: () => input.focus() };
}

export function initSearch() {
  const desktop = attach($("#search-input"), $("#search-results"));
  const overlay = $("#search-overlay");
  const mobile = attach($("#m-search-input"), $("#m-search-results"));
  $("#search-fab").onclick = () => { overlay.hidden = false; mobile.focus(); };
  overlay.addEventListener("click", (e) => { if (e.target === overlay) overlay.hidden = true; });
  document.addEventListener("keydown", (e) => {
    if (e.key === "/" && !/input|textarea|select/i.test(document.activeElement.tagName)) {
      e.preventDefault();
      if (innerWidth <= 820) $("#search-fab").click(); else desktop.focus();
    }
    if (e.key === "Escape") overlay.hidden = true;
  });
}
