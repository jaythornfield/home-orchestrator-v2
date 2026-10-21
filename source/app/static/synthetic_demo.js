"use strict";
let runtimeId = null;
let phase = null, generation = 0, unlocked = false, unlocking = false;
let startPending = false, startAttempted = false, pollFailed = false;
let startPayload = null, lastRevision = -1, retryDelay = 250, serviceStopped = false;
let pollTimer = null, pollRequest = null, pollSequence = 0, refreshQueued = false;
let controlPending = false, sceneGeneration = -1;
const REQUEST_TIMEOUT_MS = 4000;
const byId = id => document.getElementById(id);
const text = (id, value) => { byId(id).textContent = String(value ?? ""); };
function paragraph(parent, value, className) { const p = document.createElement("p"); p.textContent = value; if (className) p.className = className; parent.append(p); }
function failure(message, status = 0, kind = "network") {
  const error = new Error(message); error.status = status; error.kind = kind; return error;
}
async function api(path, body, ticket = null) {
  const controller = new AbortController();
  if (ticket !== null) ticket.controller = controller;
  let timer;
  const request = fetch(path, body === undefined ? {cache:"no-store", credentials:"same-origin", signal:controller.signal} : {method:"POST", credentials:"same-origin", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body), signal:controller.signal}).then(async response => {
    if (!response.ok) throw failure(`Preview request rejected (HTTP ${response.status}).`, response.status, "http");
    try { return await response.json(); }
    catch { throw failure("Preview returned an unreadable response. Retrying status.", response.status, "json"); }
  });
  const deadline = new Promise((resolve, reject) => {
    timer = setTimeout(() => { controller.abort(); reject(failure("Preview request timed out. Checking status; do not start again.", 0, "timeout")); }, REQUEST_TIMEOUT_MS);
  });
  try { return await Promise.race([request, deadline]); }
  catch (error) {
    if (error.status !== undefined) throw error;
    throw failure("Local preview request interrupted. Retrying status.", 0, error.name === "AbortError" ? "aborted" : "network");
  } finally { clearTimeout(timer); }
}
function unlockFailure(error) {
  if (error.status === 410) return "Scene changed. Refreshing its identity; use the code in the current launch window to unlock again.";
  if (error.status === 401) return "Preview access was not accepted. Re-enter the current launch window code; no demo was started.";
  if (error.status === 422) return "Preview input format was not accepted. Re-enter the current launch window code.";
  if (error.status === 503) return "Local audit or service is unavailable. Use STOP in the launch window.";
  if (error.status === 403) return "Local page boundary check failed. Reopen the address in the launch window.";
  return "Local preview is not connected or the request timed out. Checking status; unlock again after reconnection.";
}
function callbackAudit(interfaceName, callback, correlationId, submitter, gesture, id) {
  return api("/api/demo/client-event", {runtime_id:id, correlation_id:correlationId,
    interface:interfaceName, callback, submitter, gesture});
}
function updateControls() {
  // Keep Unlock and Start in separate stable positions: a second Unlock click cannot fall on Start.
  byId("access-panel").hidden = false; byId("start-panel").hidden = false;
  byId("preview-key").disabled = unlocked || unlocking || controlPending || runtimeId === null;
  byId("unlock-preview").disabled = unlocked || unlocking || controlPending || runtimeId === null;
  const blocked = !unlocked || unlocking || controlPending || startPending || phase !== "NOT_STARTED" || serviceStopped;
  byId("start-preview").disabled = blocked; byId("profile").disabled = blocked || startAttempted;
  byId("start-preview").textContent = startAttempted && !blocked ? "Retry the same Start" : "Start synthetic preview";
  byId("stop-app").disabled = !unlocked || controlPending || serviceStopped;
  byId("stop-demo").disabled = !unlocked || controlPending || phase !== "RUNNING";
  byId("new-scene").disabled = !unlocked || controlPending || !["FINISHED", "STOPPED"].includes(phase) || serviceStopped;
}
function invalidatePolling() {
  if (pollRequest && pollRequest.controller) pollRequest.controller.abort();
  if (pollTimer !== null) clearTimeout(pollTimer);
  pollTimer = null;
}
function lockControls(message) {
  invalidatePolling();
  generation++; unlocked = false; unlocking = false; startPending = false; pollFailed = false;
  byId("preview-key").value = ""; updateControls(); text("control-message", message);
}
function scheduleRefresh(immediate = false) {
  if (serviceStopped) return;
  if (pollTimer !== null) clearTimeout(pollTimer);
  pollTimer = null;
  if (pollRequest !== null) { refreshQueued = true; return; }
  pollTimer = setTimeout(refresh, immediate ? 0 : retryDelay);
}
function render(data) {
  if (Number.isInteger(data.scene_generation) && data.scene_generation < sceneGeneration) return;
  if (Number.isInteger(data.scene_generation)) sceneGeneration = data.scene_generation;
  if (data.runtime_id === runtimeId && data.revision < lastRevision) return;
  lastRevision = data.revision;
  runtimeId = data.runtime_id; phase = data.phase;
  if (unlocked && data.control_unlocked === false) lockControls("Controls expired. Re-enter the local code; this does not start a demo.");
  else if (data.control_unlocked === true && !unlocking) unlocked = true;
  text("service-state", data.service_state); text("build-id", data.build_id);
  if (data.safety_limit) {
    const remaining = Math.max(0, Math.ceil(data.safety_limit.remaining_seconds));
    text("safety-time", `Safety limit: ${Math.floor(remaining/60)}m ${remaining%60}s left, measured from app launch; changing scenes does not restart it.`);
  }
  text("runtime-id", runtimeId); text("effects", data.autonomous_effects); text("approvals", data.per_action_approvals); text("roots", data.distinct_command_roots);
  text("setting", data.resource_state.setting); text("state-version", data.resource_state.version); text("phase", data.phase); text("background", data.background); text("decision", data.safety_decision);
  if (data.current_need) {
    text("need", `${data.current_need.subject} → ${data.current_need.targets.join(" / ")} · ${data.current_need.satisfied ? "satisfied" : "not yet satisfied"}`);
    text("need-source", data.current_need.source_category);
  } else { text("need", "Waiting for a trusted observation."); text("need-source", ""); }
  const auth = byId("authorization"); auth.replaceChildren();
  if (data.authorization) {
    paragraph(auth, "Two virtual issuers · one standing envelope");
    paragraph(auth, data.authorization.envelope, "mono");
    paragraph(auth, `Valid ticks ${data.authorization.start}–${data.authorization.end} (exclusive end) · current ${data.authorization.tick}`);
    paragraph(auth, `Remaining roots ${data.authorization.remaining_roots} / effects ${data.authorization.remaining_effects}`);
    paragraph(auth, `Covered: ${data.authorization.covered.join(", ")} · affected now: ${data.authorization.actual_affected.join(", ")}`);
    if (data.authorization.revoked.length) paragraph(auth, `Revoked coverage: ${data.authorization.revoked.join(", ")}`);
  } else paragraph(auth, "Not started. Two independent virtual endorsements are required.", "hint");
  const actions = byId("actions"); actions.replaceChildren();
  data.actions.forEach((action, index) => {
    const row = document.createElement("article"); row.className = "action-row";
    paragraph(row, `Action ${index+1} · ${action.effect} · ${action.status}`, "action-title");
    paragraph(row, `State ${action.state_before} → ${action.confirmed_version ?? "unconfirmed"} · submissions ${action.submissions} · recovery queries ${action.queries}`);
    paragraph(row, `Command root: ${action.root}`, "mono"); paragraph(row, `Episode: ${action.episode}`, "mono");
    actions.append(row);
  });
  if (!data.actions.length) paragraph(actions, "No autonomous command has been created.", "hint");
  const timeline = byId("timeline"); timeline.replaceChildren();
  data.audit.forEach(event => { const li = document.createElement("li"); const tick = document.createElement("span"); tick.className = "tick"; tick.textContent = `tick ${event.tick}`;
    const detail = document.createElement("span"); detail.textContent = `${event.kind} · ${event.effect || (event.targets || []).join(" / ") || event.subject || ""} · ${event.source_category}`; li.append(tick, detail); timeline.append(li); });
  updateControls();
}
byId("access-form").addEventListener("submit", async event => {
  event.preventDefault();
  if (unlocking || unlocked || controlPending || runtimeId === null) return;
  const epoch = ++generation, id = runtimeId, key = byId("preview-key").value;
  invalidatePolling();
  byId("preview-key").value = ""; unlocking = true; pollFailed = false; updateControls();
  try {
    const correlationId = crypto.randomUUID();
    callbackAudit("SESSION", "SESSION_SUBMIT", correlationId,
      event.submitter === byId("unlock-preview") ? "UNLOCK_BUTTON" : "IMPLICIT", event.isTrusted === true, id).catch(() => {
        if (epoch === generation) text("control-message", "Gesture note unavailable; authentication is checked separately.");
      });
    if (epoch !== generation || id !== runtimeId) return;
    await api("/api/demo/session", {runtime_id:id, preview_key:key, correlation_id:correlationId});
    if (epoch !== generation || id !== runtimeId) return;
    generation++; invalidatePolling(); unlocking = false; unlocked = true; updateControls();
    text("control-message", "Local preview controls unlocked. Household member identity is not asserted.");
  } catch (error) {
    if (epoch !== generation || id !== runtimeId) return;
    lockControls(unlockFailure(error));
  } finally {
    if (epoch === generation) { unlocking = false; updateControls(); }
    scheduleRefresh(true);
  }
});
byId("start-form").addEventListener("submit", event => {
  event.preventDefault();
  if (runtimeId !== null) callbackAudit("START", "START_FORM_SUBMIT_BLOCKED", crypto.randomUUID(),
    "IMPLICIT", event.isTrusted === true, runtimeId).catch(() => {});
});
byId("start-preview").addEventListener("click", async event => {
  // Native keyboard button activation also produces click. Form submit never starts.
  if (event.currentTarget !== byId("start-preview") || event.isTrusted !== true) return;
  if (!unlocked || startPending || phase !== "NOT_STARTED") return;
  const epoch = ++generation, id = runtimeId;
  startPending = true; startAttempted = true; pollFailed = false; updateControls();
  try {
    if (startPayload === null) startPayload = {runtime_id:id, profile:byId("profile").value, correlation_id:crypto.randomUUID()};
    const correlationId = startPayload.correlation_id;
    callbackAudit("START", "START_CLICK", correlationId, "START_BUTTON", true, id).catch(() => {});
    if (epoch !== generation || id !== runtimeId) return;
    await api("/api/demo/start", startPayload);
    if (epoch !== generation || id !== runtimeId) return;
    text("control-message", "Trusted source started. Coordination continues in the background.");
  } catch (error) {
    if (epoch !== generation || id !== runtimeId) return;
    if (error.status === 401) {
      lockControls("Preview controls expired. Enter this process's preview code again.");
    } else if (error.status === 410) {
      lockControls("Scene changed. Use the code in the current launch window to unlock again.");
    } else text("control-message", "Start result unavailable. Checking status; do not start again.");
  } finally {
    if (epoch === generation) { startPending = false; updateControls(); }
    scheduleRefresh(true);
  }
});
async function refresh() {
  pollTimer = null;
  if (pollRequest !== null) return;
  const ticket = {sequence:++pollSequence, generation}; pollRequest = ticket;
  try {
    const data = await api("/api/demo/status", undefined, ticket);
    if (ticket !== pollRequest || ticket.generation !== generation || ticket.sequence !== pollSequence) return;
    if (runtimeId !== null && data.runtime_id !== runtimeId) {
      startAttempted = false; startPayload = null; lastRevision = -1; lockControls("Scene changed. Use the code in the current launch window to unlock again.");
    }
    retryDelay = 250;
    render(data);
    if (pollFailed) { pollFailed = false; text("control-message", "Preview status recovered."); }
  } catch (error) {
    if (ticket.generation === generation) { retryDelay = Math.min(4000, retryDelay * 2); text("service-state", "Disconnected — check the launch window"); pollFailed = true; text("control-message", error.message || "Local preview unavailable. Retrying status."); }
  } finally {
    if (pollRequest === ticket) pollRequest = null;
    const immediate = refreshQueued; refreshQueued = false; scheduleRefresh(immediate);
  }
}
async function localControl(path) {
  if (!unlocked || controlPending || runtimeId === null) return;
  const epoch = ++generation, id = runtimeId;
  controlPending = true; invalidatePolling(); updateControls();
  try {
    const data = await api(path, {runtime_id:id});
    if (epoch !== generation) return;
    if (path.endsWith("shutdown")) {
      serviceStopped = true; text("service-state", "Stopping / disconnected");
      text("control-message", "App is stopping. The launch window records the final result.");
      invalidatePolling();
    } else if (path.endsWith("reset")) {
      // Adopt the response before enabling input. Old status requests retain the old generation.
      runtimeId = data.runtime_id; sceneGeneration = data.scene_generation;
      phase = "NOT_STARTED"; startAttempted = false; startPayload = null; lastRevision = -1;
      lockControls("New scene prepared. Use the SAME code in the current launch window to unlock, choose a scene, then explicitly Start.");
      text("phase", "NOT_STARTED"); text("effects", 0); text("roots", 0); text("approvals", 0);
      text("setting", "REST"); text("state-version", 0); text("runtime-id", runtimeId);
    }
  } catch (error) {
    if (epoch !== generation) return;
    if ([401,410,422,403,503].includes(error.status)) lockControls(unlockFailure(error));
    else text("control-message", "Control result unavailable. Checking the same scene; use STOP in the launch window if needed.");
  } finally {
    controlPending = false; updateControls(); scheduleRefresh(true);
  }
}
byId("stop-demo").addEventListener("click", () => localControl("/api/demo/stop"));
byId("new-scene").addEventListener("click", () => localControl("/api/demo/reset"));
byId("stop-app").addEventListener("click", () => localControl("/api/demo/shutdown"));
updateControls(); refresh();
