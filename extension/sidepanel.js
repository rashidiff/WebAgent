// sidepanel.js - Main extension UI logic

let socket = null;
let isAgentRunning = false;
let runTarget = null;
let cancelPendingApproval = null;
const approvedPages = new Map();
let backendUrl = "http://127.0.0.1:8000";
let authToken = "";
let approvalMode = "full_access";

// DOM Elements
const wsStatus = document.getElementById('ws-status');
const logText = document.getElementById('log-text');
const btnReset = document.getElementById('btn-reset');
const btnStop = document.getElementById('btn-stop');
const messagesContainer = document.getElementById('messages-container');
const promptInput = document.getElementById('prompt-input');
const btnSend = document.getElementById('btn-send');
const approvalPanel = document.getElementById('approval-panel');
const approvalText = document.getElementById('approval-text');
const btnApprove = document.getElementById('btn-approve');
const btnReject = document.getElementById('btn-reject');
const timeline = document.getElementById('timeline');
const btnSettings = document.getElementById('btn-settings');
const btnTheme = document.getElementById('btn-theme');
const settingsPanel = document.getElementById('settings-panel');
const backendUrlInput = document.getElementById('backend-url-input');
const authTokenInput = document.getElementById('auth-token-input');
const approvalModeInput = document.getElementById('approval-mode-input');
const btnSaveSettings = document.getElementById('btn-save-settings');
const btnHistory = document.getElementById('btn-history');
const btnRuns = document.getElementById('btn-runs');
const btnWorkflows = document.getElementById('btn-workflows');
const btnClearHistory = document.getElementById('btn-clear-history');
const historyList = document.getElementById('history-list');
const replayList = document.getElementById('replay-list');
const workflowList = document.getElementById('workflow-list');
const historySearchInput = document.getElementById('history-search-input');
const targetTabLabel = document.getElementById('target-tab');

const TAB_LEVEL_ACTIONS = new Set(["navigate", "back", "forward", "reload"]);
const NAVIGATION_SETTLE_DELAY_MS = 900;
const NAVIGATION_TIMEOUT_MS = 8000;
const HISTORY_PAGE_SIZE = 10;

// Setup WebSocket Connection
function connectWS() {
  wsStatus.textContent = "Connecting...";
  wsStatus.className = "connecting";
  document.getElementById('status-dot').className = "status-indicator";
  
  const wsUrl = backendUrl.replace(/^http:/, "ws:").replace(/^https:/, "wss:").replace(/\/$/, "") + "/ws";
  socket = new WebSocket(wsUrl);
  
  socket.onopen = () => {
    wsStatus.textContent = "Connected";
    wsStatus.className = "connected";
    document.getElementById('status-dot').className = "status-indicator connected";
    console.log("WebSocket connection established");
    socket.send(JSON.stringify({ type: "auth", token: authToken, approval_mode: approvalMode }));
  };
  
  socket.onclose = () => {
    stopSessionState();
    wsStatus.textContent = "Disconnected";
    wsStatus.className = "disconnected";
    document.getElementById('status-dot').className = "status-indicator disconnected";
    console.log("WebSocket connection closed. Reconnecting in 3 seconds...");
    setTimeout(connectWS, 3000);
  };
  
  socket.onerror = (error) => {
    console.error("WebSocket error:", error);
  };
  
  socket.onmessage = async (event) => {
    try {
      const data = JSON.parse(event.data);
      console.log("Received action command:", data);
      
      if (data.type === 'agent_status') {
        updateLog(data.message);
        appendTimeline(getStatusKind(data.message), data.message);
        // If it starts with a result token, post it to the main message log
        if (data.message.startsWith("SUCCESS:") || data.message.startsWith("ERROR:") || data.message.startsWith("FINISHED:")) {
          appendMessage("assistant", data.message);
          stopSessionState();
        }
      } else if (data.type === 'agent_action') {
        try {
          const tab = await getBoundTab(data);
          approvedPages.set(data.action_id, await getTargetPageState(tab));
        } catch (error) {
          sendApprovalResponse(data, false);
          terminateTarget(error.message);
          return;
        }
        updateLog(`Agent Action: ${data.action} on ${data.selector || 'page'}`);
        appendTimeline("action", `${data.action}${data.selector ? ` ${data.selector}` : ""}${data.value ? ` -> ${data.value}` : ""}`);
        const approved = await requestActionApproval(data);
        if (!isAgentRunning) return;
        sendApprovalResponse(data, approved);
        if (!approved) approvedPages.delete(data.action_id);
        updateLog(approved ? "Approval granted. Waiting for backend execution grant..." : "Action rejected by user.");
        return;

      } else if (data.type === 'agent_execute') {
        updateLog(`Executing authorized action: ${data.action} on ${data.selector || 'page'}`);
        appendTimeline("action", `${data.action}${data.selector ? ` ${data.selector}` : ""}${data.value ? ` -> ${data.value}` : ""}`);
        
        let tab;
        try {
          tab = await getBoundTab(data);
          const state = await getTargetPageState(tab);
          const approved = approvedPages.get(data.action_id);
          approvedPages.delete(data.action_id);
          if (approved && (approved.document_id !== state.document_id || approved.url !== state.url || approved.revision !== state.revision)) {
            throw new Error("Target page changed after approval. Start a new run to approve the current page.");
          }
          data.expected_page_state = state;
        } catch (error) {
          sendResult(data, { status: "error", error: error.message });
          terminateTarget(error.message);
          return;
        }
        
        try {
          const response = await executeAgentAction(tab, data);
          acceptTargetPage(response);
          
          if (response && response.status === 'success') {
            sendResult(data, response);
          } else {
            sendResult(data, response || { status: "error", error: "Unknown error in page interaction" });
          }
        } catch (err) {
          if (!isAgentRunning || TAB_LEVEL_ACTIONS.has(data.action) || !/Receiving end does not exist|Could not establish connection/.test(err.message)) {
            sendResult(data, { status: "error", error: err.message,
              screenshot_before: err.screenshot_before, screenshot_after: err.screenshot_after });
            terminateTarget(`Target tab is inaccessible: ${err.message}`);
            return;
          }
          console.warn("Direct messaging failed, trying to inject content script and retry...", err);
          try {
            await getBoundTab(data);
            await injectContentScript(tab.id);
            const response = await executeAgentAction(tab, data);
            acceptTargetPage(response);
            if (response && response.status === 'success') {
              sendResult(data, response);
            } else {
              sendResult(data, response || { status: "error", error: "Execution error after injection" });
            }
          } catch (injectErr) {
            sendResult(data, { status: "error", error: `Script injection failed: ${injectErr.message}`,
              screenshot_before: injectErr.screenshot_before || err.screenshot_before,
              screenshot_after: injectErr.screenshot_after || err.screenshot_after });
            terminateTarget(`Target tab is inaccessible: ${injectErr.message}`);
          }
        }
      }
    } catch (err) {
      console.error("Error processing WebSocket packet:", err);
    }
  };
}

