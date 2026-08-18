const state = {
  sessionId: localStorage.getItem("seekora-session") || crypto.randomUUID(),
  controller: null,
  requestId: null,
  busy: false,
};

const elements = {
  conversation: document.querySelector("#conversation"),
  messages: document.querySelector("#messages"),
  welcome: document.querySelector("#welcome"),
  composer: document.querySelector("#composer"),
  input: document.querySelector("#promptInput"),
  send: document.querySelector("#sendButton"),
  stop: document.querySelector("#stopButton"),
  newChat: document.querySelector("#newChat"),
  clear: document.querySelector("#clearButton"),
  modelName: document.querySelector("#modelName"),
  sidebarModel: document.querySelector("#sidebarModel"),
  serviceStatus: document.querySelector("#serviceStatus"),
  statusDot: document.querySelector("#statusDot"),
  sidebar: document.querySelector("#sidebar"),
  backdrop: document.querySelector("#sidebarBackdrop"),
  menu: document.querySelector("#menuButton"),
  sidebarClose: document.querySelector("#sidebarClose"),
};

localStorage.setItem("seekora-session", state.sessionId);

function textElement(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.textContent = text;
  return node;
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    elements.conversation.scrollTop = elements.conversation.scrollHeight;
  });
}

function setBusy(busy) {
  state.busy = busy;
  elements.input.disabled = busy;
  elements.send.classList.toggle("hidden", busy);
  elements.stop.classList.toggle("hidden", !busy);
}

function autoResize() {
  elements.input.style.height = "auto";
  elements.input.style.height = `${Math.min(elements.input.scrollHeight, 150)}px`;
}

function prettyResolver(version) {
  if (!version || version === "unknown") return "未知解析器";
  if (version.startsWith("langchain-openai:")) return version.split(":").slice(1).join(":");
  if (version.startsWith("rules")) return "规则解析器";
  return version;
}

async function checkService() {
  try {
    const [healthResponse, configResponse] = await Promise.all([
      fetch("/health", { cache: "no-store" }),
      fetch("/agent/config", { cache: "no-store" }),
    ]);
    if (!healthResponse.ok || !configResponse.ok) throw new Error("service unavailable");
    const config = await configResponse.json();
    const label = prettyResolver(config.resolver_version);
    elements.modelName.textContent = label;
    elements.sidebarModel.textContent = config.resolver_version;
    elements.serviceStatus.textContent = "服务已连接";
    elements.statusDot.className = "status-dot online";
  } catch {
    elements.modelName.textContent = "服务离线";
    elements.sidebarModel.textContent = "请先启动后端服务";
    elements.serviceStatus.textContent = "无法连接服务";
    elements.statusDot.className = "status-dot offline";
  }
}

function addUserMessage(query) {
  elements.welcome.classList.add("hidden");
  const row = document.createElement("div");
  row.className = "message user-message";
  row.append(textElement("div", "user-bubble", query));
  elements.messages.append(row);
  scrollToBottom();
}

function createAssistantMessage() {
  const row = document.createElement("div");
  row.className = "message assistant-message";
  row.append(textElement("div", "assistant-avatar", "N"));

  const content = document.createElement("div");
  content.className = "assistant-content";
  const label = document.createElement("div");
  label.className = "assistant-label";
  label.append(textElement("span", "", "Seekora"));
  const resolverTag = textElement("span", "resolver-tag", "等待意图解析");
  label.append(resolverTag);
  content.append(label);

  const summary = textElement("div", "assistant-summary", "正在理解你的需求并准备检索目录…");
  content.append(summary);

  const progress = document.createElement("div");
  progress.className = "progress-panel";
  progress.append(textElement("div", "progress-title", "执行链路"));
  const progressSteps = document.createElement("div");
  progressSteps.className = "progress-steps";
  const steps = {};
  [
    ["intent", "解析意图"],
    ["route", "路径路由"],
    ["recall", "并行召回"],
    ["constraints", "约束复核"],
    ["result", "生成结果"],
  ].forEach(([key, title], index) => {
    const step = textElement("span", `progress-step${index === 0 ? " active" : ""}`, title);
    progressSteps.append(step);
    steps[key] = step;
  });
  progress.append(progressSteps);
  content.append(progress);
  row.append(content);
  elements.messages.append(row);
  scrollToBottom();
  return { row, content, summary, progress, steps, resolverTag, intent: null, results: null, receipt: null };
}

function advanceSteps(ui, current) {
  const order = ["intent", "route", "recall", "constraints", "result"];
  const currentIndex = order.indexOf(current);
  order.forEach((name, index) => {
    ui.steps[name].classList.toggle("done", index < currentIndex);
    ui.steps[name].classList.toggle("active", index === currentIndex);
  });
}

