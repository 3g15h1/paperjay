// First run: create the first account, choose how documents are read, optionally connect an AI.
import { $, api, esc, icon, toast } from "./core.js";

export const nav = "";

export async function show(view) {
  let step = 0;
  const status = await api("/api/auth/status", { quiet: true }).catch(() => ({}));
  if (!status.setup_needed) step = status.user ? 1 : -1;
  if (step === -1) { location.href = "/login"; return; }

  function frame(title, body, n) {
    view.innerHTML = `<div class="auth card pad" style="max-width:460px">
      <div class="logo" aria-label="PaperJay"><svg viewBox="0 0 712 200" role="img"><title>PaperJay</title><use href="#pj-logo"/></svg></div>
      <div class="steps">${[0, 1, 2].map((i) => `<i class="${i <= n ? "on" : ""}"></i>`).join("")}</div>
      <h1 style="font-size:19px">${title}</h1>${body}</div>`;
  }

  function account() {
    frame("Welcome. First, your account", `<form id="f" style="display:grid;gap:14px">
      <div class="muted small">This first account can do everything, including adding the rest of the household later.</div>
      <label class="field">Your name<input class="input" name="display_name" autocomplete="name" placeholder="e.g. Alex"></label>
      <label class="field">Sign-in name<input class="input" name="username" autocomplete="username" autocapitalize="none" required></label>
      <label class="field">Password (at least 8 characters)<input class="input" name="password" type="password" autocomplete="new-password" minlength="8" required></label>
      <button class="btn primary">Create account</button></form>`, 0);
    const f = $("#f", view);
    f.display_name.focus();
    f.display_name.oninput = () => { if (!f.username.dataset.touched) f.username.value = f.display_name.value.trim().toLowerCase().replace(/\s+/g, "."); };
    f.username.oninput = () => (f.username.dataset.touched = "1");
    f.onsubmit = async (e) => {
      e.preventDefault();
      await api("/api/auth/setup", { method: "POST", body: { display_name: f.display_name.value, username: f.username.value, password: f.password.value } });
      reading();
    };
  }

  async function reading() {
    const [langs, s] = await Promise.all([api("/api/ocr/languages"), api("/api/settings")]);
    const names = { eng: "English", deu: "German", fra: "French", spa: "Spanish", ita: "Italian", nld: "Dutch", por: "Portuguese", pol: "Polish", swe: "Swedish", dan: "Danish", nor: "Norwegian", fin: "Finnish", ces: "Czech", tur: "Turkish", ukr: "Ukrainian", rus: "Russian", jpn: "Japanese", chi_sim: "Chinese (simplified)" };
    const chosen = new Set(s.ocr_language.split("+"));
    frame("How are your documents written?", `<form id="f" style="display:grid;gap:14px">
      <div class="field">Languages to read<div class="chips">${langs.map((l) => `<label class="chip"><input type="checkbox" name="lang" value="${esc(l)}" ${chosen.has(l) ? "checked" : ""}> ${esc(names[l] || l)}</label>`).join("")}</div>
        <span class="faint">Choose only the ones you need: each extra language makes reading slower.</span></div>
      <label class="field">Dates like 03/04/2025 mean<select class="input" name="date_order"><option value="MDY">March 4 (US)</option><option value="DMY">3 April (most other countries)</option></select></label>
      <button class="btn primary">Next</button></form>`, 1);
    const f = $("#f", view);
    f.date_order.value = s.date_order;
    f.onsubmit = async (e) => {
      e.preventDefault();
      const ocr_language = [...f.querySelectorAll("[name=lang]:checked")].map((x) => x.value).join("+") || "eng";
      await api("/api/settings", { method: "PUT", body: { ocr_language, date_order: f.date_order.value } });
      ai();
    };
  }

  function ai() {
    frame("Add an AI? (optional)", `<form id="f" style="display:grid;gap:14px">
      <div class="muted small">With a local AI server, new documents are filed for you and questions get written answers. Without one, everything else still works. You can change this any time in Settings.</div>
      <label class="field">AI server<select class="input" name="ai_provider"><option value="off">No AI for now</option><option value="ollama">Ollama</option><option value="openai">OpenAI-compatible</option></select></label>
      <label class="field" id="urlField" hidden>Address<input class="input" name="ai_url" value="http://localhost:11434"></label>
      <label class="field" id="modelField" hidden>Model<input class="input" name="ai_model" value="qwen2.5:3b-instruct"></label>
      <div id="out" class="small"></div>
      <div class="row"><button class="btn primary">Finish</button><button type="button" class="btn" id="test" hidden>Test connection</button></div></form>`, 2);
    const f = $("#f", view);
    const sync = () => { const on = f.ai_provider.value !== "off"; $("#urlField", view).hidden = $("#modelField", view).hidden = $("#test", view).hidden = !on; };
    f.ai_provider.onchange = sync;
    const values = () => ({ ai_provider: f.ai_provider.value, ai_url: f.ai_url.value.trim(), ai_model: f.ai_model.value.trim() });
    $("#test", view).onclick = async () => {
      $("#out", view).textContent = "Testing…";
      const r = await api("/api/settings/ai/test", { method: "POST", body: values() });
      $("#out", view).innerHTML = r.steps.map((s) => `<div style="color:${s.ok ? "var(--good)" : "var(--bad)"}">${s.ok ? "✓" : "✕"} ${esc(s.step)}: ${esc(s.detail)}</div>`).join("");
    };
    f.onsubmit = async (e) => {
      e.preventDefault();
      if (f.ai_provider.value !== "off") await api("/api/settings", { method: "PUT", body: values() });
      toast("All set. Add your first documents.");
      location.href = "/upload";
    };
  }

  step === 0 ? account() : reading();
}
