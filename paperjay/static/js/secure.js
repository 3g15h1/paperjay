// The secure address: a one-time setup per device so the live camera scanner works (browsers only allow the
// camera on https). The device trusts PaperJay's own certificate authority, then opens the https address.
import { api, esc, icon } from "./core.js";

export const nav = "settings";

const platform = () => /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1) ? "ios"
  : /Android/.test(navigator.userAgent) ? "android" : "computer";

export async function show(view) {
  const h = await api("/api/https");
  const open = h.url ? `<a class="btn primary" href="${esc(h.url)}">${icon("camera")} Open the secure address</a> <code class="small">${esc(h.url)}</code>` : "";
  let body;
  if (h.secure) {
    body = `<div class="card pad" style="display:grid;gap:10px"><div style="color:var(--good)"><b>✓ You're on the secure address.</b></div>
      <div class="muted">The live camera scanner works here: <a href="/upload" style="color:var(--accent)">Add documents → Scan with camera</a>.
      If PaperJay is on your home screen from the old http address, add it again from here (and remove the old one).</div></div>`;
  } else if (!h.enabled) {
    body = `<div class="card pad muted">PaperJay's own HTTPS is turned off on this server (<code>HTTPS_PORT=off</code>). If it runs behind
      a reverse proxy with HTTPS, open that address instead; the camera scanner works there.</div>`;
  } else if (!h.usable) {
    body = `<div class="card pad muted">PaperJay can only make a certificate for home-network addresses: an IP address like 192.168.x.x,
      or a name ending in .local, .lan, .home.arpa or .ts.net. Open PaperJay by its IP address and come back to this page.</div>`;
  } else {
    const p = platform();
    const step = (n, html) => `<li><span class="n">${n}</span><div>${html}</div></li>`;
    const guides = {
      ios: ["iPhone and iPad", `<ol class="steps">
        ${step(1, `<a class="btn" href="/paperjay.mobileconfig">${icon("download")} Download the profile</a> and choose <b>Allow</b>.`)}
        ${step(2, `Open <b>Settings</b> → <b>Profile Downloaded</b> (near the top) → <b>Install</b>, and enter your passcode.`)}
        ${step(3, `<b>Settings → General → About → Certificate Trust Settings</b>: turn on <b>PaperJay</b>.`)}
        ${step(4, open)}</ol>`],
      android: ["Android", `<ol class="steps">
        ${step(1, `<a class="btn" href="/paperjay-ca.crt">${icon("download")} Download the certificate</a>.`)}
        ${step(2, `<b>Settings → Security &amp; privacy → More security settings → Encryption &amp; credentials → Install a certificate → CA certificate</b>
          → <b>Install anyway</b>, then pick <b>PaperJay.crt</b> from Downloads. (Search Settings for “CA certificate” if the menus differ.)`)}
        ${step(3, open)}</ol>`],
      computer: ["Computer", `<ol class="steps">
        ${step(1, `<a class="btn" href="/paperjay-ca.crt">${icon("download")} Download the certificate</a>.`)}
        ${step(2, `<b>Mac:</b> open it, then in Keychain Access double-click <b>PaperJay</b> → Trust → <b>Always Trust</b>.
          <b>Windows:</b> open it → Install Certificate → Local Machine → <b>Trusted Root Certification Authorities</b>.
          <b>Firefox</b> keeps its own list: Settings → Certificates → View Certificates → Authorities → Import.`)}
        ${step(3, open)}</ol>`],
    };
    const order = [p, ...Object.keys(guides).filter((k) => k !== p)];
    body = `<div class="card pad" style="display:grid;gap:12px"><h2 style="margin:0;font-size:15px">${guides[p][0]}</h2>${guides[p][1]}</div>
      ${order.slice(1).map((k) => `<details class="card pad" style="margin-top:10px"><summary>${guides[k][0]}</summary>${guides[k][1]}</details>`).join("")}
      <div class="faint small" style="margin-top:12px">This certificate is made by your own PaperJay and only covers home-network addresses
        (192.168.x.x, 10.x, .local, .lan, Tailscale…), so it can't be used to fake real websites. Its fingerprint (SHA-256):
        <code style="word-break:break-all">${esc(h.fingerprint)}</code></div>`;
  }
  view.innerHTML = `<div class="page narrow">
    <div class="head" style="margin-bottom:6px"><a class="btn ghost sm" href="/settings">← Settings</a></div>
    <div class="head"><h1>Secure address</h1></div>
    <p class="muted" style="margin-top:-6px">Phones only let web apps use the live camera over https. Set this up once on each phone,
      and the scanner finds the page while you aim and takes the photo by itself.</p>
    ${body}</div>`;
}
