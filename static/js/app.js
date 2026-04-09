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
const deleteThreadButton = document.getElementById("delete-thread-button");

document.getElementById("new-thread-button").addEventListener("click", createThread);
document.getElementById("send-button").addEventListener("click", sendPrompt);
document.getElementById("open-settings-button").addEventListener("click", openSettings);
document.getElementById("open-logs-button").addEventListener("click", openLogs);
deleteThreadButton.addEventListener("click", deleteActiveThread);
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
  syncDeleteThreadButton();
  if (state.threads.length > 0) {
    await loadThread(state.threads[0].id);
  } else {
    renderEmptyThreadState();
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

async function deleteActiveThread() {
  if (!state.activeThreadId) {
    return;
  }

  const activeThread = state.threads.find((thread) => thread.id === state.activeThreadId);
  const label = activeThread?.title || "this thread";
  const confirmed = window.confirm(`Delete "${label}"? This removes its messages, runs, and logs.`);
  if (!confirmed) {
    return;
  }

  deleteThreadButton.disabled = true;
  try {
    const response = await fetch(`/api/threads/${state.activeThreadId}`, { method: "DELETE" });
    const payload = await readJson(response);
    if (!response.ok) {
      window.alert(payload.detail || "Failed to delete thread.");
      return;
    }

    const deletedThreadId = state.activeThreadId;
    stopRunPolling();
    state.activeThreadId = null;
    state.activeRunId = null;
    state.isSending = false;
    sendButton.disabled = false;
    setStatus("Idle");

    await loadThreads();
    const nextThread = state.threads.find((thread) => thread.id !== deletedThreadId) || state.threads[0];
    if (nextThread) {
      await loadThread(nextThread.id);
    } else {
      renderEmptyThreadState();
      syncDeleteThreadButton();
    }
  } catch (error) {
    console.error(error);
    window.alert("Failed to delete thread.");
  } finally {
    syncDeleteThreadButton();
  }
}

async function loadThread(threadId) {
  stopRunPolling({ preserveStatus: true });
  state.activeThreadId = threadId;
  const payload = await fetchJson(`/api/threads/${threadId}`);
  renderThread(payload.thread, payload.messages, payload.events || []);
  renderThreads();
  applyRunState(payload.active_run);
  syncDeleteThreadButton();
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
      <span>${escapeHtml(summarizeThreadPreview(thread.last_message || "No messages yet"))}</span>
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
        <h3>Drop in a coding prompt and we will route it through the agent team.</h3>
        <p>The implementation plan, progress, and final output will be saved locally in this thread.</p>
      </div>
    `;
    return;
  }

  for (const item of timeline) {
    const article = document.createElement("article");
    if (item.kind === "activity") {
      article.className = "message system activity";
      article.innerHTML = renderActivityCard(item);
    } else {
      article.className = `message ${item.role} ${item.message_type || "text"}`.trim();
      article.innerHTML = `
        <div class="message-meta">${escapeHtml(item.meta)} · ${formatTimestamp(item.created_at)}</div>
        <div>${formatMessage(item.content)}</div>
      `;
    }
    chatPanel.appendChild(article);
  }

  chatPanel.scrollTop = chatPanel.scrollHeight;
}

function renderEmptyThreadState() {
  threadTitle.textContent = "Select a thread";
  chatPanel.classList.add("empty");
  chatPanel.innerHTML = `
    <div class="empty-state">
      <p class="eyebrow">Ready</p>
      <h3>Start a local thread and prompt the coding agent team.</h3>
      <p>The app runs locally. The only external dependency is your OpenAI API key.</p>
    </div>
  `;
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

  timeline.push(...buildRunActivityItems(events));
  timeline.push(...buildLooseEventItems(events));

  timeline.sort((left, right) => {
    if (left.created_at === right.created_at) {
      return left.order.localeCompare(right.order);
    }
    return left.created_at.localeCompare(right.created_at);
  });

  return timeline;
}

function buildRunActivityItems(events) {
  const grouped = new Map();

  for (const event of events) {
    if (!event.run_id) {
      continue;
    }
    if (!grouped.has(event.run_id)) {
      grouped.set(event.run_id, []);
    }
    grouped.get(event.run_id).push(event);
  }

  return Array.from(grouped.entries()).map(([runId, runEvents]) => {
    runEvents.sort((left, right) => {
      if (left.created_at === right.created_at) {
        return left.id - right.id;
      }
      return left.created_at.localeCompare(right.created_at);
    });

    const rows = buildAgentRows(runEvents);
    const header = summarizeRunActivity(runEvents, rows);
    return {
      id: `activity-${runId}`,
      kind: "activity",
      role: "system",
      created_at: runEvents[0].created_at,
      order: `a-${runId}`,
      runId,
      ...header,
      rows,
    };
  });
}

function buildLooseEventItems(events) {
  return events
    .filter((event) => !event.run_id)
    .map((event) => ({
      id: `event-${event.id}`,
      role: "system",
      message_type: event.status === "failed" ? "error" : "progress",
      content: event.message,
      created_at: event.created_at,
      meta: `${prettyAgentLabel(event.agent_name, event.event_type)} · ${humanizeStatus(event.status)}`,
      order: `e-${event.id}`,
    }));
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
    syncDeleteThreadButton();
    startRunPolling(run.id);
    return;
  }

  stopRunPolling({ preserveStatus: true });
  state.isSending = false;
  sendButton.disabled = false;
  state.activeRunId = null;
  setStatus(fallbackStatus);
  syncDeleteThreadButton();
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
  syncDeleteThreadButton();
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

function summarizeThreadPreview(value) {
  const compact = String(value || "").replace(/\s+/g, " ").trim();
  if (!compact) {
    return "No messages yet";
  }
  return compact.length > 120 ? `${compact.slice(0, 117)}...` : compact;
}

function renderActivityCard(item) {
  return `
    <div class="activity-card">
      <div class="activity-card-header">
        <div>
          <div class="message-meta">Agent Run · ${formatTimestamp(item.created_at)}</div>
          <h3 class="activity-card-title">${escapeHtml(item.title)}</h3>
          <p class="activity-card-copy">${escapeHtml(item.summary)}</p>
        </div>
        <div class="activity-card-status">${escapeHtml(item.statusLabel)}</div>
      </div>
      <div class="activity-grid">
        ${item.rows.map(renderAgentRow).join("")}
      </div>
    </div>
  `;
}

function renderAgentRow(row) {
  return `
    <div class="agent-step ${row.tone}">
      <span class="status-dot ${row.tone}" aria-hidden="true"></span>
      <div class="agent-step-body">
        <div class="agent-step-header">
          <span class="agent-step-name">${escapeHtml(row.label)}</span>
          <span class="agent-step-state">${escapeHtml(row.statusLabel)}</span>
        </div>
        <div class="agent-step-message">${escapeHtml(row.message)}</div>
      </div>
    </div>
  `;
}

function summarizeRunActivity(runEvents, rows) {
  const latestRunEvent = [...runEvents]
    .reverse()
    .find((event) => event.agent_name === "system" && event.event_type === "run");
  const overallStatus = latestRunEvent?.status || runEvents[runEvents.length - 1]?.status || "running";
  const completedCount = rows.filter((row) => row.tone === "complete").length;
  const runningRow = rows.find((row) => row.tone === "running");

  if (overallStatus === "failed") {
    return {
      title: "Run stopped before completion",
      summary: "The thread kept the failure in place, along with any work that already finished.",
      statusLabel: "Failed",
    };
  }

  if (overallStatus === "complete" || overallStatus === "completed") {
    return {
      title: "Agent team finished this coding run",
      summary: `${completedCount} stage${completedCount === 1 ? "" : "s"} completed. The final response is saved below.`,
      statusLabel: "Complete",
    };
  }

  if (overallStatus === "needs_human_review") {
    return {
      title: "Run needs a human pass",
      summary: "The team got through the workflow, but the latest review still wants attention before this should be trusted.",
      statusLabel: "Needs Review",
    };
  }

  return {
    title: "Agent team is working through this request",
    summary: runningRow
      ? `${completedCount} stage${completedCount === 1 ? "" : "s"} completed so far. ${runningRow.label} is active now.`
      : `${completedCount} stage${completedCount === 1 ? "" : "s"} completed so far.`,
    statusLabel: "Running",
  };
}

function buildAgentRows(runEvents) {
  const stageOrder = [
    "planner",
    "repo_explorer",
    "architect",
    "repo_worker_backend",
    "repo_worker_frontend",
    "repo_worker_tests",
    "reviewer",
    "fixer",
    "validator",
    "finalizer",
  ];
  const rowsByKey = new Map();
  const seenWorkers = new Set();
  const latestRunEvent = [...runEvents]
    .reverse()
    .find((event) => event.agent_name === "system" && event.event_type === "run");
  const isActive = !latestRunEvent || ["queued", "started", "running"].includes(latestRunEvent.status);

  for (const event of runEvents) {
    const descriptor = agentDescriptor(event);
    if (!descriptor) {
      continue;
    }
    rowsByKey.set(descriptor.key, {
      key: descriptor.key,
      label: descriptor.label,
      tone: toneForStatus(event.status),
      statusLabel: humanizeStatus(event.status),
      message: event.message,
    });
    if (descriptor.key.startsWith("repo_worker_")) {
      seenWorkers.add(descriptor.key);
    }
  }

  const rows = [];
  for (const key of stageOrder) {
    const row = rowsByKey.get(key);
    if (row) {
      rows.push(row);
      continue;
    }

    if (isActive && shouldShowPendingStage(key, seenWorkers, rowsByKey)) {
      rows.push({
        key,
        label: labelForStageKey(key),
        tone: "pending",
        statusLabel: "Pending",
        message: pendingCopyForStage(key),
      });
    }
  }

  return rows;
}

function shouldShowPendingStage(key, seenWorkers, rowsByKey) {
  if (key.startsWith("repo_worker_")) {
    return seenWorkers.has(key) || rowsByKey.has("architect");
  }
  if (key === "fixer") {
    return rowsByKey.has("reviewer");
  }
  return true;
}

function labelForStageKey(key) {
  const labels = {
    planner: "Planner",
    repo_explorer: "Repo Explorer",
    architect: "Architect",
    repo_worker_backend: "Backend Worker",
    repo_worker_frontend: "Frontend Worker",
    repo_worker_tests: "Tests Worker",
    reviewer: "Reviewer",
    fixer: "Fixer",
    validator: "Validator",
    finalizer: "Finalizer",
  };
  return labels[key] || key;
}

function pendingCopyForStage(key) {
  const messages = {
    planner: "Waiting to scope the request.",
    repo_explorer: "Waiting to scan the repository.",
    architect: "Waiting to break the work into safe tasks.",
    repo_worker_backend: "Waiting for backend implementation work.",
    repo_worker_frontend: "Waiting for frontend implementation work.",
    repo_worker_tests: "Waiting for test and coverage work.",
    reviewer: "Waiting to review worker output.",
    fixer: "Waiting to see if revisions are needed.",
    validator: "Waiting to prepare validation commands.",
    finalizer: "Waiting to package the final answer.",
  };
  return messages[key] || "Waiting for this stage to start.";
}

function agentDescriptor(event) {
  if (event.agent_name === "orchestrator" && event.event_type === "plan") {
    return { key: "planner", label: "Planner" };
  }
  if (event.agent_name === "repo_explorer" && event.event_type === "explore") {
    return { key: "repo_explorer", label: "Repo Explorer" };
  }
  if (event.agent_name === "architect" && event.event_type === "architect") {
    return { key: "architect", label: "Architect" };
  }
  if (event.event_type === "implement_work_item") {
    return { key: event.agent_name, label: prettyAgentLabel(event.agent_name, event.event_type) };
  }
  if (event.agent_name === "reviewer" && event.event_type === "review") {
    return { key: "reviewer", label: "Reviewer" };
  }
  if (event.agent_name === "fixer" && event.event_type === "fix") {
    return { key: "fixer", label: "Fixer" };
  }
  if (event.agent_name === "validator" && event.event_type === "validate") {
    return { key: "validator", label: "Validator" };
  }
  if (event.agent_name === "orchestrator" && event.event_type === "finalize") {
    return { key: "finalizer", label: "Finalizer" };
  }
  return null;
}

function prettyAgentLabel(agentName, eventType = "") {
  const labels = {
    system: "System",
    orchestrator: eventType === "plan" ? "Planner" : "Finalizer",
    repo_explorer: "Repo Explorer",
    architect: "Architect",
    code_worker: "Code Workers",
    repo_worker_backend: "Backend Worker",
    repo_worker_frontend: "Frontend Worker",
    repo_worker_tests: "Tests Worker",
    reviewer: "Reviewer",
    fixer: "Fixer",
    validator: "Validator",
  };
  return labels[agentName] || agentName.replaceAll("_", " ");
}

function toneForStatus(status) {
  if (["completed", "complete"].includes(status)) {
    return "complete";
  }
  if (["failed"].includes(status)) {
    return "failed";
  }
  if (["needs_revision", "needs_human_review"].includes(status)) {
    return "warning";
  }
  if (["queued", "started", "running"].includes(status)) {
    return "running";
  }
  return "pending";
}

function humanizeStatus(status) {
  const labels = {
    queued: "Queued",
    started: "Running",
    running: "Running",
    completed: "Done",
    complete: "Done",
    failed: "Failed",
    needs_revision: "Needs Revision",
    needs_human_review: "Needs Review",
    pending: "Pending",
  };
  return labels[status] || status;
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

function syncDeleteThreadButton() {
  deleteThreadButton.disabled = !state.activeThreadId || state.isSending;
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
