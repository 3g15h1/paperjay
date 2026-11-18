// Adding documents: drop or pick files, or photograph a document page by page (sent as one PDF).
import { $, $$, api, esc, icon, ago, plural, refreshCounts, statusChip } from "./core.js";
import { liveCameraPossible, openCamera } from "./camera.js";

export const nav = "upload";

export async function show(view, { query }) {
  view.innerHTML = `<div class="page narrow">
    <div class="head"><h1>Add documents</h1></div>
    <div class="drop" id="drop">
      <div class="muted">Drop PDFs or photos here</div>
      <div class="row" style="justify-content:center">
        <button type="button" class="btn primary" id="camBtn">${icon("camera")} Scan with camera</button><input type="file" id="cam" accept="image/*" capture="environment" hidden>
        <label class="btn">${icon("file")} Choose files<input type="file" id="pick" multiple accept="application/pdf,image/*,.heic,.heif" hidden></label>
      </div>
      <div class="faint small">PDF, JPEG, PNG, TIFF, HEIC and WebP. Each file becomes one document.</div>
      <div class="row" id="paperRow" hidden style="justify-content:center">
        <label class="check"><input type="checkbox" id="paperKeep"><span>Keeping the paper?</span></label>
        <label class="small">Paper no. <input class="input" id="paperNo" type="number" min="1" step="1" style="width:96px"></label>
      </div>
      <div class="small" id="secureHint" hidden></div>
    </div>
    <div class="card pad" id="scan" hidden style="margin-top:14px;display:grid;gap:12px">
      <div id="editor" hidden style="display:grid;gap:10px">
        <div class="row"><b id="edTitle">Page 1</b><span class="faint small" id="edHint"></span></div>
        <div class="scanbox"><div class="scanwrap"><img id="edImg" alt=""><svg id="edSvg" preserveAspectRatio="none"></svg></div></div>
        <div class="row">
          <div class="seg" id="looks"><button data-look="bw">Black &amp; white</button><button data-look="gray">Grayscale</button><button data-look="color">Color</button></div>
          <button class="btn sm" id="rotBtn" title="Turn">${icon("redo")} Turn</button>
          <button class="btn sm" id="prevBtn">Preview</button>
          <button class="btn sm ghost" id="resetBtn">Whole photo</button>
        </div>
        <div class="row"><button class="btn primary" id="useBtn">${icon("check")} Use this page</button><button class="btn ghost" id="dropBtn">Retake</button></div>
      </div>
      <div id="pagesBox" style="display:grid;gap:10px">
        <div class="row"><b>Scan</b><span class="faint" id="scanN"></span><span style="flex:1"></span>
          <button type="button" class="btn sm" id="addPage">${icon("camera")} Add page</button><input type="file" id="cam2" accept="image/*" capture="environment" hidden></div>
        <div class="shots" id="shots"></div>
        <div class="row" id="scanModeRow" hidden><span class="faint small">Save as</span>
          <div class="seg" id="scanMode"><button type="button" data-mode="one">One document</button><button type="button" data-mode="separate">Separate documents</button></div></div>
        <div class="row"><button class="btn primary" id="scanDone">Save</button><button class="btn ghost" id="scanCancel">Cancel</button></div>
      </div>
    </div>
    </div>
    <div class="section" id="nowWrap" hidden><div class="h"><h2>This session</h2></div><div class="card list" id="now"></div></div>
    <div class="section"><div class="h"><h2>Recent uploads</h2><span class="aside">Other ways in: the consume folder, your phone’s share menu and the API</span></div><div class="card list" id="recent"></div></div>
  </div>`;
  const els = Object.fromEntries($$("[id]", view).map((e) => [e.id, e]));
  const shots = [];
  const tracked = [];  // {name, progress, upload, doc}
  let poll = null;

  // ---------- paper numbers (Phase D) ----------
  // Shown only with the paper_numbers setting on. The box is prefilled with the next
  // free number as a suggestion; until the person edits it, sends claim the next free
  // number server-side (paper_next), so a page left open can never be rejected for a
  // number it never chose (REVIEW-0.4.2 #2). Once the box is edited, the typed number
  // goes as an explicit claim — and is refused if it was taken in the meantime.
  let paperTyped = false;  // the prefilled number is not the person's own claim
  const keepingPaper = () => !els.paperRow.hidden && els.paperKeep.checked;
  api("/api/paper/next", { quiet: true }).then((p) => {
    if (!p.enabled) return;
    els.paperRow.hidden = false;
    els.paperNo.value = p.next;
  }).catch(() => {});
  els.paperNo.addEventListener("input", () => { paperTyped = true; });
  function paperFormFields(form) {
    if (!keepingPaper()) return;
    if (paperTyped) {  // an edited box claims its number; emptied again, it claims nothing
      if (els.paperNo.value) form.append("paper_no", els.paperNo.value);
      paperTyped = false;
    }
    else form.append("paper_next", "true");
  }
  function paperDone(u) {
    if (u && u.paper_no && +els.paperNo.value <= u.paper_no) els.paperNo.value = u.paper_no + 1;
  }

  // ---------- sending ----------
  function send(files, combine = false) {
    const item = { name: combine ? `Scan of ${plural(files.length, "page")}` : files[0].name, progress: 0, upload: null, doc: null, error: null };
    tracked.unshift(item); drawNow();
    const form = new FormData();
    for (const f of files) form.append("file", f, f.name || "photo.jpg");
    if (combine) form.append("combine", "true");
    paperFormFields(form);
    const xhr = new XMLHttpRequest();   // XHR, not fetch: it reports upload progress
    xhr.open("POST", "/api/documents");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) { item.progress = e.loaded / e.total; drawNow(); } };
    xhr.onload = () => {
      let data = {}; try { data = JSON.parse(xhr.responseText); } catch {}
      if (data.uploads) { item.upload = data.uploads[0]; paperDone(item.upload); }
      else item.error = data.detail || `Upload failed (${xhr.status})`;
      item.progress = 1; drawNow(); watch(); refreshCounts();
    };
    xhr.onerror = () => { item.error = "Couldn't reach PaperJay"; drawNow(); };
    xhr.send(form);
  }
  function sendEach(files) { for (const f of files) send([f]); }

  function drawNow() {
    els.nowWrap.hidden = !tracked.length;
    els.now.innerHTML = tracked.map((t) => {
      const u = t.upload, d = t.doc;
      let state = "", sub = "", href = "";
      if (t.error) { state = `<span class="chip bad">Not added</span>`; sub = t.error; }
      else if (!u) { sub = `Uploading… ${Math.round(t.progress * 100)}%`; }
      else if (u.outcome === "duplicate") { state = `<span class="chip">Already here</span>`; sub = u.message; href = `/documents/${u.duplicate_of}`; }
      else if (u.outcome === "rejected") { state = `<span class="chip bad">Not added</span>`; sub = u.message; }
      else { href = `/documents/${u.document_id}`; state = d ? statusChip(d) || `<span class="chip good">Filed</span>` : `<span class="chip accent">Reading…</span>`;
        sub = d && d.status === "ready" ? [d.correspondent, d.document_type].filter(Boolean).join(" · ") || "Read" : d && d.status === "failed" ? d.error : "Reading the text…";
        if (u.paper_no) sub = `Paper no. ${u.paper_no} · ${sub}`; }
      const title = d && d.status === "ready" ? d.title : t.name;
      const inner = `<span class="main"><div class="t">${esc(title)}</div><div class="sub">${esc(sub || "")}</div>
        ${!u && !t.error ? `<div class="progress" style="margin-top:6px"><i style="width:${Math.round(t.progress * 100)}%"></i></div>` : ""}</span>${state}`;
      return href ? `<a class="item" href="${href}">${inner}</a>` : `<div class="item">${inner}</div>`;
    }).join("");
  }
  async function watch() {
    if (poll) return;
    poll = setInterval(async () => {
      const open = tracked.filter((t) => t.upload && t.upload.outcome === "accepted" && (!t.doc || ["pending", "processing"].includes(t.doc.status)));
      if (!open.length) { clearInterval(poll); poll = null; loadRecent(); refreshCounts(); return; }
      for (const t of open) t.doc = await api(`/api/documents/${t.upload.document_id}`, { quiet: true }).catch(() => t.doc);
      drawNow();
    }, 2000);
  }

  // ---------- inputs ----------
  els.pick.onchange = () => { sendEach([...els.pick.files]); els.pick.value = ""; };
  // ---------- camera scanner ----------
  let look = localStorage.getItem("pj-scan-look") || "bw";
  let cur = null;            // the page being edited: {id, width, height, corners, look, rotate}
  let editing = null;        // index in shots of the page being edited again, or null for a new one
  let previewing = false;
  const svgNS = "http://www.w3.org/2000/svg";
  async function addPhoto(input) {
    const file = input.files[0]; input.value = "";
    if (!file) return;
    els.scan.hidden = false; els.drop.hidden = true; els.editor.hidden = false; els.pagesBox.hidden = true;
    els.edTitle.textContent = `Page ${shots.length + 1}`; els.edHint.textContent = "Finding the page…"; els.dropBtn.textContent = "Retake";
    els.edImg.removeAttribute("src"); els.edSvg.innerHTML = "";
    const form = new FormData(); form.append("file", file, file.name || "photo.jpg");
    try { cur = { ...(await api("/api/scan/pages", { method: "POST", form })), look, rotate: 0 }; }
    catch { closeEditor(); return; }
    els.edHint.textContent = cur.found ? "Drag the corners if the outline isn't right." : "No page edge found: drag the corners onto the page.";
    previewing = false; drawEditor();
  }
  function drawEditor() {
    els.looks.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.look === cur.look));
    els.prevBtn.textContent = previewing ? "Edit corners" : "Preview";
    els.edHint.textContent = previewing ? "This is how the page will be saved." : (cur.found ? "Drag the corners if the outline isn't right." : "No page edge found: drag the corners onto the page.");
    els.rotBtn.disabled = !previewing;
    if (previewing) {
      els.edSvg.innerHTML = "";
      els.edImg.src = `/api/scan/pages/${cur.id}/preview?corners=${cur.corners.flat().map((v) => Math.round(v)).join(",")}&look=${cur.look}&rotate=${cur.rotate}&w=900`;
      return;
    }
    els.edImg.src = `/api/scan/pages/${cur.id}/photo?w=1200`;
    const svg = els.edSvg;
    svg.setAttribute("viewBox", `0 0 ${cur.width} ${cur.height}`);
    const r = Math.max(cur.width, cur.height) / 40;
    svg.innerHTML = `<polygon points="${cur.corners.map((p) => p.join(",")).join(" ")}" />` +
      cur.corners.map((p, i) => `<circle data-i="${i}" cx="${p[0]}" cy="${p[1]}" r="${r}" />`).join("");
  }
  // dragging a corner: pointer position in the photo's own pixels
  let dragging = null;
  els.edSvg.addEventListener("pointerdown", (e) => {
    const c = e.target.closest("circle"); if (!c) return;
    dragging = +c.dataset.i; els.edSvg.setPointerCapture(e.pointerId); e.preventDefault();
  });
  els.edSvg.addEventListener("pointermove", (e) => {
    if (dragging === null) return;
    const b = els.edSvg.getBoundingClientRect();
    const x = Math.min(cur.width, Math.max(0, (e.clientX - b.left) / b.width * cur.width));
    const y = Math.min(cur.height, Math.max(0, (e.clientY - b.top) / b.height * cur.height));
    cur.corners[dragging] = [x, y];
    const poly = els.edSvg.querySelector("polygon"), dot = els.edSvg.querySelector(`circle[data-i="${dragging}"]`);
    poly.setAttribute("points", cur.corners.map((p) => p.join(",")).join(" "));
    dot.setAttribute("cx", x); dot.setAttribute("cy", y);
  });
  const endDrag = () => { dragging = null; };
  els.edSvg.addEventListener("pointerup", endDrag); els.edSvg.addEventListener("pointercancel", endDrag);
  els.looks.onclick = (e) => { const b = e.target.closest("[data-look]"); if (!b) return; cur.look = look = b.dataset.look; localStorage.setItem("pj-scan-look", look); if (!previewing) previewing = true; drawEditor(); };
  els.prevBtn.onclick = () => { previewing = !previewing; drawEditor(); };
  els.rotBtn.onclick = () => { cur.rotate = (cur.rotate + 90) % 360; drawEditor(); };
  els.resetBtn.onclick = () => { cur.corners = [[0, 0], [cur.width, 0], [cur.width, cur.height], [0, cur.height]]; previewing = false; drawEditor(); };
  const previewUrl = (p, w) => `/api/scan/pages/${p.id}/preview?corners=${p.corners.flat().map((v) => Math.round(v)).join(",")}&look=${p.look}&rotate=${p.rotate}&w=${w}`;
  els.useBtn.onclick = () => {
    const page = { ...cur, corners: cur.corners.map((p) => [...p]), url: previewUrl(cur, 300) };
    if (editing !== null && shots[editing]) shots[editing] = page; else shots.push(page);
    cur = null; editing = null; closeEditor();
  };
  els.dropBtn.onclick = () => { const again = editing === null; cur = null; editing = null; closeEditor(); if (again) takePhoto(); };
  function closeEditor() { els.editor.hidden = true; els.pagesBox.hidden = false; drawShots(); }
  els.cam.onchange = () => addPhoto(els.cam);
  els.cam2.onchange = () => addPhoto(els.cam2);
  // the live camera (https) finds the page while you aim and takes the photo itself; otherwise the phone's camera app
  function takePhoto(first = false) {
    if (!liveCameraPossible()) return (first ? els.cam : els.cam2).click();
    openCamera({ count: shots.length, onShot: liveShot, onClose: () => drawShots() });
  }
  els.camBtn.onclick = () => takePhoto(true);
  els.addPage.onclick = () => takePhoto();
  function liveShot(blob, corners) {
    const page = { pending: true, look, rotate: 0, url: URL.createObjectURL(blob) };
    shots.push(page); drawShots();
    const form = new FormData();
    form.append("file", blob, `page-${shots.length}.jpg`);
    if (corners) form.append("hint", corners.flat().join(","));
    api("/api/scan/pages", { method: "POST", form }).then((r) => {
      URL.revokeObjectURL(page.url);
      Object.assign(page, r, { pending: false });
      page.url = previewUrl(page, 300);
    }).catch(() => { shots.splice(shots.indexOf(page), 1); }).finally(drawShots);
  }
  // several pages: one document, or one document per page (remembered)
  let scanMode = localStorage.getItem("pj-scan-mode") === "separate" ? "separate" : "one";
  els.scanMode.onclick = (e) => {
    const b = e.target.closest("[data-mode]"); if (!b) return;
    scanMode = b.dataset.mode; localStorage.setItem("pj-scan-mode", scanMode); drawShots();
  };
  function drawShots() {
    els.scan.hidden = !shots.length && !cur; els.drop.hidden = !els.scan.hidden;
    const waiting = shots.filter((s) => s.pending).length;
    els.scanN.textContent = plural(shots.length, "page") + (waiting ? ` · sending ${waiting}…` : "");
    els.shots.innerHTML = shots.map((s, i) => `<div class="shot${s.pending ? " pending" : ""}"><img src="${s.url}" alt="Page ${i + 1}" ${s.pending ? "" : `data-edit="${i}" title="Adjust this page"`}><button type="button" data-rm="${i}" aria-label="Remove">✕</button></div>`).join("");
    els.scanModeRow.hidden = shots.length < 2;
    els.scanMode.querySelectorAll("[data-mode]").forEach((b) => b.classList.toggle("on", b.dataset.mode === scanMode));
    els.scanDone.textContent = shots.length < 2 ? "Save"
      : scanMode === "separate" ? `Save as ${shots.length} documents` : `Save ${shots.length} pages as one document`;
    els.scanDone.disabled = !shots.length || waiting > 0;
  }
  els.shots.onclick = (e) => {
    const b = e.target.closest("[data-rm]"), ed = e.target.closest("[data-edit]");
    if (b) { shots.splice(+b.dataset.rm, 1); drawShots(); }
    else if (ed) {
      editing = +ed.dataset.edit;
      const s = shots[editing];
      cur = { ...s, corners: s.corners.map((p) => [...p]) };
      els.editor.hidden = false; els.pagesBox.hidden = true;
      els.edTitle.textContent = `Page ${editing + 1}`; els.dropBtn.textContent = "Cancel"; previewing = true; drawEditor();
    }
  };
  els.scanDone.onclick = async () => {
    const separate = shots.length > 1 && scanMode === "separate";
    const pages = shots.splice(0).map(({ id, corners, look, rotate }) => ({ id, corners, look, rotate }));
    drawShots();
    const item = { name: separate ? `Scan of ${plural(pages.length, "page")}, one document each` : `Scan of ${plural(pages.length, "page")}`,
      progress: 0.5, upload: null, doc: null, error: null };
    tracked.unshift(item); drawNow();
    const body = { pages, mode: separate ? "separate" : "one" };
    if (keepingPaper()) {
      if (paperTyped) {
        if (els.paperNo.value) body.paper_no = +els.paperNo.value;
        paperTyped = false;
      }
      else body.paper_next = true;
    }
    try {
      const ups = (await api("/api/scan/finish", { method: "POST", body })).uploads;
      item.upload = ups[0]; ups.forEach(paperDone);
      // each extra document gets its own line in "This session"
      const at = tracked.indexOf(item);
      tracked.splice(at + 1, 0, ...ups.slice(1).map((u, i) => ({ name: `Scan page ${i + 2}`, progress: 1, upload: u, doc: null, error: null })));
      if (separate) item.name = "Scan page 1";
    }
    catch (e) { item.error = e.message; }
    item.progress = 1; drawNow(); watch(); refreshCounts();
  };
  els.scanCancel.onclick = () => { shots.splice(0); drawShots(); };
  if (!window.isSecureContext) {
    // on plain http: point to the secure address, where the live scanner works
    api("/api/https", { quiet: true }).then((h) => {
      if (!h.enabled || h.secure) return;
      els.secureHint.hidden = false;
      els.secureHint.innerHTML = `<a href="/secure" style="color:var(--accent)">Live scanner</a>: finds the page and takes the photo by itself. It needs the secure address (a one-time setup per phone).`;
    }).catch(() => {});
  }
  const drop = els.drop;
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => sendEach([...e.dataTransfer.files]));
  const pageDrop = (e) => { if (e.dataTransfer?.types?.includes("Files")) e.preventDefault(); };
  addEventListener("dragover", pageDrop);

  async function loadRecent() {
    const ups = await api("/api/uploads?limit=15", { quiet: true }).catch(() => []);
    els.recent.innerHTML = ups.map((u) => {
      const href = u.document_id ? `/documents/${u.document_id}` : u.duplicate_of ? `/documents/${u.duplicate_of}` : "";
      const state = u.outcome === "duplicate" ? `<span class="chip">Already here</span>` : u.outcome === "rejected" ? `<span class="chip bad">Not added</span>`
        : u.document_status === "failed" ? `<span class="chip bad">Failed</span>` : u.document_status === "ready" ? "" : `<span class="chip accent">Reading…</span>`;
      const inner = `<span class="main"><div class="t">${esc(u.document_title || u.filename)}</div><div class="sub">${esc(u.source)} · ${ago(u.created_at)}${u.message ? " · " + esc(u.message) : ""}</div></span>${state}`;
      return href ? `<a class="item" href="${href}">${inner}</a>` : `<div class="item">${inner}</div>`;
    }).join("") || `<div class="empty">Nothing uploaded yet.</div>`;
  }
  // Files shared from another app (Android share sheet) arrive as /upload?shared=<upload ids>
  const shared = (query.get("shared") || "").split(",").filter(Boolean);
  if (shared.length) {
    const ups = await api("/api/uploads?limit=50", { quiet: true }).catch(() => []);
    for (const u of ups.filter((x) => shared.includes(x.id))) tracked.push({ name: u.filename, progress: 1, upload: { ...u }, doc: null });
    drawNow(); watch();
  }
  loadRecent();
  return () => { clearInterval(poll); removeEventListener("dragover", pageDrop); };
}
