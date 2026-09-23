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

  // ---- 配置 ----
  fetch("/api/config")
    .then((r) => r.json())
    .then((cfg) => {
      const badge = document.getElementById("backend-badge");
      badge.textContent = cfg.retrieval_backend === "vector+lexical+fts5"
        ? "语义检索已启用" : "词法检索（未配置向量服务）";
      if (!cfg.llm_configured) {
        toast("模型服务未配置，请设置 LAW_LLM_* 环境变量");
      }
    })
    .catch(() => {});

  // ---- 问答 ----
  const form = document.getElementById("qa-form");
  const input = document.getElementById("qa-input");
  const sendBtn = document.getElementById("qa-send");
  const messagesEl = document.getElementById("qa-messages");
  const citationsPanel = document.getElementById("qa-citations");
  const citationsList = document.getElementById("citations-list");
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
    assistantEl.textContent = "正在思考…";
    const stream = createStreamRenderer(assistantEl);

    try {
      const res = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, conversation_id: conversationId }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        assistantEl.textContent = "请求失败：" + (err.error || res.statusText);
        sending = false;
        sendBtn.disabled = false;
        return;
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          let event;
          try { event = JSON.parse(line.slice(6)); } catch { continue; }

          if (event.type === "status") {
            stream.status(event.text);
          } else if (event.type === "delta") {
            stream.push(event.text);
          } else if (event.type === "result") {
            conversationId = event.conversation_id || conversationId;
            stream.finish(event);
          } else if (event.type === "error") {
            stream.fail("出错了：" + event.error);
          }
        }
      }
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
    el.classList.remove("streaming");
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
      el.querySelectorAll(".citation-ref").forEach((ref) => {
        ref.addEventListener("click", () => {
          const label = ref.dataset.label;
          const item = citationsList.querySelector(`[data-label="${label}"]`);
          if (item) {
            item.scrollIntoView({ behavior: "smooth", block: "center" });
            item.style.background = "#dbeafe";
            setTimeout(() => { item.style.background = ""; }, 1500);
          }
        });
      });
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

    function complete() {
      if (resolveDone) resolveDone();
      resolveDone = null;
    }

    function schedule() {
      if (!frame) frame = requestAnimationFrame(typeNext);
    }

    function typeNext() {
      frame = 0;
      const remaining = target.length - shown.length;
      if (remaining > 0) {
        // Keep short responses natural and drain a fast SSE burst without a
        // long artificial wait.  The model remains the source of the text.
        const count = Math.min(24, Math.max(2, Math.ceil(remaining / 20)));
        shown += target.slice(shown.length, shown.length + count);
        el.textContent = shown;
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
      el.classList.add("streaming");
    }

    return {
      status(text) {
        if (!started) el.textContent = text;
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
        el.classList.remove("streaming");
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
    let paragraph = [];
    let list = "";
    let inCode = false;
    let codeLines = [];

    const closeList = () => {
      if (list) html.push(`</${list}>`);
      list = "";
    };
    const closeParagraph = () => {
      if (paragraph.length) html.push(`<p>${paragraph.map(formatInline).join("<br>")}</p>`);
      paragraph = [];
    };
    const closeCode = () => {
      html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      codeLines = [];
      inCode = false;
    };

    lines.forEach((line) => {
      if (/^\s*```/.test(line)) {
        closeParagraph();
        closeList();
        if (inCode) closeCode();
        else inCode = true;
        return;
      }
      if (inCode) {
        codeLines.push(line);
        return;
      }
      const heading = line.match(/^(#{1,3})\s+(.+)$/);
      const unordered = line.match(/^\s*[-*+]\s+(.+)$/);
      const ordered = line.match(/^\s*\d+[.)]\s+(.+)$/);
      const quote = line.match(/^\s*>\s?(.+)$/);
      if (heading) {
        closeParagraph();
        closeList();
        const level = heading[1].length;
        html.push(`<h${level}>${formatInline(heading[2])}</h${level}>`);
      } else if (unordered || ordered) {
        closeParagraph();
        const nextList = unordered ? "ul" : "ol";
        if (list !== nextList) {
          closeList();
          list = nextList;
          html.push(`<${list}>`);
        }
        html.push(`<li>${formatInline((unordered || ordered)[1])}</li>`);
      } else if (quote) {
        closeParagraph();
        closeList();
        html.push(`<blockquote>${formatInline(quote[1])}</blockquote>`);
      } else if (!line.trim()) {
        closeParagraph();
        closeList();
      } else {
        closeList();
        paragraph.push(line);
      }
    });
    if (inCode) closeCode();
    closeParagraph();
    closeList();
    return html.join("");
  }

  function formatInline(text) {
    let html = escapeHtml(text);
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
    html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
    return html.replace(/\[(C\d+)\]/g,
      '<button type="button" class="citation-ref" data-label="$1" aria-label="查看引用 $1">[$1]</button>');
  }

  function showCitations(citations) {
    citationsList.innerHTML = "";
    citations.forEach((c) => {
      const item = document.createElement("div");
      item.className = "citation-item";
      item.dataset.label = c.label;
      item.innerHTML = `
        <div class="label">[${c.label}]</div>
        <div class="section">${escapeHtml(c.section)}</div>
        <div class="excerpt">${escapeHtml(c.excerpt)}</div>
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

  // ---- Toast ----
  window.toast = function (msg) {
    const el = document.getElementById("toast");
    el.textContent = msg;
    el.hidden = false;
    setTimeout(() => { el.hidden = true; }, 3000);
  };
})();
