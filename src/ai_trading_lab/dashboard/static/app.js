"use strict";
/* AI Trading Lab dashboard (paper money only). All server text is untrusted: it is only ever
   put on the page with textContent / createTextNode, never as HTML. */

/* ---------- tiny DOM helpers ---------- */
const $ = (s, r = document) => r.querySelector(s);
function h(tag, props, ...kids) {
  const e = document.createElement(tag);
  if (props) {
    for (const [k, v] of Object.entries(props)) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") e.className = v;
      else if (k === "text") e.textContent = v;
      else if (k === "on") for (const [ev, fn] of Object.entries(v)) e.addEventListener(ev, fn);
      else if (k === "data") for (const [dk, dv] of Object.entries(v)) e.dataset[dk] = dv;
      else e.setAttribute(k, v === true ? "" : String(v));
    }
  }
  add(e, kids);
  return e;
}
function add(e, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k === null || k === undefined || k === false) continue;
    e.append(k.nodeType ? k : document.createTextNode(String(k)));
  }
}
const SVGNS = ["http:", "", "www.w3.org", "2000", "svg"].join("/"); // the SVG namespace name (not a link)
function s(tag, attrs, ...kids) {
  const e = document.createElementNS(SVGNS, tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) if (v !== null && v !== undefined) e.setAttribute(k, String(v));
  add(e, kids);
  return e;
}
function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); }
const kindClass = k => "k-" + String(k || "other").replace(/[^a-z]/g, "");

