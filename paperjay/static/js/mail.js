// Settings → Email: mailboxes PaperJay checks every night for emailed bills and statements (admin only).
import { $, api, esc, icon, ago, toast, modal, closeModal, confirmBox } from "./core.js";

const PRESETS = {
  gmail: { label: "Gmail", host: "imap.gmail.com", help: "Google account → Security → 2-Step Verification → App passwords" },
  icloud: { label: "iCloud", host: "imap.mail.me.com", help: "appleid.apple.com → Sign-In and Security → App-Specific Passwords" },
  fastmail: { label: "Fastmail", host: "imap.fastmail.com", help: "Settings → Privacy & Security → App passwords" },
  yahoo: { label: "Yahoo", host: "imap.mail.yahoo.com", help: "Account security → Generate app password" },
  other: { label: "Other", host: "", help: "Your email provider's help pages list their “IMAP” server. Most need an app password, not your normal one" },
};

export function mailHTML() {
  return `<div class="section admin-only" id="email"><div class="h"><h2>Email</h2><button class="btn sm aside" id="mailAdd">${icon("plus")} Add mailbox</button></div>
    <p class="sec-desc">PaperJay can collect bills and statements that arrive by email. Every night it looks in your mailbox
      and adds the PDFs and photos attached to them. Your emails themselves aren't changed unless you ask.</p>
    <div class="card list" id="mailList"></div>
    <div class="row small" style="margin:8px 2px;align-items:center">
      <span class="muted">Look for new emails every night at</span>
      <input class="input" type="time" id="mailTime" style="width:auto;height:30px">
      <span class="faint" id="mailTimeNote"></span></div></div>`;
}