function storageGet(keys, area = "local") {
  return new Promise((resolve) => chrome.storage[area].get(keys, resolve));
}

function storageSet(values, area = "local") {
  return new Promise((resolve) => chrome.storage[area].set(values, resolve));
}

async function loadSettings() {
  const saved = await storageGet({ backendUrl: backendUrl, approvalMode, theme: "dark" }, "local");
  const session = await storageGet({ authToken: "" }, "session");
  backendUrl = normalizeBackendUrl(saved.backendUrl || backendUrl);
  authToken = session.authToken || "";
  approvalMode = saved.approvalMode === "ask_every_time" ? "ask_every_time" : "full_access";
  backendUrlInput.value = backendUrl;
  authTokenInput.value = authToken;
  approvalModeInput.value = approvalMode;
  applyTheme(saved.theme || "dark");
}

function applyTheme(theme) {
  const isLight = theme === "light";
  document.body.classList.toggle("light-theme", isLight);
  btnTheme.title = isLight ? "Switch to dark theme" : "Switch to light theme";
  btnTheme.setAttribute("aria-label", btnTheme.title);
}

async function toggleTheme() {
  const nextTheme = document.body.classList.contains("light-theme") ? "dark" : "light";
  applyTheme(nextTheme);
  await storageSet({ theme: nextTheme }, "local");
}

function normalizeBackendUrl(url) {
  const clean = (url || "").trim().replace(/\/$/, "");
  if (!clean) return "http://127.0.0.1:8000";
  if (!clean.startsWith("http://") && !clean.startsWith("https://")) {
    return `http://${clean}`;
  }
  return clean;
}

async function saveSettings() {
  backendUrl = normalizeBackendUrl(backendUrlInput.value);
  authToken = authTokenInput.value.trim();
  approvalMode = approvalModeInput.value === "ask_every_time" ? "ask_every_time" : "full_access";
  backendUrlInput.value = backendUrl;
  await storageSet({ backendUrl, approvalMode }, "local");
  await storageSet({ authToken }, "session");
  updateLog("Settings saved. Reconnecting...");
  if (socket && socket.readyState !== WebSocket.CLOSED) {
    socket.close();
  } else {
    connectWS();
  }
}

