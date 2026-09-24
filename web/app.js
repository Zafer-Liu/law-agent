/* 法晓 · 法律问答 Tab */

(function () {
  "use strict";

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
    const button = event.target.closest("[data-conversation-id]");
    if (!button) return;
    if (sending) return window.toast("回答生成中，请稍候再切换对话");
    loadConversation(button.dataset.conversationId);
  });

  citationsToggle.addEventListener("click", () => {
    if (!activeCitations.length) return;
    const open = !citationsRail.classList.contains("open");
    if (open) showCitations(activeCitations);
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

    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, conversation_id: conversationId, client_id: clientId }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        stream.fail(err.error || "暂时无法回答，请稍后再试。");
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
        } else if (event.type === "delta") {
          stream.push(event.text);
        } else if (event.type === "result") {
          conversationId = event.conversation_id || conversationId;
          resultEvent = event;
          stream.finish(event);
        } else if (event.type === "error") {
          stream.fail(event.error || "暂时无法回答，请稍后再试。");
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
      const res = await fetch(`/api/conversations?client_id=${encodeURIComponent(clientId)}`);
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
      const button = document.createElement("button");
      button.type = "button";
      button.className = "conversation-item";
      button.dataset.conversationId = item.id;
      button.classList.toggle("active", item.id === conversationId);
      button.setAttribute("aria-current", item.id === conversationId ? "true" : "false");

      const title = document.createElement("span");
      title.className = "conversation-item-title";
      title.textContent = item.title;
      const time = document.createElement("time");
      time.className = "conversation-item-time";
      time.dateTime = item.updated_at || item.created_at;
      time.textContent = formatConversationTime(time.dateTime);
      button.append(title, time);
      conversationList.appendChild(button);
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

  async function loadConversation(id) {
    conversationId = id;
    renderConversationList();
    closeConversationDrawer();
    messagesEl.innerHTML = '<div class="conversation-loading">正在载入对话…</div>';
    clearCitations();
    try {
      const res = await fetch(`/api/conversations/${encodeURIComponent(id)}?client_id=${encodeURIComponent(clientId)}`);
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
    citationsList.replaceChildren();
    setCitationsOpen(false);
  }

  function setAvailableCitations(citations) {
    activeCitations = Array.isArray(citations) ? citations : [];
    const count = activeCitations.length;
    citationsCount.textContent = count ? String(count) : "";
    citationsPanelCount.textContent = count ? `${count} 条` : "";
    citationsToggle.disabled = count === 0;
    if (!count) setCitationsOpen(false);
  }

  function setCitationsOpen(open) {
    const expanded = Boolean(open && activeCitations.length);
    citationsRail.classList.toggle("open", expanded);
    citationsPanel.hidden = !expanded;
    citationsToggle.setAttribute("aria-expanded", String(expanded));
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
        </div>
        <p class="citation-source">${escapeHtml(source)}</p>
        <p class="excerpt">${escapeHtml(c.excerpt || "暂无可展示的原文片段")}</p>
      `;
      citationsList.appendChild(item);
    });
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
})();