function renderIntent(ui, intent) {
  if (ui.intent) ui.intent.remove();
  const panel = document.createElement("div");
  panel.className = "intent-panel";
  const head = document.createElement("div");
  head.className = "intent-head";
  head.append(textElement("span", "intent-mode", `${intent.mode}${intent.domain ? ` · ${intent.domain}` : ""}`));
  head.append(textElement("span", "confidence", `置信度 ${Math.round((intent.confidence || 0) * 100)}%`));
  panel.append(head, textElement("div", "intent-query", `检索语句：${intent.retrieval_query}`));
  if (intent.hard_constraints?.length) {
    const list = document.createElement("div");
    list.className = "constraint-list";
    intent.hard_constraints.forEach((rule) => {
      list.append(textElement("span", "constraint-chip", `${rule.field} ${rule.operator} ${Array.isArray(rule.value) ? rule.value.join(", ") : rule.value}`));
    });
    panel.append(list);
  }
  ui.progress.insertAdjacentElement("afterend", panel);
  ui.intent = panel;
  const resolver = prettyResolver(intent.resolver_version);
  ui.resolverTag.textContent = resolver;
  elements.modelName.textContent = resolver;
  ui.summary.textContent = intent.resolver_version?.startsWith("rules")
    ? "本次请求使用规则解析器。若你预期调用 DeepSeek，请检查 .env 配置并重启服务。"
    : `模型已完成意图解析，正在按「${intent.retrieval_query}」检索候选。`;
}

function renderResults(ui, items) {
  const section = document.createElement("div");
  section.className = "results-section";
  const head = document.createElement("div");
  head.className = "results-head";
  head.append(textElement("strong", "", items.length ? "推荐结果" : "没有匹配结果"));
  head.append(textElement("span", "results-count", `${items.length} 个候选通过复核`));
  section.append(head);
  const grid = document.createElement("div");
  grid.className = "result-grid";
  items.forEach((item, index) => {
    const card = document.createElement("article");
    card.className = "result-card";
    const topline = document.createElement("div");
    topline.className = "result-topline";
    topline.append(textElement("span", "result-rank", String(index + 1)));
    topline.append(textElement("span", "result-title", item.title || item.item_id));
    topline.append(textElement("span", "score", `RRF ${Number(item.score || 0).toFixed(4)}`));
    card.append(topline);
    if (item.reasons?.length) {
      const meta = document.createElement("div");
      meta.className = "result-meta";
      item.reasons.slice(0, 5).forEach((reason) => meta.append(textElement("span", "reason", reason)));
      card.append(meta);
    }
    grid.append(card);
  });
  section.append(grid);
  ui.content.append(section);
  ui.results = section;
  ui.summary.textContent = items.length
    ? `已找到 ${items.length} 个符合目录权限和硬约束的结果。`
    : "召回已完成，但没有候选通过最终目录与硬约束复核。";
  scrollToBottom();
}

function renderError(ui, data) {
  const message = data.message || data.error_code || "请求执行失败";
  ui.content.append(textElement("div", "error-card", `${message}。请检查后端终端和模型配置后重试。`));
  ui.summary.textContent = "这次请求没有完成。";
  Object.values(ui.steps).forEach((step) => step.classList.remove("active"));
  scrollToBottom();
}

async function attachReceipt(ui, receiptId) {
  try {
    const response = await fetch(`/agent/receipts/${encodeURIComponent(receiptId)}`);
    if (!response.ok) return;
    const receipt = await response.json();
    const details = document.createElement("details");
    details.className = "receipt-details";
    details.append(textElement("summary", "", `查看执行凭据 · ${receipt.status}`));
    details.append(textElement("pre", "receipt-json", JSON.stringify(receipt, null, 2)));
    ui.content.append(details);
    ui.receipt = details;
  } catch {
    // Receipt is an optional diagnostic surface; the result remains usable.
  }
}