async function loadSessionHistory() {
  historyList.classList.remove("hidden");
  replayList.classList.add("hidden");
  workflowList.classList.add("hidden");
  historyList.textContent = "Loading history...";

  try {
    const response = await fetch(`${backendUrl}/sessions?limit=${HISTORY_PAGE_SIZE}&offset=0`, authFetchOptions());
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const sessions = payload.sessions || [];
    historyList.innerHTML = "";

    if (!sessions.length) {
      historyList.textContent = "No sessions recorded yet.";
      return;
    }

    for (const session of sessions) {
      if (!matchesFilter(`${session.id} ${session.started_at} ${session.ended_at || ""}`)) continue;
      const row = document.createElement("div");
      row.className = "history-item";
      const meta = document.createElement("div");
      meta.className = "history-meta";
      meta.title = session.id;
      meta.textContent = `${new Date(session.started_at).toLocaleString()} ${session.ended_at ? "completed" : "open"}`;
      const button = document.createElement("button");
      button.className = "history-action";
      button.type = "button";
      button.textContent = "Open";
      button.addEventListener("click", () => openSessionHistory(session.id));
      row.appendChild(meta);
      row.appendChild(button);
      historyList.appendChild(row);
    }
  } catch (err) {
    historyList.textContent = `History error: ${err.message}`;
  }
}

async function loadRunReplays() {
  replayList.classList.remove("hidden");
  historyList.classList.add("hidden");
  workflowList.classList.add("hidden");
  replayList.textContent = "Loading replays...";

  try {
    const response = await fetch(`${backendUrl}/runs?limit=${HISTORY_PAGE_SIZE}&offset=0`, authFetchOptions());
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const runs = payload.runs || [];
    replayList.innerHTML = "";

    if (!runs.length) {
      replayList.textContent = "No replays recorded yet.";
      return;
    }

    for (const run of runs) {
      if (!matchesFilter(`${run.id} ${run.prompt} ${run.status}`)) continue;
      const row = document.createElement("div");
      row.className = "history-item";
      const meta = document.createElement("div");
      meta.className = "history-meta";
      meta.title = run.prompt;
      meta.textContent = `${run.status} · ${new Date(run.started_at).toLocaleString()} · ${run.prompt}`;
      const openButton = document.createElement("button");
      openButton.className = "history-action";
      openButton.type = "button";
      openButton.textContent = "Open";
      openButton.addEventListener("click", () => openRunReplay(run.id));
      const saveButton = document.createElement("button");
      saveButton.className = "history-action";
      saveButton.type = "button";
      saveButton.textContent = "Flow";
      saveButton.addEventListener("click", () => saveWorkflowFromRun(run.id));
      row.appendChild(meta);
      row.appendChild(openButton);
      row.appendChild(saveButton);
      replayList.appendChild(row);
    }
  } catch (err) {
    replayList.textContent = `Replay error: ${err.message}`;
  }
}

async function openRunReplay(runId) {
  const replayWindow = window.open("about:blank", "_blank");
  try {
    const response = await fetch(`${backendUrl}/runs/${encodeURIComponent(runId)}`, authFetchOptions());
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const run = await response.json();
    const steps = (run.steps || []).map((step) => `- **${step.event_type}**: ${step.title}`).join("\n");
    appendMessage("assistant", `Replay ${run.id}\n\n**Prompt:** ${run.prompt}\n\n${steps}`);
    const exportResponse = await fetch(
      `${backendUrl}/runs/${encodeURIComponent(runId)}/export?format=html`,
      authFetchOptions()
    );
    if (!exportResponse.ok) throw new Error(`Export HTTP ${exportResponse.status}`);
    const exportBlob = await exportResponse.blob();
    const exportUrl = URL.createObjectURL(exportBlob);
    if (replayWindow) replayWindow.location = exportUrl;
    setTimeout(() => URL.revokeObjectURL(exportUrl), 60_000);
  } catch (err) {
    if (replayWindow) replayWindow.close();
    appendMessage("assistant", `Error loading replay: ${err.message}`);
  }
}

async function saveWorkflowFromRun(runId) {
  try {
    const response = await fetch(`${backendUrl}/runs/${encodeURIComponent(runId)}`, authFetchOptions());
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const run = await response.json();
    const workflowResponse = await fetch(`${backendUrl}/workflows`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...(authToken ? { "X-Agent-Token": authToken } : {}) },
      body: JSON.stringify({
        name: run.prompt.slice(0, 80) || "Saved workflow",
        prompt_template: run.prompt,
        source_run_id: run.id,
        steps: (run.steps || []).map((step) => ({ event_type: step.event_type, title: step.title, action: step.action }))
      })
    });
    if (!workflowResponse.ok) throw new Error(`HTTP ${workflowResponse.status}`);
    appendMessage("assistant", "Workflow saved from replay.");
    await loadWorkflows();
  } catch (err) {
    appendMessage("assistant", `Error saving workflow: ${err.message}`);
  }
}

