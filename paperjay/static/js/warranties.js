// Warranties: everything you bought that still has (or had) a warranty, the ones ending soonest first.
import { $$, api, esc, fmtDate, money, plural, libSwitch } from "./core.js";

export const nav = "library";

export async function show(view) {
  view.innerHTML = `<div class="page narrow">
    <div class="head libhead"><h1>Library</h1><span class="faint num" id="count"></span></div>
    ${libSwitch("warranties")}
    <p class="muted small" style="margin:10px 2px">Read from your receipts and invoices ("2-year limited warranty", "warranty valid
      until…") and counted from the day you bought it. Fix one on its document's page. Their receipts are kept until the warranty
      ends, and a warranty that ends soon shows under Coming up.</p>
    <div class="section" style="margin-top:8px"><div class="card list" id="running"></div></div>
    <div class="section" id="endedWrap" hidden><div class="h"><h2>Ended</h2></div><div class="card list" id="ended"></div></div></div>`;
  const els = Object.fromEntries($$("[id]", view).map((e) => [e.id, e]));
  const r = await api("/api/warranties");
  const row = (i) => {
    const left = i.lifetime ? "Lifetime" : i.ended ? `Ended ${fmtDate(i.until)}`
      : i.days_left <= 60 ? `${plural(i.days_left, "day")} left` : `Until ${fmtDate(i.until)}`;
    return `<a class="item" href="/documents/${i.id}"><span class="main"><div class="t">${esc(i.title)}</div>
      <div class="sub">${esc([i.company, i.purchased ? "bought " + fmtDate(i.purchased) : "", i.amount != null ? money(i.amount) : "",
        i.paper_no ? "paper no. " + i.paper_no : "", i.source === "person" ? "set by you" : `“${i.phrase}”`].filter(Boolean).join(" · "))}</div></span>
      <span class="chip ${i.ended ? "" : !i.lifetime && i.days_left <= 60 ? "warn" : "good"}">${esc(left)}</span></a>`;
  };
  const running = r.items.filter((i) => !i.ended), ended = r.items.filter((i) => i.ended);
  els.count.textContent = running.length ? `${running.length} running` : "";
  els.running.innerHTML = running.map(row).join("") || `<div class="empty">No running warranties found. When a receipt says how long its warranty lasts, it shows up here.</div>`;
  els.endedWrap.hidden = !ended.length;
  els.ended.innerHTML = ended.map(row).join("");
}
