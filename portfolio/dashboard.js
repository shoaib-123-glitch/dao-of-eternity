"use strict";

// Deliberately fixed public classifications. No imported HTML or executable code.
const CONTRACT = Object.freeze({
  version: "1.0", source: "dao-observability", maxBytes: 4000000, maxEvents: 10000,
  fields: ["schema_version","event_id","occurred_at","source","mode","kind","severity","service","actor_ref","guild_ref","request_ref","operation","outcome","reason","family","sqlstate","sqlstate_class","duration_ms"],
  kinds: {authn_failure:"warning",authz_failure:"warning",unauthorized_command:"warning",transaction_replay:"warning",command_submitted:"info",command_completed:"info",database_failure:"error",service_start:"info",service_stop:"info",unexpected_error:"error",health_observation:"info"},
  operations: ["command","start","qi","hunt","craft","equip","explore","sect_commission","sect_project","story","npc","technique","bond","startup","shutdown","health","connect","guide","companion","cultivation","inventory","adopt","recover"],
  outcomes: ["accepted","denied","duplicate","success","failure","healthy","unhealthy","unknown"],
  reasons: ["not_owner","not_guild","missing_permission","unknown_operation","duplicate_interaction","cooldown","rate_limit","authentication_failed","connection_refused","connection_lost","timeout","configuration","server_rejection","decode","python","expected","unexpected","disabled","not_observed","synthetic","operator_shutdown"],
  families: ["authentication","authorization","server","driver","timeout","connection","python","configuration","unknown"],
  sqlstates: ["08000","08001","08003","08004","08006","08007","08P01","22000","22003","22007","22012","22P02","23000","23502","23503","23505","23514","25000","25001","25P02","28000","28P01","40001","40P01","42000","42501","42601","42703","42804","42883","42P01","42P07","53000","53100","53200","53300","53400","55000","57014","57P01","57P02","57P03","58000","58030","XX000"],
  rules: ["AUTHZ_BURST","REPLAY_BURST","COMMAND_RATE","DATABASE_OUTAGE","SERVICE_INTERRUPTION","AUTHN_BURST"]
});

function fail() { throw new Error("Invalid synthetic export."); }
function record(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
function exact(value, keys) { return record(value) && Object.keys(value).sort().join("|") === [...keys].sort().join("|"); }
function timestamp(value) {
  if (typeof value !== "string" || !/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$/.test(value)) return false;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) && date.getUTCFullYear() >= 1 && date.toISOString() === value;
}
function choice(value, values) { return value === null || (typeof value === "string" && values.includes(value)); }
function provenance(value, collection) {
  if (!exact(value, ["schema_version","source","mode",collection]) || value.schema_version !== CONTRACT.version || value.source !== CONTRACT.source || value.mode !== "synthetic" || !Array.isArray(value[collection])) fail();
}
function validateEvents(value) {
  provenance(value, "events");
  if (value.events.length > CONTRACT.maxEvents) fail();
  const seen = new Set();
  for (const e of value.events) {
    if (!exact(e, CONTRACT.fields) || e.schema_version !== CONTRACT.version || e.source !== CONTRACT.source || e.mode !== "synthetic") fail();
    if (typeof e.event_id !== "string" || !/^[0-9a-f]{32}$/.test(e.event_id) || seen.has(e.event_id) || !timestamp(e.occurred_at)) fail();
    seen.add(e.event_id);
    if (!Object.hasOwn(CONTRACT.kinds, e.kind) || e.severity !== CONTRACT.kinds[e.kind] || !["application","postgresql","container"].includes(e.service)) fail();
    for (const prefix of ["actor","guild","request"]) if (e[prefix + "_ref"] !== null && (typeof e[prefix + "_ref"] !== "string" || !new RegExp("^" + prefix + "_[0-9a-f]{24}$").test(e[prefix + "_ref"]))) fail();
    for (const [key, values] of [["operation",CONTRACT.operations],["outcome",CONTRACT.outcomes],["reason",CONTRACT.reasons],["family",CONTRACT.families],["sqlstate",CONTRACT.sqlstates]]) if (!choice(e[key],values)) fail();
    if (e.sqlstate_class !== (e.sqlstate === null ? null : e.sqlstate.slice(0,2))) fail();
    if (e.duration_ms !== null && (typeof e.duration_ms !== "number" || !Number.isFinite(e.duration_ms) || e.duration_ms < 0 || e.duration_ms > 3600000)) fail();
  }
  return [...value.events].sort((a,b) => a.occurred_at.localeCompare(b.occurred_at) || a.event_id.localeCompare(b.event_id));
}
function validateAlerts(value, events) {
  provenance(value, "alerts");
  if (value.alerts.length > 2000) fail();
  const byId = new Map(events.map(e => [e.event_id,e]));
  const seen = new Set();
  for (const a of value.alerts) {
    if (!exact(a,["rule_id","severity","first_seen","last_seen","count","actor_ref","service","event_ids"]) || !CONTRACT.rules.includes(a.rule_id) || !["high","medium","low"].includes(a.severity)) fail();
    if (!timestamp(a.first_seen) || !timestamp(a.last_seen) || a.first_seen > a.last_seen || !Number.isSafeInteger(a.count) || a.count < 1 || a.count > CONTRACT.maxEvents || !Array.isArray(a.event_ids) || a.event_ids.length !== a.count) fail();
    if (!["application","postgresql","container"].includes(a.service) || (a.actor_ref !== null && (typeof a.actor_ref !== "string" || !/^actor_[0-9a-f]{24}$/.test(a.actor_ref)))) fail();
    if (new Set(a.event_ids).size !== a.event_ids.length) fail();
    for (const id of a.event_ids) {
      const e = byId.get(id);
      const actorRule = !["DATABASE_OUTAGE","SERVICE_INTERRUPTION"].includes(a.rule_id);
      if (!e || e.service !== a.service || (actorRule && a.actor_ref !== null && e.actor_ref !== a.actor_ref) || e.occurred_at < a.first_seen || e.occurred_at > a.last_seen) fail();
    }
    const key = a.rule_id + ":" + a.event_ids.join(",");
    if (seen.has(key)) fail();
    seen.add(key);
  }
  return value.alerts;
}

