/* 法晓 · 法律问答 Tab */

(function () {
  "use strict";

  // ---- 路径前缀：支持挂载在子路径（如 /law/）；根路径部署时为空串 ----
  const API_BASE = location.pathname.replace(/\/[^/]*$/, "");

  // ---- 应用导航 ----
  const tabs = document.querySelectorAll(".tab");
  const panels = document.querySelectorAll(".tab-panel");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("active"));
      panels.forEach((p) => p.classList.remove("active"));
      tabs.forEach((t) => t.setAttribute("aria-selected", "false"));
      tab.classList.add("active");
      tab.setAttribute("aria-selected", "true");
      document.getElementById("tab-" + tab.dataset.tab).classList.add("active");
      document.body.classList.toggle("qa-active", tab.dataset.tab === "qa");
      closeConversationDrawer();
    });
  });
  document.body.classList.add("qa-active");

  // ---- 问答 ----
  const form = document.getElementById("qa-form");
  const input = document.getElementById("qa-input");
  const sendBtn = document.getElementById("qa-send");
  const messagesEl = document.getElementById("qa-messages");
  const citationsPanel = document.getElementById("qa-citations");
  const citationsList = document.getElementById("citations-list");
  const citationsCount = document.getElementById("citations-count");
  const citationsPanelCount = document.getElementById("citations-panel-count");
  const citationsClose = document.getElementById("citations-close");
  const citationsRail = document.getElementById("citations-rail");
  const citationsToggle = document.getElementById("citations-toggle");
  const conversationSidebar = document.getElementById("conversation-sidebar");
  const conversationMenu = document.getElementById("conversation-menu");
  const conversationBackdrop = document.getElementById("conversation-backdrop");
  const conversationList = document.getElementById("conversation-list");
  const conversationEmpty = document.getElementById("conversation-empty");
  const conversationTotal = document.getElementById("conversation-total");
  const conversationSearch = document.getElementById("conversation-search");
  const newConversationBtn = document.getElementById("new-conversation");
  const chatTitle = document.getElementById("chat-title");
  const welcomeTemplate = messagesEl.innerHTML;

  let conversationId = "";
  let sending = false;
  let conversations = [];
  let activeCitations = [];
  const clientId = getClientId();
  const PET_MEMORY_KEY = "faxiao_pet_memory_enabled";

  function getClientId() {
    const key = "faxiao_client_id";
    try {
      let value = localStorage.getItem(key);
      if (!value) {
        value = crypto.randomUUID
          ? crypto.randomUUID()
          : `browser-${Date.now()}-${Math.random().toString(36).slice(2)}`;
        localStorage.setItem(key, value);
      }
      return value;
    } catch {
      return `browser-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    }
  }

  function petMemoryEnabled() {
    try { return localStorage.getItem(PET_MEMORY_KEY) === "1"; } catch { return false; }
  }

  input.addEventListener("input", () => {
    sendBtn.disabled = !input.value.trim() || sending;
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!sendBtn.disabled) form.requestSubmit();
    }
  });

  newConversationBtn.addEventListener("click", () => {
    if (sending) return window.toast("回答生成中，请稍候再新建对话");
    startNewConversation();
  });

  conversationMenu.addEventListener("click", () => {
    const open = !conversationSidebar.classList.contains("open");
    conversationSidebar.classList.toggle("open", open);
    conversationMenu.setAttribute("aria-expanded", String(open));
    conversationBackdrop.hidden = !open;
  });

  conversationBackdrop.addEventListener("click", closeConversationDrawer);

  conversationSearch.addEventListener("input", renderConversationList);

  conversationList.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const deleteButton = event.target.closest(".conversation-delete-btn");
    if (deleteButton) {
      if (sending) return window.toast("回答生成中，请稍候再删除对话");
      deleteConversation(
        deleteButton.dataset.conversationId,
        deleteButton.dataset.conversationTitle,
        deleteButton,
      );
      return;
    }
    const openButton = event.target.closest(".conversation-item-open");
    if (!openButton) return;
    if (sending) return window.toast("回答生成中，请稍候再切换对话");
    loadConversation(openButton.dataset.conversationId);
  });

  citationsToggle.addEventListener("click", () => {
    const open = !citationsRail.classList.contains("open");
    if (open) {
      if (activeCitations.length) showCitations(activeCitations);
      else showSourceList();
    }
    setCitationsOpen(open);
  });
  citationsClose.addEventListener("click", () => setCitationsOpen(false));

  messagesEl.addEventListener("click", (event) => {
    if (!(event.target instanceof Element)) return;
    const quick = event.target.closest(".quick-btn");
    if (quick) {
      input.value = quick.dataset.q;
      sendBtn.disabled = false;
      form.requestSubmit();
      return;
    }
    const ref = event.target.closest(".citation-ref");
    if (!ref) return;
    const answer = ref.closest(".msg.assistant");
    const citations = answer && answer.citationRefs ? answer.citationRefs : activeCitations;
    setAvailableCitations(citations);
    showCitations(citations);
    setCitationsOpen(true);
    const item = citationsList.querySelector(`[data-label="${ref.dataset.label}"]`);
    if (!item) return;
    item.scrollIntoView({ behavior: "smooth", block: "center" });
    item.classList.remove("highlight");
    requestAnimationFrame(() => item.classList.add("highlight"));
    setTimeout(() => item.classList.remove("highlight"), 1500);
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const question = input.value.trim();
    if (!question || sending) return;

    sending = true;
    sendBtn.disabled = true;
    input.value = "";
    const isNewConversation = !conversationId;
    if (isNewConversation) chatTitle.textContent = question.slice(0, 60);

    // 移除欢迎页
    const welcome = messagesEl.querySelector(".welcome");
    if (welcome) welcome.remove();

    appendMessage("user", question);
    const assistantEl = appendMessage("assistant", "");
    const stream = createStreamRenderer(assistantEl);
    stream.status("正在查找相关法律…");
    clearCitations();
    let resultEvent = null;
    const requestId = crypto.randomUUID
      ? crypto.randomUUID()
      : `ask-${Date.now()}-${Math.random().toString(36).slice(2)}`;
    window.dispatchEvent(new CustomEvent("pet:ask"));

    try {
      const res = await fetch(API_BASE + "/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          conversation_id: conversationId,
          client_id: clientId,
          request_id: requestId,
          remember: petMemoryEnabled(),
        }),
      });

      if (!res.ok) {
        if (res.status === 401) {
          input.value = question; // 未登录：问题放回输入框，登录后可直接重发
          stream.fail("登录后即可提问");
          window.dispatchEvent(new CustomEvent("pet:error"));
          window.dispatchEvent(new CustomEvent("auth:request-login"));
          return;
        }
        const err = await res.json().catch(() => ({}));
        stream.fail(err.error || "暂时无法回答，请稍后再试。");
        window.dispatchEvent(new CustomEvent("pet:error"));
        return;
      }

      if (!res.body) throw new Error("当前浏览器不支持流式响应");
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      const handleLine = (line) => {
        if (!line.startsWith("data: ")) return;
        let event;
        try { event = JSON.parse(line.slice(6).trim()); } catch { return; }

        if (event.type === "status") {
          stream.status(event.text);
          window.dispatchEvent(new CustomEvent("pet:status", { detail: { text: event.text } }));
        } else if (event.type === "delta") {
          stream.push(event.text);
        } else if (event.type === "result") {
          conversationId = event.conversation_id || conversationId;
          resultEvent = event;
          stream.finish(event);
          window.dispatchEvent(new CustomEvent("pet:answer", { detail: { pet: event.pet } }));
        } else if (event.type === "error") {
          stream.fail(event.error || "暂时无法回答，请稍后再试。");
          window.dispatchEvent(new CustomEvent("pet:error"));
        }
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        lines.forEach(handleLine);
      }
      buffer += decoder.decode();
      buffer.split("\n").forEach(handleLine);
      await stream.whenDone();
      if (resultEvent) await refreshConversations();
    } catch (err) {
      stream.fail("网络错误，请重试。");
      window.dispatchEvent(new CustomEvent("pet:error"));
    } finally {
      sending = false;
      sendBtn.disabled = !input.value.trim();
      input.focus();
    }
  });

  function closeConversationDrawer() {
    conversationSidebar.classList.remove("open");
    conversationMenu.setAttribute("aria-expanded", "false");
    conversationBackdrop.hidden = true;
  }

  function startNewConversation() {
    conversationId = "";
    chatTitle.textContent = "新对话";
    messagesEl.innerHTML = welcomeTemplate;
    clearCitations();
    renderConversationList();
    closeConversationDrawer();
    input.value = "";
    sendBtn.disabled = true;
    input.focus();
  }

  async function refreshConversations() {
    try {
      const res = await fetch(`${API_BASE}/api/conversations?client_id=${encodeURIComponent(clientId)}`);
      if (res.status === 401) {
        conversations = [];
        conversationTotal.textContent = "";
        conversationEmpty.textContent = "登录后可同步历史对话";
        conversationEmpty.hidden = false;
        return;
      }
      if (!res.ok) throw new Error("history request failed");
      const data = await res.json();
      conversations = data.conversations || [];
      renderConversationList();
    } catch {
      conversationEmpty.textContent = "暂时无法加载对话记录";
      conversationEmpty.hidden = false;
    }
  }

  function renderConversationList() {
    const query = conversationSearch.value.trim().toLocaleLowerCase("zh-CN");
    const filtered = conversations.filter((item) => (
      !query || item.title.toLocaleLowerCase("zh-CN").includes(query)
      || String(item.preview || "").toLocaleLowerCase("zh-CN").includes(query)
    ));
    conversationList.replaceChildren();
    filtered.forEach((item) => {
      const row = document.createElement("div");
      row.className = "conversation-item";
      row.classList.toggle("active", item.id === conversationId);

      const openButton = document.createElement("button");
      openButton.type = "button";
      openButton.className = "conversation-item-open";
      openButton.dataset.conversationId = item.id;
      openButton.setAttribute("aria-current", item.id === conversationId ? "true" : "false");

      const title = document.createElement("span");
      title.className = "conversation-item-title";
      title.textContent = item.title;
      const time = document.createElement("time");
      time.className = "conversation-item-time";
      time.dateTime = item.updated_at || item.created_at;
      time.textContent = formatConversationTime(time.dateTime);
      openButton.append(title, time);

      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "conversation-delete-btn";
      deleteButton.dataset.conversationId = item.id;
      deleteButton.dataset.conversationTitle = item.title;
      deleteButton.setAttribute("aria-label", `删除对话：${item.title}`);
      deleteButton.title = "删除对话";
      deleteButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4.75h6V7m3 0-1 13H7L6 7m4 4v5m4-5v5"/></svg>';

      row.append(openButton, deleteButton);
      conversationList.appendChild(row);
    });
    conversationTotal.textContent = conversations.length ? String(conversations.length) : "";
    conversationEmpty.textContent = query ? "没有匹配的对话" : "还没有保存的对话";
    conversationEmpty.hidden = filtered.length > 0;
  }

  function formatConversationTime(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const now = new Date();
    if (date.toDateString() === now.toDateString()) {
      return new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit" }).format(date);
    }
    return new Intl.DateTimeFormat("zh-CN", { month: "numeric", day: "numeric" }).format(date);
  }

  async function deleteConversation(id, title, button) {
    if (!id || button.disabled) return;
    const displayTitle = title || "这条对话";
    if (!window.confirm(`确定删除“${displayTitle}”吗？删除后无法恢复。`)) return;

    button.disabled = true;
    button.closest(".conversation-item")?.setAttribute("aria-busy", "true");
    try {
      const res = await fetch(`${API_BASE}/api/conversations/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
      if (res.status === 401) {
        button.disabled = false;
        button.closest(".conversation-item")?.removeAttribute("aria-busy");
        window.toast("登录后才能删除历史对话");
        window.dispatchEvent(new CustomEvent("auth:request-login"));
        return;
      }
      if (!res.ok) throw new Error("delete request failed");
      const data = await res.json();
      if (!data.deleted) throw new Error("conversation missing");

      conversations = conversations.filter((item) => item.id !== id);
      if (conversationId === id) startNewConversation();
      else renderConversationList();
      window.toast("对话已删除");
    } catch {
      button.disabled = false;
      button.closest(".conversation-item")?.removeAttribute("aria-busy");
      window.toast("删除失败，请稍后重试");
    }
  }

  async function loadConversation(id) {
    conversationId = id;
    renderConversationList();
    closeConversationDrawer();
    messagesEl.innerHTML = '<div class="conversation-loading">正在载入对话…</div>';
    clearCitations();
    try {
      const res = await fetch(`${API_BASE}/api/conversations/${encodeURIComponent(id)}?client_id=${encodeURIComponent(clientId)}`);
      if (res.status === 401) {
        window.toast("登录后可查看历史对话");
        window.dispatchEvent(new CustomEvent("auth:request-login"));
        throw new Error("unauthorized");
      }
      if (!res.ok) throw new Error("conversation request failed");
      const data = await res.json();
      if (!data.conversation) throw new Error("conversation missing");
      chatTitle.textContent = data.conversation.title;
      messagesEl.replaceChildren();
      let latestCitations = [];
      (data.messages || []).forEach((message) => {
        const el = appendMessage(message.role, message.role === "user" ? message.content : "", false);
        if (message.role === "assistant") {
          renderAnswer(el, message, { updateCitationRail: false, shouldScroll: false });
          if (message.citations && message.citations.length) latestCitations = message.citations;
        }
      });
      setAvailableCitations(latestCitations);
      scrollToBottom();
    } catch {
      conversationId = "";
      chatTitle.textContent = "新对话";
      messagesEl.innerHTML = '<div class="conversation-loading error">对话载入失败，请稍后重试。</div>';
      renderConversationList();
    }
  }

  function appendMessage(role, text, shouldScroll = true) {
    const el = document.createElement("div");
    el.className = "msg " + role;
    el.textContent = text;
    messagesEl.appendChild(el);
    if (shouldScroll) scrollToBottom();
    return el;
  }

  function renderAnswer(el, event, options = {}) {
    const { updateCitationRail = true, shouldScroll = true } = options;
    el.classList.remove("streaming", "loading");
    el.innerHTML = renderMarkdown(event.content);
    el.citationRefs = Array.isArray(event.citations) ? event.citations : [];

    if (event.notice) {
      const notice = document.createElement("div");
      notice.className = "msg-notice";
      notice.textContent = event.notice;
      el.appendChild(notice);
    }

    if (updateCitationRail) setAvailableCitations(el.citationRefs);

    if (shouldScroll) scrollToBottom();
  }

  function clearCitations() {
    setAvailableCitations([]);
    // 无引用时回退为资料列表，面板保持可翻看
    if (citationsRail.classList.contains("open")) showSourceList();
    else citationsList.replaceChildren();
  }

  function setAvailableCitations(citations) {
    activeCitations = Array.isArray(citations) ? citations : [];
    const count = activeCitations.length;
    citationsCount.textContent = count ? String(count) : "";
    citationsPanelCount.textContent = count ? `${count} 条` : "";
  }

  function setCitationsOpen(open) {
    citationsRail.classList.toggle("open", Boolean(open));
    citationsPanel.hidden = !open;
    citationsToggle.setAttribute("aria-expanded", String(Boolean(open)));
  }

  refreshConversations();

  function createStreamRenderer(el) {
    let target = "";
    let shown = "";
    let frame = 0;
    let completion = null;
    let started = false;
    let donePromise = null;
    let resolveDone = null;
    let lastPaint = 0;

    function complete() {
      if (resolveDone) resolveDone();
      resolveDone = null;
    }

    function schedule() {
      if (!frame) frame = requestAnimationFrame(typeNext);
    }

    function nextIndex(source, start, count) {
      let index = start;
      for (let step = 0; step < count && index < source.length; step += 1) {
        index += source.codePointAt(index) > 0xffff ? 2 : 1;
      }
      return index;
    }

    function typeNext(timestamp) {
      frame = 0;
      if (timestamp - lastPaint < 18) {
        schedule();
        return;
      }
      lastPaint = timestamp;
      const remaining = target.length - shown.length;
      if (remaining > 0) {
        // 让实时 token 保持可读的打字节奏；积压较多时适度追赶，避免回答结束后久等。
        const count = remaining > 240 ? 6 : remaining > 120 ? 4 : remaining > 40 ? 2 : 1;
        const end = nextIndex(target, shown.length, count);
        shown += target.slice(shown.length, end);
        el.innerHTML = renderMarkdown(shown);
        scrollToBottom();
        schedule();
        return;
      }
      if (completion) {
        const event = completion;
        completion = null;
        renderAnswer(el, event);
        complete();
      }
    }

    function begin() {
      if (started) return;
      started = true;
      el.textContent = "";
      el.classList.remove("loading");
      el.classList.add("streaming");
    }

    return {
      status(text) {
        if (!started) {
          el.textContent = text;
          el.classList.add("loading");
        }
      },
      push(text) {
        begin();
        target += text;
        schedule();
      },
      finish(event) {
        begin();
        // The final server event is citation-sanitized, so it is the source
        // of truth if a network chunk was incomplete or buffered differently.
        target = event.content || target;
        completion = event;
        if (!donePromise) donePromise = new Promise((resolve) => { resolveDone = resolve; });
        schedule();
      },
      fail(text) {
        if (frame) cancelAnimationFrame(frame);
        frame = 0;
        completion = null;
        el.classList.remove("streaming", "loading");
        el.textContent = text;
        complete();
      },
      whenDone() {
        return donePromise || Promise.resolve();
      },
    };
  }

  function renderMarkdown(source) {
    const lines = String(source || "").replace(/\r\n?/g, "\n").split("\n");
    const html = [];
    let index = 0;

    const isBlockStart = (line, nextLine = "") => (
      /^\s*```/.test(line)
      || /^(#{1,6})\s+/.test(line)
      || /^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)
      || /^\s*>/.test(line)
      || /^\s*[-*+]\s+/.test(line)
      || /^\s*\d+[.)]\s+/.test(line)
      || (line.includes("|") && /^\s*\|?\s*:?-{3,}/.test(nextLine))
    );

    const splitTableRow = (line) => line
      .trim()
      .replace(/^\||\|$/g, "")
      .split(/(?<!\\)\|/)
      .map((cell) => cell.trim().replace(/\\\|/g, "|"));

    while (index < lines.length) {
      const line = lines[index];
      const nextLine = lines[index + 1] || "";
      if (!line.trim()) {
        index += 1;
        continue;
      }

      if (/^\s*```/.test(line)) {
        const codeLines = [];
        index += 1;
        while (index < lines.length && !/^\s*```/.test(lines[index])) {
          codeLines.push(lines[index]);
          index += 1;
        }
        if (index < lines.length) index += 1;
        html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        continue;
      }

      const heading = line.match(/^(#{1,6})\s+(.+?)\s*#*$/);
      if (heading) {
        const level = Math.min(heading[1].length, 4);
        html.push(`<h${level}>${formatInline(heading[2])}</h${level}>`);
        index += 1;
        continue;
      }

      if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
        html.push("<hr>");
        index += 1;
        continue;
      }

      if (line.includes("|") && /^\s*\|?\s*:?-{3,}/.test(nextLine)) {
        const headers = splitTableRow(line);
        index += 2;
        const rows = [];
        while (index < lines.length && lines[index].includes("|") && lines[index].trim()) {
          rows.push(splitTableRow(lines[index]));
          index += 1;
        }
        html.push("<div class=\"md-table-wrap\"><table><thead><tr>");
        headers.forEach((cell) => html.push(`<th>${formatInline(cell)}</th>`));
        html.push("</tr></thead><tbody>");
        rows.forEach((row) => {
          html.push("<tr>");
          headers.forEach((_, cellIndex) => html.push(`<td>${formatInline(row[cellIndex] || "")}</td>`));
          html.push("</tr>");
        });
        html.push("</tbody></table></div>");
        continue;
      }

      if (/^\s*>/.test(line)) {
        const quoteLines = [];
        while (index < lines.length && /^\s*>/.test(lines[index])) {
          quoteLines.push(lines[index].replace(/^\s*>\s?/, ""));
          index += 1;
        }
        html.push(`<blockquote>${quoteLines.map(formatInline).join("<br>")}</blockquote>`);
        continue;
      }

      const listMatch = line.match(/^\s*([-*+]|\d+[.)])\s+(.+)$/);
      if (listMatch) {
        const listTag = /^\d/.test(listMatch[1]) ? "ol" : "ul";
        const items = [];
        while (index < lines.length) {
          const item = lines[index].match(/^\s*([-*+]|\d+[.)])\s+(.+)$/);
          if (!item || (/^\d/.test(item[1]) ? "ol" : "ul") !== listTag) break;
          items.push(item[2]);
          index += 1;
        }
        html.push(`<${listTag}>${items.map((item) => `<li>${formatInline(item)}</li>`).join("")}</${listTag}>`);
        continue;
      }

      const paragraph = [line];
      index += 1;
      while (index < lines.length && lines[index].trim()
             && !isBlockStart(lines[index], lines[index + 1] || "")) {
        paragraph.push(lines[index]);
        index += 1;
      }
      html.push(`<p>${paragraph.map(formatInline).join("<br>")}</p>`);
    }
    return html.join("");
  }

  function formatInline(text) {
    const tokens = [];
    const hold = (html) => {
      const token = `\u0000${tokens.length}\u0000`;
      tokens.push(html);
      return token;
    };
    let value = String(text || "");
    value = value.replace(/`([^`\n]+)`/g, (_, code) => hold(`<code>${escapeHtml(code)}</code>`));
    value = value.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      (_, label, url) => hold(`<a href="${escapeAttribute(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(label)}</a>`));
    value = value.replace(/\[(C\d+)\]/g, (_, label) => hold(
      `<button type="button" class="citation-ref" data-label="${label}" aria-label="查看引用 ${label}">[${label}]</button>`
    ));
    let html = escapeHtml(value);
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/__(.+?)__/g, "<strong>$1</strong>");
    html = html.replace(/~~(.+?)~~/g, "<del>$1</del>");
    html = html.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    html = html.replace(/(^|[^_])_([^_\n]+)_/g, "$1<em>$2</em>");
    return html.replace(/\u0000(\d+)\u0000/g, (_, tokenIndex) => tokens[Number(tokenIndex)] || "");
  }

  function showCitations(citations) {
    const displayCitations = citations.flatMap(expandLegacyCitation);
    citationsList.replaceChildren();
    const browseAll = document.createElement("button");
    browseAll.type = "button";
    browseAll.className = "kb-back";
    browseAll.textContent = "← 浏览全部资料";
    browseAll.addEventListener("click", () => showSourceList());
    citationsList.appendChild(browseAll);
    citationsPanelCount.textContent = `${displayCitations.length} 条`;
    displayCitations.forEach((c) => {
      const sectionParts = String(c.section || "").split(/\s+\/\s+/).filter(Boolean);
      const article = c.article || sectionParts.find((part) => /第.+条/.test(part)) || "";
      const source = cleanCitationSource(c.source || sectionParts.filter((part) => part !== article).join(" · "));
      const item = document.createElement("article");
      item.className = "citation-item";
      item.dataset.label = c.parentLabel || c.label;
      item.innerHTML = `
        <div class="citation-meta">
          <span class="label">${escapeHtml(c.label)}</span>
          ${article ? `<strong class="article">${escapeHtml(article)}</strong>` : ""}
          ${c.url ? '<span class="citation-badge">在线</span>' : ""}
        </div>
        <p class="citation-source">${escapeHtml(source)}${c.timeliness ? `（${escapeHtml(c.timeliness)}）` : ""}</p>
        <p class="excerpt">${escapeHtml(c.excerpt || "暂无可展示的原文片段")}</p>
      `;
      if (c.url) {
        const link = document.createElement("a");
        link.className = "citation-link";
        link.href = c.url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "查看北大法宝原文 ↗";
        item.appendChild(link);
      }
      citationsList.appendChild(item);
    });
  }

  // ---- 法律原文浏览：不发起对话也能翻看知识库资料 ----
  let kbSourcesCache = null;

  async function showSourceList() {
    citationsPanelCount.textContent = "";
    if (!kbSourcesCache) {
      renderKbNotice("正在载入资料列表…");
      try {
        const res = await fetch(`${API_BASE}/api/kb`);
        if (!res.ok) throw new Error("kb request failed");
        const data = await res.json();
        kbSourcesCache = data.sources || [];
      } catch {
        renderKbNotice("资料列表载入失败，请稍后重试");
        return;
      }
    }
    citationsList.replaceChildren();
    kbSourcesCache.forEach((source) => {
      const item = document.createElement("article");
      item.className = "citation-item kb-source";
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "kb-source-btn";
      const title = document.createElement("span");
      title.className = "kb-source-title";
      title.textContent = cleanCitationSource(source.title);
      const meta = document.createElement("span");
      meta.className = "kb-source-meta";
      meta.textContent = `${source.chunk_count} 段`;
      btn.append(title, meta);
      btn.addEventListener("click", () => showSourceDetail(source.id));
      item.appendChild(btn);
      citationsList.appendChild(item);
    });
  }

  async function showSourceDetail(id) {
    const known = (kbSourcesCache || []).find((s) => s.id === id);
    renderKbNotice(`正在载入《${known ? known.title : "法律资料"}》…`);
    try {
      const res = await fetch(`${API_BASE}/api/kb/${encodeURIComponent(id)}`);
      if (!res.ok) throw new Error("kb detail request failed");
      const data = await res.json();
      citationsList.replaceChildren();
      const back = document.createElement("button");
      back.type = "button";
      back.className = "kb-back";
      back.textContent = "← 返回资料列表";
      back.addEventListener("click", () => showSourceList());
      citationsList.appendChild(back);
      const doc = document.createElement("article");
      doc.className = "kb-doc";
      const heading = document.createElement("h4");
      heading.className = "kb-doc-title";
      heading.textContent = cleanCitationSource(data.source.title);
      doc.appendChild(heading);
      (data.chunks || []).forEach((chunk) => {
        if (chunk.section) {
          const section = document.createElement("h5");
          section.className = "kb-chunk-section";
          section.textContent = chunk.section;
          doc.appendChild(section);
        }
        const text = document.createElement("p");
        text.className = "kb-chunk-text";
        text.textContent = chunk.text;
        doc.appendChild(text);
      });
      citationsList.appendChild(doc);
    } catch {
      renderKbNotice("原文载入失败，请稍后重试");
    }
  }

  function renderKbNotice(text) {
    citationsList.replaceChildren();
    const notice = document.createElement("p");
    notice.className = "kb-notice";
    notice.textContent = text;
    citationsList.appendChild(notice);
  }

  function cleanCitationSource(value) {
    const source = String(value || "法律资料")
      .replace(/^(?:法条|判例)\s*·\s*/, "")
      .replace(/\s*\(FBM[-\w.]+(?:\s*\(\d+\))?\)\s*$/i, "")
      .trim();
    return source || "法律资料";
  }

  function expandLegacyCitation(citation) {
    if (citation.article || !String(citation.section || "").startsWith("法条 ·")) return [citation];
    const text = String(citation.excerpt || "");
    const pattern = /^(?:#{1,6}\s*)?(第[一-龥〇\d]+条(?:之[一-龥〇\d]+)?)\s*/gm;
    const matches = [...text.matchAll(pattern)];
    if (matches.length < 2) return [citation];
    return matches.map((match, index) => ({
      ...citation,
      label: `${citation.label}.${index + 1}`,
      parentLabel: citation.label,
      source: cleanCitationSource(citation.section),
      article: match[1],
      excerpt: text.slice(match.index + match[0].length,
        index + 1 < matches.length ? matches[index + 1].index : text.length).trim(),
    })).filter((item) => item.excerpt);
  }

  function scrollToBottom() {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }

  function escapeAttribute(text) {
    return escapeHtml(text).replace(/`/g, "&#96;");
  }

  // ---- Toast ----
  window.toast = function (msg) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.hidden = false;
    setTimeout(() => { el.hidden = true; }, 3000);
  };

  // ---- 账户体系：登录/注册/登出；未登录可浏览，写入类接口由服务端返回 401 ----
  const authArea = document.getElementById("auth-area");
  const authModal = document.getElementById("auth-modal");
  const authBackdrop = document.getElementById("auth-backdrop");
  const authClose = document.getElementById("auth-close");
  const authForm = document.getElementById("auth-form");
  const authUsername = document.getElementById("auth-username");
  const authPassword = document.getElementById("auth-password");
  const authHint = document.getElementById("auth-hint");
  const authSubmit = document.getElementById("auth-submit");
  const authTabLogin = document.getElementById("auth-tab-login");
  const authTabRegister = document.getElementById("auth-tab-register");
  let authUser = null;
  let authMode = "login";

  function setAuthMode(mode, hint = "") {
    authMode = mode;
    authTabLogin.classList.toggle("active", mode === "login");
    authTabRegister.classList.toggle("active", mode === "register");
    authTabLogin.setAttribute("aria-selected", String(mode === "login"));
    authTabRegister.setAttribute("aria-selected", String(mode === "register"));
    authSubmit.textContent = mode === "login" ? "登录" : "注册并登录";
    authPassword.autocomplete = mode === "login" ? "current-password" : "new-password";
    authHint.hidden = !hint;
    authHint.textContent = hint;
  }

  function openAuth(mode = "login", hint = "") {
    setAuthMode(mode, hint);
    authModal.hidden = false;
    authBackdrop.hidden = false;
    authUsername.focus();
  }

  function closeAuth() {
    authModal.hidden = true;
    authBackdrop.hidden = true;
    authPassword.value = "";
  }

  function renderAuthArea() {
    authArea.replaceChildren();
    if (authUser) {
      const chip = document.createElement("span");
      chip.className = "auth-chip";
      chip.textContent = authUser.username;
      const logout = document.createElement("button");
      logout.type = "button";
      logout.className = "auth-logout";
      logout.textContent = "退出";
      logout.addEventListener("click", async () => {
        logout.disabled = true;
        try {
          await fetch(`${API_BASE}/api/auth/logout`, { method: "POST" });
        } catch { /* 网络异常也回到未登录态（整页刷新） */ }
        location.reload();
      });
      authArea.append(chip, logout);
    } else {
      const login = document.createElement("button");
      login.type = "button";
      login.className = "auth-login-btn";
      login.textContent = "登录";
      login.addEventListener("click", () => openAuth("login"));
      authArea.appendChild(login);
    }
  }

  authTabLogin.addEventListener("click", () => setAuthMode("login"));
  authTabRegister.addEventListener("click", () => setAuthMode("register"));
  authClose.addEventListener("click", closeAuth);
  authBackdrop.addEventListener("click", closeAuth);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !authModal.hidden) closeAuth();
  });

  authForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const username = authUsername.value.trim();
    const password = authPassword.value;
    if (username.length < 2) return setAuthMode(authMode, "请输入用户名（2-24 位）");
    if (password.length < 6) return setAuthMode(authMode, "密码至少 6 位");
    authSubmit.disabled = true;
    try {
      // client_id 用于把本浏览器匿名期的对话/桌宠数据并入新账户
      const res = await fetch(`${API_BASE}/api/auth/${authMode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password, client_id: clientId }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setAuthMode(authMode, data.error || (authMode === "login" ? "登录失败，请重试" : "注册失败，请重试"));
        return;
      }
      authUser = data.user;
      closeAuth();
      window.toast(authMode === "register"
        ? `欢迎加入，${authUser.username}`
        : `欢迎回来，${authUser.username}`);
      renderAuthArea();
      window.dispatchEvent(new CustomEvent("auth:changed", { detail: { user: authUser } }));
      refreshConversations();
    } catch {
      setAuthMode(authMode, "网络错误，请重试");
    } finally {
      authSubmit.disabled = false;
    }
  });

  async function loadAuth() {
    try {
      const res = await fetch(`${API_BASE}/api/auth/me`);
      authUser = (await res.json()).user || null;
    } catch {
      authUser = null;
    }
    renderAuthArea();
    if (authUser) {
      // 页面加载时已登录：让桌宠等模块补拉账户数据
      window.dispatchEvent(new CustomEvent("auth:changed", { detail: { user: authUser } }));
    }
  }
  loadAuth();

  window.addEventListener("auth:request-login", (event) => {
    openAuth("login", (event.detail && event.detail.hint) || "");
  });

  // ---- 设置与诊断：检索增强开关 + 运行日志 ----
  const settingsButton = document.getElementById("settings-button");
  const settingsDrawer = document.getElementById("settings-drawer");
  const settingsBackdrop = document.getElementById("settings-backdrop");
  const settingsClose = document.getElementById("settings-close");
  const settingsFeatures = document.getElementById("settings-features");
  const settingsLog = document.getElementById("settings-log");
  const logsRefresh = document.getElementById("logs-refresh");
  const FEATURE_META = {
    vector: { title: "语义检索", desc: "向量模型参与检索排序（RRF 融合）" },
    jev: { title: "JEV 门控", desc: "检索质量门控：过滤低相关与提示注入片段" },
    pkulaw: { title: "MCP · 北大法宝", desc: "问答时在线语义检索权威法条（消耗积分）" },
  };
  let logsTimer = null;

  function setSettingsOpen(open) {
    settingsDrawer.hidden = !open;
    settingsBackdrop.hidden = !open;
    settingsButton.setAttribute("aria-expanded", String(open));
    if (open) {
      loadSettings();
      loadLogs();
      logsTimer = setInterval(loadLogs, 5000);
    } else if (logsTimer) {
      clearInterval(logsTimer);
      logsTimer = null;
    }
  }

  async function loadSettings() {
    settingsFeatures.replaceChildren(renderSettingNotice("正在读取服务状态…"));
    try {
      const res = await fetch(`${API_BASE}/api/settings`);
      if (res.status === 403) {
        settingsFeatures.replaceChildren(renderSettingNotice("检索增强开关仅管理员可调整"));
        return;
      }
      if (!res.ok) throw new Error("settings request failed");
      renderFeatures((await res.json()).features || {});
    } catch {
      settingsFeatures.replaceChildren(renderSettingNotice("服务状态读取失败，请稍后重试"));
    }
  }

  function renderFeatures(features) {
    settingsFeatures.replaceChildren();
    Object.keys(FEATURE_META).forEach((key) => {
      const meta = FEATURE_META[key];
      const state = features[key] || { enabled: false, configured: false, model: "", note: "" };
      const row = document.createElement("div");
      row.className = "setting-row";
      if (!state.configured) row.classList.add("unavailable");
      const info = document.createElement("div");
      info.className = "setting-info";
      const titleLine = document.createElement("div");
      titleLine.className = "setting-title-line";
      const title = document.createElement("strong");
      title.textContent = meta.title;
      titleLine.appendChild(title);
      if (state.configured && state.model) {
        const chip = document.createElement("span");
        chip.className = "setting-model-chip";
        chip.textContent = state.model;
        titleLine.appendChild(chip);
      }
      const desc = document.createElement("small");
      desc.textContent = state.configured ? meta.desc : (state.note || meta.desc);
      info.append(titleLine, desc);
      const toggleLabel = document.createElement("label");
      toggleLabel.className = "setting-toggle";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.checked = Boolean(state.enabled);
      input.disabled = !state.configured;
      input.setAttribute("aria-label", `${meta.title} 开关`);
      input.addEventListener("change", () => updateSetting(key, input.checked));
      const track = document.createElement("span");
      track.className = "setting-track";
      track.setAttribute("aria-hidden", "true");
      toggleLabel.append(input, track);
      row.append(info, toggleLabel);
      settingsFeatures.appendChild(row);
    });
  }

  async function updateSetting(key, enabled) {
    try {
      const res = await fetch(`${API_BASE}/api/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [key]: enabled }),
      });
      if (!res.ok) throw new Error("settings update failed");
      window.toast(`${FEATURE_META[key].title} 已${enabled ? "开启" : "关闭"}`);
    } catch {
      window.toast("设置保存失败，请稍后重试");
      loadSettings();
    }
  }

  async function loadLogs() {
    try {
      const res = await fetch(`${API_BASE}/api/logs`);
      if (res.status === 403) {
        settingsLog.replaceChildren(renderSettingNotice("运行日志仅管理员可查看"));
        return;
      }
      if (!res.ok) throw new Error("logs request failed");
      const logs = (await res.json()).logs || [];
      if (!logs.length) {
        settingsLog.replaceChildren(renderSettingNotice("暂无日志"));
        return;
      }
      settingsLog.replaceChildren();
      logs.forEach((entry) => {
        const line = document.createElement("div");
        line.className = `log-line log-${(entry.level || "INFO").toLowerCase()}`;
        const time = document.createElement("span");
        time.className = "log-time";
        time.textContent = `${entry.time} ${entry.name}`;
        const message = document.createElement("span");
        message.className = "log-msg";
        message.textContent = entry.message;
        line.append(time, message);
        settingsLog.appendChild(line);
      });
      settingsLog.scrollTop = 0;
    } catch {
      settingsLog.replaceChildren(renderSettingNotice("日志读取失败，请稍后重试"));
    }
  }

  function renderSettingNotice(text) {
    const notice = document.createElement("p");
    notice.className = "settings-hint";
    notice.textContent = text;
    return notice;
  }

  // ---- 桌宠偏好：显示开关 + 独立的记忆授权 ----
  const settingsPet = document.getElementById("settings-pet");
  if (settingsPet) {
    let petVisible = true;
    try { petVisible = localStorage.getItem("faxiao_pet_hidden") !== "1"; } catch { /* 存储不可用按可见处理 */ }
    const makeToggleRow = (titleText, descText, checked, label) => {
      const row = document.createElement("div");
      row.className = "setting-row";
      const info = document.createElement("div");
      info.className = "setting-info";
      const title = document.createElement("strong");
      title.textContent = titleText;
      const desc = document.createElement("small");
      desc.textContent = descText;
      info.append(title, desc);
      const toggleLabel = document.createElement("label");
      toggleLabel.className = "setting-toggle";
      const toggle = document.createElement("input");
      toggle.type = "checkbox";
      toggle.checked = checked;
      toggle.setAttribute("aria-label", label);
      const track = document.createElement("span");
      track.className = "setting-track";
      track.setAttribute("aria-hidden", "true");
      toggleLabel.append(toggle, track);
      row.append(info, toggleLabel);
      return { row, toggle };
    };

    const visibleSetting = makeToggleRow(
      "獬豸桌宠", "3D 獬豸陪伴问答 · 眼随鼠标 · 可拖拽移动",
      petVisible, "獬豸桌宠开关");
    visibleSetting.toggle.addEventListener("change", () => {
      // pet.js 负责实际显隐与 localStorage 持久化，这里只发事件 + 提示
      window.dispatchEvent(new CustomEvent("pet:visibility", {
        detail: { visible: visibleSetting.toggle.checked },
      }));
      window.toast(`獬豸桌宠已${visibleSetting.toggle.checked ? "开启" : "隐藏"}`);
    });
    // 宠物可能通过面板里的「隐藏宠物」按钮隐藏（或事件来自其它入口），保持开关同步
    const syncPetToggle = () => {
      try { visibleSetting.toggle.checked = localStorage.getItem("faxiao_pet_hidden") !== "1"; } catch { /* 存储不可用按可见处理 */ }
    };
    window.addEventListener("pet:visibility", (e) => {
      visibleSetting.toggle.checked = !(e.detail && e.detail.visible === false);
    });
    settingsButton.addEventListener("click", syncPetToggle);

    const memorySetting = makeToggleRow(
      "咨询记忆", "主动开启后，才会把有依据的问答提炼为最多 50 条摘要",
      petMemoryEnabled(), "允许獬豸记录咨询摘要");
    memorySetting.toggle.addEventListener("change", () => {
      try {
        if (memorySetting.toggle.checked) localStorage.setItem(PET_MEMORY_KEY, "1");
        else localStorage.removeItem(PET_MEMORY_KEY);
      } catch { /* 存储不可用则本次偏好不持久化 */ }
      window.dispatchEvent(new CustomEvent("pet:memory-preference", {
        detail: { enabled: memorySetting.toggle.checked },
      }));
      window.toast(`咨询记忆已${memorySetting.toggle.checked ? "开启" : "关闭"}`);
    });

    // 咨询记忆相关的两行收进一张子卡片：授权开关 + 清空操作，和上面的显隐开关分开
    const memoryCard = document.createElement("div");
    memoryCard.className = "setting-subcard";
    memoryCard.appendChild(memorySetting.row);

    const clearRow = document.createElement("div");
    clearRow.className = "setting-subcard-action";
    const clearInfo = document.createElement("div");
    clearInfo.className = "setting-info";
    const clearTitle = document.createElement("strong");
    clearTitle.textContent = "已保存的记忆";
    const clearDesc = document.createElement("small");
    clearDesc.textContent = "关闭记录不会自动删除已有内容，可随时全部清空";
    clearInfo.append(clearTitle, clearDesc);
    const clearButton = document.createElement("button");
    clearButton.className = "settings-danger-btn";
    clearButton.type = "button";
    clearButton.textContent = "全部清空";
    clearButton.addEventListener("click", () => {
      window.dispatchEvent(new CustomEvent("pet:clear-memories"));
    });
    clearRow.append(clearInfo, clearButton);
    memoryCard.appendChild(clearRow);

    settingsPet.replaceChildren(visibleSetting.row, memoryCard);
  }

  settingsButton.addEventListener("click", () =>
    setSettingsOpen(settingsDrawer.hidden));
  settingsClose.addEventListener("click", () => setSettingsOpen(false));
  settingsBackdrop.addEventListener("click", () => setSettingsOpen(false));
  logsRefresh.addEventListener("click", loadLogs);
  // 母标题折叠：三个栏目默认收起，点标题展开/收起各自的子内容
  document.querySelectorAll(".settings-fold-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const expanded = btn.getAttribute("aria-expanded") === "true";
      btn.setAttribute("aria-expanded", String(!expanded));
      const panel = document.getElementById(btn.getAttribute("aria-controls") || "");
      if (panel) panel.hidden = expanded;
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !settingsDrawer.hidden) setSettingsOpen(false);
  });
})();
