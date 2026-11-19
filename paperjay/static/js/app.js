// PaperJay's front end: plain ES modules, no build step. Each page is a module with show(view, params).
import { route, render, refreshCounts, refreshAI, refreshUpdateNotice, loadMe, signOut, toast } from "./core.js";
import { initSearch } from "./search.js";

route("/", () => import("./home.js"));
route("/browse", () => import("./browse.js"));
route("/index", () => import("./browse.js"));
route("/library", () => import("./library.js"));
route("/documents/:id", () => import("./document.js"));
route("/upload", () => import("./upload.js"));
route("/review", () => import("./review.js"));
route("/trash", () => import("./trash.js"));
route("/labels", () => import("./labels.js"));
route("/settings", () => import("./settings.js"));
route("/settings/:section", () => import("./settings.js"));
route("/secure", () => import("./secure.js"));
route("/taxpack", () => import("./taxpack.js"));
route("/login", () => import("./login.js"));
route("/setup", () => import("./setup.js"));

const bare = ["/login", "/setup"].includes(location.pathname);
document.body.classList.toggle("bare", bare);   // sign-in and setup pages have no navigation
if (!bare) await loadMe();
document.getElementById("signout").onclick = signOut;
render();
if (!bare) {
  initSearch();
  refreshCounts(); refreshAI(); refreshUpdateNotice();
  setInterval(() => { if (!document.hidden) { refreshCounts(); refreshAI(); } }, 30000);
  setInterval(() => { if (!document.hidden) refreshUpdateNotice(); }, 5 * 60 * 1000);
  // a message left by the page before it reloaded (e.g. "Updated")
  const left = sessionStorage.getItem("pj-toast");
  if (left) { sessionStorage.removeItem("pj-toast"); toast(left); }
}
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