async function loadWorkflows() {
  workflowList.classList.remove("hidden");
  historyList.classList.add("hidden");
  replayList.classList.add("hidden");
  workflowList.textContent = "Loading workflows...";

  try {
    const response = await fetch(`${backendUrl}/workflows`, authFetchOptions());
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    const workflows = payload.workflows || [];
    workflowList.innerHTML = "";

    if (!workflows.length) {
      workflowList.textContent = "No workflows saved yet.";
      return;
    }

    for (const workflow of workflows) {
      if (!matchesFilter(`${workflow.name} ${workflow.prompt_template}`)) continue;
      const row = document.createElement("div");
      row.className = "history-item";
      const meta = document.createElement("div");
      meta.className = "history-meta";
      meta.title = workflow.prompt_template;
      meta.textContent = `${workflow.name} · ${workflow.prompt_template}`;
      const runButton = document.createElement("button");
      runButton.className = "history-action";
      runButton.type = "button";
      runButton.textContent = "Run";
      runButton.addEventListener("click", () => prepareWorkflowRun(workflow.id));
      const deleteButton = document.createElement("button");
      deleteButton.className = "history-action";
      deleteButton.type = "button";
      deleteButton.textContent = "Del";
      deleteButton.addEventListener("click", () => deleteWorkflow(workflow.id));
      row.appendChild(meta);
      row.appendChild(runButton);
      row.appendChild(deleteButton);
      workflowList.appendChild(row);
    }
  } catch (err) {
    workflowList.textContent = `Workflow error: ${err.message}`;
  }
}

async function prepareWorkflowRun(workflowId) {
  try {
    const response = await fetch(`${backendUrl}/workflows/${encodeURIComponent(workflowId)}/run`, { method: "POST", ...authFetchOptions() });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    promptInput.value = payload.prompt || "";
    promptInput.focus();
    appendMessage("assistant", "Workflow loaded into the prompt box. Review it, adjust parameters if needed, then send.");
  } catch (err) {
    appendMessage("assistant", `Error preparing workflow: ${err.message}`);
  }
}

async function deleteWorkflow(workflowId) {
  try {
    const response = await fetch(`${backendUrl}/workflows/${encodeURIComponent(workflowId)}`, { method: "DELETE", ...authFetchOptions() });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    await loadWorkflows();
  } catch (err) {
    appendMessage("assistant", `Error deleting workflow: ${err.message}`);
  }
}

function matchesFilter(text) {
  const needle = (historySearchInput.value || "").trim().toLowerCase();
  if (!needle) return true;
  return String(text || "").toLowerCase().includes(needle);
}

async function clearSessionHistory() {
  historyList.classList.remove("hidden");
  historyList.textContent = "Clearing history...";

  try {
    const response = await fetch(`${backendUrl}/sessions`, { method: "DELETE", ...authFetchOptions() });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const payload = await response.json();
    historyList.textContent = `Deleted ${payload.deleted || 0} recorded session(s).`;
  } catch (err) {
    historyList.textContent = `Clear history error: ${err.message}`;
  }
}

async function openSessionHistory(sessionId) {
  try {
    const response = await fetch(`${backendUrl}/sessions/${encodeURIComponent(sessionId)}`, authFetchOptions());
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const history = await response.json();
    appendMessage("assistant", `Session ${sessionId}\n\n${history.messages.map((m) => `- **${m.role}**: ${m.content}`).join("\n")}`);
  } catch (err) {
    appendMessage("assistant", `Error loading session history: ${err.message}`);
  }
}

function authFetchOptions() {
  if (!authToken) return {};
  return { headers: { "X-Agent-Token": authToken } };
}

function requestActionApproval(data) {
  const risk = data.risk_level ? data.risk_level.toUpperCase() : "UNKNOWN";
  const target = data.target_summary || `${data.action}${data.selector ? `: ${data.selector}` : ""}`;
  approvalText.textContent = `${risk}: ${data.approval_reason || "Sensitive action"} — ${target}\nTarget: ${runTarget.title} (${runTarget.page.url})`;
  approvalPanel.classList.remove("hidden");
  updateLog("Waiting for user approval...");

  return new Promise((resolve) => {
    const cleanup = () => {
      btnApprove.removeEventListener("click", onApprove);
      btnReject.removeEventListener("click", onReject);
      approvalPanel.classList.add("hidden");
      cancelPendingApproval = null;
    };
    const onApprove = () => {
      cleanup();
      resolve(true);
    };
    const onReject = () => {
      cleanup();
      resolve(false);
    };
    btnApprove.addEventListener("click", onApprove);
    btnReject.addEventListener("click", onReject);
    cancelPendingApproval = onReject;
  });
}

