// Sign in.
import { $, api, esc, ApiError } from "./core.js";

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
    <div class="small" id="err" style="color:var(--bad)" hidden></div>
    <button class="btn primary">Sign in</button>
    <div class="faint small">Forgot your password? Ask whoever runs PaperJay at home to set a new one under Settings → People.</div></form>`;
  const f = $("#f", view);
  f.username.focus();
  f.onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/api/auth/login", { method: "POST", quiet: true, body: { username: f.username.value, password: f.password.value } });
      location.href = next.startsWith("/") && !next.startsWith("//") ? next : "/";
    } catch (err) {
      const box = $("#err", view); box.hidden = false;
      box.textContent = err instanceof ApiError && err.status ? err.message : "Can't reach PaperJay.";
      f.password.select();
    }
  };
}