function dispatchAgentEvent(ui, name, payload) {
  state.requestId = payload.request_id || state.requestId;
  const data = payload.data || {};
  if (name === "request.accepted") {
    ui.summary.textContent = "请求已接收，正在解析自然语言意图…";
  } else if (name === "intent.resolved") {
    advanceSteps(ui, "route");
    renderIntent(ui, data);
  } else if (name === "routing.completed") {
    advanceSteps(ui, "recall");
    ui.summary.textContent = data.route === "deep"
      ? "检测到复杂或低置信请求，正在执行低成本 Retrieval Probe。"
      : "请求适合快速路径，正在准备并行召回。";
  } else if (name === "probe.completed") {
    ui.summary.textContent = `Probe 找到 ${data.candidate_count || 0} 个候选摘要，正在生成受预算约束的计划。`;
  } else if (name === "plan.created") {
    ui.summary.textContent = `Deep Path 已生成 ${(data.steps || []).length} 个可执行查询步骤。`;
  } else if (name === "recall.started") {
    ui.summary.textContent = `正在并行调用 ${(data.sources || []).join("、")}…`;
  } else if (name === "recall.completed") {
    advanceSteps(ui, "constraints");
    ui.summary.textContent = `召回 ${data.candidate_count || 0} 个候选，正在执行硬约束与目录复核。`;
  } else if (name === "constraints.applied") {
    advanceSteps(ui, "result");
    ui.summary.textContent = `${data.accepted_count || 0} 个候选通过约束，正在组织结果。`;
  } else if (name === "result") {
    Object.values(ui.steps).forEach((step) => { step.classList.remove("active"); step.classList.add("done"); });
    renderResults(ui, data.items || []);
  } else if (name === "error" || name === "cancelled") {
    renderError(ui, data);
  } else if (name === "done") {
    if (data.receipt_id) attachReceipt(ui, data.receipt_id);
  }
}

async function consumeSSE(response, ui) {
  if (!response.body) throw new Error("浏览器无法读取流式响应");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done }).replace(/\r\n/g, "\n");
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      let eventName = "message";
      let dataText = "";
      block.split("\n").forEach((line) => {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        if (line.startsWith("data:")) dataText += line.slice(5).trim();
      });
      if (dataText) dispatchAgentEvent(ui, eventName, JSON.parse(dataText));
    }
    if (done) break;
  }
}

async function sendQuery(query) {
  if (!query.trim() || state.busy) return;
  addUserMessage(query.trim());
  const ui = createAssistantMessage();
  setBusy(true);
  state.controller = new AbortController();
  state.requestId = null;
  try {
    const response = await fetch("/agent/query", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
      body: JSON.stringify({
        query: query.trim(),
        tenant_id: "demo",
        session_id: state.sessionId,
        client_request_id: crypto.randomUUID(),
        top_k: 10,
      }),
      signal: state.controller.signal,
    });
    if (!response.ok) {
      const detail = await response.text();
      throw new Error(`HTTP ${response.status}: ${detail}`);
    }
    await consumeSSE(response, ui);
  } catch (error) {
    if (error.name === "AbortError") {
      renderError(ui, { message: "请求已在浏览器端停止" });
    } else {
      renderError(ui, { message: error.message });
    }
  } finally {
    setBusy(false);
    state.controller = null;
    elements.input.disabled = false;
    elements.input.focus();
  }
}

function resetConversation() {
  if (state.controller) state.controller.abort();
  state.sessionId = crypto.randomUUID();
  localStorage.setItem("seekora-session", state.sessionId);
  state.requestId = null;
  elements.messages.replaceChildren();
  elements.welcome.classList.remove("hidden");
  elements.input.value = "";
  autoResize();
  closeSidebar();
  elements.input.focus();
}

function openSidebar() {
  elements.sidebar.classList.add("open");
  elements.backdrop.classList.add("visible");
}

function closeSidebar() {
  elements.sidebar.classList.remove("open");
  elements.backdrop.classList.remove("visible");
}

elements.composer.addEventListener("submit", (event) => {
  event.preventDefault();
  const query = elements.input.value;
  if (!query.trim()) return;
  elements.input.value = "";
  autoResize();
  sendQuery(query);
});

elements.input.addEventListener("input", autoResize);
elements.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    elements.composer.requestSubmit();
  }
});

elements.stop.addEventListener("click", async () => {
  if (state.requestId) {
    fetch(`/agent/requests/${encodeURIComponent(state.requestId)}/cancel`, { method: "POST" }).catch(() => {});
  }
  state.controller?.abort();
});

elements.newChat.addEventListener("click", resetConversation);
elements.clear.addEventListener("click", resetConversation);
elements.menu.addEventListener("click", openSidebar);
elements.sidebarClose.addEventListener("click", closeSidebar);
elements.backdrop.addEventListener("click", closeSidebar);

document.querySelectorAll(".prompt-shortcut").forEach((button) => {
  button.addEventListener("click", () => {
    closeSidebar();
    elements.input.value = button.dataset.prompt || "";
    autoResize();
    elements.input.focus();
  });
});

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    resetConversation();
  }
});

checkService();
elements.input.focus();