async function executeAgentAction(tab, data) {
  await getBoundTab(data);
  const captureScreenshots = data.capture_screenshots === true;
  const screenshotBefore = captureScreenshots ? await captureVisibleScreenshot(tab) : null;
  let response = null;
  try {
  await getBoundTab(data);
  const currentPage = await getTargetPageState(tab);
  if (data.expected_page_state && (data.expected_page_state.document_id !== currentPage.document_id || data.expected_page_state.url !== currentPage.url || data.expected_page_state.revision !== currentPage.revision)) {
    throw new Error("Target page changed before execution.");
  }
  if (TAB_LEVEL_ACTIONS.has(data.action)) {
    const navigationWait = waitForTabToSettle(tab.id);
    await executeTabAction(tab.id, data.action, data.value);
    await navigationWait;
    await injectContentScript(tab.id);
    response = await sendMessageToTab(tab.id, { type: 'get_dom' });
  } else if (data.action === "get_text") {
    response = await sendMessageToTab(tab.id, { type: 'get_page_text' });
  } else {
    response = await sendMessageToTab(tab.id, {
      type: 'execute_action',
      action: data.action,
      selector: data.selector,
      value: data.value,
      timeout_ms: data.action_timeout_ms,
      expected_page_state: data.expected_page_state,
      expected_fingerprint: data.expected_fingerprint || ""
    });
  }

  const screenshotAfter = captureScreenshots ? await captureVisibleScreenshot(tab) : null;
  return {
    ...(response || {}),
    screenshot_before: screenshotBefore,
    screenshot_after: screenshotAfter
  };
  } catch (error) {
    error.screenshot_before = screenshotBefore;
    error.screenshot_after = captureScreenshots ? await captureVisibleScreenshot(tab) : null;
    throw error;
  }
}

async function captureVisibleScreenshot(tab) {
  const visible = await new Promise(resolve => chrome.tabs.query({ active: true, windowId: tab.windowId }, resolve));
  if (visible[0]?.id !== tab.id) return null;
  return new Promise((resolve) => {
    if (!chrome.tabs || typeof chrome.tabs.captureVisibleTab !== "function") {
      resolve(null);
      return;
    }
    chrome.tabs.captureVisibleTab(tab.windowId, { format: "jpeg", quality: 55 }, (dataUrl) => {
      if (chrome.runtime.lastError) {
        console.warn("Screenshot capture failed:", chrome.runtime.lastError.message);
        resolve(null);
      } else {
        resolve(dataUrl || null);
      }
    });
  });
}

function waitForTabToSettle(tabId) {
  return new Promise((resolve) => {
    let settled = false;
    let quietTimer = null;
    let maxTimer = null;

    const cleanup = () => {
      chrome.tabs.onUpdated.removeListener(onUpdated);
      clearTimeout(quietTimer);
      clearTimeout(maxTimer);
    };

    const finish = () => {
      if (settled) return;
      settled = true;
      cleanup();
      setTimeout(resolve, NAVIGATION_SETTLE_DELAY_MS);
    };

    const onUpdated = (updatedTabId, changeInfo) => {
      if (updatedTabId !== tabId) return;
      if (changeInfo.status === "loading") {
        clearTimeout(quietTimer);
      } else if (changeInfo.status === "complete") {
        finish();
      }
    };

    chrome.tabs.onUpdated.addListener(onUpdated);
    quietTimer = setTimeout(finish, NAVIGATION_SETTLE_DELAY_MS);
    maxTimer = setTimeout(finish, NAVIGATION_TIMEOUT_MS);
  });
}

async function executeTabAction(tabId, action, value) {
  return new Promise((resolve, reject) => {
    const callback = () => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else {
        resolve();
      }
    };

    if (action === "navigate") {
      chrome.tabs.update(tabId, { url: value }, callback);
    } else if (action === "back") {
      chrome.tabs.goBack(tabId, callback);
    } else if (action === "forward") {
      chrome.tabs.goForward(tabId, callback);
    } else if (action === "reload") {
      chrome.tabs.reload(tabId, {}, callback);
    } else {
      reject(new Error(`Unsupported tab action: ${action}`));
    }
  });
}

function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

// Send execution results back to backend WebSocket
function sendResult(actionContext, payload) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    const serialized = serializeActionResult(actionContext, payload);
    appendTimeline(payload.status === "success" ? "result" : "error", payload.error || "Action completed");
    socket.send(serialized);
  }
}

