// Settings: a list of sections (/settings); each opens on its own page (/settings/<key>) with a back button.
import { mailHTML, mailSetup } from "./mail.js";
import { $, $$, api, esc, icon, ago, toast, refreshAI, refreshUpdateNotice, plural, me, confirmBox, promptBox, modal, closeModal, signOut, go } from "./core.js";

export const nav = "settings";

const ROLE_NAMES = { admin: "Can do everything", member: "Can add and edit", readonly: "Can only look" };
// Tesseract's language codes, as people know them
const LANG_NAMES = { eng: "English", deu: "German", fra: "French", spa: "Spanish", ita: "Italian", nld: "Dutch", por: "Portuguese",
  pol: "Polish", ukr: "Ukrainian", rus: "Russian", ces: "Czech", slk: "Slovak", hun: "Hungarian", ron: "Romanian", bul: "Bulgarian",
  hrv: "Croatian", srp: "Serbian", slv: "Slovenian", sqi: "Albanian", ell: "Greek", tur: "Turkish", swe: "Swedish", nor: "Norwegian",
  dan: "Danish", fin: "Finnish", isl: "Icelandic", est: "Estonian", lav: "Latvian", lit: "Lithuanian", cat: "Catalan", eus: "Basque",
  glg: "Galician", gle: "Irish", cym: "Welsh", ara: "Arabic", heb: "Hebrew", fas: "Persian", hin: "Hindi", ben: "Bengali",
  urd: "Urdu", tha: "Thai", vie: "Vietnamese", ind: "Indonesian", msa: "Malay", tgl: "Filipino", jpn: "Japanese", kor: "Korean",
  chi_sim: "Chinese (simplified)", chi_tra: "Chinese (traditional)", afr: "Afrikaans", swa: "Swahili" };

// Settings is a list; each entry opens its own page at /settings/<key> with a back button.
export const SECTIONS = [
  { key: "account", color: "blue", icon: "user", title: "Your account", sub: "Your password, your calendar link and access keys for apps" },
  { key: "assistants", color: "purple", icon: "ask", title: "AI assistants (MCP)", sub: "Let Claude or another AI assistant find, read and file your documents, with the permissions you choose" },
  { key: "people", color: "green", icon: "users", title: "People", sub: "Who in your household can use PaperJay, and what each person may do", admin: true },
  { key: "household", color: "orange", icon: "users", title: "Household", sub: "The people your documents are about, so search and questions understand them", admin: true },
  { key: "reading", color: "teal", icon: "eye", title: "Reading documents", sub: "The languages your documents are in, and how dates are written", admin: true },
  { key: "ai", color: "purple", icon: "sparkle", title: "Smart filing (AI)", sub: "An AI program on your own computer that names and sorts documents, and smart search", admin: true },
  { key: "questions", color: "blue", icon: "ask", title: "Questions", sub: "Example questions on the Ask page, and the words your household uses", admin: true },
  { key: "reminders", color: "amber", icon: "calendar", title: "Reminders", sub: "Bills that are due and things that expire", admin: true },
  { key: "filing", color: "pink", icon: "redo", title: "Filing", sub: "Re-file every document after you've tidied up", admin: true },
  { key: "fields", color: "teal", icon: "rows", title: "Your own fields", sub: "Things you want on every document, like a policy number or a licence plate", admin: true },
  { key: "keeping", color: "red", icon: "trash", title: "Keeping paper", sub: "How long to keep each kind of paper before it can be shredded", admin: true },
  { key: "paper", color: "amber", icon: "archive", title: "Paper shelf", sub: "Number the paper originals you keep, and scan stacks with separator sheets", admin: true },
  { key: "email", color: "orange", icon: "mail", title: "Email", sub: "Collect bills and statements that arrive by email", admin: true },
  { key: "appearance", color: "blue", icon: "sun", title: "Appearance", sub: "Light or dark" },
  { key: "updates", color: "green", icon: "download", title: "Updates", sub: "New versions of PaperJay with fixes and new features", admin: true },
  { key: "import", color: "teal", icon: "upload", title: "Moving in and backing up", sub: "Bring documents over from Paperless-ngx, or download a copy of everything", admin: true },
  { key: "feedback", color: "red", icon: "down", title: "Answers marked wrong", sub: "Answers someone gave a 👎, with tips to help PaperJay next time", admin: true },
];
// Old links pointed at a spot on the one long page (/settings#calendar); send them to the page that has it now.
const OLD_ANCHORS = { calendar: "account", email: "email", updates: "updates", import: "import" };

const backBar = () => `<div class="head" style="margin-bottom:6px"><a class="btn ghost sm" href="/settings">← Settings</a></div>`;
// The overview groups everything the way a phone's settings do: a card per group, a coloured tile per row
// and the current choice on the right. Pages that aren't sections (Tags & companies, Trash, the camera) sit
// in the groups too.
const LINKS = [
  { href: "/labels", color: "blue", icon: "tag", title: "Tags & companies", sub: "Rename, merge or remove the labels your documents are sorted by" },
  { href: "/trash", color: "red", icon: "trash", title: "Trash", sub: "Deleted documents, kept for 30 days in case you change your mind" },
  { href: "/shred", color: "red", icon: "trash", title: "Paper to shred", sub: "Paper you've kept long enough, by its number on the shelf" },
  { href: "/taxpack", color: "green", icon: "download", title: "Tax pack", sub: "Every document for one tax year in one zip, with a spreadsheet of the amounts" },
  { href: "/secure", color: "teal", icon: "camera", title: "Camera scanner", sub: "A one-time setup on this phone so the camera can find the page and take the photo for you" },
];
const GROUPS = [
  { title: "Documents", items: ["/labels", "fields", "/trash", "/taxpack", "filing", "paper", "keeping", "/shred"] },
  { title: "Adding documents", items: ["/secure", "email", "import"] },
  { title: "Reading and asking", items: ["reading", "ai", "questions", "feedback"] },
  { title: "People and reminders", items: ["people", "household", "reminders"] },
  { title: "Connections and look", items: ["assistants", "appearance", "updates"] },
];
const THEME_NAMES = { system: "Automatic", light: "Light", dark: "Dark" };

function settingsRow(x) {
  const href = x.href || `/settings/${x.key}`;
  return `<a class="srow" href="${href}" data-find="${esc((x.title + " " + x.sub).toLowerCase())}">
    <span class="tile t-${x.color}">${icon(x.icon)}</span><span class="t">${esc(x.title)}</span>
    <span class="v" data-val="${esc(x.key || x.href.slice(1))}"></span>${icon("chev", "i chev")}</a>`;
}

function overview(view) {
  const old = location.hash.slice(1);
  const target = OLD_ANCHORS[old] || (SECTIONS.some((x) => x.key === old) ? old : "");
  if (target) { go(`/settings/${target}${old === "calendar" ? "#calendar" : ""}`, { replace: true }); return; }
  const admin = me?.role === "admin";
  const byId = Object.fromEntries([...SECTIONS.map((x) => [x.key, x]), ...LINKS.map((x) => [x.href, x])]);
  const groups = GROUPS.map((g) => ({ ...g, rows: g.items.map((id) => byId[id]).filter((x) => x && (admin || !x.admin)) }))
    .filter((g) => g.rows.length);
  const name = me?.display_name || "You";
  view.innerHTML = `<div class="page narrow settings-home">
    <h1>Settings</h1>
    <label class="search set-search">${icon("search")}<input class="input" id="setFind" type="search" placeholder="Search settings" autocomplete="off"></label>
    <a class="card acct" href="/settings/account" data-find="${esc((name + " your account password calendar link access keys sign out").toLowerCase())}">
      <span class="avatar">${esc(name.trim().charAt(0).toUpperCase() || "?")}</span>
      <span class="main"><div class="t">${esc(name)}</div><div class="sub">Password, calendar and access keys</div></span>${icon("chev", "i chev")}</a>
    ${groups.map((g) => `<section class="sgroup"><h2>${esc(g.title)}</h2><div class="card scard">${g.rows.map(settingsRow).join("")}</div></section>`).join("")}
    <div class="empty" id="setNone" hidden>No setting matches that.</div>
  </div>`;
  const find = $("#setFind", view);
  find.addEventListener("input", () => {
    const q = find.value.trim().toLowerCase();
    let any = false;
    $$("[data-find]", view).forEach((r) => { const hit = !q || r.dataset.find.includes(q) || r.closest(".sgroup")?.querySelector("h2").textContent.toLowerCase().includes(q); r.hidden = !hit; any ||= hit; });
    $$(".sgroup", view).forEach((g) => { g.hidden = !$$(".srow:not([hidden])", g).length; });
    $("#setNone", view).hidden = any;
  });
  fillValues(view, admin);
}

