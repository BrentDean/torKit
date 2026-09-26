"use strict";

const $ = (id) => document.getElementById(id);
const feedback = $("feedback");
const dialog = $("confirm-dialog");
const field = $("confirmation");
const execute = $("execute");
let pendingAction = null;
let busy = false;

function title(name) {
  return name.charAt(0).toUpperCase() + name.slice(1);
}
function actionName(action, service) {
  return action === "nuke" ? "Nuke TorKit"
    : action === "clear_board" ? "Clear Board posts"
      : "Burn " + title(service);
}
function setBusy(value) {
  busy = value;
  $("refresh").disabled = value;
  $("nuke-button").disabled = value;
  for (const button of document.querySelectorAll(".service-actions button")) {
    button.disabled = value;
  }
}
async function request(path, options) {
  let response;
  try {
    response = await fetch(path, options);
  } catch (_) {
    throw new Error("Operator connection lost.");
  }
  let data;
  try {
    data = await response.json();
  } catch (_) {
    throw new Error("Invalid response from operator console.");
  }
  if (!response.ok) {
    throw new Error(data.error || "Operation failed.");
  }
  return data;
}
function apiAction(action, service, confirmation = "") {
  return request("/api/action", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "Content-Type": "application/json",
      "X-TorKit-Action": "1",
    },
    body: JSON.stringify({action, service, confirmation}),
  });
}
function openConfirmation(action, service) {
  if (busy) return;
  pendingAction = {action, service};
  const isBurn = action === "burn";
  const verb = action === "clear_board" ? "CLEAR" : isBurn ? "BURN" : "NUKE";
  $("dialog-title").textContent = actionName(action, service) + "?";
  $("dialog-description").textContent = action === "clear_board"
    ? "Delete Board discussions and replies, but keep its onion address and authorization key. " +
      "If online, Board will briefly stop and resume. Backups are unaffected."
    : isBurn
      ? "Stop this service and destroy its saved onion identity and private runtime state. " +
        "Configured user content directories remain."
      : "Destroy all TorKit containers, the image, and private runtime identities. " +
        "Configuration and user content directories remain.";
  $("confirmation-label").textContent = "Type " + verb + " to confirm";
  execute.textContent = actionName(action, service);
  field.value = "";
  execute.disabled = true;
  dialog.showModal();
  field.focus();
}
field.addEventListener("input", () => {
  const required = pendingAction && pendingAction.action === "nuke"
    ? "nuke" : pendingAction && pendingAction.action === "clear_board"
      ? "clear" : "burn";
  execute.disabled = field.value.trim().toLowerCase() !== required;
});
$("cancel").addEventListener("click", () => dialog.close());
$("confirm-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy || execute.disabled || !pendingAction) return;
  const {action, service} = pendingAction;
  const confirmation = field.value;
  dialog.close();
  setBusy(true);
  feedback.textContent = actionName(action, service) + " in progress…";
  try {
    const result = await apiAction(action, service, confirmation);
    feedback.textContent = result.message;
    if (action === "nuke") {
      $("active-services").replaceChildren();
      $("available-services").replaceChildren();
      $("active-count").textContent = "0";
      $("available-count").textContent = "0";
      // Nuke terminates the temporary operator server.
    } else {
      await loadStatus();
    }
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    if (action !== "nuke") setBusy(false);
  }
});

async function showAccess(service) {
  if (busy) return;
  try {
    const access = await request("/api/access", {
      method: "POST",
      credentials: "same-origin",
      headers: {"Content-Type": "application/json", "X-TorKit-Action": "1"},
      body: JSON.stringify({service}),
    });
    $("access-title").textContent = title(service) + " access";
    $("access-address").value = access.onion_address;
    $("access-credential").value = access.authorization_key;
    $("access-credential").type = "password";
    $("access-dialog").showModal();
  } catch (error) {
    feedback.textContent = error.message;
  }
}

async function launch(service, label) {
  if (busy) return;
  setBusy(true);
  feedback.textContent = label + " " + title(service) + "…";
  try {
    const result = await apiAction("launch", service);
    feedback.textContent = result.message;
    await loadStatus();
  } catch (error) {
    feedback.textContent = error.message;
    await loadStatus();
  } finally {
    setBusy(false);
  }
}

async function stop(service) {
  if (busy) return;
  setBusy(true);
  feedback.textContent = "Stopping " + title(service) + "…";
  try {
    const result = await apiAction("stop", service);
    feedback.textContent = result.message;
    await loadStatus();
  } catch (error) {
    feedback.textContent = error.message;
  } finally {
    setBusy(false);
  }
}