function serializeActionResult(actionContext, payload) {
    const result = {
      type: "action_result",
      run_id: actionContext.run_id,
      action_id: actionContext.action_id,
      capability_token: actionContext.capability_token,
      status: payload.status,
      error: payload.error,
      dom_tree: payload.dom_tree,
      page_text: payload.page_text || null
    };
    for (const field of ["screenshot_before", "screenshot_after"]) {
      const value = payload[field];
      if (actionContext.capture_screenshots === true && typeof value === "string") {
        if (value.length <= 2_000_000 && /^data:image\/(jpeg|png);base64,[A-Za-z0-9+/=]+$/.test(value)) result[field] = value;
        else result.screenshot_warning = "Screenshots omitted: invalid image or schema size limit exceeded.";
      }
    }
    const limit = Math.max(4096, Math.min(actionContext.max_result_bytes || 512000, 10000000));
    const bytes = () => new TextEncoder().encode(JSON.stringify(result)).length;
    if (bytes() > limit) {
      delete result.screenshot_before;
      delete result.screenshot_after;
      result.screenshot_warning = "Screenshots omitted: total WebSocket payload exceeds configured size limit.";
    }
    if (bytes() > limit) {
      delete result.dom_tree;
      delete result.page_text;
      result.status = "error";
      result.error = "Action result exceeds configured WebSocket payload limit.";
    }
    return JSON.stringify(result);
}

function sendApprovalResponse(actionContext, approved) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({
      type: "approval_response",
      run_id: actionContext.run_id,
      action_id: actionContext.action_id,
      capability_token: actionContext.capability_token,
      approved: Boolean(approved)
    }));
  }
}

// Helper to query the active webpage tab
async function getActiveTab() {
  const tabs = await new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, resolve);
  });
  return tabs[0];
}

async function getBoundTab(data) {
  if (!isAgentRunning || !runTarget) throw new Error("No bound target tab for this run.");
  if (runTarget.runId && runTarget.runId !== data.run_id) throw new Error("Stale run packet rejected.");
  runTarget.runId = data.run_id;
  const target = runTarget;
  const tab = await new Promise((resolve, reject) => chrome.tabs.get(target.id, tab => {
    if (chrome.runtime.lastError || !tab) reject(new Error("Target tab was closed or is inaccessible."));
    else resolve(tab);
  }));
  if (runTarget !== target || tab.windowId !== target.windowId) throw new Error("Target tab moved or run stopped. Start a new run.");
  if (!/^https?:\/\//.test(tab.url || "")) throw new Error("Target tab is no longer an accessible web page.");
  return tab;
}

async function getTargetPageState(tab) {
  const target = runTarget;
  if (!target || target.id !== tab.id) throw new Error("Target run was stopped.");
  const revision = target.revision;
  const response = await sendMessageToTab(tab.id, { type: "get_page_state" });
  const page = response?.page_state;
  if (runTarget !== target || target.revision !== revision || !page || page.document_id !== target.page?.document_id || page.url !== target.page?.url) {
    throw new Error("Target page changed outside the agent run. Start a new run on the current page.");
  }
  return { ...page, revision };
}

function acceptTargetPage(response) {
  if (runTarget && response?.page_state) runTarget.page = response.page_state;
}

function terminateTarget(message) {
  if (socket && socket.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: "stop_agent" }));
  appendMessage("assistant", `ERROR: ${message}`);
  updateLog(message);
  stopSessionState();
}

// Helper to send message to webpage
async function sendMessageToTab(tabId, message) {
  return new Promise((resolve, reject) => {
    chrome.tabs.sendMessage(tabId, message, (response) => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else {
        resolve(response);
      }
    });
  });
}

// Helper to inject content script programmatically if needed
async function injectContentScript(tabId) {
  return new Promise((resolve, reject) => {
    chrome.scripting.executeScript({
      target: { tabId: tabId },
      files: ['content.js']
    }, () => {
      if (chrome.runtime.lastError) {
        reject(new Error(chrome.runtime.lastError.message));
      } else {
        resolve();
      }
    });
  });
}

// Update log status text in header console
function updateLog(text) {
  logText.textContent = text;
}

function getStatusKind(message) {
  if (message.startsWith("PLAN:")) return "plan";
  if (message.startsWith("VERIFY:")) return "verify";
  if (message.startsWith("SUCCESS:")) return "done";
  if (message.startsWith("ERROR:")) return "error";
  return "status";
}

