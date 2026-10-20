"use strict";
// API values remain text. Only SVG coordinates use approximate browser numbers.
const byId = id => document.getElementById(id);
const missing = value => value === null || value === undefined;
const display = value => missing(value) ? "Unavailable" : String(value);
const node = (tag, text, className) => {
  const element = document.createElement(tag);
  if (!missing(text)) element.textContent = String(text);
  if (className) element.className = className;
  return element;
};
let offset = 0;
let activeId = null;
let listVersion = 0;
let detailVersion = 0;
const limit = 20;
function status(id, text, error = false) {
  byId(id).textContent = text;
  byId(id).className = error ? "error" : "";
}
async function request(path) {
  const response = await fetch(path, {credentials: "same-origin", cache: "no-store"});
  if (!response.ok) {
    const messages = {401: "Authentication required. Reload and sign in.", 403: "Access denied.", 404: "Experiment not found.", 409: "Evidence is incompatible or failed integrity checks. No results are shown.", 503: "Database unavailable. Try again when it is healthy."};
    throw new Error(messages[response.status] || "Report request failed. Try again.");
  }
  return response.json();
}
function table(caption, headings, rows) {
  const wrap = node("div", null, "table-wrap"); wrap.tabIndex = 0; wrap.setAttribute("role", "region"); wrap.setAttribute("aria-label", `${caption} scrollable table`);
  const element = node("table");
  element.append(node("caption", caption));
  const head = node("tr");
  headings.forEach(text => {const th = node("th", text); th.scope = "col"; head.append(th);});
  const thead = node("thead"); thead.append(head); element.append(thead);
  const body = node("tbody");
  rows.forEach(row => {const tr = node("tr"); row.forEach(value => tr.append(node("td", display(value)))); body.append(tr);});
  element.append(body); wrap.append(element); return wrap;
}
function evidence(title, value) {
  const details = node("details"); details.append(node("summary", title), node("pre", JSON.stringify(value, null, 2))); return details;
}
function curve(points, label) {
  const container = node("div");
  const valid = points.filter(point => !missing(point.nav) && Number.isFinite(Number(point.nav)));
  if (valid.length) {
    const values = valid.map(point => Number(point.nav));
    const min = Math.min(...values), range = Math.max(...values) - min || 1;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 600 160"); svg.setAttribute("class", "curve");
    svg.setAttribute("role", "img"); svg.setAttribute("aria-label", `${label} NAV overview. Exact values in following table; gaps are unavailable marks.`);
    let segment = [];
    function flush() {
      if (segment.length) {
        const line = document.createElementNS(svg.namespaceURI, "polyline"); line.setAttribute("points", segment.join(" ")); svg.append(line); segment = [];
      }
    }
    points.forEach((point, i) => {
      if (missing(point.nav) || !Number.isFinite(Number(point.nav))) {flush(); return;}
      segment.push(`${10 + i * 580 / Math.max(points.length - 1, 1)},${145 - (Number(point.nav) - min) * 130 / range}`);
    }); flush(); container.append(svg);
  } else container.append(node("p", "NAV curve unavailable: no qualified marks."));
  container.append(table(`${label} exact NAV samples`, ["UTC time", "NAV", "Cash", "Exposure cost basis", "Open positions", "Mark coverage / reasons"], points.map(point => [point.at_utc, point.nav, point.cash, point.exposure_cost_basis, point.open_positions, point.marks_complete ? "Complete" : (point.mark_reasons || []).join(", ") || "Unavailable"])));
  return container;
}
function render(report) {
  const target = byId("report"); target.replaceChildren();
  const life = report.lifecycle || {};
  target.append(node("p", `State: ${display(life.state)} · Completed cells: ${display(life.completed_cells)} · Recorded at: ${display(life.as_of_utc)}`, "facts"));
  target.append(node("p", `Window: ${display(report.start_at_utc)} → ${display(report.end_at_utc)}. Recorded evidence is historical; refresh does not collect new market data.`, "muted"));
  target.append(node("p", `Consolidated economic net: ${display(report.consolidated_economic_net_pnl)} ${display(report.reporting_currency)}`));
  target.append(node("p", `Minimum evidence sufficient: ${report.evidence_sufficient === true ? "Yes, for the declared engineering evaluation only" : "No"}. No empirical performance claim.`));
  const download = node("a", "Download audit JSON"); download.href = `/api/reports/experiments/${encodeURIComponent(report.experiment_id)}/export`; download.download = ""; target.append(download);
  for (const cell of report.cells || []) {
    const block = node("article", null, "cell");
    block.append(node("h3", `${cell.cell_id} · ${cell.architecture}`));
    block.append(node("p", `Execution evidence: ${cell.provider === "fixture" ? "Fixture AI (not a real provider call)" : cell.provider || "Rules only"}. Completion: ${cell.completed ? "Completed" : "Pending"}.`));
    if (cell.entry_suspended) block.append(node("p", `Risk / safety halt: entries suspended. Reason: ${display(cell.suspension_reason)}.`, "risk"));
    const m = cell.metrics || {}, counts = m.counts || {}, ai = cell.ai_metrics || {};
    block.append(table("Accounting and execution", ["Metric", "Recorded value", "Unit / basis"], [
      ["NAV", m.nav, cell.cash_unit], ["Trading net P&L", m.trading_net_pnl, cell.cash_unit], ["Economic net P&L", cell.economic_net_pnl, report.reporting_currency], ["Operating costs", (cell.costs || {}).total_cost, report.reporting_currency], ["Paid execution fees", m.paid_fees, cell.cash_unit], ["Max drawdown (fraction)", m.max_drawdown, "Qualified NAV"], ["Unresolved open positions", m.final_open_positions, "Count"], ["Maximum exposure cost basis", m.max_exposure_cost_basis, cell.cash_unit], ["Decisions / fills / rejections", [counts.decisions, counts.fills, counts.rejections].map(display).join(" / "), "Counts"], ["AI recorded calls", ai.recorded_calls, "Count"], ["AI input / output tokens", [ai.input_tokens, ai.output_tokens].map(display).join(" / "), "Tokens"]
    ]));
    if ((cell.unavailable_reasons || []).length) block.append(node("p", `Unavailable reasons: ${cell.unavailable_reasons.join(", ")}`, "muted"));
    block.append(curve(cell.equity_curve || [], cell.cell_id));
    block.append(evidence("Risk profile and provenance", {profile: cell.profile, provenance: cell.provenance}), evidence("AI usage and cost evidence", {ai_metrics: cell.ai_metrics, inference_budget: cell.inference_budget, costs: cell.costs}));
    target.append(block);
  }
  target.append(table("Paired comparisons", ["Cell", "Baseline", "Economic net delta", "Trading net delta", "Trading unit"], (report.paired_comparisons || []).map(row => [row.cell_id, row.baseline_cell, row.economic_net_delta, row.trading_net_delta, row.trading_cash_unit])));
  const limitations = node("ul"); (report.limitations || []).forEach(text => limitations.append(node("li", text)));
  target.append(node("h3", "Limitations"), limitations, evidence("Provenance, lifecycle and consolidated expense evidence", { config_hash: report.config_hash, engine_hash: report.engine_hash, dataset_hash: report.dataset_hash, lifecycle: life, costs: report.costs}));
}
async function selectExperiment(id, button) {
  activeId = id; const version = ++detailVersion;
  document.querySelectorAll("#experiment-list button").forEach(item => item.removeAttribute("aria-current"));
  if (button) button.setAttribute("aria-current", "true");
  byId("report").replaceChildren(); byId("detail").setAttribute("aria-busy", "true");
  status("detail-status", "Loading experiment evidence…");
  try {
    const report = await request(`/api/reports/experiments/${encodeURIComponent(id)}`);
    if (version !== detailVersion) return;
    render(report); status("detail-status", `Experiment ${id}`); byId("detail-heading").focus();
  } catch (error) {if (version === detailVersion) status("detail-status", error.message, true);}
  finally {if (version === detailVersion) byId("detail").setAttribute("aria-busy", "false");}
}
async function load() {
  const version = ++listVersion;
  byId("experiment-list").replaceChildren(); byId("next").disabled = true; byId("previous").disabled = true;
  status("list-status", "Loading experiments…");
  try {
    const data = await request(`/api/reports/experiments?limit=${limit}&offset=${offset}`);
    if (version !== listVersion) return;
    for (const item of data.items) {
      const li = node("li"), button = node("button", `${item.experiment_id} · ${display(item.state)} · ${display(item.completed_cells)}/${display(item.total_cells)} cells${item.runtime_compatible === false ? " · Historical runtime" : ""}`);
      button.type = "button"; button.addEventListener("click", () => selectExperiment(item.experiment_id, button));
      if (item.experiment_id === activeId) button.setAttribute("aria-current", "true");
      li.append(button); byId("experiment-list").append(li);
    }
    status("list-status", data.items.length ? `Showing ${offset + 1}–${offset + data.items.length}. Index metadata is unverified; select a report for verified evidence.` : "No experiments on this page.");
    byId("previous").disabled = offset === 0; byId("next").disabled = data.items.length < limit;
  } catch (error) {if (version === listVersion) status("list-status", error.message, true);}
}
async function systemStatus() {
  try {
    const data = await request("/api/reports/status");
    const observation = data.observation || {};
    status("system-status", `${display(data.execution_mode)} · Database: ${display(data.database)} · Trading ready: ${data.trading_ready === true ? "Unexpected enabled state" : "No"}. Server status at: ${display(data.as_of_utc)}. Latest stored receipt: ${display(observation.received_at_utc)}; event time: ${display(observation.event_time_utc)}; source: ${display(observation.source)}; recorded quality: ${display(observation.quality)}; data mode: ${display(observation.data_mode)}. A healthy database does not establish fresh market data.`);
  } catch (error) {status("system-status", error.message, true);}
}
byId("refresh").addEventListener("click", () => {load(); systemStatus(); if (activeId) selectExperiment(activeId);});
byId("previous").addEventListener("click", () => {offset = Math.max(0, offset - limit); load();});
byId("next").addEventListener("click", () => {offset += limit; load();});
load(); systemStatus();

async function loadRuntime() {
  const target = byId("runtime-list"); target.replaceChildren();
  byId("runtime-detail").textContent = "";
  try {
    const data = await request("/api/reports/runtime?limit=20");
    status("runtime-status", "Last durable progress only; this is not a worker liveness probe. Index metadata is unverified.");
    if (!data.items.length) status("runtime-status", "No runtime sessions. Starting the dashboard does not start a worker.");
    for (const item of data.items) {
      const row = node("li");
      const button = node("button", `${item.session_id} · ${display(item.status)} · ${display(item.last_at)}${item.runtime_compatible ? "" : " · incompatible runtime"}`);
      button.addEventListener("click", async () => {
        byId("runtime-detail").textContent = "Loading verified checkpoint…";
        try {
          const report = await request(`/api/reports/runtime/${encodeURIComponent(item.session_id)}`);
          byId("runtime-detail").textContent = JSON.stringify(report, null, 2);
        } catch (error) {byId("runtime-detail").textContent = error.message;}
      }); row.append(button); target.append(row);
    }
  } catch (error) {status("runtime-status", error.message, true);}
}
byId("refresh").addEventListener("click", loadRuntime);
loadRuntime();
