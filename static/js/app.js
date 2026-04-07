const state = {
  threads: [],
  activeThreadId: null,
  activeRunId: null,
  isSending: false,
  pollTimer: null,
};

const threadList = document.getElementById("thread-list");
const threadTitle = document.getElementById("thread-title");
const chatPanel = document.getElementById("chat-panel");
const promptInput = document.getElementById("prompt-input");
const sendButton = document.getElementById("send-button");
const statusPill = document.getElementById("status-pill");

document.getElementById("new-thread-button").addEventListener("click", createThread);
document.getElementById("send-button").addEventListener("click", sendPrompt);
document.getElementById("open-settings-button").addEventListener("click", openSettings);
document.getElementById("open-logs-button").addEventListener("click", openLogs);
document.getElementById("settings-form").addEventListener("submit", saveSettings);

for (const button of document.querySelectorAll("[data-close-modal]")) {
  button.addEventListener("click", () => closeModal(button.dataset.closeModal));
}

promptInput.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    sendPrompt();
  }
});

boot();

async function boot() {
  await loadThreads();
  if (state.threads.length > 0) {
    await loadThread(state.threads[0].id);
  }
}

async function loadThreads() {
  const payload = await fetchJson("/api/threads");
  state.threads = payload.threads;
  renderThreads();
}

