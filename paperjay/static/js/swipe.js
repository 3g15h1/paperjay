// Swipe a document left to show its Delete button (Library, on touch screens). Each card is
// wrapped as <div class="swipe" data-id><button class="swipe-del">(trash icon)</button><a class="doc">…</a></div>;
// the card slides left over the button. One card is open at a time; a tap on an open card,
// a swipe back to the right, or a touch anywhere else closes it. A swipe never opens the document.
import { icon } from "./core.js";

const OPEN = 72;          // px the card moves to uncover the button (the button's width)
const DECIDE = 10;        // px of movement before deciding between a swipe and a scroll

export const swipeWrap = (id, cardHtml) =>
  `<div class="swipe" data-id="${id}"><button type="button" class="swipe-del" tabindex="-1" aria-label="Delete" title="Delete">${icon("trash")}</button>${cardHtml}</div>`;

export function swipeToDelete(container, { enabled = () => true, onDelete }) {
  let open = null;        // the .swipe that's open
  let drag = null;        // {row, card, x0, y0, base, dx, dir}
  let swallowClick = false;

  const cardOf = (row) => row.querySelector(".doc");
  function set(row, x, animate) {
    const card = cardOf(row);
    card.style.transition = animate ? "" : "none";
    card.style.transform = x ? `translateX(${x}px)` : "";
    row.classList.toggle("open", x !== 0);
  }
  function close(row = open) { if (row) set(row, 0, true); if (row === open) open = null; }

  container.addEventListener("touchstart", (e) => {
    const row = e.target.closest(".swipe");
    swallowClick = false;
    if (open && row !== open) { close(); if (!e.target.closest(".swipe-del")) swallowClick = !!row; }
    if (!row || e.touches.length > 1 || !enabled() || e.target.closest(".swipe-del")) { drag = null; return; }
    const t = e.touches[0];
    drag = { row, x0: t.clientX, y0: t.clientY, base: row === open ? -OPEN : 0, dx: 0, dir: null };
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
    set(drag.row, Math.max(-OPEN * 1.25, Math.min(0, drag.base + dx)), false);
  }, { passive: false });

  function end() {
    if (!drag) return;
    const { row, base, dx, dir } = drag;
    drag = null;
    if (dir !== "x") return;
    swallowClick = true;                  // the click that follows a swipe must not open the document
    if (base + dx < -OPEN / 2) { if (open && open !== row) close(); set(row, -OPEN, true); open = row; }
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

  return { close: () => close(), detach: () => document.removeEventListener("touchstart", outside) };
}
