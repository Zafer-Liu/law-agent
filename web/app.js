/* 法晓 · 法律问答 Tab */

(function () {
  "use strict";

  // ---- Tab 切换 ----
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
    });
  });

  // ---- 问答 ----
  const form = document.getElementById("qa-form");
  const input = document.getElementById("qa-input");
  const sendBtn = document.getElementById("qa-send");
  const messagesEl = document.getElementById("qa-messages");
  const citationsPanel = document.getElementById("qa-citations");
  const citationsList = document.getElementById("citations-list");
  const citationsCount = document.getElementById("citations-count");
  const citationsClose = document.getElementById("citations-close");

  let conversationId = "";
  let sending = false;

  input.addEventListener("input", () => {
    sendBtn.disabled = !input.value.trim() || sending;
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!sendBtn.disabled) form.requestSubmit();
    }
  });

  // 快捷提问
  document.querySelectorAll(".quick-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      input.value = btn.dataset.q;
      sendBtn.disabled = false;
      form.requestSubmit();
    });
  });

  citationsClose.addEventListener("click", () => {
    citationsPanel.hidden = true;
  });

  messagesEl.addEventListener("click", (event) => {
    const ref = event.target.closest(".citation-ref");
    if (!ref) return;
    citationsPanel.hidden = false;
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

    // 移除欢迎页
    const welcome = messagesEl.querySelector(".welcome");
    if (welcome) welcome.remove();

    appendMessage("user", question);
    const assistantEl = appendMessage("assistant", "");
    const stream = createStreamRenderer(assistantEl);
    stream.status("正在查找相关法律…");
    citationsPanel.hidden = true;
    citationsList.replaceChildren();
    citationsCount.textContent = "";

    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, conversation_id: conversationId }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        stream.fail(err.error || "暂时无法回答，请稍后再试。");
        sending = false;
        sendBtn.disabled = false;
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
    } catch (err) {
      stream.fail("网络错误，请重试。");
    }

    sending = false;
    sendBtn.disabled = !input.value.trim();
    input.focus();
  });

  function appendMessage(role, text) {
    const el = document.createElement("div");
    el.className = "msg " + role;
    el.textContent = text;
    messagesEl.appendChild(el);
    scrollToBottom();
    return el;
  }

  function renderAnswer(el, event) {
    el.classList.remove("streaming", "loading");
    el.innerHTML = renderMarkdown(event.content);

    if (event.notice) {
      const notice = document.createElement("div");
      notice.className = "msg-notice";
      notice.textContent = event.notice;
      el.appendChild(notice);
    }

    // 引用面板
    if (event.citations && event.citations.length > 0) {
      showCitations(event.citations);
    }

    scrollToBottom();
  }

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
    citationsList.replaceChildren();
    citationsCount.textContent = `${citations.length} 条`;
    citations.forEach((c) => {
      const item = document.createElement("article");
      item.className = "citation-item";
      item.dataset.label = c.label;
      item.innerHTML = `
        <div class="citation-meta">
          <span class="label">[${escapeHtml(c.label)}]</span>
          <span class="section">${escapeHtml(c.section || "法律资料")}</span>
        </div>
        <p class="excerpt">${escapeHtml(c.excerpt || "暂无可展示的原文片段")}</p>
      `;
      citationsList.appendChild(item);
    });
    citationsPanel.hidden = false;
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