function appendTimeline(kind, text) {
  timeline.classList.remove("hidden");
  const item = document.createElement("div");
  item.className = "timeline-item";
  const kindNode = document.createElement("span");
  kindNode.className = "timeline-kind";
  kindNode.textContent = kind;
  const textNode = document.createElement("span");
  textNode.className = "timeline-text";
  textNode.title = text;
  textNode.textContent = text;
  item.appendChild(kindNode);
  item.appendChild(textNode);
  timeline.appendChild(item);

  while (timeline.children.length > 20) {
    timeline.removeChild(timeline.firstChild);
  }
  timeline.scrollTop = timeline.scrollHeight;
}

// Clean status prefixes from agent responses
function cleanOutput(text) {
  let clean = text.trim();
  if (clean.startsWith("SUCCESS:")) {
    clean = clean.substring(8).trim();
  } else if (clean.startsWith("FINISHED:")) {
    clean = clean.substring(9).trim();
  } else if (clean.startsWith("ERROR:")) {
    clean = clean.substring(6).trim();
  }
  return clean;
}

// Simple markdown formatter to convert **, paragraphs, and lists into HTML
function parseMarkdown(text) {
  // Escape HTML to prevent XSS
  let html = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
  
  // Format bold text (**text**)
  html = html.replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>");
  
  // Parse lines to handle lists and paragraphs
  const lines = html.split('\n');
  const result = [];
  let inList = false;
  
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    
    // Bullet point check (- item or * item)
    const listMatch = lines[i].match(/^\s*[-\*]\s+(.*)$/);
    if (listMatch) {
      if (!inList) {
        result.push("<ul>");
        inList = true;
      }
      result.push(`<li>${listMatch[1]}</li>`);
    } else {
      if (inList) {
        result.push("</ul>");
        inList = false;
      }
      
      if (line.length > 0) {
        result.push(`<p>${lines[i]}</p>`);
      }
    }
  }
  
  if (inList) {
    result.push("</ul>");
  }
  
  return result.join('\n');
}

// Append message block to conversation pane
function appendMessage(sender, text) {
  const bubble = document.createElement('div');
  bubble.className = `message-wrapper ${sender}`;
  
  const iconSvg = sender === 'user'
    ? `<svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.8" stroke="currentColor" style="width: 14px; height: 14px;">
        <path stroke-linecap="round" stroke-linejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z" />
       </svg>`
    : `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" style="width: 15px; height: 15px;">
        <path d="M12 2a1 1 0 0 0-1 1v6.5a2.5 2.5 0 0 1-2.5 2.5H2a1 1 0 0 0 0 2h6.5a2.5 2.5 0 0 1 2.5 2.5V21a1 1 0 0 0 2 0v-6.5a2.5 2.5 0 0 1 2.5-2.5H22a1 1 0 0 0 0-2h-6.5a2.5 2.5 0 0 1-2.5-2.5V3a1 1 0 0 0-1-1z" />
       </svg>`; // Iconic 4-point Gemini star spark

  const processedText = sender === 'assistant' ? parseMarkdown(cleanOutput(text)) : parseMarkdown(text);

  bubble.innerHTML = `
    <div class="avatar">
      ${iconSvg}
    </div>
    <div class="bubble">${processedText}</div>
  `;
  
  messagesContainer.appendChild(bubble);
  messagesContainer.scrollTop = messagesContainer.scrollHeight;
}

