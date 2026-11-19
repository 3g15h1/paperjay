// Paper to shred: papers whose keep-until date has passed, by shelf number. "Shredded" frees the number
// (never reused); the digital copy always stays. "Keep it" keeps that paper for good.
import { $$, api, esc, icon, fmtDate, plural, toast, confirmBox, me } from "./core.js";

export const nav = "settings";

export async function show(view, { query }) {
  let all = query.get("all") === "true", docs = [];
  view.innerHTML = `<div class="page narrow">
    <div class="head mobile-only" style="margin-bottom:6px"><a class="btn ghost sm" href="/settings">← Settings</a></div>
    <div class="head"><h1>Paper to shred</h1><span class="spacer"></span><button class="btn sm" id="allDone" hidden>${icon("check")} All shredded</button></div>
    <p class="muted small" style="margin-top:-8px">Paper you've kept as long as your <a href="/settings/keeping">keep rules</a> say.
      Shredding is only about the paper: the digital copy stays in PaperJay.</p>
    <label class="check small" style="margin:6px 0 14px"><input type="checkbox" id="all"> Also documents without a number on the paper shelf</label>
    <div class="card list" id="list"></div></div>`;
  const els = Object.fromEntries($$("[id]", view).map((e) => [e.id, e]));
  els.all.checked = all;
  const canEdit = me?.role !== "readonly";

  async function load() {
    const r = await api("/api/shred?all=" + all);
    docs = r.documents;
    history.replaceState(null, "", "/shred" + (all ? "?all=true" : ""));
    els.allDone.hidden = !canEdit || !docs.length;
    els.list.innerHTML = docs.map((d) => `<div class="item">
        <span class="paperno" style="flex:none;min-width:58px;text-align:center">${d.paper_no ? "No. " + d.paper_no : "—"}</span>
        <a class="main" href="/documents/${d.id}"><div class="t">${esc(d.title)}</div>
          <div class="sub">${esc([fmtDate(d.date), d.company, `${d.why}: kept until ${fmtDate(d.until)}`].filter(Boolean).join(" · "))}</div></a>
        ${canEdit ? `<button class="btn sm ghost" data-keep="${d.id}" title="Keep this paper for good">Keep it</button>
        <button class="btn sm" data-done="${d.id}">${icon("check")} Shredded</button>` : ""}</div>`).join("")
      || `<div class="empty">Nothing to shred. ${r.paper_numbers || all ? "" : "Number the paper you keep (Settings → Paper shelf) and PaperJay tells you when it can go."}</div>`;
  }
  els.all.onchange = () => { all = els.all.checked; load(); };
  els.list.onclick = async (e) => {
    const done = e.target.closest("[data-done]"), keep = e.target.closest("[data-keep]");
    if (done) {
      const id = +done.dataset.done;
      await api(`/api/documents/${id}/shredded`, { method: "POST" });
      toast("Marked as shredded", { label: "Undo", run: async () => { await api(`/api/documents/${id}/unshred`, { method: "POST" }); load(); } });
      load();
    }
    if (keep) {
      await api(`/api/documents/${keep.dataset.keep}`, { method: "PATCH", body: { keep_until: "9999-12-31" } });
      toast("Kept for good"); load();
    }
  };
  els.allDone.onclick = async () => {
    if (!await confirmBox(`Mark ${plural(docs.length, "paper")} as shredded?`, "Their shelf numbers are freed. The digital copies stay in PaperJay.", { ok: "All shredded" })) return;
    await api("/api/shred/bulk", { method: "POST", body: { ids: docs.map((d) => d.id) } });
    toast(`${plural(docs.length, "paper")} marked as shredded`); load();
  };
  await load();
}