async function createThread() {
  const payload = await fetchJson("/api/threads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  await loadThreads();
  await loadThread(payload.thread.id);
  promptInput.focus();
}

async function loadThread(threadId) {
  stopRunPolling({ preserveStatus: true });
  state.activeThreadId = threadId;
  const payload = await fetchJson(`/api/threads/${threadId}`);
  renderThread(payload.thread, payload.messages, payload.events || []);
  renderThreads();
  applyRunState(payload.active_run);
}

function renderThreads() {
  threadList.innerHTML = "";
  if (state.threads.length === 0) {
    const empty = document.createElement("p");
    empty.className = "eyebrow";
    empty.textContent = "No threads yet";
    threadList.appendChild(empty);
    return;
  }

  for (const thread of state.threads) {
    const button = document.createElement("button");
    button.className = `thread-item${thread.id === state.activeThreadId ? " active" : ""}`;
    button.innerHTML = `
      <strong>${escapeHtml(thread.title)}</strong>
      <span>${escapeHtml(thread.last_message || "No messages yet")}</span>
    `;
    button.addEventListener("click", () => loadThread(thread.id));
    threadList.appendChild(button);
  }
}

function renderThread(thread, messages, events = []) {
  threadTitle.textContent = thread.title;
  chatPanel.classList.remove("empty");
  chatPanel.innerHTML = "";

  const timeline = buildTimeline(messages, events);
  if (timeline.length === 0) {
    chatPanel.classList.add("empty");
    chatPanel.innerHTML = `
      <div class="empty-state">
        <p class="eyebrow">Thread ready</p>
        <h3>Drop in a prompt and we will route it through the agent team.</h3>
        <p>The response and progress will be saved locally in this thread.</p>
      </div>
    `;
    return;
  }

  for (const item of timeline) {
    const article = document.createElement("article");
    article.className = `message ${item.role} ${item.message_type || "text"}`.trim();
    article.innerHTML = `
      <div class="message-meta">${escapeHtml(item.meta)} · ${formatTimestamp(item.created_at)}</div>
      <div>${formatMessage(item.content)}</div>
    `;
    chatPanel.appendChild(article);
  }

  chatPanel.scrollTop = chatPanel.scrollHeight;
}

function buildTimeline(messages, events) {
  const timeline = [];

  for (const message of messages) {
    timeline.push({
      id: message.id,
      role: message.role,
      message_type: message.message_type || "text",
      content: message.content,
      created_at: message.created_at,
      meta: labelForRole(message.role, message.message_type),
      order: `m-${message.id}`,
    });
  }

  for (const event of events) {
    timeline.push({
      id: `event-${event.id}`,
      role: "system",
      message_type: event.status === "failed" ? "error" : "progress",
      content: event.message,
      created_at: event.created_at,
      meta: `${event.agent_name} · ${event.status}`,
      order: `e-${event.id}`,
    });
  }

  timeline.sort((left, right) => {
    if (left.created_at === right.created_at) {
      return left.order.localeCompare(right.order);
    }
    return left.created_at.localeCompare(right.created_at);
  });

  return timeline;
}

function labelForRole(role, messageType) {
  if (messageType === "error") {
    return `${role} · failed`;
  }
  return role;
}

async function sendPrompt() {
  const content = promptInput.value.trim();
  if (!content || state.isSending) {
    return;
  }

  if (!state.activeThreadId) {
    await createThread();
  }

  state.isSending = true;
  sendButton.disabled = true;
  setStatus("Queued");
  renderOptimisticUserMessage(content);
  promptInput.value = "";

  try {
    const response = await fetch(`/api/threads/${state.activeThreadId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    const payload = await readJson(response);

    await loadThreads();

    if (!response.ok) {
      await loadThread(state.activeThreadId);
      applyRunState(null, "Failed");
      return;
    }

    renderThread(payload.thread, payload.messages, payload.events || []);
    const initialRun = payload.active_run || payload.run;
    const fallbackStatus = initialRun?.status === "failed" ? "Failed" : "Idle";
    applyRunState(initialRun, fallbackStatus);
  } catch (error) {
    console.error(error);
    await safeReloadActiveThread();
    applyRunState(null, "Failed");
  }
}

function renderOptimisticUserMessage(content) {
  if (chatPanel.classList.contains("empty")) {
    chatPanel.classList.remove("empty");
    chatPanel.innerHTML = "";
  }

  const article = document.createElement("article");
  article.className = "message user progress";
  article.innerHTML = `
    <div class="message-meta">user · pending</div>
    <div>${formatMessage(content)}</div>
  `;
  chatPanel.appendChild(article);
  chatPanel.scrollTop = chatPanel.scrollHeight;
}

function applyRunState(run, fallbackStatus = "Idle") {
  if (run && run.status === "running") {
    state.activeRunId = run.id;
    state.isSending = true;
    sendButton.disabled = true;
    setStatus("Running");
    startRunPolling(run.id);
    return;
  }

  stopRunPolling({ preserveStatus: true });
  state.isSending = false;
  sendButton.disabled = false;
  state.activeRunId = null;
  setStatus(fallbackStatus);
}

function startRunPolling(runId) {
  if (!state.activeThreadId) {
    return;
  }
  if (state.pollTimer && state.activeRunId === runId) {
    return;
  }

  stopRunPolling({ preserveStatus: true });
  state.activeRunId = runId;
  state.pollTimer = window.setInterval(() => {
    pollRun(runId).catch((error) => {
      console.error(error);
    });
  }, 1200);
  void pollRun(runId);
}

function stopRunPolling({ preserveStatus = false } = {}) {
  if (state.pollTimer) {
    window.clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
  if (!preserveStatus) {
    state.activeRunId = null;
  }
}

async function pollRun(runId) {
  if (!state.activeThreadId) {
    stopRunPolling();
    return;
  }

  const payload = await fetchJson(`/api/threads/${state.activeThreadId}/runs/${runId}`);
  renderThread(payload.thread, payload.messages, payload.events || []);

  if (payload.run && payload.run.status === "running") {
    setStatus("Running");
    return;
  }

  await loadThreads();
  state.isSending = false;
  sendButton.disabled = false;
  stopRunPolling();
  setStatus(payload.run?.status === "failed" ? "Failed" : "Idle");
}

async function safeReloadActiveThread() {
  if (!state.activeThreadId) {
    return;
  }
  try {
    const payload = await fetchJson(`/api/threads/${state.activeThreadId}`);
    renderThread(payload.thread, payload.messages, payload.events || []);
  } catch (error) {
    console.error(error);
  }
}

async function openSettings() {
  const payload = await fetchJson("/api/settings");
  document.getElementById("api-key-input").value = "";
  document.getElementById("clear-api-key-input").checked = false;
  document.getElementById("model-input").value = payload.openai_model;
  document.getElementById("concurrency-input").value = payload.max_concurrent_research;
  document.getElementById("api-key-preview").textContent = payload.has_api_key
    ? `Saved key: ${payload.api_key_preview}`
    : "No API key saved yet.";
  openModal("settings-modal");
}

async function saveSettings(event) {
  event.preventDefault();

  const body = {
    openai_api_key: document.getElementById("api-key-input").value,
    clear_api_key: document.getElementById("clear-api-key-input").checked,
    openai_model: document.getElementById("model-input").value,
    max_concurrent_research: Number(document.getElementById("concurrency-input").value),
  };

  const response = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await readJson(response);
  if (!response.ok) {
    return;
  }

  document.getElementById("api-key-preview").textContent = payload.has_api_key
    ? `Saved key: ${payload.api_key_preview}`
    : "No API key saved yet.";
  closeModal("settings-modal");
}

async function openLogs() {
  const params = new URLSearchParams({ limit: "100" });
  if (state.activeThreadId) {
    params.set("thread_id", state.activeThreadId);
  }
  if (state.activeRunId) {
    params.set("run_id", state.activeRunId);
  }
  const payload = await fetchJson(`/api/logs?${params.toString()}`);
  const logsList = document.getElementById("logs-list");
  logsList.innerHTML = "";

  const subtitle = document.getElementById("logs-subtitle");
  subtitle.textContent = state.activeThreadId
    ? "Recent activity for the active thread."
    : "Recent activity across the whole local app.";

  if (payload.logs.length === 0) {
    logsList.innerHTML = "<p>No logs yet.</p>";
  } else {
    for (const log of payload.logs) {
      const entry = document.createElement("div");
      entry.className = "log-item";
      entry.innerHTML = `
        <strong>${escapeHtml(log.agent_name)} · ${escapeHtml(log.event_type)} · ${escapeHtml(log.status)}</strong>
        <span>${formatTimestamp(log.created_at)}</span>
        <p>${escapeHtml(log.message)}</p>
      `;
      logsList.appendChild(entry);
    }
  }

  openModal("logs-modal");
}

function openModal(id) {
  document.getElementById(id).classList.remove("hidden");
}

function closeModal(id) {
  document.getElementById(id).classList.add("hidden");
}

function setStatus(label) {
  statusPill.textContent = label;
}

async function fetchJson(url, options) {
  const response = await fetch(url, options);
  const payload = await readJson(response);
  if (!response.ok) {
    throw new Error(payload.detail || "Request failed");
  }
  return payload;
}

async function readJson(response) {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

function formatTimestamp(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatMessage(value) {
  return escapeHtml(String(value || ""))
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
    .replace(/\n/g, "<br>");
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
