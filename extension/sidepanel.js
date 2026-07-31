// sidepanel.js - Main extension UI logic

let socket = null;
let isAgentRunning = false;
let backendUrl = "http://127.0.0.1:8000";
let authToken = "";

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
const settingsPanel = document.getElementById('settings-panel');
const backendUrlInput = document.getElementById('backend-url-input');
const authTokenInput = document.getElementById('auth-token-input');
const btnSaveSettings = document.getElementById('btn-save-settings');
const btnHistory = document.getElementById('btn-history');
const historyList = document.getElementById('history-list');

const TAB_LEVEL_ACTIONS = new Set(["navigate", "back", "forward", "reload"]);

// Setup WebSocket Connection
function connectWS() {
  wsStatus.textContent = "Connecting...";
  wsStatus.className = "connecting";
  document.getElementById('status-dot').className = "status-indicator";
  
  let wsUrl = backendUrl.replace(/^http:/, "ws:").replace(/^https:/, "wss:").replace(/\/$/, "") + "/ws";
  if (authToken) {
    wsUrl += `?token=${encodeURIComponent(authToken)}`;
  }
  socket = new WebSocket(wsUrl);
  
  socket.onopen = () => {
    wsStatus.textContent = "Connected";
    wsStatus.className = "connected";
    document.getElementById('status-dot').className = "status-indicator connected";
    console.log("WebSocket connection established");
  };
  
  socket.onclose = () => {
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
        updateLog(`Agent Action: ${data.action} on ${data.selector || 'page'}`);
        appendTimeline("action", `${data.action}${data.selector ? ` ${data.selector}` : ""}${data.value ? ` -> ${data.value}` : ""}`);

        if (data.requires_approval) {
          const approved = await requestActionApproval(data);
          if (!approved) {
            sendResult({ status: "error", error: `User rejected action: ${data.approval_reason || data.action}` });
            updateLog("Action rejected by user.");
            return;
          }
        }
        
        const tab = await getActiveTab();
        if (!tab) {
          sendResult({ status: "error", error: "No active browser tab found" });
          return;
        }
        
        try {
          const response = await executeAgentAction(tab, data);
          
          if (response && response.status === 'success') {
            sendResult({ status: "success", dom_tree: response.dom_tree, page_text: response.page_text || null });
          } else {
            sendResult({ status: "error", error: response ? response.error : "Unknown error in page interaction" });
          }
        } catch (err) {
          console.warn("Direct messaging failed, trying to inject content script and retry...", err);
          try {
            await injectContentScript(tab.id);
            const response = await executeAgentAction(tab, data);
            if (response && response.status === 'success') {
              sendResult({ status: "success", dom_tree: response.dom_tree, page_text: response.page_text || null });
            } else {
              sendResult({ status: "error", error: response ? response.error : "Execution error after injection" });
            }
          } catch (injectErr) {
            sendResult({ status: "error", error: `Script injection failed: ${injectErr.message}` });
          }
        }
      }
    } catch (err) {
      console.error("Error processing WebSocket packet:", err);
    }
  };
}

function storageGet(keys) {
  return new Promise((resolve) => chrome.storage.local.get(keys, resolve));
}

function storageSet(values) {
  return new Promise((resolve) => chrome.storage.local.set(values, resolve));
}

async function loadSettings() {
  const saved = await storageGet({ backendUrl: backendUrl, authToken: "" });
  backendUrl = normalizeBackendUrl(saved.backendUrl || backendUrl);
  authToken = saved.authToken || "";
  backendUrlInput.value = backendUrl;
  authTokenInput.value = authToken;
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
  backendUrlInput.value = backendUrl;
  await storageSet({ backendUrl, authToken });
  updateLog("Settings saved. Reconnecting...");
  if (socket && socket.readyState !== WebSocket.CLOSED) {
    socket.close();
  } else {
    connectWS();
  }
}

async function loadSessionHistory() {
  historyList.classList.remove("hidden");
  historyList.textContent = "Loading history...";

  try {
    const response = await fetch(`${backendUrl}/sessions`, authFetchOptions());
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const sessions = await response.json();
    historyList.innerHTML = "";

    if (!sessions.length) {
      historyList.textContent = "No sessions recorded yet.";
      return;
    }

    for (const session of sessions.slice(0, 10)) {
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
  approvalText.textContent = `${data.approval_reason || "Sensitive action"} (${data.action}${data.selector ? `: ${data.selector}` : ""})`;
  approvalPanel.classList.remove("hidden");
  updateLog("Waiting for user approval...");

  return new Promise((resolve) => {
    const cleanup = () => {
      btnApprove.removeEventListener("click", onApprove);
      btnReject.removeEventListener("click", onReject);
      approvalPanel.classList.add("hidden");
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
  });
}

async function executeAgentAction(tab, data) {
  if (TAB_LEVEL_ACTIONS.has(data.action)) {
    await executeTabAction(tab.id, data.action, data.value);
    await delay(900);
    await injectContentScript(tab.id);
    return await sendMessageToTab(tab.id, { type: 'get_dom' });
  }

  if (data.action === "get_text") {
    return await sendMessageToTab(tab.id, { type: 'get_page_text' });
  }

  return await sendMessageToTab(tab.id, {
    type: 'execute_action',
    action: data.action,
    selector: data.selector,
    value: data.value
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
function sendResult(payload) {
  if (socket && socket.readyState === WebSocket.OPEN) {
    appendTimeline(payload.status === "success" ? "result" : "error", payload.error || "Action completed");
    socket.send(JSON.stringify({
      type: "action_result",
      ...payload
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
  
  updateLog("Parsing DOM from active webpage...");
  
  let domTree = [];
  try {
    const response = await sendMessageToTab(tab.id, { type: 'get_dom' });
    if (response && response.status === 'success') {
      domTree = response.dom_tree;
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
    socket.send(JSON.stringify({
      type: "user_input",
      prompt: prompt,
      dom_tree: domTree
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
btnSaveSettings.addEventListener('click', saveSettings);
btnHistory.addEventListener('click', loadSessionHistory);

// Initialize Connection on Load
loadSettings().then(connectWS);