function badge(label, tone) {
  const element = document.createElement("span");
  element.className = "badge " + tone;
  element.textContent = label;
  return element;
}
function makeButton(label, className, callback) {
  const element = document.createElement("button");
  element.type = "button";
  element.className = className;
  element.textContent = label;
  element.disabled = busy;
  element.addEventListener("click", callback);
  return element;
}
function card(name, item) {
  const active = ["running", "restarting"].includes(item.state);
  const saved = item.identity_saved;
  const row = document.createElement("article");
  row.className = "service";
  const information = document.createElement("div");
  information.className = "service-info";
  const heading = document.createElement("div");
  heading.className = "service-heading";
  const nameEl = document.createElement("h3");
  nameEl.textContent = title(name);
  const stateText = item.ready ? "ONLINE"
    : active ? "STARTING / NEEDS ATTENTION"
    : saved ? "OFFLINE · IDENTITY SAVED" : "NOT DEPLOYED";
  const tone = item.ready ? "online" : active ? "warning" : "muted";
  heading.append(nameEl, badge(stateText, tone));

  const detail = document.createElement("p");
  detail.className = "service-detail";
  detail.textContent = active
    ? "Docker: " + item.state + " · Health: " + item.health +
      " · Restarts: " + item.restarts
    : saved ? "Stopped; the existing onion identity can be resumed."
      : "Launch to create a new onion address and authorization key.";
  information.append(heading, detail);

  const actions = document.createElement("div");
  actions.className = "service-actions";
  if (active) {
    if (!item.ready) {
      actions.append(makeButton("Repair", "primary",
        () => launch(name, "Repairing")));
    }
    if (item.ready) actions.append(makeButton("Access", "primary", () => showAccess(name)));
    if (name === "board" && saved) {
      actions.append(makeButton("Clear posts", "ghost-danger",
        () => openConfirmation("clear_board", name)));
    }
    actions.append(makeButton("Stop", "secondary", () => stop(name)));
    actions.append(makeButton("BURN", "danger",
      () => openConfirmation("burn", name)));
  } else {
    actions.append(makeButton(saved ? "Resume" : "Launch new", "primary",
      () => launch(name, saved ? "Resuming" : "Launching")));
    if (saved) {
      if (name === "board") {
        actions.append(makeButton("Clear posts", "ghost-danger",
          () => openConfirmation("clear_board", name)));
      }
      actions.append(makeButton("Clear saved state", "ghost-danger",
        () => openConfirmation("burn", name)));
    }
  }
  row.append(information, actions);
  return row;
}
function render(statuses) {
  const active = $("active-services");
  const available = $("available-services");
  active.replaceChildren();
  available.replaceChildren();
  let activeCount = 0;
  let availableCount = 0;
  for (const [name, item] of Object.entries(statuses)) {
    if (["running", "restarting"].includes(item.state)) {
      active.append(card(name, item));
      activeCount += 1;
    } else {
      available.append(card(name, item));
      availableCount += 1;
    }
  }
  $("active-count").textContent = String(activeCount);
  $("available-count").textContent = String(availableCount);
  $("no-active").hidden = activeCount !== 0;
}
async function loadStatus() {
  try {
    const statuses = await request("/api/status");
    $("login-panel").hidden = true;
    $("dashboard").hidden = false;
    render(statuses);
  } catch (error) {
    if (error.message === "Operator authentication required.") {
      $("dashboard").hidden = true;
      $("login-panel").hidden = false;
    } else {
      feedback.textContent = error.message;
    }
  }
}
$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await apiLogin($("token").value);
    $("token").value = "";
    $("login-error").textContent = "";
    await loadStatus();
  } catch (error) {
    $("login-error").textContent = error.message;
  }
});
function apiLogin(token) {
  return request("/api/login", {
    method: "POST",
    credentials: "same-origin",
    headers: {"Content-Type": "application/json", "X-TorKit-Action": "1"},
    body: JSON.stringify({token}),
  });
}
$("close-access").addEventListener("click", () => $("access-dialog").close());
$("access-dialog").addEventListener("close", () => {
  $("access-address").value = "";
  $("access-credential").value = "";
  $("access-credential").type = "password";
  $("reveal-credential").textContent = "Reveal";
  $("reveal-credential").setAttribute("aria-pressed", "false");
  $("access-feedback").textContent = "";
});
$("reveal-credential").addEventListener("click", () => {
  const revealed = $("access-credential").type === "password";
  $("access-credential").type = revealed ? "text" : "password";
  $("reveal-credential").textContent = revealed ? "Hide" : "Reveal";
  $("reveal-credential").setAttribute("aria-pressed", String(revealed));
});
async function copyAccessValue(id) {
  try {
    await navigator.clipboard.writeText($(id).value);
    $("access-feedback").textContent = "Copied to clipboard.";
  } catch (_) {
    $("access-feedback").textContent = "Clipboard unavailable; reveal and copy manually.";
  }
}
$("copy-address").addEventListener("click", () => copyAccessValue("access-address"));
$("copy-credential").addEventListener("click", () => copyAccessValue("access-credential"));
$("refresh").addEventListener("click", loadStatus);
$("nuke-button").addEventListener("click",
  () => openConfirmation("nuke", null));
loadStatus();
