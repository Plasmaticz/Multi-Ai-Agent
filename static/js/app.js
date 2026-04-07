const state = {
  threads: [],
  activeThreadId: null,
  isSending: false,
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
  const response = await fetch("/api/threads");
  const payload = await response.json();
  state.threads = payload.threads;
  renderThreads();
}

async function createThread() {
  const response = await fetch("/api/threads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  const payload = await response.json();
  await loadThreads();
  await loadThread(payload.thread.id);
  promptInput.focus();
}

async function loadThread(threadId) {
  state.activeThreadId = threadId;
  const response = await fetch(`/api/threads/${threadId}`);
  const payload = await response.json();
  renderThread(payload.thread, payload.messages);
  renderThreads();
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

function renderThread(thread, messages) {
  threadTitle.textContent = thread.title;
  chatPanel.classList.remove("empty");
  chatPanel.innerHTML = "";

  if (messages.length === 0) {
    chatPanel.classList.add("empty");
    chatPanel.innerHTML = `
      <div class="empty-state">
        <p class="eyebrow">Thread ready</p>
        <h3>Drop in a prompt and we will route it through the agent team.</h3>
        <p>The response will be saved locally in this thread.</p>
      </div>
    `;
    return;
  }

  for (const message of messages) {
    const article = document.createElement("article");
    article.className = `message ${message.role}`;
    article.innerHTML = `
      <div class="message-meta">${escapeHtml(message.role)} · ${formatTimestamp(message.created_at)}</div>
      <div>${formatMessage(message.content)}</div>
    `;
    chatPanel.appendChild(article);
  }

  chatPanel.scrollTop = chatPanel.scrollHeight;
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
  setStatus("Running");
  sendButton.disabled = true;

  try {
    const response = await fetch(`/api/threads/${state.activeThreadId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "Request failed");
    }

    promptInput.value = "";
    renderThread(payload.thread, payload.messages);
    await loadThreads();
  } catch (error) {
    alert(error.message || "Request failed");
  } finally {
    state.isSending = false;
    sendButton.disabled = false;
    setStatus("Idle");
  }
}

async function openSettings() {
  const response = await fetch("/api/settings");
  const payload = await response.json();
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
  const payload = await response.json();
  if (!response.ok) {
    alert(payload.detail || "Could not save settings");
    return;
  }

  document.getElementById("api-key-preview").textContent = payload.has_api_key
    ? `Saved key: ${payload.api_key_preview}`
    : "No API key saved yet.";
  closeModal("settings-modal");
}

async function openLogs() {
  const query = state.activeThreadId ? `?thread_id=${encodeURIComponent(state.activeThreadId)}&limit=100` : "?limit=100";
  const response = await fetch(`/api/logs${query}`);
  const payload = await response.json();
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

function formatTimestamp(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatMessage(value) {
  return escapeHtml(value)
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
    .replace(/\n/g, "<br>");
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}