const state = {events: [], alerts: [], envelope: null};
const el = id => document.getElementById(id);
const node = (tag, text, className) => { const n = document.createElement(tag); n.textContent = String(text); if (className) n.className = className; return n; };
function notify(message, error=false) { el("notice").textContent = message; el("notice").className = error ? "error" : ""; }
function renderEvents() {
  const kind = el("kind-filter").value;
  const values = state.events.filter(e => !kind || e.kind === kind);
  el("timeline").replaceChildren();
  // A bounded visible page keeps large exports responsive. All evidence stays in export.
  for (const e of values.slice(0,500)) {
    const row = document.createElement("tr");
    const when = node("td", e.occurred_at.replace("T"," ").replace("Z",""));
    const signal = node("td", e.kind.replaceAll("_"," ")); signal.append(node("span", e.severity,"sub"));
    const context = node("td", e.operation || e.service); context.append(node("span",e.actor_ref || "service-wide","sub"));
    const outcome = node("td",e.outcome || "—"); outcome.append(node("span",e.reason || e.family || "—","sub"));
    row.append(when,signal,context,outcome); el("timeline").append(row);
  }
  el("shown").textContent = "Showing " + Math.min(values.length,500) + " of " + values.length + " matching events (maximum 500 displayed).";
}
function renderAlerts() {
  el("alert-count").textContent = String(state.alerts.length); el("alerts").replaceChildren();
  if (!state.alerts.length) el("alerts").append(node("p","No analyzer alerts loaded.","muted"));
  for (const a of state.alerts) {
    const box = node("div","","alert");
    box.append(node("b",a.rule_id + " · " + a.severity.toUpperCase()),node("p",String(a.count) + " events · " + (a.actor_ref || a.service)),node("p",a.first_seen + " → " + a.last_seen));
    el("alerts").append(box);
  }
}
async function readJson(file) {
  if (!file || !/\.json$/i.test(file.name) || file.size > CONTRACT.maxBytes) fail();
  return JSON.parse(await file.text());
}
function download(filename, content, type) {
  const blob = new Blob([content],{type}); const url = URL.createObjectURL(blob); const link = document.createElement("a");
  link.href = url; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
}
function investigation() {
  return {schema_version:CONTRACT.version,source:"dao-observability-investigation",mode:"synthetic",status:el("status").value,notes:el("notes").value.slice(0,6000),events:state.envelope.events,alerts:state.alerts,caveat:"Synthetic training data. Alerts are patterns, not proof of attack. Notes require manual privacy review."};
}
el("events-file").addEventListener("change",async event => {
  const file = event.target.files[0]; if (!file) return;
  try {
    const envelope = await readJson(file); const events = validateEvents(envelope);
    state.envelope = envelope; state.events = events; state.alerts = [];
    el("event-count").textContent = String(events.length); el("notes").value = ""; el("status").selectedIndex = 0;
    el("range").textContent = events.length ? events[0].occurred_at + " → " + events[events.length-1].occurred_at : "Empty synthetic export";
    el("alerts-file").disabled = false; el("export-json").disabled = false; el("export-text").disabled = false;
    el("kind-filter").replaceChildren(node("option","All event kinds")); el("kind-filter").firstChild.value = "";
    for (const kind of [...new Set(events.map(e => e.kind))].sort()) { const option = node("option",kind.replaceAll("_"," ")); option.value = kind; el("kind-filter").append(option); }
    renderEvents(); renderAlerts(); notify("Synthetic events validated. Optional: select the matching analyzer alerts. Previous notes were cleared.");
  } catch (_) { notify("Import rejected: use a bounded synthetic v1.0 events.json. No imported values are shown in errors.",true); }
  finally { event.target.value = ""; }
});
el("alerts-file").addEventListener("change",async event => {
  const file = event.target.files[0]; if (!file) return;
  try { state.alerts = validateAlerts(await readJson(file),state.events); renderAlerts(); notify("Analyzer alerts validated against loaded event IDs; thresholds were not recalculated in the browser."); }
  catch (_) { notify("Alert import rejected: select matching synthetic analyzer alerts.json.",true); }
  finally { event.target.value = ""; }
});
el("kind-filter").addEventListener("change",renderEvents);
el("export-json").addEventListener("click",() => download("dao_synthetic_investigation.json",JSON.stringify(investigation(),null,2),"application/json"));
el("export-text").addEventListener("click",() => {
  const report = investigation();
  const lines = ["DAO OBSERVATORY — SYNTHETIC ANALYST REPORT","",report.caveat,"","Disposition: " + report.status,"Events: " + state.events.length,"Analyzer-supplied alerts: " + report.alerts.length,"","ALERT EVIDENCE",...report.alerts.map(a => a.rule_id + " | " + a.severity + " | " + a.count + " | " + a.first_seen + " | " + a.last_seen),"","ANALYST NOTES",report.notes || "No notes entered.","","TIMELINE",...state.events.map(e => [e.occurred_at,e.kind,e.operation || e.service,e.outcome || "—",e.reason || "—"].join(" | "))];
  download("dao_synthetic_incident_report.txt",lines.join("\n"),"text/plain;charset=utf-8");
});