export function mailSetup(view, me) {
  if (me?.role !== "admin") return () => {};
  const list = $("#mailList", view), time = $("#mailTime", view);
  let poll = null, data = { accounts: [], running: [] };

  async function draw() {
    data = await api("/api/mail", { quiet: true }).catch(() => data);
    time.value = data.mail_time || "";
    $("#mailTimeNote", view).textContent = data.mail_time ? "" : "Off: only when you tap “Check now”";
    list.innerHTML = data.accounts.map((a) => {
      const busy = data.running.includes(a.id);
      const last = busy ? "Checking now…" : a.last_run_at ? `${a.last_ok ? "" : "✕ "}${a.last_message} · ${ago(new Date(a.last_run_at * 1000).toISOString())}` : "Not checked yet";
      const filters = [a.from_filter && `only from ${a.from_filter}`, a.subject_filter && `subject mentions ${a.subject_filter}`, a.unread_only && "unread only", a.email_pdf === "always" ? "the email too" : a.email_pdf === "no_attachment" ? "the email when nothing is attached" : ""].filter(Boolean).join(" · ");
      return `<div class="item"><span class="main"><div class="t">${esc(a.name)} <span class="faint small">${esc(a.folder)}${a.enabled ? "" : " · not checked at night"}</span></div>
        <div class="sub" style="${a.last_ok === false && !busy ? "color:var(--bad)" : ""}">${esc(last)}</div>
        ${filters ? `<div class="sub faint">${esc(filters)}</div>` : ""}</span>
        <button class="btn ghost sm" data-check="${a.id}" ${busy ? "disabled" : ""}>Check now</button>
        <button class="btn ghost sm" data-edit="${a.id}">Edit</button></div>`;
    }).join("") || `<div class="empty" style="padding:14px">No mailboxes yet. Add the one your bills and statements arrive in.</div>`;
    clearInterval(poll); poll = null;
    if (data.running.length) poll = setInterval(draw, 2500);
  }

  time.onchange = async () => { await api("/api/settings", { method: "PUT", body: { mail_time: time.value } }); toast(time.value ? `Mailboxes are checked every night at ${time.value}` : "Nightly check turned off"); draw(); };
  list.onclick = async (e) => {
    const c = e.target.closest("[data-check]"), ed = e.target.closest("[data-edit]");
    if (c) { await api(`/api/mail/accounts/${c.dataset.check}/check`, { method: "POST" }); draw(); }
    if (ed) edit(data.accounts.find((a) => a.id === +ed.dataset.edit));
  };
  $("#mailAdd", view).onclick = () => wizard();

  // ---------- adding a mailbox: email address → app password → pick senders ----------
  function wizard(start = "") {
    let info = null, acc = null, found = null;
    const box = modal(`<div class="wiz" style="display:grid;gap:14px;max-height:75vh;overflow:auto;padding:2px"></div>`);
    const root = box.querySelector(".wiz");
    const step = (n, title, body) => {
      root.innerHTML = `<div class="faint small">Step ${n} of 3</div><h1 style="font-size:17px;margin:0">${title}</h1>${body}`;
      root.scrollTop = 0;
    };
    const err = (el, msg) => { el.innerHTML = msg ? `<span style="color:var(--bad)">✕ ${esc(msg[0].toUpperCase() + msg.slice(1))}</span>` : ""; el.scrollIntoView({ block: "nearest" }); };

    function one() {
      step(1, "Which email address do your bills arrive at?", `<form style="display:grid;gap:12px">
        <input class="input" name="email" type="email" autocomplete="email" autocapitalize="none" placeholder="you@example.com" value="${esc(start)}" required>
        <div class="help">PaperJay works out the rest. Next you'll make a password just for PaperJay, so it never needs your real one.</div>
        <div data-out class="small"></div>
        <div class="acts"><button type="button" class="btn ghost" data-manual>Set it up by hand</button><span style="flex:1"></span>
          <button type="button" class="btn" data-x>Cancel</button><button class="btn primary">Continue</button></div></form>`);
      const f = root.querySelector("form");
      f.email.focus();
      root.querySelector("[data-x]").onclick = closeModal;
      root.querySelector("[data-manual]").onclick = () => edit(null);
      f.onsubmit = async (e) => {
        e.preventDefault();
        const btn = f.querySelector("button.primary"); btn.disabled = true; btn.textContent = "Looking it up…";
        try { info = await api("/api/mail/discover", { method: "POST", body: { email: f.email.value.trim() }, quiet: true }); start = info.email; two(); }
        catch (x) { err(root.querySelector("[data-out]"), x.message); btn.disabled = false; btn.textContent = "Continue"; }
      };
    }

    function two() {
      const steps = `<ol class="steps">${info.steps.map((t, i) => `<li><span class="n">${i + 1}</span><div>${esc(t)}</div></li>`).join("")}</ol>`;
      if (info.unsupported) {
        step(2, `${esc(info.label)} can't be added yet`, `${steps}
          <div class="acts"><button type="button" class="btn" data-back>Use another address</button><button type="button" class="btn primary" data-x>Close</button></div>`);
        root.querySelector("[data-back]").onclick = one; root.querySelector("[data-x]").onclick = closeModal;
        return;
      }
      const normal = info.password === "normal";
      step(2, normal ? `Allow PaperJay into ${esc(info.label)}` : `Make a password for PaperJay`, `
        <div class="muted small">${normal ? "" : `${esc(info.label)} wants programs like PaperJay to use their own password, so your real one stays private. It takes about a minute:`}</div>
        ${steps}
        ${info.url ? `<div><a class="btn" href="${esc(info.url)}" target="_blank" rel="noopener">${normal ? `Open ${esc(info.label)}` : "Open the app passwords page"} ↗</a></div>` : ""}
        <form style="display:grid;gap:12px">
          <label class="field">${normal ? "Your password" : "Paste the password here"}<input class="input" name="password" type="password" autocomplete="off" required></label>
          <details class="adv" ${info.found ? "" : "open"}><summary>Server details${info.found ? " (filled in for you)" : ""}</summary>
            <div style="display:grid;gap:10px;margin-top:10px">
              <label class="field">Mail server (IMAP)<input class="input" name="host" autocapitalize="none" value="${esc(info.host || "")}"></label>
              <div class="row"><label class="field" style="flex:1">Port<input class="input" name="port" type="number" value="${info.port}"></label>
                <label class="field" style="flex:2">Encryption<select class="input" name="security"><option value="ssl">SSL/TLS</option><option value="starttls">STARTTLS</option><option value="none">None (not safe)</option></select></label></div>
              <label class="field">Sign-in name<input class="input" name="username" autocapitalize="none" value="${esc(info.username)}"></label></div></details>
          <div data-out class="small"></div>
          <div class="acts"><button type="button" class="btn ghost" data-back>Back</button><span style="flex:1"></span><button class="btn primary">Sign in</button></div></form>`);
      const f = root.querySelector("form");
      f.security.value = info.security;
      root.querySelector("[data-back]").onclick = one;
      f.onsubmit = async (e) => {
        e.preventDefault();
        const btn = f.querySelector("button.primary"); btn.disabled = true; btn.textContent = "Signing in…";
        const pw = info.provider === "google" ? f.password.value.replace(/\s+/g, "") : f.password.value;  // Google shows it in groups of four
        acc = { name: info.email, host: f.host.value.trim(), port: +f.port.value || 993, security: f.security.value,
          username: f.username.value.trim(), password: pw, folder: "INBOX" };
        try {
          const r = await api("/api/mail/senders", { method: "POST", body: acc, quiet: true });
          if (!r.ok) throw new Error(r.message);
          found = r; three();
        } catch (x) { err(root.querySelector("[data-out]"), x.message); btn.disabled = false; btn.textContent = "Sign in"; }
      };
    }

    function three() {
      const list = found.senders;
      step(3, "Which senders should PaperJay collect from?", `<form style="display:grid;gap:12px">
        <div class="muted small" style="color:var(--good)">✓ Signed in.</div>
        ${list.length ? `<div class="muted small">In the last ${Math.round(found.days / 30)} months, ${found.with_documents} email${found.with_documents === 1 ? "" : "s"} had a PDF or photo attached.
          Untick anything that isn't a bill, statement or other paperwork (shops' adverts, newsletters).</div>
          <div class="card list senders">${list.map((x, i) => `<label class="item check" style="align-items:flex-start"><input type="checkbox" value="${i}" checked>
            <span class="main"><div class="t">${esc(x.name)} <span class="faint small">${x.count} email${x.count === 1 ? "" : "s"}</span></div>
            <div class="sub">${esc(x.address)}${x.subject ? ` · “${esc(x.subject)}”` : ""}</div></span></label>`).join("")}</div>
          <label class="check"><input type="checkbox" name="everyone"><span>Also take documents from new senders<div class="help">Any email with a PDF or photo attached, from anyone. Handy at first; you can narrow it later.</div></span></label>`
        : `<div class="muted small">No emails with a PDF or photo attached in the last ${Math.round(found.days / 30)} months, so there's nobody to pick yet.
          PaperJay will take documents from any new email that has one; you can narrow it later with Edit.</div>`}
        <label class="field">The email itself<select class="input" name="email_pdf"><option value="never">Only take its attachments</option><option value="no_attachment">Save it as a PDF when nothing is attached</option><option value="always">Always save it as a PDF too</option></select></label>
        <label class="field">After taking the attachments<select class="input" name="after"><option value="keep">Leave the email as it is</option><option value="read">Mark the email as read</option></select></label>
        <div data-out class="small"></div>
        <div class="acts"><button type="button" class="btn ghost" data-back>Back</button><span style="flex:1"></span><button class="btn primary">Start collecting</button></div></form>`);
      const f = root.querySelector("form");
      root.querySelector("[data-back]").onclick = two;
      f.onsubmit = async (e) => {
        e.preventDefault();
        const picked = [...f.querySelectorAll(".senders input:checked")].map((c) => list[+c.value].address);
        const everyone = !list.length || f.everyone?.checked;
        if (!everyone && !picked.length) return err(root.querySelector("[data-out]"), "Tick at least one sender, or “Also take documents from new senders”.");
        const btn = f.querySelector("button.primary"); btn.disabled = true; btn.textContent = "Saving…";
        try {
          const made = await api("/api/mail/accounts", { method: "POST", quiet: true, body: { ...acc, after: f.after.value, email_pdf: f.email_pdf.value,
            from_filter: everyone ? "" : picked.join(", "), look_back_days: found.days, enabled: true } });
          await api(`/api/mail/accounts/${made.id}/check`, { method: "POST", quiet: true }).catch(() => {});
          closeModal(); draw();
          toast(everyone ? "Mailbox added. Collecting its documents now." : `Mailbox added. Collecting documents from ${picked.length} sender${picked.length === 1 ? "" : "s"} now.`);
        } catch (x) { err(root.querySelector("[data-out]"), x.message); btn.disabled = false; btn.textContent = "Start collecting"; }
      };
    }
    one();
  }

  function edit(acc) {
    const a = acc || { port: 993, security: "ssl", folder: "INBOX", look_back_days: 30, after: "keep", enabled: true };
    const preset = Object.entries(PRESETS).find(([k, p]) => p.host && p.host === a.host)?.[0] || (acc ? "other" : "gmail");
    const box = modal(`<h1 style="font-size:17px">${acc ? "Edit mailbox" : "Add a mailbox"}</h1>
      <form style="display:grid;gap:12px;max-height:70vh;overflow:auto;padding:2px">
        <label class="field">Your email provider<select class="input" name="preset">${Object.entries(PRESETS).map(([k, p]) => `<option value="${k}">${p.label}</option>`).join("")}</select></label>
        <div class="row" style="align-items:flex-end" data-server>
          <label class="field" style="flex:3;min-width:160px">Mail server (IMAP)<input class="input" name="host" placeholder="imap.example.com" autocapitalize="none"></label>
          <label class="field" style="flex:1;min-width:70px">Port<input class="input" name="port" type="number" min="1" max="65535"></label>
          <label class="field" style="flex:1;min-width:110px">Encryption<select class="input" name="security"><option value="ssl">SSL/TLS (usual, port 993)</option><option value="starttls">STARTTLS (port 143)</option><option value="none">None (not safe)</option></select></label></div>
        <label class="field">Your email address<input class="input" name="username" autocapitalize="none" autocomplete="off" required></label>
        <label class="field">App password <span class="faint small" data-help></span><input class="input" name="password" type="password" autocomplete="new-password" required></label>
        <div class="row"><button type="button" class="btn sm" data-test>Check the sign-in</button><span class="small" data-out></span></div>
        <label class="field">Folder to look in<input class="input" name="folder" list="mailFolders"><span class="help">INBOX is your main inbox. After “Check the sign-in” you can pick from your folders.</span></label>
        <datalist id="mailFolders"></datalist>
        <label class="field">Only emails from<input class="input" name="from_filter" placeholder="bills@acme.com, fastnet.net" autocapitalize="none"><span class="help">Senders whose attachments you want, separated by commas. A name after the @ (like fastnet.net) covers everyone at that company. Leave empty to take attachments from everyone.</span></label>
        <label class="field">Only if the subject mentions<input class="input" name="subject_filter" placeholder="invoice, statement, bill"><span class="help">Any one of these words is enough. Leave empty for any subject.</span></label>
        <div class="row" style="align-items:flex-end">
          <label class="field" style="flex:1">Include emails from the last … days<input class="input" name="look_back_days" type="number" min="1" max="3650"></label>
          <label class="check" style="flex:2"><input type="checkbox" name="unread_only"><span>Only unread emails</span></label></div>
        <label class="field">The email itself<select class="input" name="email_pdf"><option value="never">Only take its attachments</option><option value="no_attachment">Save it as a PDF when nothing is attached</option><option value="always">Always save it as a PDF too</option></select><span class="help">For bills and statements written in the email instead of attached. The PDF shows the sender, date, subject and text (no pictures).</span></label>
        <label class="field">After taking the attachments<select class="input" name="after"><option value="keep">Leave the email as it is</option><option value="read">Mark the email as read</option><option value="move">Move the email to a folder</option></select></label>
        <label class="field" data-move>Move it to this folder<input class="input" name="move_to" list="mailFolders" placeholder="PaperJay"><span class="help">Make the folder in your email app first.</span></label>
        <label class="field">A name for this mailbox<input class="input" name="name" placeholder="e.g. Bills inbox"><span class="help">Only shown here, to tell your mailboxes apart.</span></label>
        <label class="check"><input type="checkbox" name="enabled"><span>Check it every night</span></label>
        <div class="help">Only emails with a PDF or photo attached are used, and each email only once. The app password stays on your own PaperJay (and its backups) and isn't shown again.</div>
        <div class="acts">${acc ? `<button type="button" class="btn ghost danger" data-del>Remove</button><span style="flex:1"></span>` : ""}
          <button type="button" class="btn" data-x>Cancel</button><button class="btn primary">${acc ? "Save" : "Add"}</button></div>
      </form>`);
    const f = box.querySelector("form"), out = box.querySelector("[data-out]");
    for (const k of ["host", "port", "security", "username", "password", "folder", "from_filter", "subject_filter", "look_back_days", "after", "move_to", "name"]) f[k].value = a[k] ?? "";
    f.email_pdf.value = a.email_pdf || "never";
    f.unread_only.checked = !!a.unread_only; f.enabled.checked = a.enabled !== false; f.preset.value = preset;
    const sync = () => {
      const p = PRESETS[f.preset.value];
      box.querySelector("[data-help]").textContent = p.help ? `(${p.help})` : "";
      box.querySelector("[data-server]").hidden = f.preset.value !== "other";
      box.querySelector("[data-move]").hidden = f.after.value !== "move";
    };
    f.preset.onchange = () => { const p = PRESETS[f.preset.value]; if (p.host) { f.host.value = p.host; f.port.value = 993; f.security.value = "ssl"; } sync(); };
    f.after.onchange = sync;
    if (!acc) f.preset.onchange(); else sync();
    const values = () => ({ ...(acc ? { id: acc.id } : {}), name: f.name.value.trim() || f.username.value.trim(), host: f.host.value.trim(), port: +f.port.value || 993,
      security: f.security.value, username: f.username.value.trim(), password: f.password.value, folder: f.folder.value.trim() || "INBOX",
      from_filter: f.from_filter.value, subject_filter: f.subject_filter.value, look_back_days: +f.look_back_days.value || 30,
      unread_only: f.unread_only.checked, after: f.after.value, move_to: f.move_to.value.trim(), enabled: f.enabled.checked,
      email_pdf: f.email_pdf.value });
    box.querySelector("[data-test]").onclick = async (e) => {
      e.target.disabled = true; out.innerHTML = `<span class="faint">Signing in…</span>`;
      try {
        const r = await api("/api/mail/test", { method: "POST", body: values(), quiet: true });
        if (!r.ok) { out.innerHTML = `<span style="color:var(--bad)">✕ ${esc(r.message)}</span>`; return; }
        box.querySelector("#mailFolders").innerHTML = r.folders.map((x) => `<option value="${esc(x)}">`).join("");
        out.innerHTML = `<span style="color:var(--good)">✓ Signed in. ${r.emails} email${r.emails === 1 ? "" : "s"} in the last ${values().look_back_days} days; ${r.matching}${r.checked < r.emails ? ` of the newest ${r.checked}` : ""} match what you chose below.</span>`;
      } catch (err) { out.innerHTML = `<span style="color:var(--bad)">✕ ${esc(err.message)}</span>`; }
      finally { e.target.disabled = false; }
    };
    box.querySelector("[data-x]").onclick = closeModal;
    const del = box.querySelector("[data-del]");
    if (del) del.onclick = async () => {
      if (!await confirmBox("Remove this mailbox?", "Documents already taken from it stay. Nothing changes in the mailbox.", { ok: "Remove", danger: true })) return;
      await api(`/api/mail/accounts/${acc.id}`, { method: "DELETE" }); closeModal(); draw();
    };
    f.onsubmit = async (e) => {
      e.preventDefault();
      const btn = f.querySelector("button.primary"); btn.disabled = true;
      try {
        const body = values();
        if (acc) await api(`/api/mail/accounts/${acc.id}`, { method: "PATCH", body });
        else await api("/api/mail/accounts", { method: "POST", body });
        closeModal(); toast(acc ? "Saved" : "Mailbox added. Tap “Check now” to collect what's already there."); draw();
      } catch { btn.disabled = false; }
    };
  }

  draw();
  return () => clearInterval(poll);
}