// The current choice on the right of each row; each one is filled in as its answer arrives.
async function fillValues(view, admin) {
  const put = (key, text, cls = "") => { const el = $(`[data-val="${key}"]`, view); if (el && text) { el.textContent = text; el.className = "v " + cls; } };
  put("appearance", THEME_NAMES[localStorage.getItem("pj-theme") || "system"]);
  const get = (path) => api(path, { quiet: true }).catch(() => null);
  get("/api/settings").then((s) => {
    if (!s) return;
    put("reading", (s.ocr_language || "eng").split("+").map((c) => LANG_NAMES[c] || c).join(", "));
    put("ai", s.ai_provider && s.ai_provider !== "off" ? "On" : "Off");
    put("reminders", `${s.reminder_days} days ahead`);
    put("paper", s.paper_numbers ? "On" : "Off");
    put("keeping", plural((s.keep_rules || []).length, "rule"));
  });
  get("/api/summary").then((s) => { if (!s) return; put("trash", s.trash ? plural(s.trash, "document") : "Empty"); put("shred", s.shred ? plural(s.shred, "paper") : "Nothing", s.shred ? "warn" : ""); });
  get("/api/fields").then((f) => f && put("fields", f.length ? plural(f.length, "field") : "None"));
  get("/api/auth/tokens").then((ts) => { if (ts) { const n = ts.filter((t) => t.permissions).length; put("assistants", n ? plural(n, "key") : "Not set up"); } });
  get("/healthz").then((h) => h && put("updates", "Version " + h.version));
  if (!admin) return;
  get("/api/update/notice").then((n) => n?.show && put("updates", "New: " + n.latest, "new"));
  get("/api/users").then((u) => u && put("people", plural(u.filter((x) => !x.disabled).length, "person", "people")));
  get("/api/mail").then((m) => m && put("email", m.accounts.length ? plural(m.accounts.length, "mailbox", "mailboxes") : "Off"));
  get("/api/feedback").then((f) => f && f.down && put("feedback", String(f.down), "warn"));
}

