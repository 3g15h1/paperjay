// Sign in.
import { $, api } from "./core.js";

export const nav = "";

export async function show(view, { query }) {
  const s = await api("/api/auth/status", { quiet: true }).catch(() => ({}));
  if (s.setup_needed) { location.href = "/setup"; return; }
  const next = query.get("next") || "/";
  if (s.user) { location.href = next.startsWith("/") ? next : "/"; return; }
  view.innerHTML = `<form class="auth card pad" id="f">
    <div class="logo" aria-label="PaperJay"><svg viewBox="0 0 712 200" role="img"><title>PaperJay</title><use href="#pj-logo"/></svg></div>
    <label class="field">Name<input class="input" name="username" autocomplete="username" autocapitalize="none" required></label>
    <label class="field">Password<input class="input" name="password" type="password" autocomplete="current-password" required></label>
    <label class="field" id="codeRow" hidden>Code from your authenticator app<input class="input" name="code" inputmode="numeric" autocomplete="one-time-code" placeholder="123 456" autocapitalize="none">
      <span class="help">Lost your phone? Type one of your recovery codes instead.</span></label>
    <div class="small" id="err" style="color:var(--bad)" hidden></div>
    <button class="btn primary">Sign in</button>
    <div class="faint small">Forgot your password? Ask whoever runs PaperJay at home to set a new one under Settings → People.</div></form>`;
  const f = $("#f", view);
  f.username.focus();
  f.onsubmit = async (e) => {
    e.preventDefault();
    const box = $("#err", view), codeRow = $("#codeRow", view);
    let r;
    try {
      r = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: f.username.value, password: f.password.value, code: codeRow.hidden ? undefined : f.code.value }) });
    } catch { box.hidden = false; box.textContent = "Can't reach PaperJay."; return; }
    if (r.ok) { location.href = next.startsWith("/") && !next.startsWith("//") ? next : "/"; return; }
    const data = await r.json().catch(() => ({}));
    if (data.code_needed) {   // the password was right; two-step sign-in is on
      codeRow.hidden = false; box.hidden = true; f.code.required = true; f.code.focus();
      return;
    }
    box.hidden = false;
    box.textContent = data.detail || "Something went wrong";
    if (!codeRow.hidden) f.code.select(); else f.password.select();
  };
}
