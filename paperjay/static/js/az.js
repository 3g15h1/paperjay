// The A–Z index (spec C.2): the book's back pages inside Browse. Four sections —
// Companies, Numbers, People, Topics — each alphabetical, with a letter bar and a
// filter box. Numbers show only their last 4; Reveal asks the server for the full
// value of one entry, and the server audit-logs it. Typing part of a number finds
// it: the match runs on the server, the list stays masked.
import { $, el, esc, api, toast } from "./core.js";

const SECTIONS = [["company", "Companies"], ["number", "Numbers"], ["person", "People"], ["topic", "Topics"]];
const state = { kind: "company", letter: null, q: "" };

function cap(s) { return s ? s[0].toUpperCase() + s.slice(1) : s; }

export async function loadAz(list, crumbs) {
  crumbs.innerHTML = "";
  list.innerHTML = `
    <div class="seg browse-tabs" role="tablist"></div>
    <div class="letters"></div>
    <input class="input" id="az-q" type="search" placeholder="Filter this section — a name, or part of a number…" autocomplete="off">
    <div class="card list" id="az-list" style="margin-top:12px"></div>`;
  const seg = list.querySelector(".seg");
  for (const [kind, label] of SECTIONS) {
    const b = el(`<button type="button">${label}</button>`);
    if (kind === state.kind) b.classList.add("on");
    b.onclick = () => { state.kind = kind; state.letter = null; loadAz(list, crumbs); };
    seg.append(b);
  }
  const letters = list.querySelector(".letters");
  for (const ch of "ABCDEFGHIJKLMNOPQRSTUVWXYZ#") {
    const b = el(`<button type="button">${ch}</button>`);
    if (state.letter === ch) b.classList.add("on");
    b.onclick = () => { state.letter = state.letter === ch ? null : ch; loadAz(list, crumbs); };
    letters.append(b);
  }
  const q = list.querySelector("#az-q");
  q.value = state.q;
  let t;
  q.oninput = () => { clearTimeout(t); t = setTimeout(() => { state.q = q.value.trim(); refresh(); }, 250); };

  const out = list.querySelector("#az-list");
  async function refresh(revealKey = null) {
    const params = new URLSearchParams({ kind: state.kind });
    if (state.letter) params.set("letter", state.letter);
    if (state.q) params.set("q", state.q);
    if (revealKey) params.set("reveal", revealKey);
    history.replaceState(null, "", "/index?" + params.toString());
    try {
      const data = await api("/api/index?" + params.toString());
      render(data.entries);
    } catch (e) { toast(e.message); }
  }
  function render(entries) {
    out.innerHTML = "";
    if (!entries.length) { out.append(el(`<div class="item"><span class="t1 muted">Nothing filed here yet.</span></div>`)); return; }
    for (const e of entries) {
      let main, sub;
      if (state.kind === "number") {
        main = `${cap(e.label)} ${e.term}`;
        sub = `${e.count} document${e.count === 1 ? "" : "s"}`;
      } else {
        main = e.term;
        sub = [e.see ? "another name for " + e.see : e.label, `${e.count} document${e.count === 1 ? "" : "s"}`]
          .filter(Boolean).join(" · ");
      }
      const row = el(`<div class="item browse-item"><span class="grow"><span class="t1"></span><br><span class="t2"></span></span></div>`);
      row.querySelector(".t1").textContent = main;
      row.querySelector(".t2").textContent = sub;
      row.onclick = () => open(e);
      if (state.kind === "number" && e.masked) {
        const rev = el(`<button type="button" class="mini">Reveal</button>`);
        rev.onclick = (ev) => { ev.stopPropagation(); refresh(e.key); };
        row.append(rev);
      }
      out.append(row);
    }
  }
  function open(e) {
    if (e.correspondent_id) location.href = "/library" + "?correspondent_id=" + e.correspondent_id;
    else location.href = "/library?term=" + encodeURIComponent(state.kind + ":" + e.key);
  }
  refresh();
}