export async function show(view, params) {
  const sec = params?.section || "";
  if (!sec) return overview(view);
  const [s, status] = await Promise.all([api("/api/settings"), sec === "ai" ? api("/api/ai/status", { quiet: true }).catch(() => null) : null]);
  const theme = localStorage.getItem("pj-theme") || "system";
  const HTML = {
    account: () => `<div class="section"><div class="h"><h2>Your account</h2><span class="aside">${esc(me?.display_name || "")} · ${esc(ROLE_NAMES[me?.role] || "")}</span></div>
    <p class="sec-desc">Your password, two-step sign-in, and adding your reminders to your phone's calendar.</p>
    <div class="card pad" style="display:grid;gap:16px">
      <form id="pwForm" style="display:grid;gap:8px">
        <b>Change your password</b>
        <div class="row" style="align-items:flex-start">
          <label class="field" style="flex:1;min-width:160px">Current password<input class="input" type="password" name="current" autocomplete="current-password"></label>
          <label class="field" style="flex:1;min-width:160px">New password<input class="input" type="password" name="new" autocomplete="new-password" minlength="8"><span class="help">At least 8 characters.</span></label></div>
        <div class="help">You'll stay signed in here, but need to sign in again on your other devices.</div>
        <div><button class="btn">Change password</button></div></form>
      <div id="twoStep" style="display:grid;gap:8px"><b>Two-step sign-in</b>
        <div class="help">After your password, PaperJay also asks for a code from an authenticator app on your phone (Google or
          Microsoft Authenticator, Aegis, 1Password, Bitwarden, iPhone Passwords…), so a guessed or leaked password alone can't open your
          documents.</div><div id="tsOut"></div></div>
      <div id="calendar" style="display:grid;gap:8px"><b>Reminders in your phone's calendar</b>
        <div class="help">Bills that are due and things that expire (insurance, passports, warranties) show up as events in your phone's calendar,
          with reminders. The link only shows those dates; nobody can open your documents with it.</div>
        <div><button class="btn sm" id="calBtn">${icon("calendar")} Get my calendar link</button></div><div id="calOut"></div></div>
      <details class="adv"><summary>Advanced: access keys for apps and shortcuts</summary>
        <div style="display:grid;gap:8px;margin-top:10px">
          <div class="help">Only needed if you set up an iPhone Shortcut, a scanner or another app to send documents to PaperJay.
            A key works like your password, so give each one a name you'll recognise and remove it when you stop using that app.</div>
          <div class="list card" id="tokens"></div><div><button class="btn sm" id="tokBtn">${icon("plus")} New access key</button></div></div></details>
      <div><button class="btn ghost sm" id="outBtn">Sign out</button></div>
    </div></div>`,
    people: () => `<div class="section"><div class="h"><h2>People</h2><button class="btn sm aside" id="addPerson">${icon("plus")} Add someone</button></div>
      <p class="sec-desc">Who in your household can use PaperJay, and what each person may do. Everyone sees the same documents.</p>
      <div class="card list people" id="people"></div>
      <div class="help" style="margin:8px 2px"><b>Can do everything</b>: also change these settings and add people.
        <b>Can add and edit</b>: add, sort and change documents. <b>Can only look</b>: browse, search and ask questions.</div></div>`,
    household: () => `<div class="section"><div class="h"><h2>Household</h2><button class="btn sm aside" id="addHousehold">${icon("plus")} Add a person</button></div>
      <p class="sec-desc">The people your documents are about, so search and questions understand them. A person can be
        named by any of their other names (“Dad”, a name their older papers use), and linking a person to an account
        makes “my papers” mean theirs when they ask.</p>
      <div class="card list" id="household"></div>
      <div style="margin-top:10px"><button class="btn primary sm" id="saveHousehold">Save household</button></div></div>`,
    reading: () => `<div class="section"><div class="h"><h2>Reading documents</h2></div>
    <p class="sec-desc">How PaperJay reads the text in your scans, photos and PDFs.</p>
    <form class="card pad" id="readForm" style="display:grid;gap:16px">
      <div class="field">Languages your documents are written in<div class="help">Tick every language you get letters in. Fewer languages read faster and more accurately.</div><div class="chips" id="langs"></div></div>
      <label class="field">How dates are written in your documents<select class="input" name="date_order"><option value="MDY">Month first: 03/04/2025 is March 4 (US)</option><option value="DMY">Day first: 03/04/2025 is 3 April (most other countries)</option></select></label>
      <div><button class="btn primary">Save</button></div></form></div>`,
    ai: () => `<div class="section"><div class="h"><h2>Smart filing (AI)</h2><span class="aside" id="aiState"></span></div>
    <p class="sec-desc">Optional. An AI program on your own computer can name and sort new documents for you, and answer questions in full sentences.
      Without it PaperJay still reads, searches and finds everything. Your documents never leave your home.</p>
    <form class="card pad" id="aiForm" style="display:grid;gap:16px">
      <label class="field">AI program<select class="input" name="ai_provider">
        <option value="off">Off</option><option value="ollama">Ollama (recommended, free)</option><option value="openai">Another AI program (LM Studio, llama.cpp, vLLM or a similar one)</option></select>
        <span class="help">Install Ollama on a computer in your home first. <a href="https://github.com/3g15h1/paperjay/blob/main/docs/ai-models.md" target="_blank" rel="noopener" style="color:var(--accent)">How to set it up and which model to pick</a></span></label>
      <div id="aiFields" style="display:grid;gap:16px">
        <label class="field">Where the AI program runs<input class="input" name="ai_url" placeholder="http://localhost:11434">
          <span class="help">Its web address, for example http://192.168.1.10:11434 (11434 is Ollama's usual port).</span></label>
        <label class="field">AI model<input class="input" name="ai_model" list="models" placeholder="qwen2.5:3b-instruct">
          <span class="help">The model PaperJay uses to sort documents and write answers. Smaller models are quicker; bigger ones make fewer mistakes. “Check it works” lists the ones you have.</span></label>
        <datalist id="models"></datalist>
        <label class="field" id="keyField">Password for the AI program<input class="input" name="ai_api_key" type="password" autocomplete="off" placeholder="Only if it asks for one">
          <span class="help">Some AI programs and online services need a key; Ollama doesn't.</span></label>
        <label class="check"><input type="checkbox" name="ai_no_think"><span>Answer without a thinking step<div class="help">For models that think before they answer (Qwen3 and similar). Much faster; turn it off if your AI program complains.</div></span></label>
        <label class="check"><input type="checkbox" name="ai_filing"><span>Name and sort new documents for me<div class="help">Fills in the title, date, company, type and tags. It never changes anything you've set yourself, and anything it isn't sure about waits for you in Needs review.</div></span></label>
        <label class="check"><input type="checkbox" name="ai_new_correspondents"><span>Add companies it hasn't seen before<div class="help">Otherwise it only picks from the companies you already have.</div></span></label>
        <details class="adv" id="threadField"><summary>Advanced</summary><label class="field" style="margin-top:10px">Processor cores the AI may use<input class="input" name="ai_threads" type="number" min="0" max="128">
          <span class="help">Leave it at 0 and Ollama decides. Set it to the number of cores of the computer running Ollama if answers are very slow.</span></label></details>
      </div>
      <label class="field">Smart search<select class="input" name="embed_source">
        <option value="builtin">Built into PaperJay (recommended)</option><option value="ai">Use the AI program's model</option><option value="off">Off</option></select>
        <span class="help">Lets search find documents by what they mean: “electricity” also finds your power bill. The built-in one works even with the AI program off.</span></label>
      <label class="field" id="embedModelField">Smart search model<input class="input" name="embed_model" list="models" placeholder="nomic-embed-text">
        <span class="help">The AI program's embedding model, for example nomic-embed-text.</span></label>
      <div id="testOut" class="small" style="display:grid;gap:4px"></div>
      <div class="row"><button class="btn primary">Save</button><button type="button" class="btn" id="testBtn">Check it works</button></div>
    </form></div>`,
    questions: () => `<div class="section"><div class="h"><h2>Questions</h2></div>
    <p class="sec-desc">Make the Ask page fit your household.</p>
    <form class="card pad" id="askForm" style="display:grid;gap:16px">
      <label class="field">Example questions on the Ask page<textarea class="input" name="ask_suggestions" rows="4" placeholder="When does my car insurance expire?"></textarea>
        <span class="help">One per line. They're shown as suggestions you can tap.</span></label>
      <label class="field">Words your household uses<input class="input" name="ask_synonyms" placeholder="electric=acme energy; internet=fastnet">
        <span class="help">If you say “electric” but your bills say “Acme Energy”, tell PaperJay they mean the same thing: <code>electric=acme energy</code>.
          Several names go after one word with commas; separate words with a semicolon.</span></label>
      <div><button class="btn primary">Save</button></div>
    </form></div>`,
    reminders: () => `<div class="section"><div class="h"><h2>Reminders</h2></div>
    <p class="sec-desc">Bills that are due and things that expire, on the start page and in your phone's calendar.</p>
    <form class="card pad" id="upForm" style="display:grid;gap:16px">
      <div class="row" style="align-items:flex-start">
        <label class="field" style="flex:1">How far ahead to show (days)<input class="input" type="number" min="1" max="3660" name="reminder_days"><span class="help">60 shows the next two months.</span></label>
        <label class="field" style="flex:1">Remind me this many days before<input class="input" name="alert_days" placeholder="14, 3, 1, 0"><span class="help">Separate with commas; 0 is the day itself.</span></label></div>
      <label class="field">Tags that mean a bill is paid<input class="input" name="done_tags" placeholder="paid"><span class="help">Bills with one of these tags no longer get reminders.</span></label>
      <div><button class="btn primary">Save</button></div>
    </form></div>`,
    filing: () => `<div class="section"><div class="h"><h2>Filing</h2></div>
    <p class="sec-desc">PaperJay files each document when it arrives. After you've tidied up or changed a lot of filing by hand, re-file everything so older documents learn from it too. Anything you set yourself is never changed.</p>
    <div class="card pad" style="display:grid;gap:12px">
      <label class="check" id="refileAiWrap" hidden><input type="checkbox" id="refileAi"><span>Also let the AI read every document again<div class="help" id="refileAiHelp"></div></span></label>
      <div class="row" style="gap:10px;flex-wrap:wrap"><button class="btn" id="refileAll">${icon("redo")} Re-file everything</button><span class="help" id="refileNote"></span></div>
    </div></div>`,
    paper: () => `<div class="section"><div class="h"><h2>Paper shelf</h2></div>
    <p class="sec-desc">Number the paper originals you keep, so a document in PaperJay points at a sheet on the shelf — and the sheet points back.</p>
    <form class="card pad" id="paperForm" style="display:grid;gap:16px">
      <label class="check"><input type="checkbox" name="paper_numbers"><span>Number the paper I keep<div class="help">Uploading and scanning then offer “Keeping the paper?” with the next free number. Write it on the original (or print labels from the Library — selected documents, or a sheet of numbers ahead of time). Find a sheet by typing its number, like #124, in the Library search or in Ask.</div></span></label>
      <label class="check"><input type="checkbox" name="barcodes"><span>Read separator sheets and labels when scanning<div class="help">Scan a whole stack of documents in one go with a separator sheet between them, and PaperJay makes one document of each (the separator pages are left out). A blank label you printed ahead of time, stuck on the first page, gives the document its number.</div></span></label>
      <div class="row" style="gap:8px;flex-wrap:wrap"><a class="btn sm" href="/api/paper/separator.pdf?paper=letter" target="_blank" rel="noopener">${icon("download")} Separator sheet (Letter)</a>
        <a class="btn sm" href="/api/paper/separator.pdf?paper=a4" target="_blank" rel="noopener">${icon("download")} Separator sheet (A4)</a></div>
      <div><button class="btn primary">Save</button></div>
    </form></div>`,
    fields: () => `<div class="section"><div class="h"><h2>Your own fields</h2></div>
    <p class="sec-desc">Add the things you want to see and search on your documents: a policy number, a licence plate, a member
      number, a renewal date. Each document's page then has them. A field can fill itself from what PaperJay reads in the
      document; what you type always wins. They're searchable and are columns in a Library spreadsheet.</p>
    <div class="card list" id="flist"></div>
    <form class="card pad" id="fadd" style="display:grid;gap:10px;margin-top:12px">
      <b>Add a field</b>
      <div class="row" style="gap:8px;flex-wrap:wrap;align-items:flex-end">
        <label class="field" style="flex:2;min-width:160px">Name<input class="input" name="name" placeholder="Licence plate" required></label>
        <label class="field" style="flex:1;min-width:120px">Kind<select class="input" name="kind"><option value="text">Text</option><option value="number">Number</option><option value="money">Amount</option><option value="date">Date</option></select></label></div>
      <div class="row" style="gap:8px;flex-wrap:wrap;align-items:flex-end">
        <label class="field" style="flex:1;min-width:160px">Fill itself from<select class="input" name="from"><option value="">Nothing: I'll type it</option><option value="id">A number labelled…</option><option value="date">A date labelled…</option><option value="total">The total</option></select></label>
        <label class="field" style="flex:1;min-width:140px" id="fwords" hidden>Label words<input class="input" name="words" placeholder="policy"><span class="help" id="fwordsHelp"></span></label></div>
      <div><button class="btn primary">Add field</button></div></form></div>`,
    keeping: () => `<div class="section"><div class="h"><h2>Keeping paper</h2></div>
    <p class="sec-desc">How long to keep each kind of paper. When a paper on your shelf has been kept long enough, it shows up
      in <a href="/shred">Paper to shred</a>. The first rule that matches a document decides; a document no rule matches is
      kept for good. Only the paper is shredded: the digital copy stays in PaperJay. Categories are medical, insurance,
      utilities, shopping, home, vehicle, tax, income, travel, food and finance; kinds are receipt, invoice, bill,
      statement, insurance, tax, medical, pay, contract, id and letter; topics are the ones in Browse.</p>
    <p class="help" style="margin:-4px 2px 12px">These start from common advice for US households. What you must keep differs by country and
      situation (a business, a house sale, an audit), so check with your tax office or adviser and change them to fit.</p>
    <div class="card list" id="rules"></div>
    <div class="row" style="margin-top:12px;gap:8px;flex-wrap:wrap"><button class="btn" id="addRule">${icon("plus")} Add a rule</button>
      <span class="spacer" style="flex:1"></span><button class="btn ghost" id="resetRules">Back to the defaults</button><button class="btn primary" id="saveRules">Save</button></div></div>`,
    email: () => mailHTML(),
    assistants: () => `<div class="section"><div class="h"><h2>AI assistants (MCP)</h2></div>
      <p class="sec-desc">An AI assistant such as Claude can work with your documents through MCP: search them, read them,
        ask questions, add documents, change their filing or delete them. Make an access key for it here and choose what
        it may do. It acts as you, and never more than your account can do.</p>
      <form class="card pad" id="mcpForm" style="display:grid;gap:14px">
        <label class="field">Name<input class="input" name="name" value="Claude" maxlength="60"><span class="help">So you can tell your keys apart, e.g. “Claude on my laptop”.</span></label>
        <div style="display:grid;gap:10px"><b>It may</b>
          <label class="check"><input type="checkbox" name="perm" value="read" checked><span>Read<div class="help">Search, open and read documents, ask questions, see tags, companies and what's coming up.</div></span></label>
          <label class="check"><input type="checkbox" name="perm" value="create"><span>Create<div class="help">Add documents, notes, tags, companies and types.</div></span></label>
          <label class="check"><input type="checkbox" name="perm" value="update"><span>Update<div class="help">Change titles, dates, companies, types and tags; mark documents reviewed; bring documents back from the Trash.</div></span></label>
          <label class="check"><input type="checkbox" name="perm" value="delete"><span>Delete<div class="help">Move documents to the Trash (kept for 30 days) and delete tags, companies and types. It can never delete anything for good.</div></span></label>
          <div class="help" id="mcpRo" hidden>Your account can only look, so its keys can only read.</div>
        </div>
        <div><button class="btn primary">${icon("plus")} Make access key</button></div>
      </form>
      <div class="section"><div class="h"><h2>Keys for AI assistants</h2></div><div class="card list" id="mcpKeys"></div>
        <div class="help" style="margin:8px 2px">Removing a key cuts that assistant off straight away. The address it connects to is <code id="mcpUrl"></code>.</div></div></div>`,
    appearance: () => `<div class="section"><div class="h"><h2>Appearance</h2></div>
    <p class="sec-desc">Light or dark. “Automatic” follows your phone or computer.</p>
    <div class="card pad"><div class="seg" id="theme"><button data-v="system">Automatic</button><button data-v="dark">Dark</button><button data-v="light">Light</button></div></div></div>`,
    updates: () => `<div class="section" id="updates"><div class="h"><h2>Updates</h2></div>
    <p class="sec-desc">New versions of PaperJay with fixes and new features.</p>
    <div class="card pad" id="upd" style="display:grid;gap:10px"><span class="faint">Checking…</span></div></div>`,
    import: () => `<div class="section" id="import"><div class="h"><h2>Moving in and backing up</h2></div>
    <p class="sec-desc">Bring documents over from Paperless-ngx, or take a copy of everything with you.</p>
    <div class="card pad" style="display:grid;gap:18px">
      <form id="plForm" style="display:grid;gap:10px">
        <b>Bring documents over from Paperless-ngx</b>
        <div class="help">Copies every document with its title, date, company, type, tags and notes. Nothing changes in Paperless,
          and if you run it again it skips what's already here.</div>
        <div class="row" style="align-items:flex-end">
          <label class="field" style="flex:2;min-width:200px">Paperless web address<input class="input" name="url" placeholder="http://paperless.local:8000"></label>
          <label class="field" style="flex:2;min-width:200px">Paperless access key <span class="faint">(in Paperless: your name → My Profile → API Auth Token)</span><input class="input" name="token" type="password" autocomplete="off"></label></div>
        <div class="help">The key is only used for this one import and isn't kept.</div>
        <div class="row"><button type="button" class="btn" id="plCheck">Check first</button><button class="btn primary" id="plGo" disabled>Bring them over</button></div>
        <div id="plOut" class="small"></div>
      </form>
      <div style="display:grid;gap:8px"><b>Download a copy of everything</b>
        <div class="help">All your documents as ordinary PDF and photo files you can open anywhere, plus a list of how they're sorted.
          Keep it somewhere safe as a backup, or take it with you if you ever stop using PaperJay.</div>
        <div><a class="btn" href="/api/export.zip" download>${icon("download")} Download everything (.zip)</a></div>
        <details class="adv"><summary>Advanced: automatic copies every night</summary>
          <div class="help" style="margin-top:8px">On the computer PaperJay runs on, schedule <code>paperjay export /path/to/folder</code>
            (for example to a NAS). It only copies what changed since last time.</div></details></div>
    </div></div>`,
    feedback: () => `<div class="section"><div class="h"><h2>Answers marked wrong</h2><span class="aside" id="fbSum"></span></div>
      <p class="sec-desc">Answers someone gave a 👎, with tips on how to help PaperJay find the right document next time.</p>
      <div class="card list" id="fb"></div></div>`,
  };
  const meta = SECTIONS.find((x) => x.key === sec);
  if (!meta) { view.innerHTML = `<div class="page narrow"><div class="empty">This page doesn't exist. <a href="/settings">Back to Settings</a></div></div>`; return; }
  if (meta.admin && me?.role !== "admin") {
    view.innerHTML = `<div class="page narrow">${backBar()}<div class="empty">Only someone who can do everything can change this.</div></div>`; return;
  }
  view.innerHTML = `<div class="page narrow settings-page">${backBar()}${HTML[sec]()}</div>`;
  const els = Object.fromEntries($$("[id]", view).map((e) => [e.id, e]));

  // ---------- AI ----------
  if (sec === "ai") {
    const f = els.aiForm;
    const fill = () => {
      for (const k of ["ai_provider", "ai_url", "ai_model", "embed_model", "embed_source", "ai_api_key", "ai_threads"]) f.elements[k].value = s[k] ?? "";
      f.elements.ai_filing.checked = s.ai_filing; f.elements.ai_new_correspondents.checked = s.ai_new_correspondents;
      f.elements.ai_no_think.checked = s.ai_no_think;
      sync();
    };
    function sync() {
      const p = f.elements.ai_provider.value;
      els.aiFields.hidden = p === "off";
      els.keyField.hidden = p !== "openai"; els.threadField.hidden = p !== "ollama";
      els.embedModelField.hidden = f.elements.embed_source.value !== "ai";
      if (p === "ollama" && !f.elements.ai_url.value) f.elements.ai_url.value = "http://localhost:11434";
    }
    f.elements.ai_provider.onchange = sync; f.elements.embed_source.onchange = sync;
    const aiValues = () => ({
      ai_provider: f.elements.ai_provider.value, ai_url: f.elements.ai_url.value.trim() || "http://localhost:11434",
      ai_model: f.elements.ai_model.value.trim(), embed_model: f.elements.embed_model.value.trim(), ai_api_key: f.elements.ai_api_key.value,
      embed_source: f.elements.embed_source.value, ai_no_think: f.elements.ai_no_think.checked,
      ai_threads: +f.elements.ai_threads.value || 0, ai_filing: f.elements.ai_filing.checked, ai_new_correspondents: f.elements.ai_new_correspondents.checked,
    });
    f.onsubmit = async (e) => { e.preventDefault(); Object.assign(s, await api("/api/settings", { method: "PUT", body: aiValues() })); toast("Saved"); refreshAI(); drawStatus(); };
    els.testBtn.onclick = async () => {
      els.testOut.innerHTML = `<span class="faint">Checking… (on a computer without a graphics card the first answer can take a minute)</span>`;
      els.testBtn.disabled = true;
      try {
        const r = await api("/api/settings/ai/test", { method: "POST", body: aiValues() });
        const names = { AI: "AI", server: "AI program", "answer model": "AI model", "embedding model": "Smart search model" };
        els.testOut.innerHTML = r.steps.map((st) => `<div style="color:${st.ok ? "var(--good)" : "var(--bad)"}">${st.ok ? "✓" : "✕"} ${esc(names[st.step] || st.step)}: <span class="muted">${esc(st.detail)}</span></div>`).join("");
        $("#models", view).innerHTML = (r.models || []).map((m) => `<option value="${esc(m)}">`).join("");
      } finally { els.testBtn.disabled = false; }
    };
    async function drawStatus() {
      const a = await api("/api/ai/status", { quiet: true }).catch(() => status);
      if (!a) return;
      const smart = a.meaning ? `${a.embedded} of ${plural(a.documents, "document")} ready for smart search` : "";
      els.aiState.textContent = !a.on ? (smart ? `AI off, ${smart}` : "Off") : `${a.analyzed} of ${plural(a.documents, "document")} sorted${smart ? `, ${a.embedded} ready for smart search` : ""}`;
    }
    fill(); drawStatus();
  }

  // ---------- questions ----------
  if (sec === "questions") {
    const af = els.askForm;
    af.elements.ask_suggestions.value = (s.ask_suggestions || []).join("\n");
    af.elements.ask_synonyms.value = s.ask_synonyms || "";
    af.onsubmit = async (e) => {
      e.preventDefault();
      await api("/api/settings", { method: "PUT", body: { ask_suggestions: af.elements.ask_suggestions.value.split("\n").map((x) => x.trim()).filter(Boolean),
        ask_synonyms: af.elements.ask_synonyms.value.trim() } });
      toast("Saved");
    };
  }

  // ---------- reminders ----------
  if (sec === "reminders") {
    const uf = els.upForm;
    uf.elements.reminder_days.value = s.reminder_days;
    uf.elements.alert_days.value = (s.alert_days || []).join(", ");
    uf.elements.done_tags.value = (s.done_tags || []).join(", ");
    uf.onsubmit = async (e) => {
      e.preventDefault();
      const days = uf.elements.alert_days.value.split(",").map((x) => parseInt(x, 10)).filter((x) => x >= 0);
      await api("/api/settings", { method: "PUT", body: { reminder_days: +uf.elements.reminder_days.value || 60, alert_days: days,
        done_tags: uf.elements.done_tags.value.split(",").map((x) => x.trim()).filter(Boolean) } });
      toast("Saved");
    };
  }

  // ---------- filing: re-file everything (spec E) ----------
  if (sec === "filing") {
    api("/api/refile", { method: "POST", body: {}, quiet: true }).then((est) => {
      els.refileNote.textContent = `${plural(est.documents, "document")}. The text patterns and your own filing take seconds.`;
      if (est.ai_on) {
        els.refileAiWrap.hidden = false;
        els.refileAiHelp.textContent = `About ${est.ai_estimate.replace(/^about /, "")} for ${plural(est.documents, "document")}, in the background. Questions still work meanwhile.`;
      }
      els.refileAll.onclick = async () => {
        const withAi = est.ai_on && els.refileAi.checked;
        const ok = await confirmBox("Re-file everything?", `${plural(est.documents, "document")} will be filed again from their text and your own filing${withAi ? `, and the AI will read each one again (${est.ai_estimate})` : ""}. Anything you set yourself stays as it is.`, { ok: "Re-file" });
        if (!ok) return;
        const r = await api("/api/refile", { method: "POST", body: { confirm: true, ai: withAi } });
        toast(`Re-filing ${plural(r.queued, "document")}${r.queued_ai ? "; the AI reads them in the background" : ""}`);
      };
    }).catch(() => {});
  }

  // ---------- your own fields ----------
  if (sec === "fields") {
    const FROM = (fill) => !fill ? "You type it" : fill === "total" ? "Fills itself with the total"
      : fill.startsWith("id:") ? `Fills itself with the number labelled “${fill.slice(3)}”` : `Fills itself with the “${fill.slice(5)}” date`;
    const KIND = { text: "Text", number: "Number", money: "Amount", date: "Date" };
    const load = async () => {
      const list = await api("/api/fields");
      els.flist.innerHTML = list.map((f) => `<div class="item"><span class="main"><div class="t">${esc(f.name)}</div>
          <div class="sub">${esc(KIND[f.kind])} · ${esc(FROM(f.fill))}</div></span>
          <button class="btn ghost sm" data-ren="${f.id}">Rename</button><button class="btn ghost sm danger" data-del="${f.id}">Remove</button></div>`).join("")
        || `<div class="item"><span class="main muted">No fields yet. Add one below.</span></div>`;
    };
    const f = els.fadd;
    const syncFrom = () => {
      els.fwords.hidden = !["id", "date"].includes(f.from.value);
      f.words.placeholder = f.from.value === "date" ? "expires" : "policy";
      $("#fwordsHelp", view).textContent = f.from.value === "date" ? "due, expires, issued, paid, service or effective" : "Words in its label on the document, like policy, member or account";
    };
    f.from.onchange = syncFrom; syncFrom();
    f.onsubmit = async (e) => {
      e.preventDefault();
      const from = f.from.value, words = f.words.value.trim();
      if (["id", "date"].includes(from) && !words) { toast("Say which label to look for"); return; }
      await api("/api/fields", { method: "POST", body: { name: f.name.value, kind: f.kind.value, fill: from === "total" ? "total" : from ? `${from}:${words}` : "" } });
      f.reset(); syncFrom(); toast("Added"); load();
    };
    els.flist.onclick = async (e) => {
      const ren = e.target.closest("[data-ren]"), del = e.target.closest("[data-del]");
      if (ren) { const name = await promptBox("Rename the field", { value: ren.closest(".item").querySelector(".t").textContent }); if (name) { await api(`/api/fields/${ren.dataset.ren}`, { method: "PATCH", body: { name } }); load(); } }
      if (del && await confirmBox("Remove this field?", "It goes from every document, with the values you typed.", { ok: "Remove", danger: true })) { await api(`/api/fields/${del.dataset.del}`, { method: "DELETE" }); load(); }
    };
    load();
  }

  // ---------- keeping paper ----------
  if (sec === "keeping") {
    let rules = structuredClone(s.keep_rules || []);
    const list = (v) => (v || []).join(", ");
    const words = (v) => v.split(",").map((x) => x.trim().toLowerCase()).filter(Boolean);
    const draw = () => {
      els.rules.innerHTML = rules.map((r, i) => `<div class="item" style="display:grid;gap:8px;align-items:start">
        <div class="row" style="gap:8px;align-items:center">
          <input class="input" data-r="name" data-i="${i}" value="${esc(r.name)}" style="flex:1;min-width:160px" aria-label="Rule name">
          <button type="button" class="btn ghost sm icon" data-up="${i}" title="Check this rule earlier" aria-label="Move up" ${i ? "" : "disabled"}>↑</button>
          <button type="button" class="btn ghost sm icon" data-del="${i}" title="Remove this rule">${icon("x")}</button></div>
        <div class="row" style="gap:8px;align-items:center;flex-wrap:wrap">
          <span class="small">Keep</span>
          <select class="input" data-r="forever" data-i="${i}" style="width:auto"><option value="years">for</option><option value="forever" ${r.years == null ? "selected" : ""}>for good</option></select>
          <input class="input" type="number" min="0" max="100" data-r="years" data-i="${i}" value="${r.years ?? ""}" style="width:70px" ${r.years == null ? "hidden" : ""}>
          <span class="small" ${r.years == null ? "hidden" : ""}>years after</span>
          <select class="input" data-r="from" data-i="${i}" style="width:auto" ${r.years == null ? "hidden" : ""}><option value="year_end">the end of its year</option><option value="expires" ${r.from === "expires" ? "selected" : ""}>it expires</option></select></div>
        <div class="row small" style="gap:8px;flex-wrap:wrap">
          <label class="field" style="flex:1;min-width:140px">Categories<input class="input" data-r="categories" data-i="${i}" value="${esc(list(r.categories))}" placeholder="none"></label>
          <label class="field" style="flex:1;min-width:140px">Kinds<input class="input" data-r="kinds" data-i="${i}" value="${esc(list(r.kinds))}" placeholder="none"></label>
          <label class="field" style="flex:2;min-width:160px">Topics<input class="input" data-r="topics" data-i="${i}" value="${esc(list(r.topics))}" placeholder="none"></label></div>
      </div>`).join("") || `<div class="item"><span class="main muted">No rules: every paper is kept for good.</span></div>`;
    };
    els.rules.addEventListener("input", (e) => {
      const f = e.target.closest("[data-r]"); if (!f) return;
      const r = rules[+f.dataset.i], k = f.dataset.r;
      if (k === "name") r.name = f.value;
      if (k === "years") r.years = f.value === "" ? 0 : +f.value;
      if (k === "from") r.from = f.value;
      if (["categories", "kinds", "topics"].includes(k)) r[k] = words(f.value);
    });
    els.rules.addEventListener("change", (e) => {
      const f = e.target.closest("[data-r=forever]"); if (!f) return;
      rules[+f.dataset.i].years = f.value === "forever" ? null : 1; draw();
    });
    els.rules.onclick = (e) => {
      const up = e.target.closest("[data-up]"), del = e.target.closest("[data-del]");
      if (up) { const i = +up.dataset.up; [rules[i - 1], rules[i]] = [rules[i], rules[i - 1]]; draw(); }
      if (del) { rules.splice(+del.dataset.del, 1); draw(); }
    };
    els.addRule.onclick = () => { rules.push({ name: "New rule", years: 1, from: "year_end", categories: [], kinds: [], topics: [] }); draw(); };
    els.saveRules.onclick = async () => { Object.assign(s, await api("/api/settings", { method: "PUT", body: { keep_rules: rules } })); rules = structuredClone(s.keep_rules); draw(); toast("Saved"); };
    els.resetRules.onclick = async () => {
      if (!await confirmBox("Go back to the default rules?", "Your own rules are replaced by the ones PaperJay starts with.", { ok: "Use the defaults" })) return;
      Object.assign(s, await api("/api/settings", { method: "PUT", body: { keep_rules: null } })); rules = structuredClone(s.keep_rules); draw(); toast("Back to the defaults");
    };
    draw();
  }

  // ---------- paper shelf ----------
  if (sec === "paper") {
    const paperF = els.paperForm;
    paperF.elements.paper_numbers.checked = !!s.paper_numbers;
    paperF.elements.barcodes.checked = s.barcodes !== false;
    paperF.onsubmit = async (e) => {
      e.preventDefault();
      Object.assign(s, await api("/api/settings", { method: "PUT", body: { paper_numbers: paperF.elements.paper_numbers.checked, barcodes: paperF.elements.barcodes.checked } }));
      toast("Saved");
    };
  }

  // ---------- theme ----------
  if (sec === "appearance") {
    const setTheme = (v) => {
      localStorage.setItem("pj-theme", v);
      if (v === "system") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = v;
      els.theme.querySelectorAll("button").forEach((b) => b.classList.toggle("on", b.dataset.v === v));
    };
    els.theme.onclick = (e) => { const b = e.target.closest("[data-v]"); if (b) setTheme(b.dataset.v); };
    setTheme(theme);
  }

  // ---------- feedback ----------
  if (sec === "feedback") {
    const fb = await api("/api/feedback", { quiet: true }).catch(() => ({ items: [] }));
    const down = fb.items.filter((x) => x.vote === "down");
    els.fbSum.textContent = fb.total ? `${fb.down} marked wrong · ${fb.up} marked right` : "";
    els.fb.innerHTML = down.slice(0, 30).map((x) => `<div class="item" style="display:grid;gap:3px">
      <div class="t"><b>${esc(x.question)}</b> <span class="faint small">${ago(x.created_at)}</span></div>
      ${x.answer ? `<div class="sub" style="white-space:normal">${esc(x.answer)}</div>` : ""}
      ${x.comment ? `<div class="small" style="color:var(--warn)">“${esc(x.comment)}”</div>` : ""}
      ${x.missing.length ? `<div class="small faint">No document uses ${x.missing.map((w) => `“${esc(w)}”`).join(", ")}. If yours say it differently, add it under “Words your household uses”, e.g. <code>${esc(x.missing[0].toLowerCase())}=otherword</code>.</div>` : ""}
    </div>`).join("") || `<div class="empty">Nothing marked wrong yet. Tap 👍 or 👎 under an answer to tell PaperJay how it did.</div>`;
  }

  // ---------- account ----------
  if (sec === "account") {
    els.pwForm.onsubmit = async (e) => {
      e.preventDefault();
      await api("/api/auth/password", { method: "POST", body: { current: els.pwForm.current.value, new: els.pwForm.new.value } });
      els.pwForm.reset(); toast("Password changed. You're signed out on your other devices.");
    };
    els.outBtn.onclick = signOut;
    // two-step sign-in (totp.py)
    const codesBox = (codes) => modal(`<h1 style="font-size:17px">Your recovery codes</h1>
      <div class="muted small">If you lose your phone, sign in with one of these instead of a code. Each works once. Keep them somewhere
        safe (print them or save them in your password manager): this is the only time PaperJay shows them.</div>
      <pre class="mono" style="margin:0;padding:12px;background:var(--bg);border-radius:8px;columns:2;font-size:15px">${codes.map(esc).join("\n")}</pre>
      <div class="acts"><button class="btn" data-copy>Copy</button><button class="btn primary" data-close>I've saved them</button></div>`, { onMount: (box) => {
        box.querySelector("[data-copy]").onclick = () => navigator.clipboard.writeText(codes.join("\n")).then(() => toast("Copied"));
        box.querySelector("[data-close]").onclick = () => { closeModal(); drawTwoStep(); };
      } });
    async function drawTwoStep() {
      const st = await api("/api/auth/2fa");
      els.tsOut.innerHTML = st.enabled
        ? `<div class="row" style="gap:8px;flex-wrap:wrap;align-items:center"><span class="chip good">On</span>
            <span class="small ${st.recovery_left <= 2 ? "" : "faint"}" style="${st.recovery_left <= 2 ? "color:var(--warn)" : ""}">${plural(st.recovery_left, "recovery code")} left</span>
            <span class="spacer" style="flex:1"></span><button class="btn sm" id="tsNew">New recovery codes</button><button class="btn sm ghost danger" id="tsOff">Turn off</button></div>`
        : `<div><button class="btn sm" id="tsOn">Turn on two-step sign-in</button></div>`;
      const on = $("#tsOn", view), off = $("#tsOff", view), fresh = $("#tsNew", view);
      if (on) on.onclick = async () => {
        const password = await promptBox("Your password", { label: "To turn on two-step sign-in, type your password.", ok: "Next", type: "password" });
        if (!password) return;
        const r = await api("/api/auth/2fa/setup", { method: "POST", body: { password } });
        modal(`<h1 style="font-size:17px">Scan this with your authenticator app</h1>
          <div style="display:grid;place-items:center;background:#fff;border-radius:8px;padding:8px">${r.qr}</div>
          <div class="muted small">Can't scan? Add an account by hand with this key (time-based):</div>
          <input class="input mono" readonly value="${esc(r.secret.replace(/(.{4})/g, "$1 ").trim())}" onclick="this.select()">
          <label class="field">The 6-digit code the app shows now<input class="input" id="tsCode" inputmode="numeric" autocomplete="one-time-code" placeholder="123 456"></label>
          <div class="acts"><button class="btn ghost" data-cancel>Cancel</button><button class="btn primary" data-ok>Turn on</button></div>`, { onMount: (box) => {
            box.querySelector("[data-cancel]").onclick = closeModal;
            box.querySelector("[data-ok]").onclick = async () => {
              const res = await api("/api/auth/2fa/confirm", { method: "POST", body: { code: box.querySelector("#tsCode").value } }).catch(() => null);
              if (res) { toast("Two-step sign-in is on"); codesBox(res.recovery_codes); }
            };
          } });
      };
      if (off) off.onclick = async () => {
        const password = await promptBox("Turn off two-step sign-in?", { label: "Type your password. Signing in then needs only the password.", ok: "Turn off", type: "password" });
        if (!password) return;
        await api("/api/auth/2fa/disable", { method: "POST", body: { password } }); toast("Two-step sign-in is off"); drawTwoStep();
      };
      if (fresh) fresh.onclick = async () => {
        const password = await promptBox("New recovery codes", { label: "Type your password. Your old recovery codes stop working.", ok: "Make new codes", type: "password" });
        if (!password) return;
        const res = await api("/api/auth/2fa/recovery", { method: "POST", body: { password } }); codesBox(res.recovery_codes);
      };
    }
    drawTwoStep();
    const showSecret = (title, value, note) => modal(`<h1 style="font-size:17px">${esc(title)}</h1>
      <input class="input" readonly value="${esc(value)}" onclick="this.select()"><div class="muted small">${esc(note)}</div>
      <div class="acts"><button class="btn" data-copy>Copy</button><button class="btn primary" data-close>Done</button></div>`, { onMount: (box) => {
        box.querySelector("[data-copy]").onclick = () => navigator.clipboard.writeText(value).then(() => toast("Copied"));
        box.querySelector("[data-close]").onclick = () => { closeModal(); drawTokens(); };
      } });
    async function drawTokens() {
      const ts = await api("/api/auth/tokens");
      els.tokens.innerHTML = ts.map((t) => `<div class="item"><span class="main"><div class="t">${esc(t.name)} <span class="faint small">${esc(t.prefix)}…${t.scope === "calendar" ? " · calendar link" : t.permissions ? " · " + esc(t.permissions.join(", ")) : ""}</span></div>
        <div class="sub">made ${ago(t.created_at)}${t.last_used_at ? ", last used " + ago(t.last_used_at) : ", never used"}</div></span>
        <button class="btn ghost sm danger" data-revoke="${t.id}">Remove</button></div>`).join("") || `<div class="empty" style="padding:14px">No access keys yet.</div>`;
    }
    els.tokens.onclick = async (e) => {
      const b = e.target.closest("[data-revoke]"); if (!b) return;
      if (await confirmBox("Remove this access key?", "The app or shortcut using it stops working straight away.", { ok: "Remove", danger: true })) { await api(`/api/auth/tokens/${b.dataset.revoke}`, { method: "DELETE" }); drawTokens(); }
    };
    els.tokBtn.onclick = async () => {
      const name = await promptBox("New API token", { label: "What is it for?", placeholder: "e.g. iPhone Shortcut", ok: "Create" });
      if (!name) return;
      const t = await api("/api/auth/tokens", { method: "POST", body: { name } });
      showSecret("Your new token", t.token, "Copy it now: it won't be shown again. It can do everything your account can.");
    };
    els.calBtn.onclick = async () => {
      const t = await api("/api/auth/tokens", { method: "POST", body: { name: "Calendar", scope: "calendar" } });
      const url = (location.protocol === "https:" ? "https://" : "webcal://") + location.host + "/calendar.ics?token=" + t.token;
      showSecret("Your calendar link", url, "Add it as a calendar subscription (iPhone: Settings → Calendar → Accounts → Add Subscribed Calendar). It can only read the calendar.");
    };
    drawTokens();
  }

  // ---------- AI assistants (MCP) ----------
  if (sec === "assistants") {
    const url = location.origin + "/mcp";
    els.mcpUrl.textContent = url;
    const PERM_WORDS = { read: "Read", create: "Create", update: "Update", delete: "Delete" };
    if (me?.role === "readonly") {
      $$('input[name="perm"]', els.mcpForm).forEach((i) => { if (i.value !== "read") { i.checked = false; i.disabled = true; } });
      els.mcpRo.hidden = false;
    }
    async function drawKeys() {
      const ts = (await api("/api/auth/tokens")).filter((t) => t.permissions);
      els.mcpKeys.innerHTML = ts.map((t) => `<div class="item"><span class="main"><div class="t">${esc(t.name)} <span class="faint small">${esc(t.prefix)}…</span></div>
        <div class="sub">${t.permissions.map((p) => PERM_WORDS[p] || p).join(", ")} · made ${ago(t.created_at)}${t.last_used_at ? ", last used " + ago(t.last_used_at) : ", never used"}</div></span>
        <button class="btn ghost sm danger" data-revoke="${t.id}">Remove</button></div>`).join("") || `<div class="empty" style="padding:14px">No keys for AI assistants yet.</div>`;
    }
    els.mcpKeys.onclick = async (e) => {
      const b = e.target.closest("[data-revoke]"); if (!b) return;
      if (await confirmBox("Remove this key?", "The assistant using it can't reach your documents any more.", { ok: "Remove", danger: true })) { await api(`/api/auth/tokens/${b.dataset.revoke}`, { method: "DELETE" }); drawKeys(); }
    };
    els.mcpForm.onsubmit = async (e) => {
      e.preventDefault();
      const permissions = $$('input[name="perm"]:checked', els.mcpForm).map((i) => i.value);
      if (!permissions.length) { toast("Choose at least one thing it may do"); return; }
      const name = els.mcpForm.name.value.trim() || "Claude";
      const t = await api("/api/auth/tokens", { method: "POST", body: { name, permissions } });
      const code = mcpSetup(url, t.token);
      modal(`<h1 style="font-size:17px">Your access key for ${esc(name)}</h1>
        <div class="muted small">Copy what you need now: the key won't be shown again. It may: ${esc(permissions.map((p) => PERM_WORDS[p]).join(", "))}.</div>
        ${Object.entries(code).map(([k, c]) => `<div style="display:grid;gap:4px"><b class="small">${esc(c.title)}</b>
          <textarea class="input mono" readonly rows="${c.rows}" onclick="this.select()">${esc(c.text)}</textarea>
          <div><button class="btn sm" data-copy="${k}">Copy</button>${c.help ? ` <span class="help">${c.help}</span>` : ""}</div></div>`).join("")}
        <div class="acts"><button class="btn primary" data-close>Done</button></div>`, { onMount: (box) => {
          box.onclick = (ev) => {
            const b = ev.target.closest("[data-copy]");
            if (b) navigator.clipboard.writeText(code[b.dataset.copy].text).then(() => toast("Copied"));
            if (ev.target.closest("[data-close]")) { closeModal(); drawKeys(); }
          };
        } });
    };
    drawKeys();
  }

  // ---------- people (admin) ----------
  if (sec === "people") {
    async function drawPeople() {
      if (me?.role !== "admin") return;
      const ps = await api("/api/users");
      els.people.innerHTML = ps.map((p) => `<div class="item"><span class="main"><div class="t">${esc(p.display_name)} <span class="faint small">${esc(p.username)}</span></div>
        <div class="sub">${p.disabled ? "Paused: can't sign in" : "Added " + ago(p.created_at)}${p.two_step ? " · two-step sign-in on" : ""}</div></span>
        <select class="input" data-role="${p.id}" style="width:auto;height:30px">${["admin", "member", "readonly"].map((r) => `<option value="${r}" ${r === p.role ? "selected" : ""}>${ROLE_NAMES[r]}</option>`).join("")}</select>
        <button class="btn ghost sm" data-pw="${p.id}">Set password</button>
        ${p.two_step && p.id !== me.id ? `<button class="btn ghost sm" data-ts="${p.id}" title="For a lost phone: they sign in with their password and can turn it on again">Turn off two-step</button>` : ""}
        ${p.id === me.id ? "" : `<button class="btn ghost sm" data-off="${p.id}" data-v="${p.disabled ? 0 : 1}">${p.disabled ? "Let them in" : "Pause"}</button>
        <button class="btn ghost sm icon danger" data-del="${p.id}" title="Remove">${icon("trash")}</button>`}</div>`).join("");
    }
    els.people.onchange = async (e) => { const s = e.target.closest("[data-role]"); if (s) { await api(`/api/users/${s.dataset.role}`, { method: "PATCH", body: { role: s.value } }).catch(() => {}); drawPeople(); } };
    els.people.onclick = async (e) => {
      const pw = e.target.closest("[data-pw]"), off = e.target.closest("[data-off]"), del = e.target.closest("[data-del]"), ts = e.target.closest("[data-ts]");
      if (ts && await confirmBox("Turn off their two-step sign-in?", "For a lost phone: they can then sign in with just their password, and turn it on again with their new phone.", { ok: "Turn off" })) { await api(`/api/users/${ts.dataset.ts}`, { method: "PATCH", body: { reset_two_step: true } }); toast("Two-step sign-in is off for them"); drawPeople(); }
      if (pw) { const p = await promptBox("Set a new password", { label: "At least 8 characters", ok: "Set", type: "password" }); if (p) { await api(`/api/users/${pw.dataset.pw}`, { method: "PATCH", body: { password: p } }); toast("Password set. They need to sign in again with it."); } }
      if (off) { await api(`/api/users/${off.dataset.off}`, { method: "PATCH", body: { disabled: off.dataset.v === "1" } }); drawPeople(); }
      if (del && await confirmBox("Remove this person?", "They can't sign in any more. Documents and notes they added stay.", { ok: "Remove", danger: true })) { await api(`/api/users/${del.dataset.del}`, { method: "DELETE" }); drawPeople(); }
    };
    els.addPerson.onclick = () => {
      const box = modal(`<h1 style="font-size:17px">Add someone</h1><form style="display:grid;gap:12px">
        <label class="field">Their name<input class="input" name="display_name" required></label>
        <label class="field">Name they sign in with<input class="input" name="username" autocapitalize="none" required></label>
        <label class="field">A password for them <span class="faint">(at least 8 characters; they can change it later)</span><input class="input" name="password" type="password" minlength="8" required></label>
        <label class="field">What they may do<select class="input" name="role"><option value="member">Can add and edit: add, sort and change documents</option><option value="readonly">Can only look: browse, search and ask</option><option value="admin">Can do everything: also settings and people</option></select></label>
        <div class="acts"><button type="button" class="btn" data-x>Cancel</button><button class="btn primary">Add</button></div></form>`);
      const f = box.querySelector("form");
      f.display_name.oninput = () => (f.username.value = f.display_name.value.trim().toLowerCase().replace(/\s+/g, "."));
      box.querySelector("[data-x]").onclick = closeModal;
      f.onsubmit = async (e) => { e.preventDefault(); await api("/api/users", { method: "POST", body: Object.fromEntries(new FormData(f)) }); closeModal(); drawPeople(); };
    };
    drawPeople();
  }

  // ---------- household (spec C.6) ----------
  if (sec === "household") {
    let household = (s.household_people || []).map((p) => ({ names: [...(p.names || [])], words: [...(p.words || [])], user_ids: [...(p.user_ids || [])] }));
    async function drawHousehold() {
      if (me?.role !== "admin") return;
      const users = await api("/api/users").catch(() => []);
      els.household.innerHTML = household.map((p, i) => `<div class="item"><span class="main" style="display:grid;gap:6px;flex:1">
        <input class="input" data-hh="names" data-i="${i}" value="${esc(p.names.join(", "))}" placeholder="The names on their papers, comma-separated">
        <input class="input" data-hh="words" data-i="${i}" value="${esc(p.words.join(", "))}" placeholder="What others call them, comma-separated (Dad, …)">
        <select class="input" data-hh="user" data-i="${i}" style="max-width:320px"><option value="">No account linked (“my” won't mean them)</option>
          ${users.map((u) => `<option value="${u.id}" ${p.user_ids.includes(u.id) ? "selected" : ""}>${esc(u.display_name)} (${esc(u.username)})</option>`).join("")}</select>
        </span><button type="button" class="btn ghost sm" data-hh-del="${i}">Remove</button></div>`).join("") ||
        `<div class="item"><span class="main muted">Nobody yet. Add the people your papers are about.</span></div>`;
    }
    function readHousehold() {
      for (const inp of els.household.querySelectorAll("[data-hh]")) {
        const p = household[+inp.dataset.i], field = inp.dataset.hh;
        if (field === "names") p.names = inp.value.split(",").map((w) => w.trim()).filter(Boolean);
        if (field === "words") p.words = inp.value.split(",").map((w) => w.trim()).filter(Boolean);
        if (field === "user") p.user_ids = inp.value ? [+inp.value] : [];
      }
    }
    els.household.onchange = readHousehold;
    els.household.onclick = (e) => {
      const del = e.target.closest("[data-hh-del]");
      if (del) { readHousehold(); household.splice(+del.dataset.hhDel, 1); drawHousehold(); }
    };
    els.addHousehold.onclick = () => { readHousehold(); household.push({ names: [], words: [], user_ids: [] }); drawHousehold(); };
    els.saveHousehold.onclick = async () => {
      readHousehold();
      const clean = household.filter((p) => p.names.length);
      Object.assign(s, await api("/api/settings", { method: "PUT", body: { household_people: clean } }));
      household = clean; drawHousehold(); toast("Household saved");
    };
    drawHousehold();
  }

  // ---------- reading ----------
  if (sec === "reading") {
    const langs = await api("/api/ocr/languages", { quiet: true }).catch(() => []);
    const chosen = new Set((s.ocr_language || "eng").split("+"));
    els.langs.innerHTML = langs.map((l) => `<label class="chip"><input type="checkbox" value="${esc(l)}" ${chosen.has(l) ? "checked" : ""}> ${esc(LANG_NAMES[l] || l)}</label>`).join("");
    els.readForm.date_order.value = s.date_order;
    els.readForm.onsubmit = async (e) => {
      e.preventDefault();
      await api("/api/settings", { method: "PUT", body: { date_order: els.readForm.date_order.value,
        ocr_language: [...els.langs.querySelectorAll("input:checked")].map((x) => x.value).join("+") || "eng" } });
      toast("Saved. Documents added from now on are read this way.");
    };
  }
  // ---------- updates ----------
  let updPoll = null, importPoll = null;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  async function checkNow(btn) {
    btn.disabled = true;
    btn.innerHTML = `<span class="spin" aria-hidden="true"></span>Checking for a new version…`;
    // ask GitHub now; wait a moment at least, so the check is seen to happen
    const [u] = await Promise.all([api("/api/update?force=true", { quiet: true }).catch(() => null), sleep(700)]);
    if (!u) { toast("Couldn't check: PaperJay didn't answer."); btn.disabled = false; btn.textContent = "Check for a new version"; return; }
    if (u.error && !u.latest) toast(`Couldn't check for a new version: ${u.error}.`);
    else if (u.newer) toast(`PaperJay ${u.latest} is available (you have ${u.current}).`);
    else if (u.error) toast(`Couldn't reach GitHub just now; the newest version seen was ${u.latest}.`);
    else toast(`You have the newest version (${u.current}).`);
    refreshUpdateNotice();
    drawUpdates(u);
  }
  async function drawUpdates(given) {
    const u = given || await api("/api/update", { quiet: true }).catch(() => null);
    if (!u) { els.upd.innerHTML = `<span class="faint">Couldn't load the update information.</span>`; return; }
    if (!els.upd.isConnected) return;
    const st = u.status || {};
    const busy = st.state === "running";
    const lxc = u.install === "lxc";
    const auto = await api("/api/settings", { quiet: true }).then((s) => s.update_check).catch(() => true);
    els.upd.innerHTML = `<div>You have version <b>${esc(u.current)}</b>${u.latest && u.newer ? ` · version <b>${esc(u.latest)}</b> is out` : ""}
        ${u.newer ? `<span class="chip accent">Update available</span>` : u.latest && !u.error ? `<span class="chip good">Up to date</span>` : ""}</div>
      ${u.error ? `<div class="faint small">Couldn't check for a newer version (${esc(u.error)}).</div>` : ""}
      ${st.state && !busy ? `<div class="small" style="color:${st.state === "ok" ? "var(--good)" : st.state === "failed" ? "var(--bad)" : "var(--muted)"}">Last update: ${esc(st.message || st.state)}</div>` : ""}
      ${u.newer && lxc ? `<div><button class="btn primary" id="updBtn" ${busy ? "disabled" : ""}>Update to ${esc(u.latest)}</button>
        <div class="faint small" style="margin-top:6px">Your documents are backed up first, and if the new version doesn't start, PaperJay goes back to this one by itself.</div></div>` : ""}
      <div id="updProgress" class="small" ${busy ? "" : "hidden"} role="status"></div>
      ${u.newer && !lxc ? `<div class="small">To update, run this on the computer PaperJay runs on: <code>${esc(u.how)}</code></div>` : ""}
      ${u.notes && u.newer ? `<details><summary class="small">What's new</summary><div class="small muted" style="white-space:pre-wrap">${esc(u.notes)}</div></details>` : ""}
      <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><button class="btn sm" id="updCheck" ${busy ? "disabled" : ""}>Check for a new version</button>
        ${u.checked_at ? `<span class="faint small">Last checked ${esc(ago(u.checked_at * 1000))}</span>` : ""}</div>
      <label class="check"><input type="checkbox" id="updAuto" ${auto ? "checked" : ""}><span>Check by myself every few hours<div class="help">When a new version comes out, people who can change settings see a note at the top of PaperJay.</div></span></label>`;
    $("#updCheck", view).onclick = (e) => checkNow(e.currentTarget);
    $("#updAuto", view).onchange = async (e) => {
      await api("/api/settings", { method: "PUT", body: { update_check: e.target.checked } });
      toast(e.target.checked ? "PaperJay checks for new versions every few hours." : "PaperJay won't check by itself; use the button.");
    };
    const b = $("#updBtn", view);
    if (b) b.onclick = async () => {
      if (!await confirmBox(`Update to ${u.latest}?`, "PaperJay is unavailable for a moment while it updates, usually less than two minutes.", { ok: "Update" })) return;
      const r = await api("/api/update", { method: "POST", body: { tag: u.latest } }).catch(() => null);
      if (!r) return;
      b.disabled = true; $("#updCheck", view).disabled = true;
      watchUpdate(u.current, u.latest, st.at);
    };
    if (busy) watchUpdate(u.current, st.tag, null);
  }
  // Follow an update: the root updater's steps, then the restart. Done when PaperJay answers with a new version.
  function watchUpdate(from, to, lastAt) {
    const box = $("#updProgress", view);
    const show = (text) => { if (box) { box.hidden = false; box.innerHTML = `<span class="spin" aria-hidden="true"></span>${esc(text)}`; } };
    show(`Updating to ${to}: starting…`);
    const started = Date.now();
    clearInterval(updPoll);
    updPoll = setInterval(async () => {
      const h = await fetch("/healthz", { cache: "no-store" }).then((r) => r.json()).catch(() => null);
      if (h && h.version && h.version !== from) {
        clearInterval(updPoll);
        sessionStorage.setItem("pj-toast", `Updated: PaperJay ${h.version} is running.`);
        location.reload();
        return;
      }
      const u = h ? await api("/api/update", { quiet: true }).catch(() => null) : null;
      const st = u?.status || {};
      if (st.state === "failed" && st.at !== lastAt) {
        clearInterval(updPoll);
        if (box) box.innerHTML = `<span style="color:var(--bad)">${esc(st.message || "The update failed.")}</span>`;
        toast("The update didn't work; PaperJay is still on " + from + ".");
        refreshUpdateNotice();
        setTimeout(() => drawUpdates(u), 4000);
        return;
      }
      if (!h) show(`Updating to ${to}: PaperJay is restarting…`);
      else if (st.state === "running" && st.message) show(`Updating to ${to}: ${st.message}…`);
      if (Date.now() - started > 20 * 60 * 1000) {
        clearInterval(updPoll);
        if (box) box.innerHTML = `<span style="color:var(--bad)">The update is taking much longer than it should. Reload this page to see where it stands.</span>`;
      }
    }, 2000);
  }
  if (sec === "updates") drawUpdates();

  // ---------- import ----------
  if (sec === "import") {
    const pf = els.plForm;
    const plBody = () => ({ url: pf.url.value.trim(), token: pf.token.value.trim() });
    els.plCheck.onclick = async () => {
      els.plOut.textContent = "Checking…";
      try {
        const c = await api("/api/import/paperless/check", { method: "POST", body: plBody() });
        els.plOut.innerHTML = `<div style="color:var(--good)">✓ ${plural(c.documents, "document")} found${c.already_here ? `, ${c.already_here} already here` : ""}:
          ${c.without_text ? `${c.without_text} still need their text read (PaperJay does that after bringing them over), ` : ""}
          ${plural(c.tags, "tag")}, ${plural(c.correspondents, "company", "companies")}, ${plural(c.document_types, "type")}${c.notes ? `, ${plural(c.notes, "note")}` : ""}.</div>`;
        els.plGo.disabled = c.documents === c.already_here;
      } catch (e) { els.plOut.innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
    };
    async function showImport() {
      const p = await api("/api/import/status", { quiet: true }).catch(() => null);
      if (!p || p.state === "idle") return;
      const pct = p.total ? Math.round((p.done / p.total) * 100) : 0;
      els.plOut.innerHTML = `<div>${p.state === "running" ? `Bringing documents over… ${p.done} of ${p.total}` : p.state === "done" ? "Done" : "Stopped: " + esc(p.message || "")}
        · ${p.imported || 0} brought over${p.duplicates ? `, ${p.duplicates} already here` : ""}${p.failed ? `, <span style="color:var(--bad)">${p.failed} failed</span>` : ""}</div>
        <div class="progress" style="margin-top:6px"><i style="width:${pct}%"></i></div>
        ${(p.errors || []).map((e) => `<div class="faint">${esc(e)}</div>`).join("")}`;
      if (p.state !== "running") { clearInterval(importPoll); importPoll = null; }
    }
    pf.onsubmit = async (e) => {
      e.preventDefault();
      await api("/api/import/paperless", { method: "POST", body: plBody() });
      pf.token.value = ""; els.plGo.disabled = true;
      importPoll = setInterval(showImport, 1500); showImport();
    };
    showImport();
  }

  const stopMail = sec === "email" ? mailSetup(view, me) : () => {};
  if (location.hash) view.querySelector(location.hash)?.scrollIntoView();
  return () => { clearInterval(importPoll); clearInterval(updPoll); stopMail(); };
}

// What to paste into an AI assistant to connect it (docs/mcp.md has the same).
export function mcpSetup(url, token) {
  const caNote = url.startsWith("https:") ? `PaperJay's own HTTPS certificate: download <a href="/paperjay-ca.crt">paperjay-ca.crt</a> and point NODE_EXTRA_CA_CERTS at it, or use the http:// address on port 8000.` : "";
  const desktop = { paperjay: { command: "npx", args: ["-y", "mcp-remote", url, "--allow-http", "--header", "Authorization:" + "$" + "{PAPERJAY_AUTH}"],
                                env: { PAPERJAY_AUTH: `Bearer ${token}` } } };
  return {
    key: { title: "The key", rows: 2, text: token },
    code: { title: "Claude Code (run in a terminal)", rows: 4, help: caNote,
            text: `claude mcp add --transport http --scope user paperjay ${url} --header "Authorization: Bearer ${token}"` },
    desktop: { title: "Claude Desktop (Settings → Developer → Edit Config, inside \"mcpServers\"; needs Node.js)", rows: 9, help: caNote,
               text: JSON.stringify(desktop, null, 2).slice(2, -2) },
  };
}