// Start executing user request
async function startSession() {
  if (isAgentRunning) return;
  const prompt = promptInput.value.trim();
  if (!prompt) return;
  
  isAgentRunning = true;
  promptInput.value = "";
  
  // Disable fields during agent execution
  promptInput.disabled = true;
  btnSend.disabled = true;
  
  appendMessage("user", prompt);
  
  const tab = await getActiveTab();
  if (!tab) {
    appendMessage("assistant", "Error: No active browser tab found.");
    stopSessionState();
    return;
  }
  
  // Check security constraints
  if (tab.url.startsWith("chrome://") || tab.url.startsWith("chrome-extension://") || tab.url.startsWith("https://chromewebstore.google.com")) {
    appendMessage("assistant", "Security Error: Extension APIs cannot script on core chrome:// settings pages or the Chrome Web Store. Please navigate to a standard public website.");
    stopSessionState();
    return;
  }
  runTarget = { id: tab.id, windowId: tab.windowId, title: tab.title || tab.url, revision: 0, page: null, runId: null };
  targetTabLabel.textContent = `Target: ${runTarget.title} · window ${tab.windowId}, tab ${tab.id}`;
  targetTabLabel.classList.remove("hidden");
  
  updateLog("Parsing DOM from active webpage...");
  
  let domTree = [];
  let pageText = null;
  try {
    const response = await sendMessageToTab(tab.id, { type: 'get_dom' });
    if (response && response.status === 'success') {
      domTree = response.dom_tree;
      pageText = response.page_text || null;
      acceptTargetPage(response);
    } else {
      throw new Error(response ? response.error : "Failed to obtain elements");
    }
  } catch (err) {
    console.log("No content script responsive. Injecting content.js...", err);
    try {
      await injectContentScript(tab.id);
      const response = await sendMessageToTab(tab.id, { type: 'get_dom' });
      if (response && response.status === 'success') {
        domTree = response.dom_tree;
        pageText = response.page_text || null;
        acceptTargetPage(response);
      } else {
        throw new Error(response ? response.error : "Failed after script injection");
      }
    } catch (injectErr) {
      appendMessage("assistant", `Script Error: Could not bind agent script to page (${injectErr.message}). Try refreshing the page.`);
      stopSessionState();
      return;
    }
  }
  
  if (socket && socket.readyState === WebSocket.OPEN) {
    if (!runTarget?.page) {
      terminateTarget("Could not identify the target page. Reload the extension and page, then retry.");
      return;
    }
    socket.send(JSON.stringify({
      type: "user_input",
      prompt: prompt,
      dom_tree: domTree,
      page_text: pageText
    }));
    updateLog("Agent loop initialized.");
  } else {
    appendMessage("assistant", "Connection Error: WebSocket is disconnected. Reconnecting. Please resend prompt.");
    connectWS();
    stopSessionState();
  }
}

// Reset UI/Session State to default
function stopSessionState() {
  cancelPendingApproval?.();
  approvedPages.clear();
  const target = runTarget;
  runTarget = null;
  if (target) sendMessageToTab(target.id, { type: "cancel_wait" }).catch(() => {});
  targetTabLabel.classList.add("hidden");
  isAgentRunning = false;
  approvalPanel.classList.add("hidden");
  promptInput.disabled = false;
  btnSend.disabled = false;
  promptInput.focus();
}

// Stop execution command
function sendStopSignal() {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "stop_agent" }));
  }
  updateLog("Stop signal sent. Agent terminating...");
  stopSessionState();
}

// Reset entire session state
function resetSession() {
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "reset_session" }));
  }
  messagesContainer.innerHTML = `
    <div class="message-wrapper assistant">
      <div class="avatar">
        <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke-width="1.5" stroke="currentColor" style="width: 16px; height: 16px;">
          <path stroke-linecap="round" stroke-linejoin="round" d="M9 17.25v1.007a3 3 0 0 1-.879 2.122L7.5 21h9l-.621-.621A3 3 0 0 1 15 18.257V17.25m6-12V15a2.25 2.25 0 0 1-2.25 2.25H5.25A2.25 2.25 0 0 1 3 15V5.25m18 0A2.25 2.25 0 0 0 18.75 3H5.25A2.25 2.25 0 0 0 3 5.25m18 0V12a2.25 2.25 0 0 1-2.25 2.25H5.25A2.25 2.25 0 0 1 3 12V5.25" />
        </svg>
      </div>
      <div class="bubble">
        Session reset. How can I help you on this page?
      </div>
    </div>
  `;
  timeline.innerHTML = "";
  timeline.classList.add("hidden");
  updateLog("Session reset complete.");
  stopSessionState();
}

// Event Listeners
chrome.tabs.onRemoved.addListener(tabId => {
  if (runTarget?.id === tabId) terminateTarget("Target tab was closed. Run stopped.");
});
chrome.tabs.onUpdated.addListener((tabId, change) => {
  if (runTarget?.id === tabId && (change.url || change.status === "loading")) runTarget.revision++;
});
btnSend.addEventListener('click', startSession);
promptInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    startSession();
  }
});
btnStop.addEventListener('click', sendStopSignal);
btnReset.addEventListener('click', resetSession);
btnSettings.addEventListener('click', () => {
  settingsPanel.classList.toggle("hidden");
});
btnTheme.addEventListener('click', toggleTheme);
btnSaveSettings.addEventListener('click', saveSettings);
btnHistory.addEventListener('click', loadSessionHistory);
btnRuns.addEventListener('click', loadRunReplays);
btnWorkflows.addEventListener('click', loadWorkflows);
btnClearHistory.addEventListener('click', clearSessionHistory);
historySearchInput.addEventListener('input', () => {
  if (!historyList.classList.contains("hidden")) loadSessionHistory();
  if (!replayList.classList.contains("hidden")) loadRunReplays();
  if (!workflowList.classList.contains("hidden")) loadWorkflows();
});

// Initialize Connection on Load
loadSettings().then(connectWS);
