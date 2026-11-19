// Swipe a document left to show its Delete button (Library, on touch screens). Each card is
// wrapped as <div class="swipe" data-id><button class="swipe-del">(trash icon)</button><a class="doc">…</a></div>;
// the card slides left over the button. One card is open at a time; a tap on an open card,
// a swipe back to the right, or a touch anywhere else closes it. A swipe never opens the document.
// Swiping on past the button (fullAt(): most of the card) arms it — the button fills the gap and
// darkens — and letting go there deletes at once, so a full swipe is a deliberate delete.
import { icon } from "./core.js";

const OPEN = 72;          // px the card moves to uncover the button (the button's width)
const DECIDE = 10;        // px of movement before deciding between a swipe and a scroll
export const fullAt = (width) => Math.max(OPEN + 60, width * 0.6);   // px of swipe that deletes on release

export const swipeWrap = (id, cardHtml) =>
  `<div class="swipe" data-id="${id}"><button type="button" class="swipe-del" tabindex="-1" aria-label="Delete" title="Delete">${icon("trash")}</button>${cardHtml}</div>`;

export function swipeToDelete(container, { enabled = () => true, onDelete }) {
  let open = null;        // the .swipe that's open
  let drag = null;        // {row, card, x0, y0, base, dx, dir}
  let swallowClick = false;

  const cardOf = (row) => row.querySelector(".doc");
  function set(row, x, animate) {
    const card = cardOf(row), del = row.querySelector(".swipe-del");
    card.style.transition = del.style.transition = animate ? "" : "none";
    card.style.transform = x ? `translateX(${x}px)` : "";
    del.style.width = x < -OPEN ? `${-x}px` : "";      // the button fills whatever the card uncovers
    row.classList.toggle("open", x !== 0);
  }
  function arm(row, on) {
    if (row.classList.contains("armed") === on) return;
    row.classList.toggle("armed", on);
    if (on && navigator.vibrate) navigator.vibrate(10);
  }
  function close(row = open) { if (row) { arm(row, false); set(row, 0, true); } if (row === open) open = null; }

  container.addEventListener("touchstart", (e) => {
    const row = e.target.closest(".swipe");
    swallowClick = false;
    if (open && row !== open) { close(); if (!e.target.closest(".swipe-del")) swallowClick = !!row; }
    if (!row || e.touches.length > 1 || !enabled() || e.target.closest(".swipe-del")) { drag = null; return; }
    const t = e.touches[0];
    drag = { row, x0: t.clientX, y0: t.clientY, base: row === open ? -OPEN : 0, dx: 0, dir: null, width: row.offsetWidth };
  }, { passive: true });

  container.addEventListener("touchmove", (e) => {
    if (!drag) return;
    const t = e.touches[0], dx = t.clientX - drag.x0, dy = t.clientY - drag.y0;
    if (!drag.dir) {
      if (Math.abs(dx) < DECIDE && Math.abs(dy) < DECIDE) return;
      drag.dir = Math.abs(dx) > Math.abs(dy) ? "x" : "y";
      if (drag.dir === "y") { drag = null; return; }   // a scroll: leave it to the page
    }
    e.preventDefault();
    drag.dx = dx;
    const x = Math.max(-drag.width, Math.min(0, drag.base + dx));
    set(drag.row, x, false);
    arm(drag.row, -x >= fullAt(drag.width));
  }, { passive: false });

  function end() {
    if (!drag) return;
    const { row, base, dx, dir, width } = drag;
    drag = null;
    if (dir !== "x") return;
    swallowClick = true;                  // the click that follows a swipe must not open the document
    if (row.classList.contains("armed")) {   // full swipe: slide it away and delete
      if (open && open !== row) close();
      open = null;
      set(row, -width, true);
      setTimeout(() => onDelete(+row.dataset.id, row), 150);
    } else if (base + dx < -OPEN / 2) { if (open && open !== row) close(); set(row, -OPEN, true); open = row; }
    else close(row);
  }
  container.addEventListener("touchend", end);
  container.addEventListener("touchcancel", end);

  // capture: runs before the router's link handler and the Library's own click handler
  container.addEventListener("click", (e) => {
    const del = e.target.closest(".swipe-del");
    if (del) {
      e.preventDefault(); e.stopPropagation();
      const row = del.closest(".swipe");
      open = null;
      onDelete(+row.dataset.id, row);
      return;
    }
    const row = e.target.closest(".swipe");
    if (swallowClick || (row && row === open)) {
      e.preventDefault(); e.stopPropagation();
      if (row && row === open) close();
    }
    swallowClick = false;
  }, true);
  const outside = (e) => { if (open && !container.contains(e.target)) close(); };
  document.addEventListener("touchstart", outside, { passive: true });

  return { close: (row) => close(row), detach: () => document.removeEventListener("touchstart", outside) };
}
