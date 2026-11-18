// The live camera scanner: the page's outline follows the page while you aim, and when it holds still the
// photo is taken by itself (or tap the button). Page after page, then Done. Needs https (browsers only allow
// the camera there); otherwise upload.js falls back to the phone's own camera app.

export const liveCameraPossible = () => !!(window.isSecureContext && navigator.mediaDevices?.getUserMedia);

const FRAME = 480;          // long side of the frames sent to find the page
const EVERY_MS = 120;       // at most this often
const STEADY_MS = 800;      // the outline must hold still this long
const STILL = 0.02;         // "still": no corner moves more than 2% of the picture
const NEW_PAGE = 0.12;      // after a photo, the outline must move this much (or vanish) before the next one
const MIN_AREA = 0.12;      // the page must fill at least 12% of the picture

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const moved = (a, b) => Math.max(...a.map((p, i) => Math.hypot(p[0] - b[i][0], p[1] - b[i][1])));
const area = (c) => Math.abs(c.reduce((s, p, i) => { const q = c[(i + 1) % 4]; return s + p[0] * q[1] - q[0] * p[1]; }, 0)) / 2;

/** Opens the camera full screen. onShot(blob, corners 0-1 or null) for each photo; onClose() when done. */
export function openCamera({ onShot, onClose, count = 0 }) {
  const root = document.createElement("div");
  root.className = "cam";
  root.innerHTML = `<video playsinline muted autoplay></video>
    <svg preserveAspectRatio="xMidYMid meet"><polygon/></svg>
    <div class="cam-flash"></div>
    <div class="cam-top"><button class="cam-btn" data-close aria-label="Close">✕</button>
      <span class="cam-hint">Starting the camera…</span>
      <span class="cam-tools"><button class="cam-btn" data-torch hidden aria-label="Light">⚡</button>
      <button class="cam-btn cam-auto" data-auto>Auto</button></span></div>
    <div class="cam-bottom">
      <button class="cam-thumb" data-done aria-label="Pages taken"><img alt="" hidden><b></b></button>
      <button class="cam-shutter" data-shoot aria-label="Take the photo"><i></i></button>
      <button class="cam-btn cam-done" data-done>Done</button></div>`;
  document.body.appendChild(root);
  document.documentElement.classList.add("cam-open");
  const video = root.querySelector("video"), svg = root.querySelector("svg"), poly = root.querySelector("polygon");
  const hintEl = root.querySelector(".cam-hint"), thumb = root.querySelector(".cam-thumb img"), countEl = root.querySelector(".cam-thumb b");
  const autoBtn = root.querySelector("[data-auto]"), torchBtn = root.querySelector("[data-torch]");
  const small = document.createElement("canvas");
  let stream = null, track = null, running = true, shooting = false, torch = false;
  let auto = localStorage.getItem("pj-cam-auto") !== "off";
  let last = null, anchor = null, steadySince = 0, armed = true, lastShot = null, lost = 0;
  let thumbUrl = null;

  const hint = (t) => { if (hintEl.textContent !== t) hintEl.textContent = t; };
  const drawCount = () => { countEl.textContent = count || ""; root.querySelector(".cam-thumb").hidden = !count; };
  const drawAuto = () => { autoBtn.classList.toggle("on", auto); autoBtn.textContent = auto ? "Auto" : "Manual"; };
  drawCount(); drawAuto();

  function close() {
    if (!running) return;
    running = false;
    stream?.getTracks().forEach((t) => t.stop());
    if (thumbUrl) URL.revokeObjectURL(thumbUrl);
    document.documentElement.classList.remove("cam-open");
    root.remove();
    removeEventListener("popstate", close);
    onClose?.();
  }
  // the phone's back gesture closes the camera instead of leaving the page
  history.pushState({ cam: 1 }, "");
  addEventListener("popstate", close);
  const closeByButton = () => { if (history.state?.cam) history.back(); else close(); };
  root.querySelectorAll("[data-close], [data-done]").forEach((b) => (b.onclick = closeByButton));
  autoBtn.onclick = () => { auto = !auto; localStorage.setItem("pj-cam-auto", auto ? "on" : "off"); drawAuto(); };
  torchBtn.onclick = async () => { torch = !torch; try { await track.applyConstraints({ advanced: [{ torch }] }); } catch {} torchBtn.classList.toggle("on", torch); };
  root.querySelector("[data-shoot]").onclick = () => shoot(last && area(last) >= MIN_AREA ? last : null);

  async function start() {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: false,
        video: { facingMode: { ideal: "environment" }, width: { ideal: 3840 }, height: { ideal: 2160 } } });
    } catch (e) {
      hint(e.name === "NotAllowedError" ? "PaperJay isn't allowed to use the camera. Allow it in the browser's settings for this site."
        : "Couldn't start the camera: " + (e.message || e.name));
      return;
    }
    if (!running) { stream.getTracks().forEach((t) => t.stop()); return; }
    track = stream.getVideoTracks()[0];
    video.srcObject = stream;
    await video.play().catch(() => {});
    const caps = track.getCapabilities?.() || {};
    try { if (caps.focusMode?.includes("continuous")) await track.applyConstraints({ advanced: [{ focusMode: "continuous" }] }); } catch {}
    torchBtn.hidden = !caps.torch;
    hint("Point at a page");
    while (running) {
      const t0 = performance.now();
      if (video.readyState >= 2 && video.videoWidth && !shooting) await look();
      await sleep(Math.max(0, EVERY_MS - (performance.now() - t0)));
    }
  }

  async function look() {
    const vw = video.videoWidth, vh = video.videoHeight, s = Math.min(1, FRAME / Math.max(vw, vh));
    small.width = Math.round(vw * s); small.height = Math.round(vh * s);
    small.getContext("2d").drawImage(video, 0, 0, small.width, small.height);
    const blob = await new Promise((r) => small.toBlob(r, "image/jpeg", 0.6));
    let d;
    try {
      const r = await fetch("/api/scan/detect", { method: "POST", body: blob, headers: { "Content-Type": "image/jpeg" } });
      if (!r.ok) return;
      d = await r.json();
    } catch { return; }
    if (!running) return;
    svg.setAttribute("viewBox", `0 0 ${vw} ${vh}`);
    const now = performance.now();
    if (!d.found || area(d.corners) < MIN_AREA) {
      last = anchor = null; poly.setAttribute("points", ""); root.style.setProperty("--steady", 0);
      if (!armed && ++lost >= 2) armed = true;
      hint(armed ? (d.found ? "Move closer" : "Point at a page") : "Got it. Next page, or Done");
      return;
    }
    lost = 0; last = d.corners;
    poly.setAttribute("points", d.corners.map(([x, y]) => `${x * vw},${y * vh}`).join(" "));
    if (!armed) {
      if (lastShot && moved(lastShot, d.corners) > NEW_PAGE) armed = true;
      else { root.style.setProperty("--steady", 0); hint("Got it. Next page, or Done"); return; }
    }
    if (!anchor || moved(anchor, d.corners) > STILL) { anchor = d.corners; steadySince = now; }
    const p = Math.min(1, (now - steadySince) / STEADY_MS);
    root.style.setProperty("--steady", auto ? p : 0);
    if (!auto) { hint("Tap the button to take it"); return; }
    hint("Hold still…");
    if (p >= 1) await shoot(d.corners);
  }

  async function shoot(corners) {
    if (shooting || !video.videoWidth) return;
    shooting = true;
    root.classList.remove("flash"); void root.offsetWidth; root.classList.add("flash");
    navigator.vibrate?.(30);
    const c = document.createElement("canvas");
    c.width = video.videoWidth; c.height = video.videoHeight;
    c.getContext("2d").drawImage(video, 0, 0);
    const blob = await new Promise((r) => c.toBlob(r, "image/jpeg", 0.92));
    armed = false; lastShot = corners || last || [[0, 0], [1, 0], [1, 1], [0, 1]]; anchor = null;
    root.style.setProperty("--steady", 0);
    count += 1; drawCount();
    if (thumbUrl) URL.revokeObjectURL(thumbUrl);
    thumbUrl = URL.createObjectURL(blob); thumb.src = thumbUrl; thumb.hidden = false;
    hint("Got it. Next page, or Done");
    onShot(blob, corners);
    shooting = false;
  }

  start();
  return { close };
}