/* ---------- formatting ---------- */
const toNum = x => (x === null || x === undefined || x === "" ? null : Number(x));
function eur(v, signed) {
  const n = toNum(v);
  if (n === null || !isFinite(n)) return "no data yet";
  const r = Math.round(n * 100) / 100;
  const sign = r < 0 ? "−" : signed && r > 0 ? "+" : "";
  return sign + Math.abs(r).toFixed(2) + " EUR";
}
function pct(v) {
  const n = toNum(v);
  if (n === null || !isFinite(n)) return "no data yet";
  const r = Math.round(n * 100) / 100;
  return (r < 0 ? "−" : r > 0 ? "+" : "") + Math.abs(r).toFixed(2) + "%";
}
function usd(v, digits) {
  const n = toNum(v);
  return n === null || !isFinite(n) ? "no data yet" : "USD " + n.toFixed(digits === undefined ? 2 : digits);
}
const signCls = v => { const n = toNum(v); return n === null ? "" : n > 0.004 ? "pos" : n < -0.004 ? "neg" : ""; };
const pad2 = n => String(n).padStart(2, "0");
function fmtT(iso) {
  const d = new Date(iso);
  if (isNaN(d)) return "unknown time";
  return `${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())} UTC`;
}
function hhmmUtc(iso) {
  const d = new Date(iso);
  return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())} UTC`;
}
function localT(iso) {
  const d = new Date(iso);
  return isNaN(d) ? "unknown time" : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}
function agoMin(m) {
  if (m === null || m === undefined || !isFinite(m)) return "no data yet";
  if (m < 1) return "just now";
  if (m < 60) return Math.round(m) + " min ago";
  if (m < 1440) return (m / 60).toFixed(1) + " h ago";
  return (m / 1440).toFixed(1) + " days ago";
}
const agoIso = iso => (iso ? agoMin((Date.now() - Date.parse(iso)) / 60000) : "no data yet");
function about(m) { return m < 90 ? "about " + Math.max(1, Math.round(m)) + " min" : "about " + Math.round(m / 60) + " h"; }
const pairName = p => (p ? String(p).replace(/EUR$/, "/EUR") : "no pair");
function qty(q) { const n = toNum(q); return n === null ? "" : String(parseFloat(n.toPrecision(5))); }

/* ---------- wording ---------- */
const REASONS = {
  benchmark_entry: "Bought once to hold (the baseline)",
  no_breakout: "Price has not broken above its recent high",
  stop_loss: "Sold: lost 5% from the buy price (stop-loss)",
  take_profit: "Sold: gained 8% (take-profit)",
  max_hold: "Sold: held for 72 hours (time limit)",
  already_held: "Already holds this coin",
  cooldown_after_stop: "Waiting after a stop-loss",
  entry_interval: "Waiting: at most one new buy per day",
  drawdown_latched: "Buying paused: portfolio fell 10% below its best",
  daily_loss_suspended: "Buying paused for today: lost 3% today",
  daily_suspended: "Buying paused for today: lost 3% today",
  insufficient_history: "Not enough price history yet",
  stale_candles: "Skipped: Kraken prices were too old",
  candle_gap: "Skipped: gap in Kraken prices",
  volume_below_median: "Skipped: trading volume too low",
  ai_buy: "AI said buy",
  ai_sell: "AI said sell",
  ai_hold: "AI said hold",
  ai_not_reviewed: "AI has not looked yet",
  size_below_minimum_or_no_room: "Order too small or no room left within the limits",
};
function reasonText(code) {
  const c = String(code === null || code === undefined ? "" : code);
  if (Object.prototype.hasOwnProperty.call(REASONS, c)) return REASONS[c];
  if (c.startsWith("ai_unavailable_")) return "AI could not be asked (" + c.slice(15).replace(/_/g, " ") + ")";
  return c.replace(/_/g, " ") || "no reason recorded";
}
const STATUS_TEXT = { filled: "Filled", partial: "Partly filled", logged: "Logged", rejected: "Refused by safety check" };
const COMP = { data: "Data collection", rules: "Rules strategy", ai: "AI strategy", broker: "Paper broker", db: "Database", dashboard: "Dashboard", loop: "Loop", kraken: "Kraken", openai: "AI provider" };
const compName = c => COMP[c] || String(c || "unknown").replace(/_/g, " ");
const META = {
  rules: { name: "Rules breakout", blurb: "Buys when price breaks above its 48 h high; fixed rules, no AI." },
  ai: { name: "AI strategy", blurb: "Asks an AI model for buy / sell / hold every 4 h per pair." },
  benchmark: { name: "Buy-and-hold benchmark", blurb: "Buys once and never sells. The yardstick to beat." },
};
const stratName = st => (META[st.kind] ? META[st.kind].name : st.label || st.strategy_id);
const LIMITS = [
  ["Starting capital per strategy", "EUR 100.00 (virtual)"],
  ["Fee per side", "0.80%"],
  ["Max size of one position", "20% of equity"],
  ["Max total exposure", "40% of equity"],
  ["Daily-loss pause", "3% of starting capital: no new entries until 00:00 UTC"],
  ["Drawdown latch", "10% below the best value reached: stops new entries until cleared by hand"],
  ["Stop-loss", "−5% from entry"],
  ["Take-profit", "+8% from entry"],
  ["Maximum hold", "72 hours"],
  ["Cooldown after a stop-out", "6 hours"],
  ["Entries per pair", "1 per 24 hours"],
];
const PAGE_ONLY = [
  ["Daily-loss pause", "It lifts itself at 00:00 UTC. Nothing for you to do."],
  ["A strategy you switched off", "You did it on purpose; it is in the audit trail."],
  ["AI not activated, or the first hours of the run", "Informational."],
  ["A single Kraken or AI timeout that recovered", "Shown in Errors; it counts toward the rules above only if it keeps happening."],
  ["Who is ahead", "Performance is never an alert."],
];

/* ---------- state ---------- */
const S = {
  signedIn: false, csrf: null, idleMinutes: null,
  view: "overview", overview: null, overviewAt: null, overviewError: null,
  range: "all", hidden: {},
  D: { filters: { strategy: "", pair: "", action: "" }, rows: [], next: null, loaded: false, loading: false, error: null, open: {} },
  A: { rows: null, error: null },
  E: { filters: { status: "", level: "" }, data: null, error: null, loading: false, open: {} },
  Y: { audit: null, error: null },
  lastActivity: Date.now(),
};
const VIEWS = ["overview", "decisions", "ai", "errors", "system"];
const TITLES = { overview: "Overview", decisions: "Decisions", ai: "AI & safety", errors: "Errors", system: "System" };
const main = $("#main");
let toastTimer = null;

/* ---------- API ---------- */
class ApiError extends Error {
  constructor(status, code, message, data) { super(message); this.status = status; this.code = code; this.data = data; }
}
class SignedOut extends Error {}
async function api(path, opts) {
  const o = opts || {};
  const method = o.method || "GET";
  const headers = { Accept: "application/json" };
  if (method !== "GET") { headers["Content-Type"] = "application/json"; headers["X-CSRF-Token"] = S.csrf || ""; }
  let res;
  try {
    res = await fetch(path, { method, headers, credentials: "same-origin", body: o.body === undefined ? undefined : JSON.stringify(o.body) });
  } catch (e) {
    throw new ApiError(0, "network", "Could not reach the server. Check that the tunnel is still open, then try again.");
  }
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  if (res.ok) return data;
  const code = data && data.error ? String(data.error) : "http_error";
  const msg = data && data.message ? String(data.message) : "The server answered with an error (" + res.status + ").";
  if (res.status === 401 && code === "not_signed_in") {
    const wasIn = S.signedIn;
    signedOut(wasIn ? "Your session expired." : "");
    throw new SignedOut();
  }
  throw new ApiError(res.status, code, msg, data);
}
const isOut = e => e instanceof SignedOut;

/* ---------- sign-in ---------- */
function signedOut(message) {
  S.signedIn = false; S.csrf = null; S.overview = null; S.overviewAt = null;
  S.D.rows = []; S.D.loaded = false; S.A.rows = null; S.E.data = null; S.Y.audit = null;
  const dlgEl = $("#dlg");
  if (dlgEl.open) dlgEl.close();
  $("#toast").hidden = true;
  renderWho();
  $("#nav").hidden = true;
  $("#statusbar").hidden = true;
  document.title = "Sign in - AI Trading Lab";
  clear(main);
  const err = h("p", { class: "err-text", role: "alert", id: "pw-err" }, message || "");
  err.hidden = !message;
  const pw = h("input", { id: "pw", class: "inp", type: "password", autocomplete: "current-password", "aria-describedby": "pw-err" });
  const btn = h("button", { class: "btn primary block", type: "submit" }, "Sign in");
  const form = h("form", { class: "mt12", novalidate: true },
    h("label", { for: "pw", class: "sub" }, "Password"), pw, err, btn);
  form.addEventListener("submit", async ev => {
    ev.preventDefault();
    const pass = pw.value;
    if (!pass) { showErr("Enter your password."); pw.focus(); return; }
    btn.disabled = true;
    try {
      const r = await api("/api/login", { method: "POST", body: { password: pass } });
      pw.value = "";
      S.signedIn = true; S.csrf = r.csrf;
      try { const me = await api("/api/me"); S.idleMinutes = me.idle_minutes || null; } catch (e2) { if (isOut(e2)) return; }
      await afterSignIn();
    } catch (e) {
      btn.disabled = false;
      if (isOut(e)) return;
      if (e instanceof ApiError && e.code === "wrong_password") showErr("Wrong password. Try again.");
      else if (e instanceof ApiError && e.code === "too_many_attempts") showErr("Too many wrong passwords. Try again in " + (e.data && e.data.retry_after ? e.data.retry_after : "a few") + " seconds.");
      else if (e instanceof ApiError && e.code === "dashboard_not_configured") showErr("No dashboard password is configured on the server yet.");
      else showErr(e.message || "Could not sign in.");
      pw.focus();
    }
  });
  function showErr(t) { err.textContent = t; err.hidden = false; }
  main.append(h("div", { class: "signin stack" },
    h("section", { class: "card" },
      h("h2", null, "Sign in"),
      h("p", { class: "sub mt4" }, "This page can switch strategies on and off, so it asks for your password first. It is only reachable through your SSH tunnel (localhost)."),
      form),
    h("p", { class: "hint" }, "Paper money only: nothing here can place a real order.")));
  if (message) pw.focus();
}
async function afterSignIn() {
  renderWho();
  $("#nav").hidden = false;
  $("#statusbar").hidden = false;
  S.lastActivity = Date.now();
  go(viewFromHash(), false);
  main.focus();
}

function renderWho() {
  const who = $("#who");
  clear(who);
  if (S.signedIn) {
    who.append(h("span", { class: "pill good" }, "● Signed in as owner"),
      h("button", { class: "btn", type: "button", on: { click: signOut } }, "Sign out"));
  } else {
    who.append(h("span", { class: "pill off" }, "Signed out"));
  }
}
async function signOut() {
  try { await api("/api/logout", { method: "POST", body: {} }); } catch (e) { /* the cookie is cleared by the server when it can; either way show sign-in */ }
  S.signedIn = false;
  signedOut("");
  main.focus();
}

/* ---------- routing ---------- */
function viewFromHash() { const v = location.hash.slice(1); return VIEWS.includes(v) ? v : "overview"; }
function go(v, focus) {
  S.view = v;
  if (location.hash !== "#" + v) history.replaceState(null, "", "#" + v);
  document.querySelectorAll("nav a").forEach(a => {
    if (a.dataset.v === v) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  });
  document.title = TITLES[v] + " - AI Trading Lab";
  enterView(v);
  if (focus) { main.focus(); window.scrollTo(0, 0); }
}
async function enterView(v) {
  render();
  try {
    if (v === "overview" || v === "ai" || v === "system") { if (!S.overview) await refreshOverview(true); }
    if (v === "decisions") { if (!S.overview) await refreshOverview(true); if (!S.D.loaded) await loadDecisions(true); }
    if (v === "ai") { await loadAiCalls(); }
    if (v === "errors") { await loadErrors(); }
    if (v === "system") { await loadAudit(); }
  } catch (e) { if (!isOut(e)) console.error(e); }
}
document.querySelectorAll("nav a").forEach(a => a.addEventListener("click", ev => { ev.preventDefault(); if (S.signedIn) go(a.dataset.v, true); }));
addEventListener("hashchange", () => { if (S.signedIn) { const v = viewFromHash(); if (v !== S.view) go(v, false); } });

/* ---------- rendering with focus kept ---------- */
function render() {
  if (!S.signedIn) return;
  const active = document.activeElement;
  const key = active && active.dataset ? active.dataset.key : null;
  const y = window.scrollY;
  clear(main);
  const fn = { overview: vOverview, decisions: vDecisions, ai: vAI, errors: vErrors, system: vSystem }[S.view];
  main.append(fn());
  updateNav();
  updateStatusbar();
  if (S.view === "overview") drawChartNow();
  if (key) { const again = main.querySelector('[data-key="' + key + '"]'); if (again) again.focus({ preventScroll: true }); }
  window.scrollTo(0, y);
}
function updateNav() {
  const n = S.overview && S.overview.errors ? Number(S.overview.errors.unacknowledged_errors) || 0 : (S.E.data ? Number(S.E.data.summary.unacknowledged_errors) || 0 : 0);
  const a = $("#nav-err");
  clear(a);
  a.append("Errors");
  if (n > 0) a.append(" ", h("span", { class: "badge", "aria-hidden": "true" }, String(n)), h("span", { class: "sr" }, " (" + n + " not acknowledged)"));
}
function updateStatusbar() {
  const u = $("#updated");
  if (S.overviewError && S.overviewAt) u.textContent = "Could not refresh. Showing data from " + new Date(S.overviewAt).toLocaleTimeString() + ".";
  else if (S.overviewError) u.textContent = "Could not load the data.";
  else if (S.overviewAt) u.textContent = "Updated " + new Date(S.overviewAt).toLocaleTimeString();
  else u.textContent = "Loading…";
}

/* ---------- overview data ---------- */
async function refreshOverview(quiet) {
  $("#refresh").disabled = true;
  try {
    const o = await api("/api/overview");
    S.overview = o; S.overviewAt = Date.now(); S.overviewError = null;
  } catch (e) {
    if (isOut(e)) throw e;
    S.overviewError = e.message || "Could not load.";
  } finally {
    $("#refresh").disabled = false;
  }
  if (["overview", "ai", "system", "decisions", "errors"].includes(S.view)) {
    if (S.view === "decisions" || S.view === "errors") { updateNav(); updateStatusbar(); } else render();
  }
  if (!quiet && S.overviewError) toast(h("p", null, h("b", null, "Could not refresh. "), S.overviewError));
}
$("#refresh").addEventListener("click", () => {
  refreshOverview(false).catch(() => {});
  if (S.view === "errors") loadErrors().catch(() => {});
  if (S.view === "system") loadAudit().catch(() => {});
  if (S.view === "ai") loadAiCalls().catch(() => {});
});
const POLL_MS = 60000;
function idleLimitMs() { return (S.idleMinutes || 30) * 60000; }
setInterval(() => {
  if (!S.signedIn || document.visibilityState !== "visible") return;
  // Do not keep the session alive by polling alone: stop refreshing once the owner has been away
  // for the server's idle time, so the server can sign the session out.
  if (Date.now() - S.lastActivity > idleLimitMs()) return;
  refreshOverview(true).catch(() => {});
}, POLL_MS);
function noteActivity() {
  const idle = Date.now() - S.lastActivity > idleLimitMs();
  S.lastActivity = Date.now();
  if (idle && S.signedIn) refreshOverview(true).catch(() => {});
}
["pointerdown", "keydown", "touchstart"].forEach(ev => addEventListener(ev, noteActivity, { passive: true }));
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && S.signedIn) {
    if (!S.overviewAt || Date.now() - S.overviewAt > POLL_MS) refreshOverview(true).catch(() => {});
  }
});

/* ---------- shared widgets ---------- */
function banner(kind, title, text) {
  return h("div", { class: "banner " + kind, role: kind === "bad" ? "alert" : "status" },
    h("span", { class: "ic", "aria-hidden": "true" }, kind === "info" ? "i" : "!"),
    h("div", null, h("b", null, title), h("p", null, text)));
}
function empty(title, text, ok) {
  return h("div", { class: "empty" }, ok ? h("span", { class: "ic", "aria-hidden": "true" }, "✓") : null,
    h("div", null, h("b", null, title), h("p", null, text)));
}
const pill = (cls, text) => h("span", { class: "pill " + cls }, text);
const kv = (label, node) => h("div", { class: "kv" }, h("span", null, label), h("span", null, node));
function selectField(id, label, options, value, onChange) {
  const sel = h("select", { id, "data-key": id });
  for (const [v, t] of options) {
    const o = h("option", { value: v }, t);
    if (v === value) o.selected = true;
    sel.append(o);
  }
  sel.addEventListener("change", () => onChange(sel.value));
  return h("div", null, h("label", { for: id, class: "sub" }, label), sel);
}
function toast(content, goTo) {
  const t = $("#toast");
  clear(t);
  const acts = h("div", { class: "acts" });
  if (goTo) acts.append(h("button", { class: "btn", type: "button", on: { click: () => { t.hidden = true; go(goTo, true); } } }, "View"));
  acts.append(h("button", { class: "btn", type: "button", "aria-label": "Dismiss message", on: { click: () => { t.hidden = true; } } }, "Close"));
  t.append(h("div", null, content), acts);
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 12000);
}
function loadingBox(text) { return h("p", { class: "loading", role: "status" }, text || "Loading…"); }
function failBox(text, retry) {
  return h("div", { class: "banner bad", role: "alert" }, h("span", { class: "ic", "aria-hidden": "true" }, "!"),
    h("div", null, h("b", null, "Could not load this"), h("p", null, text), retry ? h("button", { class: "btn mt8", type: "button", on: { click: retry } }, "Try again") : null));
}

/* ---------- Overview ---------- */
function bannersFor(o) {
  const out = [];
  if (!o.run || !o.run.started_at) {
    out.push(banner("info", "Just started: nothing recorded yet", "No equity point has been written yet. Numbers appear after the first cycle finishes."));
  } else if (o.loop && o.loop.healthy === false) {
    const m = o.loop.age_seconds === null ? null : Math.round(o.loop.age_seconds / 60);
    out.push(banner("bad", "The loop has stopped", (m === null ? "No cycle has finished." : "No cycle has finished for " + m + " minutes (normal is every 15).") + " Nothing new is being traded or recorded, and open positions are not being watched until it restarts. On the server: run the health check and look at the logs, see the runbook."));
  }
  const stale = Object.entries(o.prices || {}).filter(([, p]) => p.age_seconds === null || p.age_seconds > 45 * 60);
  if (stale.length) out.push(banner("bad", "Kraken prices are old or missing", "Fresh prices are missing for " + stale.map(([p]) => pairName(p)).join(", ") + ". While data is missing or stale no strategy opens new positions, and equity shown comes from the last good prices."));
  const b = aiBanner(o);
  if (b) out.push(b);
  for (const st of o.strategies || []) {
    if (!st.enabled) out.push(banner("info", stratName(st) + " is switched off (wind-down)", "It opens nothing new" + (st.kind === "ai" ? " and the AI is not asked" : "") + ". Its stop-loss, take-profit and time exits still run, and its equity is still recorded so the comparison stays fair. Switch it back on at any time."));
  }
  return out;
}
function aiBanner(o) {
  const ai = o.ai;
  if (!ai) return null;
  const used = Math.round((toNum(ai.used_fraction) || 0) * 100);
  if (ai.state === "budget_exhausted") return banner("warn", "The AI is paused: its " + usd(ai.total_limit, 0) + " budget is used up", "The AI strategy makes no more calls and only manages exits for positions it already holds. It does not resume by itself: raising the cap needs a new decision from you. Rules and benchmark continue.");
  if (ai.state === "breaker_open") return banner("warn", "The AI is paused: the failure breaker tripped", breakerSentence(ai) + " Rules and benchmark are unaffected.");
  if (ai.state === "budget_warning") return banner("warn", "The AI has used " + used + "% of its " + usd(ai.total_limit, 0) + " budget", usd(Math.max(0, toNum(ai.total_limit) - toNum(ai.spent_estimated) - toNum(ai.held_unresolved))) + " is left. At 100% the AI stops asking and only manages exits for positions it already holds; rules and benchmark continue.");
  return null;
}
function breakerSentence(ai) {
  const n = ai.breaker_limit || 5;
  let s2 = n + " AI calls in the last 24 hours ended with an unknown result (for example a timeout). The AI stops asking until the oldest of them is more than 24 hours old.";
  if (ai.breaker_resumes_at) s2 += " It resumes by itself at about " + localT(ai.breaker_resumes_at) + " your time (" + hhmmUtc(ai.breaker_resumes_at) + ").";
  return s2;
}
function verdictBox(o) {
  const v = o.verdict || {};
  const stand = (v.standings || []).map(x => `${x.label} ${eur(toNum(x.equity) - 100, true)}`).join(", ");
  let text, early = false;
  if (v.state === "leader") {
    text = `${v.label} is ahead by ${eur(v.gap_eur)} after fees.` + (stand ? ` Standings: ${stand}.` : "");
  } else {
    early = true;
    let why;
    if (v.why === "run_under_72h") {
      const hrs = o.run && o.run.age_hours !== null && o.run.age_hours !== undefined ? ` (about ${Math.round(o.run.age_hours)} hours)` : "";
      why = "the run is under 3 days old" + hrs + (v.gap_eur !== null && v.gap_eur !== undefined ? ` and the gap between the first two is ${eur(v.gap_eur)}` : "");
    } else if (v.why === "gap_under_1_eur") {
      why = `the gap between the first two is only ${eur(v.gap_eur)}, which a single lucky or unlucky day can produce`;
    } else {
      why = "there is not enough data yet";
    }
    text = `Too early to tell: ${why}.` + (stand ? ` For now, after fees: ${stand}.` : "");
  }
  return h("section", { class: "card", "aria-label": "Verdict" },
    h("div", { class: "verdict-wrap" }, h("p", { class: "verdict", "aria-live": "polite" }, text), early ? pill("off", "Too early to tell") : pill("good", "✓ Leader named")),
    h("p", { class: "hint mt8" }, "Each strategy started with 100 EUR of fake money. \"Benchmark\" just buys once and holds: a strategy is only worth running if it beats it, after fees. We only name a leader when the run is at least 3 days old and the lead over the runner-up is at least 1 EUR, so one lucky day is not read as a result."));
}
function riskPill(st, aiOff) {
  if (aiOff) return pill("off", "Not activated");
  if (!st.enabled) return pill("off", "⏻ Switched off");
  if (st.risk && st.risk.drawdown_latched) return pill("bad", "⛔ Drawdown latch on");
  if (st.risk && st.risk.daily_suspended) return pill("warn", "⏸ Paused for today");
  return st.kind === "benchmark" ? pill("good", "✓ Holding") : pill("good", "✓ Trading normally");
}
function lastDecisionBlock(st) {
  const d = st.last_decision;
  if (!d) return h("div", null, h("p", { class: "sub small caps" }, "Last decision"), h("p", { class: "sub" }, "None recorded yet."));
  const act = { buy: "Bought", sell: "Sold", hold: "Hold" }[d.action] || d.action;
  return h("div", null,
    h("p", { class: "sub small caps" }, "Last decision · " + agoIso(d.at)),
    h("p", { class: "bold" }, act + (d.pair ? " " + pairName(d.pair) : "")),
    h("p", { class: "reason mt4" }, reasonText(d.reason)));
}
function strategyCard(st, o) {
  const aiOff = st.kind === "ai" && o.ai && o.ai.state === "off";
  const p = toNum(st.pnl_eur);
  const label = aiOff ? "Not activated" : st.enabled ? "On" : "Off (wind-down)";
  const sw = h("button", {
    class: "switch", type: "button", role: "switch", "aria-checked": String(!!st.enabled && !aiOff),
    "aria-label": stratName(st) + " on or off", disabled: aiOff, "data-key": "sw-" + st.strategy_id,
    on: { click: () => confirmSwitch(st, sw) },
  }, h("span", { class: "track", "aria-hidden": "true" }), h("span", { class: "lbl" }, label));
  const risk = st.risk || {};
  const riskText = st.enabled && risk.drawdown_latched
    ? h("p", { class: "reason bad" }, "Fell 10% or more below its best value (best so far " + eur(risk.peak_equity) + "). New entries stay blocked until you clear it by hand; exits still run.")
    : st.enabled && risk.daily_suspended
      ? h("p", { class: "reason warn" }, "Lost 3% of its starting capital today. No new entries until 00:00 UTC; stop-loss and exits still run.")
      : null;
  const changed = st.last_change
    ? h("p", { class: "sub small" }, "Last switch change: " + (st.last_change.enabled ? "on" : "off") + ", " + fmtT(st.last_change.at_utc) + ", " + st.last_change.actor + ". Reason: " + st.last_change.reason)
    : null;
  return h("article", { class: "card scard", "aria-labelledby": "sc-" + st.strategy_id },
    h("header", null,
      h("div", null, h("h3", { id: "sc-" + st.strategy_id }, h("span", { class: "swatch " + kindClass(st.kind), "aria-hidden": "true" }), stratName(st)),
        h("p", { class: "sub" }, META[st.kind] ? META[st.kind].blurb : "")),
      riskPill(st, aiOff)),
    h("div", null,
      h("div", { class: "equity num" }, st.equity === null ? "no data yet" : [toNum(st.equity).toFixed(2), " ", h("span", { class: "unit" }, "EUR")]),
      h("div", { class: "pl num " + signCls(p) }, p === null ? "no data yet" : [p > 0.004 ? "▲ " : p < -0.004 ? "▼ " : "▬ ", eur(p, true) + " (" + pct(st.pnl_pct) + ") ", h("span", { class: "since" }, "since the 100 EUR start")])),
    riskText,
    h("hr", { class: "sep" }),
    h("div", null,
      h("p", { class: "sub small caps" }, "Open positions"),
      st.positions && st.positions.length
        ? h("ul", { class: "pos-list" }, st.positions.map(ps => h("li", null,
          h("span", null, pairName(ps.pair) + " ", h("span", { class: "sub" }, qty(ps.quantity))),
          h("span", { class: "num " + signCls(ps.pnl_eur) }, ps.pnl_eur === null ? "no price" : eur(ps.pnl_eur, true) + " (" + pct(ps.pnl_pct) + ")"))))
        : h("p", { class: "sub" }, "None. Holding only cash (" + eur(st.cash) + ")."),
      h("p", { class: "sub mt6" }, "Fees paid so far: ", h("span", { class: "num" }, eur(st.fees_paid)))),
    h("hr", { class: "sep" }),
    lastDecisionBlock(st),
    changed,
    h("div", { class: "switch-row" }, sw));
}

/* attention */
function goFor(rule) { return { R1: "system", R2: "errors", R3: "system", R4: "ai", R5: "ai", R6: "errors" }[rule]; }
function attentionItems(o) {
  const items = [];
  const firing = (o.alerts && o.alerts.firing ? o.alerts.firing : []).slice().sort((a, b) => (a.severity === "critical" ? 0 : 1) - (b.severity === "critical" ? 0 : 1));
  for (const f of firing) items.push({ crit: f.severity === "critical", title: f.title, detail: f.detail, rule: f.rule, go: goFor(f.rule) });
  for (const st of o.strategies || []) {
    if (st.enabled && st.risk && st.risk.drawdown_latched) items.push({ crit: true, pageOnly: true, title: stratName(st) + " latched by drawdown", detail: "It is 10% or more below its best value. It will not open positions again until you clear the latch. Decide whether to clear it or leave it for the comparison." });
    else if (st.enabled && st.risk && st.risk.daily_suspended) items.push({ crit: false, pageOnly: true, title: stratName(st) + " paused for the day", detail: "Daily-loss limit (3%) reached. It resumes by itself at 00:00 UTC. Nothing to do." });
    if (!st.enabled) items.push({ info: true, pageOnly: true, title: stratName(st) + " is switched off", detail: "Wind-down: it opens nothing new" + (st.kind === "ai" ? " and spends nothing" : "") + "; its exits still run and its curve keeps being recorded." });
  }
  if (o.ai && o.ai.state === "off") items.push({ info: true, pageOnly: true, title: "AI strategy not activated", detail: "The AI strategy is switched off on the server. Rules and benchmark are running." });
  return items;
}
function attentionCard(o) {
  const items = attentionItems(o);
  const body = items.length
    ? [h("p", { class: "hint mt4" }, "Items tagged with a rule are the ones that will also reach you on Telegram once that is set up (a later slice). The rules are listed under System, \"What alerts you\"."),
      h("ul", { class: "att-list" }, items.map((x, idx) => h("li", null,
        h("span", { class: "pill flex-none " + (x.info ? "off" : x.crit ? "bad" : "warn") }, x.info ? "ℹ Info" : x.crit ? "❗ Critical" : "⚠ Warning"),
        h("div", null, h("b", null, x.title), h("p", { class: "sub" }, x.detail),
          x.rule ? h("span", { class: "al yes" }, "🔔 Alert rule " + x.rule) : h("span", { class: "al no" }, "Page only, no alert"),
          x.go ? [" ", h("button", { class: "exp", type: "button", "data-key": "go-" + idx, on: { click: () => go(x.go, true) } }, "Open " + TITLES[x.go])] : null))))]
    : [empty("Nothing needs your attention.", "The loop is running, prices are fresh, no errors are waiting, the AI is within its budget and no safety limit has been hit.", true)];
  return h("section", { class: "card", "aria-labelledby": "at-h" }, h("h2", { id: "at-h" }, "Needs your attention"), body);
}

function vOverview() {
  const o = S.overview;
  if (!o) return S.overviewError ? failBox(S.overviewError, () => refreshOverview(false)) : loadingBox("Loading the dashboard…");
  const ai = o.ai || {};
  const krakenOk = Object.values(o.prices || {}).every(p => p.age_seconds !== null && p.age_seconds <= 45 * 60);
  const used = Math.round((toNum(ai.used_fraction) || 0) * 100);
  const aiPill = { off: pill("off", "Not activated"), ok: pill("good", "✓ Asking"), budget_warning: pill("warn", "⚠ Asking, budget " + used + "% used"), budget_exhausted: pill("warn", "⏸ Stopped: budget used"), breaker_open: pill("warn", "⏸ Paused by itself") }[ai.state] || pill("off", String(ai.state));
  const nErr = o.errors ? Number(o.errors.unacknowledged_errors) || 0 : 0;
  const hrs = o.run ? toNum(o.run.age_hours) : null;
  const loopOk = o.loop && o.loop.healthy;
  return h("div", { class: "stack" },
    bannersFor(o),
    h("section", { class: "card", "aria-label": "Status" }, h("div", { class: "status-strip" },
      kv("Loop", !o.run || !o.run.started_at ? pill("off", "Waiting for first cycle") : loopOk ? pill("good", "● Running") : pill("bad", "✖ Stalled")),
      kv("Last cycle", h("span", { class: "num" }, o.loop && o.loop.age_seconds !== null ? agoMin(o.loop.age_seconds / 60) : "no data yet")),
      kv("Kraken prices", krakenOk ? pill("good", "✓ Fresh") : pill("bad", "✖ Stale")),
      kv("AI", aiPill),
      kv("AI spend (USD, estimate)", h("span", { class: "num" }, "USD " + (toNum(ai.spent_estimated) || 0).toFixed(2) + " of " + (toNum(ai.total_limit) || 0).toFixed(0))),
      kv("Errors to review", nErr ? pill(o.alerts && o.alerts.firing.some(f => f.rule === "R6") ? "warn" : "bad", nErr + " to review") : pill("good", "✓ None")),
      kv("Running for", hrs === null ? "no data yet" : hrs >= 48 ? (hrs / 24).toFixed(1) + " days" : Math.round(hrs) + " h"))),
    verdictBox(o),
    h("section", { class: "grid3", "aria-label": "Strategies" }, (o.strategies || []).map(st => strategyCard(st, o))),
    chartCard(o),
    attentionCard(o));
}

/* ---------- equity chart (inline SVG) ---------- */
const RANGES = [["24h", "24 h", 24], ["3d", "3 days", 72], ["all", "All", Infinity]];
let chartKit = null;
function chartCard(o) {
  const seg = h("div", { class: "seg", role: "group", "aria-label": "Time range" }, RANGES.map(([id, text]) =>
    h("button", { type: "button", "aria-pressed": String(S.range === id), "data-key": "range-" + id, on: { click: () => { S.range = id; seg.querySelectorAll("button").forEach((b, i) => b.setAttribute("aria-pressed", String(RANGES[i][0] === id))); drawChartNow(); } } }, text)));
  const box = h("div", { class: "chart-box", id: "chart" });
  const legend = h("div", { class: "legend", role: "group", "aria-label": "Show or hide a line" }, (o.strategies || []).map(st =>
    h("button", { type: "button", "aria-pressed": String(S.hidden[st.strategy_id] !== true), "data-key": "lg-" + st.strategy_id, on: { click: ev => { S.hidden[st.strategy_id] = !S.hidden[st.strategy_id]; ev.currentTarget.setAttribute("aria-pressed", String(!S.hidden[st.strategy_id])); drawChartNow(); } } },
      h("span", { class: "swatch " + kindClass(st.kind), "aria-hidden": "true" }),
      stratName(st) + ({ rules: " (solid)", ai: " (dotted)", benchmark: " (dashed)" }[st.kind] || ""))));
  return h("section", { class: "card", "aria-labelledby": "ch-h" },
    h("div", { class: "chart-head" }, h("div", null, h("h2", { id: "ch-h" }, "Equity compared"), h("p", { class: "sub" }, "Total value of each portfolio in EUR, after fees. The dotted horizontal line is the 100 EUR start.")), seg),
    box, legend);
}
function valueAt(pts, t) {
  let lo = 0, hi = pts.length - 1, ans = -1;
  while (lo <= hi) { const mid = (lo + hi) >> 1; if (pts[mid].t <= t) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
  return ans < 0 ? null : pts[ans].v;
}
function drawChartNow() {
  const box = $("#chart");
  if (!box || !S.overview) return;
  const o = S.overview;
  clear(box);
  const hrs = RANGES.find(r => r[0] === S.range)[2];
  const series = [];
  for (const st of o.strategies || []) {
    if (S.hidden[st.strategy_id]) continue;
    const raw = (o.curves && o.curves[st.strategy_id]) || [];
    const pts = raw.map(p => ({ t: Date.parse(p[0]), v: Number(p[1]) })).filter(p => isFinite(p.t) && isFinite(p.v));
    if (pts.length) series.push({ st, pts });
  }
  if (!series.length) { box.append(empty("No equity data to draw yet.", "The curves appear after the first cycle has been recorded.", false)); return; }
  const latest = Math.max(...series.map(x => x.pts[x.pts.length - 1].t));
  const from = hrs === Infinity ? -Infinity : latest - hrs * 3600e3;
  series.forEach(x => { x.pts = x.pts.filter(p => p.t >= from); });
  const vis = series.filter(x => x.pts.length);
  const W = Math.max(320, box.clientWidth || 640), mobile = W < 560, H = mobile ? 250 : 310;
  const pad = { l: 48, r: mobile ? 78 : 130, t: 14, b: 28 };
  let lo = 100, hi = 100, t0 = Infinity, t1 = -Infinity;
  vis.forEach(x => x.pts.forEach(p => { lo = Math.min(lo, p.v); hi = Math.max(hi, p.v); t0 = Math.min(t0, p.t); t1 = Math.max(t1, p.t); }));
  lo = Math.floor((lo - 0.4) * 2) / 2; hi = Math.ceil((hi + 0.4) * 2) / 2;
  const X = t => pad.l + (t1 > t0 ? (t - t0) / (t1 - t0) : 0.5) * (W - pad.l - pad.r);
  const Y = v => pad.t + (hi - v) / (hi - lo) * (H - pad.t - pad.b);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H, role: "img", tabindex: "0", "aria-label": "Equity over time. " + vis.map(x => `${stratName(x.st)} ends at ${eur(x.pts[x.pts.length - 1].v)}`).join(", ") + ". Use the left and right arrow keys to read values." });
  const step = (hi - lo) > 6 ? 2 : (hi - lo) > 3 ? 1 : 0.5;
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) {
    svg.append(s("line", { class: "c-grid", x1: pad.l, x2: W - pad.r, y1: Y(v), y2: Y(v) }),
      s("text", { class: "c-txt", x: pad.l - 8, y: Y(v) + 4, "text-anchor": "end" }, v.toFixed(v % 1 ? 1 : 0)));
  }
  svg.append(s("line", { class: "c-start", x1: pad.l, x2: W - pad.r, y1: Y(100), y2: Y(100) }),
    s("text", { class: "c-txt strong", x: pad.l + 4, y: Y(100) - 5 }, "Start: 100 EUR"));
  const nt = mobile ? 3 : 5;
  if (t1 > t0) for (let j = 0; j < nt; j++) {
    const t = t0 + (t1 - t0) * j / (nt - 1), d = new Date(t);
    const lab = (t1 - t0) > 36 * 3600e3 ? `${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}` : `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`;
    svg.append(s("text", { class: "c-txt", x: X(t), y: H - 8, "text-anchor": j === 0 ? "start" : j === nt - 1 ? "end" : "middle" }, lab));
  }
  const labels = [];
  vis.forEach(x => {
    const d = x.pts.map((p, i) => (i ? "L" : "M") + X(p.t).toFixed(1) + " " + Y(p.v).toFixed(1)).join(" ");
    svg.append(s("path", { class: "line " + kindClass(x.st.kind), d }));
    const last = x.pts[x.pts.length - 1];
    labels.push({ x, y: Y(last.v), v: last.v });
  });
  labels.sort((a, b) => a.y - b.y);
  for (let i = 1; i < labels.length; i++) if (labels[i].y - labels[i - 1].y < 15) labels[i].y = labels[i - 1].y + 15;
  const shortName = st => ({ rules: "Rules", ai: "AI", benchmark: "Benchmark" }[st.kind] || stratName(st));
  labels.forEach(l => svg.append(s("text", { class: "endlbl " + kindClass(l.x.st.kind), x: W - pad.r + 8, y: l.y + 4 }, shortName(l.x.st) + " " + l.v.toFixed(1))));
  const cur = s("g", { visibility: "hidden" });
  const curLine = s("line", { class: "c-cursor", y1: pad.t, y2: H - pad.b });
  cur.append(curLine);
  const dots = vis.map(x => { const c = s("circle", { class: "dot " + kindClass(x.st.kind), r: 4.5 }); cur.append(c); return c; });
  svg.append(cur);
  const tip = h("div", { class: "tip", hidden: true });
  box.append(svg, tip);
  const times = [...new Set(vis.flatMap(x => x.pts.map(p => p.t)))].sort((a, b) => a - b);
  let pos = times.length - 1;
  function show(j) {
    pos = Math.max(0, Math.min(times.length - 1, j));
    const t = times[pos], x = X(t);
    cur.setAttribute("visibility", "visible");
    curLine.setAttribute("x1", x); curLine.setAttribute("x2", x);
    clear(tip);
    tip.append(h("b", null, fmtT(new Date(t).toISOString())));
    vis.forEach((sr, i) => {
      const v = valueAt(sr.pts, t);
      dots[i].setAttribute("visibility", v === null ? "hidden" : "visible");
      if (v === null) return;
      dots[i].setAttribute("cx", x); dots[i].setAttribute("cy", Y(v));
      tip.append(h("div", null, h("b", null, shortName(sr.st)), " ", h("span", { class: "num" }, eur(v))));
    });
    tip.hidden = false;
    tip.style.left = Math.max(0, x > W / 2 ? x - tip.offsetWidth - 12 : x + 12) + "px";
  }
  function hide() { cur.setAttribute("visibility", "hidden"); tip.hidden = true; }
  svg.addEventListener("pointermove", ev => {
    const r = svg.getBoundingClientRect(), px = (ev.clientX - r.left) * (W / r.width);
    const t = t0 + (px - pad.l) / (W - pad.l - pad.r) * (t1 - t0);
    let best = 0, bd = Infinity;
    times.forEach((tt, j) => { const d = Math.abs(tt - t); if (d < bd) { bd = d; best = j; } });
    show(best);
  });
  svg.addEventListener("pointerleave", hide);
  svg.addEventListener("blur", hide);
  svg.addEventListener("keydown", ev => {
    if (ev.key === "ArrowLeft") { ev.preventDefault(); show(pos - 1); }
    else if (ev.key === "ArrowRight") { ev.preventDefault(); show(pos + 1); }
    else if (ev.key === "Home") { ev.preventDefault(); show(0); }
    else if (ev.key === "End") { ev.preventDefault(); show(times.length - 1); }
  });
  chartKit = { W };
}
let resizeFrame = 0;
addEventListener("resize", () => {
  if (S.view !== "overview" || !S.signedIn) return;
  cancelAnimationFrame(resizeFrame);
  resizeFrame = requestAnimationFrame(() => { const b = $("#chart"); if (b && chartKit && Math.abs(b.clientWidth - chartKit.W) > 4) drawChartNow(); });
});

/* ---------- Decisions ---------- */
function strategyOptions() {
  const o = [["", "All"]];
  if (S.overview) S.overview.strategies.forEach(st => o.push([st.strategy_id, stratName(st)]));
  return o;
}
function stratLabel(id) {
  const st = S.overview && S.overview.strategies.find(x => x.strategy_id === id);
  return st ? stratName(st) : id;
}
async function loadDecisions(reset) {
  const D = S.D;
  if (D.loading) return;
  if (reset) { D.rows = []; D.next = null; D.open = {}; }
  D.loading = true; D.error = null;
  render();
  try {
    const q = new URLSearchParams({ limit: "50" });
    if (D.filters.strategy) q.set("strategy_id", D.filters.strategy);
    if (D.filters.pair) q.set("pair", D.filters.pair);
    if (D.filters.action) q.set("action", D.filters.action);
    if (!reset && D.next) q.set("before_seq", String(D.next));
    const r = await api("/api/decisions?" + q.toString());
    D.rows = D.rows.concat(r.decisions || []);
    D.next = r.next_before_seq || null;
    D.loaded = true;
  } catch (e) {
    if (isOut(e)) throw e;
    D.error = e.message;
  } finally {
    D.loading = false;
  }
  render();
}
function chip(a) { return h("span", { class: "chip " + (a === "buy" ? "buy" : a === "sell" ? "sell" : "") }, a === "buy" ? "▲ Buy" : a === "sell" ? "▼ Sell" : a === "hold" ? "— Hold" : String(a)); }
function aiDetail(d) {
  const a = d.ai || {};
  const ans = a.answer || {};
  const usage = a.usage || {};
  return h("tr", { class: "detail" }, h("td", { colspan: "6" },
    h("p", { class: "sub small caps" }, "What the AI answered"),
    h("pre", null, ans.action ? `${ans.action}${ans.limit_price ? " (limit " + ans.limit_price + ")" : ""}: ${ans.reason || ""}` : "(no answer recorded)"),
    h("p", { class: "mt8 num" }, h("b", null, "Tokens: "), usage.input_tokens !== undefined ? `${usage.input_tokens} in / ${usage.output_tokens} out` : "none recorded",
      " · ", h("b", null, "Cost: "), a.cost_usd !== undefined ? "USD " + Number(a.cost_usd).toFixed(4) + " (estimate)" : "not recorded",
      " · ", h("b", null, "Model: "), a.model || "none",
      " · ", h("b", null, "Call: "), a.call_id || "none"),
    a.provider_status ? h("p", { class: "sub mt4" }, "Provider result: " + a.provider_status + (a.provider_reason ? " (" + a.provider_reason + ")" : "")) : null,
    h("p", { class: "hint mt4" }, "The AI only proposes a direction. Size, limits and the fill are decided by fixed code, never by the AI.")));
}
function vDecisions() {
  const D = S.D, f = D.filters;
  const reload = () => { loadDecisions(true).catch(() => {}); };
  const rows = D.rows.flatMap(d => {
    const open = !!D.open[d.seq];
    const main1 = h("tr", null,
      h("td", { class: "num nowrap", "data-label": "Time" }, fmtT(d.at)),
      h("td", { "data-label": "Strategy" }, stratLabel(d.strategy_id)),
      h("td", { "data-label": "Pair" }, pairName(d.pair)),
      h("td", { "data-label": "Action" }, chip(d.action), d.result && d.result.notional ? h("div", { class: "sub num" }, eur(d.result.notional)) : null),
      h("td", { "data-label": "Status" }, STATUS_TEXT[d.status] || d.status),
      h("td", { "data-label": "Why" }, reasonText(d.reason), d.ai ? [" ", h("button", { class: "exp", type: "button", "aria-expanded": String(open), "data-key": "x-" + d.seq, on: { click: () => { D.open[d.seq] = !open; render(); } } }, (open ? "Hide" : "Show") + " AI detail")] : null));
    return open && d.ai ? [main1, aiDetail(d)] : [main1];
  });
  let list;
  if (D.error && !D.rows.length) list = failBox(D.error, reload);
  else if (!D.loaded && D.loading) list = loadingBox();
  else if (!D.rows.length) list = empty("No decisions match these filters.", "Clear a filter to see more, or wait for the next cycle.", false);
  else list = h("div", { class: "tbl-wrap" }, h("table", { class: "cards-tbl" },
    h("thead", null, h("tr", null, ["Time", "Strategy", "Pair", "Action", "Status", "Why"].map(t => h("th", null, t)))), h("tbody", null, rows)));
  return h("div", { class: "stack" },
    h("section", { class: "card" }, h("h2", null, "Decisions and trades"),
      h("p", { class: "sub" }, "Every time a strategy decided something, with the reason in plain words. \"Hold\" means it looked and chose to do nothing."),
      h("div", { class: "filters mt12" },
        selectField("f-strategy", "Strategy", strategyOptions(), f.strategy, v => { f.strategy = v; reload(); }),
        selectField("f-pair", "Pair", [["", "All"], ["XBTEUR", "XBT/EUR"], ["ETHEUR", "ETH/EUR"], ["SOLEUR", "SOL/EUR"]], f.pair, v => { f.pair = v; reload(); }),
        selectField("f-action", "Action", [["", "All"], ["buy", "Buy"], ["sell", "Sell"], ["hold", "Hold"]], f.action, v => { f.action = v; reload(); }))),
    h("section", { class: "card" },
      h("p", { class: "sub", "aria-live": "polite" }, D.rows.length + " decision" + (D.rows.length === 1 ? "" : "s") + " shown" + (D.next ? " (more available)" : "")),
      D.error && D.rows.length ? h("p", { class: "err-text", role: "alert" }, D.error) : null,
      list,
      D.next ? h("button", { class: "btn mt12", type: "button", disabled: D.loading, "data-key": "more", on: { click: () => { loadDecisions(false).catch(() => {}); } } }, D.loading ? "Loading…" : "Load more") : null));
}

/* ---------- AI & safety ---------- */
async function loadAiCalls() {
  const ai = S.overview && S.overview.strategies.find(x => x.kind === "ai");
  if (!ai) { S.A.rows = []; render(); return; }
  try {
    const r = await api("/api/decisions?" + new URLSearchParams({ strategy_id: ai.strategy_id, limit: "100" }).toString());
    S.A.rows = (r.decisions || []).filter(d => d.ai);
    S.A.error = null;
  } catch (e) { if (isOut(e)) throw e; S.A.error = e.message; }
  render();
}
function vAI() {
  const o = S.overview;
  if (!o) return S.overviewError ? failBox(S.overviewError, () => refreshOverview(false)) : loadingBox();
  const ai = o.ai || {};
  const limit = toNum(ai.total_limit) || 0;
  const p = (toNum(ai.used_fraction) || 0) * 100;
  const warn = (toNum(ai.warn_at) || 0.8) * 100;
  const spent = toNum(ai.spent_estimated) || 0, held = toNum(ai.held_unresolved) || 0;
  const overall = { off: ["off", "Not activated"], ok: ["good", "✓ Active"], budget_warning: ["warn", "⚠ Active, budget warning"], budget_exhausted: ["warn", "⏸ Stopped: budget used"], breaker_open: ["warn", "⏸ Paused: failure breaker"] }[ai.state] || ["off", String(ai.state)];
  const nUnk = Number(ai.unknown_outcomes_24h) || 0, lim = ai.breaker_limit || 5;
  const brk = ai.breaker_open ? ["warn", "⏸ Tripped: paused"] : nUnk >= lim - 1 && nUnk > 0 ? ["warn", "⚠ One more and it pauses"] : ["good", "✓ Clear"];
  const bState = p >= 100 ? ["warn", "⏸ Stopped (100% used)"] : p >= warn ? ["warn", "⚠ Warning: " + Math.round(p) + "% used"] : ["good", "✓ Within budget"];
  const mcls = p >= 100 ? "full" : p >= warn ? "warn" : "";
  const meter = h("div", { class: "meter " + mcls, role: "progressbar", "aria-valuemin": "0", "aria-valuemax": "100", "aria-valuenow": String(Math.min(100, Math.round(p))), "aria-valuetext": Math.round(p) + "% used" + (p >= 100 ? ", stopped" : p >= warn ? ", warning" : ""), "aria-label": "AI budget used" });
  const fill = h("i"); fill.style.width = Math.min(100, p) + "%";
  const tick = h("b", { class: "tick", "aria-hidden": "true" }); tick.style.left = warn + "%";
  meter.append(fill, tick);
  const unkTimes = (ai.unknown_outcome_times || []).slice().sort();
  const breakerLine = ai.breaker_open && ai.breaker_resumes_at
    ? [h("b", null, "Resumes by itself at about " + localT(ai.breaker_resumes_at) + " your time (" + fmtT(ai.breaker_resumes_at) + ")"), ", when the oldest of the " + lim + " failed calls is 24 h old."]
    : [h("span", { class: "sub" }, nUnk + " of " + lim + " failed calls in the last 24 h. At " + lim + " it stops asking.")];
  const calls = S.A.rows;
  return h("div", { class: "stack" },
    aiBanner(o),
    h("section", { class: "card" }, h("h2", null, "Is the AI allowed to ask right now?"),
      h("p", { class: "sub" }, "The AI pauses in exactly two ways. Both only stop it from asking; exits for positions it holds still run."),
      h("dl", { class: "dl mt10" },
        h("dt", null, "Overall"), h("dd", null, pill(overall[0], overall[1]), ai.state === "off" ? " The AI strategy is switched off on the server." : ""),
        h("dt", null, "A. Failure breaker"), h("dd", null, pill(brk[0], brk[1]), " ", breakerLine),
        h("dt", null, "B. Budget"), h("dd", null, pill(bState[0], bState[1]), " ",
          p >= 100 ? [h("b", null, "Does not resume by itself."), " Raising the cap needs a new decision from you."]
            : h("span", { class: "sub" }, "Heads-up at " + Math.round(warn) + "% (" + usd(limit * warn / 100) + "), stops asking at 100% (" + usd(limit) + ").")))),
    h("section", { class: "grid3", "aria-label": "AI summary" },
      h("div", { class: "card" }, h("h2", null, "Budget used (USD)"),
        h("p", { class: "big num" }, usd(spent + held), " ", h("span", { class: "of" }, "of " + limit.toFixed(2))),
        meter,
        h("p", { class: "hint mt4" }, "The bar turns amber at the line (" + Math.round(warn) + "%) and red at 100%."),
        h("p", { class: "sub mt6" }, h("b", null, Math.round(p) + "% used" + (p >= 100 ? ": stopped" : p >= warn ? ": warning" : "") + "."), " " + usd(Math.max(0, limit - spent - held)) + " left" + (held > 0 ? " (" + usd(held) + " is held for calls whose cost is not settled yet)" : "") + ". The application refuses any call that could cross the cap."),
        h("p", { class: "hint mt6" }, "This is an estimate from token counts and the reviewed price, not an invoice. It is in US dollars; the paper trading money elsewhere on this page is in euros and is separate.")),
      h("div", { class: "card" }, h("h2", null, "AI calls so far"), h("p", { class: "big num" }, String(ai.call_count === undefined ? "no data yet" : ai.call_count)),
        h("p", { class: "sub" }, "Expect about 18 a day at most (3 pairs, one review per 4 h)."), h("p", { class: "mt6" }, pill(overall[0], overall[1]))),
      h("div", { class: "card" }, h("h2", null, "Failure breaker"), h("p", { class: "big num" }, String(nUnk), " ", h("span", { class: "of" }, "of " + lim + " unknown outcomes in 24 h")),
        h("p", { class: "mt6" }, pill(brk[0], brk[1])),
        h("p", { class: "sub mt6" }, "An unknown outcome (a call that timed out or could not be read) is charged at its worst case. At " + lim + " within 24 hours the AI stops asking until the oldest of them is more than 24 hours old."))),
    unkTimes.length ? h("section", { class: "card" }, h("h2", null, "Failed calls in the last 24 hours"), h("p", { class: "sub" }, "Each one counts toward the breaker until it is 24 hours old."),
      h("div", { class: "tbl-wrap mt8" }, h("table", null, h("thead", null, h("tr", null, ["When", "Counts for another"].map(t => h("th", null, t)))),
        h("tbody", null, unkTimes.map(t => h("tr", null, h("td", { class: "num" }, fmtT(t) + " (" + agoIso(t) + ")"), h("td", { class: "num" }, about(Math.max(0, (Date.parse(t) + 864e5 - Date.now()) / 60000))))))))) : null,
    h("section", { class: "card" }, h("h2", null, "Recent AI reviews"), h("p", { class: "sub" }, "Newest first. Cost is an estimate in USD from token counts and the reviewed price."),
      S.A.error ? failBox(S.A.error, () => { loadAiCalls().catch(() => {}); })
        : calls === null ? loadingBox()
          : calls.length ? h("div", { class: "tbl-wrap mt8" }, h("table", { class: "cards-tbl" },
            h("thead", null, h("tr", null, ["Time", "Pair", "Answer", "Tokens", "Cost (USD)", "Result"].map((t, i) => h("th", { class: i === 4 ? "r" : null }, t)))),
            h("tbody", null, calls.map(d => { const a = d.ai || {}, u = a.usage || {}; return h("tr", null,
              h("td", { class: "num nowrap", "data-label": "Time" }, fmtT(d.at)), h("td", { "data-label": "Pair" }, pairName(d.pair)),
              h("td", { "data-label": "Answer" }, (a.answer && a.answer.action) || reasonText(d.reason)),
              h("td", { class: "num", "data-label": "Tokens" }, u.input_tokens !== undefined ? u.input_tokens + " in / " + u.output_tokens + " out" : "none"),
              h("td", { class: "r num", "data-label": "Cost (USD)" }, a.cost_usd !== undefined ? Number(a.cost_usd).toFixed(4) : "no data yet"),
              h("td", { "data-label": "Result" }, a.provider_status ? String(a.provider_status) : reasonText(d.reason))); }))))
            : empty("No AI calls yet.", ai.state === "off" ? "The AI strategy is switched off on the server, so nothing has been asked and nothing has been spent." : "Nothing has been asked yet.", false)),
    h("section", { class: "card" }, h("h2", null, "Risk limits in force"),
      h("p", { class: "sub" }, "Read-only. These are fixed by decision DEC-043 and enforced by code; neither the AI nor this page can change them. A change needs a new decision."),
      h("dl", { class: "dl mt10" }, LIMITS.map(l => [h("dt", null, l[0]), h("dd", null, l[1])]),
        h("dt", null, "AI budget in total"), h("dd", null, usd(limit, 2) + ": heads-up at " + Math.round(warn) + "%, stops asking at 100%"),
        h("dt", null, "AI failure breaker"), h("dd", null, "After " + lim + " calls with an unknown outcome in the last 24 h the AI stops asking, until the oldest of those " + lim + " is more than 24 h old")),
      h("details", { class: "how mt12" }, h("summary", null, "What do these words mean?"),
        h("p", { class: "sub mt6" }, h("b", null, "Drawdown"), ": how far below its best value the portfolio has fallen. ", h("b", null, "Latch"), ": a switch that stays on until you clear it by hand. ", h("b", null, "Exposure"), ": the share of the portfolio currently invested rather than held as cash. ", h("b", null, "Stop-loss"), ": an automatic sell if a position loses too much."))));
}

/* ---------- Errors ---------- */
async function loadErrors() {
  const E = S.E;
  E.loading = true; E.error = null;
  render();
  try {
    const q = new URLSearchParams();
    if (E.filters.status) q.set("status", E.filters.status);
    if (E.filters.level) q.set("level", E.filters.level);
    E.data = await api("/api/errors" + (q.toString() ? "?" + q.toString() : ""));
  } catch (e) { if (isOut(e)) throw e; E.error = e.message; } finally { E.loading = false; }
  render();
}
async function openProblem(fp) {
  const st = S.E.open[fp] || (S.E.open[fp] = {});
  if (st.shown) { st.shown = false; render(); return; }
  st.shown = true; st.loading = true; st.error = null; render();
  try { st.detail = await api("/api/errors/" + encodeURIComponent(fp)); } catch (e) { if (isOut(e)) throw e; st.error = e.message; } finally { st.loading = false; }
  render();
}
function statusPill(g) {
  return g.status === "new" ? pill("bad", "● New") : g.status === "seen" ? pill("warn", "Seen") : pill("off", "✓ Acknowledged");
}
function problemCard(g) {
  const st = S.E.open[g.fingerprint] || {};
  const detail = st.shown ? h("div", { class: "errd", id: "ed-" + g.fingerprint },
    st.loading ? loadingBox() : st.error ? failBox(st.error, () => { st.shown = false; openProblem(g.fingerprint).catch(() => {}); })
      : errDetail(st.detail)) : null;
  return h("article", { class: "card err", "aria-labelledby": "eh-" + g.fingerprint },
    h("div", { class: "err-h" }, h("div", null, h("p", { class: "sub small caps bold" }, compName(g.component)), h("b", { id: "eh-" + g.fingerprint }, g.headline)),
      h("div", null, statusPill(g))),
    h("p", { class: "sub num mt6" }, g.count + " time" + (g.count === 1 ? "" : "s") + " · first " + fmtT(g.first_seen_utc) + " · last " + fmtT(g.last_seen_utc) + " (" + agoIso(g.last_seen_utc) + ")"),
    h("p", { class: "sub" }, "Level: " + (g.level === "error" ? "Error" : "Warning") + " · Strategy: " + (g.last_strategy_id ? stratLabel(g.last_strategy_id) : "not specific to one strategy") + " · Pair: " + (g.last_pair ? pairName(g.last_pair) : "not known") + " · Kind: ", h("code", null, g.error_type)),
    h("div", { class: "mt4" }, g.level === "error" ? h("span", { class: "al yes" }, "🔔 Counts toward alert rule R6 until acknowledged") : h("span", { class: "al no" }, "Warning: expected kind of hiccup, no alert")),
    h("div", { class: "row2" },
      h("button", { class: "exp", type: "button", "aria-expanded": String(!!st.shown), "aria-controls": "ed-" + g.fingerprint, "data-key": "ex-" + g.fingerprint, on: { click: () => { openProblem(g.fingerprint).catch(() => {}); } } }, (st.shown ? "Hide" : "Show") + " detail"),
      g.status === "acknowledged"
        ? h("span", { class: "sub" }, "Acknowledged by " + (g.acknowledged_by || "owner") + (g.acknowledged_at_utc ? ", " + fmtT(g.acknowledged_at_utc) : "") + (g.acknowledged_reason ? ". Reason: " + g.acknowledged_reason : ""))
        : h("button", { class: "btn", type: "button", "data-key": "ack-" + g.fingerprint, on: { click: ev => confirmAck(g, ev.currentTarget) } }, "Acknowledge")),
    detail);
}
function errDetail(d) {
  if (!d) return null;
  const p = d.problem, occ = d.recent_occurrences || [];
  const redacted = h("span", { class: "pill good" }, "secrets redacted");
  return [
    h("div", { class: "reason" }, h("p", { class: "sub small caps" }, "Last message ", redacted), h("pre", null, p.last_message || "(no message recorded)")),
    h("div", { class: "reason" }, h("p", { class: "sub small caps" }, "Stack trace ", redacted), h("pre", null, p.last_traceback || "(no stack trace recorded)")),
    p.last_context ? h("div", { class: "reason" }, h("p", { class: "sub small caps" }, "Context ", redacted), h("pre", null, JSON.stringify(p.last_context, null, 2))) : null,
    h("p", { class: "sub" }, "Where: ", h("code", null, p.location)),
    h("p", { class: "sub" }, "Find it in the logs: ", h("code", null, "fingerprint=" + p.fingerprint)),
    h("div", null, h("p", { class: "sub small caps" }, "Recent occurrences"),
      occ.length ? h("ul", { class: "occ" }, occ.map(o => h("li", null, fmtT(o.at_utc) + (o.strategy_id ? " · " + stratLabel(o.strategy_id) : "") + (o.pair ? " · " + pairName(o.pair) : "") + (o.message ? " · " + o.message : "")))) : h("p", { class: "sub" }, "None stored.")),
    h("p", { class: "hint" }, "Keys, tokens and passwords are removed before an error is stored. Acknowledging only marks that you have seen it; it does not fix anything. If the same error comes back after you acknowledged it, it returns to \"New\"."),
  ];
}
function vErrors() {
  const E = S.E, f = E.filters;
  const reload = () => { loadErrors().catch(() => {}); };
  const sum = E.data ? E.data.summary : null;
  const probs = E.data ? E.data.problems : [];
  let body;
  if (E.error) body = failBox(E.error, reload);
  else if (!E.data) body = loadingBox();
  else if (!probs.length) body = h("section", { class: "card" }, (f.status || f.level) ? empty("No errors match these filters.", "Clear a filter to see more.", false) : empty("No errors recorded since the loop started.", "Every unexpected exception the code raises is written here. An empty list means none has happened.", true));
  else body = probs.map(problemCard);
  return h("div", { class: "stack" },
    h("section", { class: "card" }, h("h2", null, "Errors"),
      h("p", { class: "sub" }, "Every time the code raised an exception, grouped by kind (same error type in the same place)." + (sum ? ` ${sum.unacknowledged_errors} not yet acknowledged; in the last 24 hours ${sum.errors_24h} error${sum.errors_24h === 1 ? "" : "s"} and ${sum.warnings_24h} warning${sum.warnings_24h === 1 ? "" : "s"}.` + (sum.last_error_utc ? " Last error " + fmtT(sum.last_error_utc) + "." : "") : "")),
      h("div", { class: "filters mt12" },
        selectField("ef-status", "Status", [["", "All"], ["new", "New"], ["seen", "Seen"], ["acknowledged", "Acknowledged"]], f.status, v => { f.status = v; reload(); }),
        selectField("ef-level", "Level", [["", "All"], ["error", "Errors"], ["warning", "Warnings"]], f.level, v => { f.level = v; reload(); })),
      h("p", { class: "hint mt8" }, "New: nobody has opened it. Seen: it is a known kind of hiccup or you opened it. Acknowledged: you pressed Acknowledge.")),
    body);
}

/* ---------- System ---------- */
async function loadAudit() {
  try { const r = await api("/api/audit?limit=50"); S.Y.audit = r.audit || []; S.Y.error = null; } catch (e) { if (isOut(e)) throw e; S.Y.error = e.message; }
  render();
}
function vSystem() {
  const o = S.overview;
  if (!o) return S.overviewError ? failBox(S.overviewError, () => refreshOverview(false)) : loadingBox();
  const firingBy = {};
  (o.alerts.firing || []).forEach(f => { firingBy[f.rule] = (firingBy[f.rule] || 0) + 1; });
  const loopOk = o.loop && o.loop.healthy;
  return h("div", { class: "stack" },
    o.run && o.run.started_at && !loopOk ? banner("bad", "The loop has stopped", "No cycle has finished for " + (o.loop.age_seconds === null ? "a long time" : Math.round(o.loop.age_seconds / 60) + " minutes") + ". See the runbook.") : null,
    h("section", { class: "card" }, h("h2", null, "Loop health"),
      h("dl", { class: "dl mt8" },
        h("dt", null, "State"), h("dd", null, !o.run.started_at ? pill("off", "Waiting for first cycle") : loopOk ? pill("good", "● Running") : pill("bad", "✖ Stalled")),
        h("dt", null, "Last cycle"), h("dd", { class: "num" }, o.loop.last_cycle_at ? fmtT(o.loop.last_cycle_at) + " (" + agoIso(o.loop.last_cycle_at) + ")" : "no data yet"),
        h("dt", null, "Healthy means"), h("dd", null, "some strategy recorded equity in the last 40 minutes"),
        h("dt", null, "First cycle"), h("dd", { class: "num" }, o.run.started_at ? fmtT(o.run.started_at) : "no data yet"),
        h("dt", null, "Deployed commit"), h("dd", { class: "num" }, o.deployed_commit || "not reported by the server"),
        h("dt", null, "Data as of"), h("dd", { class: "num" }, o.generated_at ? fmtT(o.generated_at) : "unknown"),
        h("dt", null, "Session"), h("dd", null, S.idleMinutes ? "Signs you out after " + S.idleMinutes + " minutes without activity." : "Signs you out after a period without activity."))),
    h("section", { class: "card" }, h("h2", null, "What alerts you"),
      h("p", { class: "sub" }, "These rules decide what will reach you on Telegram (a later slice; until then this page is the only place these show). Everything else stays on the page."),
      h("div", { class: "tbl-wrap mt8" }, h("table", null,
        h("thead", null, h("tr", null, ["Rule", "It alerts you when", "Right now"].map(t => h("th", null, t)))),
        h("tbody", null, (o.alerts.rules || []).map(r => h("tr", null,
          h("td", null, h("b", null, r.id), " " + r.name), h("td", null, r.when),
          h("td", null, firingBy[r.id] ? pill("bad", "● Firing (" + firingBy[r.id] + ")") : pill("good", "✓ Quiet"))))))),
      h("p", { class: "sub mt10" }, h("b", null, "How alerts behave: "), "no repeat of the same condition within " + o.alerts.repeat_after_hours + " hours."),
      h("h3", { class: "mt12" }, "Page only, never an alert"),
      h("ul", { class: "tl mt4" }, PAGE_ONLY.map(x => h("li", null, h("b", null, x[0]), h("span", { class: "sub" }, x[1]))))),
    h("section", { class: "card" }, h("h2", null, "Kraken price freshness"),
      h("p", { class: "sub" }, "How old the newest price data is for each pair. Over 45 minutes counts as stale, blocks new entries and triggers rule R3."),
      h("div", { class: "tbl-wrap mt8" }, h("table", null, h("thead", null, h("tr", null, ["Pair", "Latest price", "Newest data", "State"].map(t => h("th", null, t)))),
        h("tbody", null, Object.entries(o.prices || {}).map(([pair, p]) => { const ok = p.age_seconds !== null && p.age_seconds <= 45 * 60; return h("tr", null,
          h("td", null, pairName(pair)), h("td", { class: "num" }, p.price === null ? "no data yet" : p.price + " EUR"),
          h("td", { class: "num" }, p.age_seconds === null ? "no data yet" : p.age_seconds < 60 ? "under 1 min ago" : agoMin(p.age_seconds / 60)),
          h("td", null, ok ? pill("good", "✓ Fresh") : pill("bad", "✖ Stale"))); }))))),
    h("section", { class: "card" }, h("h2", null, "On/off audit trail"),
      h("p", { class: "sub" }, "Every switch change, who made it and why. Newest first."),
      S.Y.error ? failBox(S.Y.error, () => { loadAudit().catch(() => {}); })
        : S.Y.audit === null ? loadingBox()
          : S.Y.audit.length ? h("ul", { class: "tl mt8" }, S.Y.audit.map(r => h("li", null,
            h("div", null, h("b", null, stratLabel(r.strategy_id) + " switched " + (r.enabled ? "on" : "off")), h("p", { class: "sub" }, "Reason: " + r.reason)),
            h("div", { class: "sub num right" }, fmtT(r.at_utc), h("br"), r.actor))))
            : h("p", { class: "sub mt8" }, "No changes recorded yet.")));
}

/* ---------- confirm dialogs ---------- */
const dlg = $("#dlg");
let opener = null;
dlg.addEventListener("close", () => { if (opener && document.contains(opener)) opener.focus(); opener = null; });
function dialogForm(title, introNodes, reasonLabel, reasonDefault, confirmLabel, danger, submit) {
  const body = $("#dlg-body");
  clear(body);
  const why = h("input", { id: "why", class: "inp", maxlength: "200", autocomplete: "off", value: reasonDefault || "" });
  const whyErr = h("p", { class: "err-text", role: "alert", hidden: true }, "Please give a short reason.");
  const errBox = h("div", { class: "banner bad mt12", role: "alert", hidden: true });
  const no = h("button", { class: "btn", type: "button", on: { click: () => dlg.close() } }, "Cancel");
  const yes = h("button", { class: "btn " + (danger ? "danger" : "primary"), type: "button" }, confirmLabel);
  let retryAction = null;
  yes.addEventListener("click", async () => {
    if (retryAction) { retryAction(); return; }
    const w = why.value.trim();
    if (!w) { whyErr.hidden = false; why.focus(); return; }
    whyErr.hidden = true; errBox.hidden = true;
    yes.disabled = true; no.disabled = true; why.readOnly = true;
    yes.textContent = "Saving…";
    const fail = (t, p, btnText, action) => {
      clear(errBox);
      errBox.append(h("span", { class: "ic", "aria-hidden": "true" }, "!"), h("div", null, h("b", null, t), h("p", null, p)));
      errBox.hidden = false; no.disabled = false; why.readOnly = false; yes.disabled = false;
      yes.textContent = btnText || "Try again"; retryAction = action || null;
    };
    await submit(w, fail);
  });
  body.append(h("h2", { id: "dlg-title" }, title), introNodes,
    h("label", { for: "why", class: "sub" }, reasonLabel), why, whyErr,
    h("p", { class: "hint mt6" }, "Saved with who (owner, signed in), when, and this reason."), errBox,
    h("div", { class: "row" }, no, yes));
  return why;
}
function confirmSwitch(st, btn) {
  opener = btn;
  const to = !st.enabled, name = stratName(st);
  const intro = to
    ? h("div", null, h("p", null, "It resumes normal operation from the next 15-minute cycle: it may open new positions again" + (st.kind === "ai" ? " and the AI is asked again (this spends from the USD " + (toNum(S.overview.ai.total_limit) || 0).toFixed(0) + " budget)" : "") + "."))
    : h("div", null, h("p", null, "This is a wind-down, not a freeze. After you confirm:"), h("ul", null,
      h("li", null, h("b", null, "No new positions"), " will be opened."),
      st.kind === "ai" ? h("li", null, h("b", null, "The AI is not asked again"), ", so it costs nothing more.") : null,
      h("li", null, "Its ", h("b", null, "stop-loss, take-profit and time exits still run"), ", so nothing is left unwatched."),
      h("li", null, "Its value keeps being recorded, so the comparison stays fair."),
      st.kind === "benchmark" ? h("li", null, "The benchmark never sells, so this only stops its one-time purchases.") : null),
      h("p", { class: "sub" }, "You can switch it back on at any time. Nothing is sold by this switch."));
  const why = dialogForm((to ? "Switch on " : "Switch off ") + name + "?", intro, "Reason (required)", "", to ? "Switch on" : "Switch off", !to, async (reason, fail) => {
    try {
      const r = await api("/api/strategies/" + encodeURIComponent(st.strategy_id) + "/enabled", { method: "POST", body: { enabled: to, reason } });
      dlg.close();
      await refreshOverview(true).catch(() => {});
      const a = r.audit;
      toast(h("p", null, h("b", null, r.changed ? "Saved. " : "Nothing to change. "), name + " is now " + (r.enabled ? "on" : "off (wind-down)") + ". " +
        (a ? "Recorded in the audit trail: " + a.actor + ", " + fmtT(a.at_utc) + ", reason \"" + a.reason + "\"." : "")), "system");
      const nb = main.querySelector('[data-key="sw-' + st.strategy_id + '"]');
      if (nb) nb.focus();
    } catch (e) {
      if (isOut(e)) return;
      saveFail(e, fail);
    }
  });
  dlg.showModal();
  why.focus();
}
function saveFail(e, fail) {
  if (e instanceof ApiError && e.status === 503) fail("Could not save: the database did not answer.", (e.message || "") + " Nothing was written to the audit trail. Wait a minute and try again. If it keeps failing, check the System page and the runbook.");
  else if (e instanceof ApiError && e.status === 403) fail("The page is out of date.", (e.message || "") + " Nothing was changed.", "Reload the page", () => location.reload());
  else fail("Could not save.", (e.message || "Something went wrong.") + (e instanceof ApiError && e.status === 0 ? "" : " Nothing was changed."));
}
function confirmAck(g, btn) {
  opener = btn;
  const why = dialogForm("Acknowledge this error?", h("p", null, "\"" + g.headline + "\""), "Note (required)", "Seen and understood", "Acknowledge", false, async (reason, fail) => {
    try {
      const r = await api("/api/errors/" + encodeURIComponent(g.fingerprint) + "/ack", { method: "POST", body: { reason } });
      dlg.close();
      toast(h("p", null, h("b", null, "Acknowledged. "), "\"" + compName(g.component) + " / " + g.error_type + "\" is marked as seen by you (" + r.acknowledged + " problem" + (r.acknowledged === 1 ? "" : "s") + " updated). It returns to New if it happens again."));
      await Promise.all([loadErrors(), refreshOverview(true)]).catch(() => {});
      const nb = main.querySelector('[data-key="ex-' + g.fingerprint + '"]');
      if (nb) nb.focus();
    } catch (e) {
      if (isOut(e)) return;
      saveFail(e, fail);
    }
  });
  dlg.showModal();
  why.focus(); why.select();
}

/* ---------- theme + start ---------- */
$("#theme").addEventListener("click", () => {
  const root = document.documentElement;
  const cur = root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const next = cur === "dark" ? "light" : "dark";
  root.dataset.theme = next;
  try { localStorage.setItem("atl-theme", next); } catch (e) { /* storage may be blocked */ }
  if (S.signedIn && S.view === "overview") drawChartNow();
});
(function initTheme() {
  try { const t = localStorage.getItem("atl-theme"); if (t === "dark" || t === "light") document.documentElement.dataset.theme = t; } catch (e) { /* ignore */ }
})();

(async function start() {
  renderWho();
  try {
    const me = await api("/api/me");
    S.signedIn = true; S.csrf = me.csrf; S.idleMinutes = me.idle_minutes || null;
    await afterSignIn();
  } catch (e) {
    if (isOut(e)) return;
    clear(main);
    main.append(failBox(e.message || "Could not reach the server.", () => location.reload()));
  }
})();
