/* 法晓 · 法律问答 Tab */

(function () {
  "use strict";

  // ---- 路径前缀：支持挂载在子路径（如 /law/）；根路径部署时为空串 ----
  const API_BASE = location.pathname.replace(/\/[^/]*$/, "");

  // ---- 应用导航 ----
  const tabs = document.querySelectorAll(".tab");
  const panels = document.querySelectorAll(".tab-panel");
  let activeTab = "qa";
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      const next = tab.dataset.tab;
      if (next !== activeTab) track("module_switch", { from: activeTab, to: next });
      activeTab = next;
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
  const conversationCategories = document.getElementById("conversation-categories");
  const newConversationBtn = document.getElementById("new-conversation");
  const chatTitle = document.getElementById("chat-title");
  const welcomeTemplate = messagesEl.innerHTML;

  const CATEGORY_LABELS = ["劳动", "合同", "消费", "侵权", "婚姻家庭", "刑事", "其他"];

  let conversationId = "";
  let sending = false;
  let conversations = [];
  let activeCitations = [];
  let categoryFilter = "";   // "" = 全部；"__none__" = 未分类
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

  // ---- 产品事件埋点：批量、静默、不阻塞业务（需登录，未登录直接丢弃） ----
  const EVENT_ENDPOINT = API_BASE + "/api/events";
  const eventQueue = [];
  let eventFlushTimer = 0;

  function track(name, props = {}) {
    if (!authUser) return;
    eventQueue.push({ name, props });
    if (eventQueue.length >= 10) {
      flushEvents();
      return;
    }
    if (!eventFlushTimer) eventFlushTimer = setTimeout(flushEvents, 3000);
  }

  async function flushEvents() {
    eventFlushTimer = 0;
    if (!eventQueue.length) return;
    const batch = eventQueue.splice(0, 20);
    try {
      await fetch(EVENT_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ events: batch }),
        keepalive: true,
      });
    } catch { /* 埋点失败不影响业务 */ }
  }
  window.trackEvent = track;
  window.addEventListener("pagehide", flushEvents);

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
    // 点击别处时收起已展开的"更多"菜单
    const currentRow = event.target.closest(".conversation-item");
    conversationList.querySelectorAll(".conversation-item.more-open").forEach((row) => {
      if (row !== currentRow) {
        row.classList.remove("more-open");
        row.querySelector(".conversation-more-btn")?.setAttribute("aria-expanded", "false");
      }
    });
    const moreButton = event.target.closest(".conversation-more-btn");
    if (moreButton) {
      const row = moreButton.closest(".conversation-item");
      const open = !row.classList.contains("more-open");
      conversationList.querySelectorAll(".conversation-item.more-open").forEach((item) => {
        item.classList.remove("more-open");
        item.querySelector(".conversation-more-btn")?.setAttribute("aria-expanded", "false");
      });
      if (open) row.classList.add("more-open");
      moreButton.setAttribute("aria-expanded", String(open));
      return;
    }
    const confirmButton = event.target.closest(".conversation-confirm-btn");
    if (confirmButton) {
      if (sending) return window.toast("回答生成中，请稍后再删除对话");
      deleteConversation(
        confirmButton.dataset.conversationId,
        confirmButton.dataset.conversationTitle,
        confirmButton,
      );
      return;
    }
    const deleteButton = event.target.closest(".conversation-delete-btn");
    if (deleteButton) {
      if (sending) return window.toast("回答生成中，请稍后再删除对话");
      // 原位确认：本行变红，删除按钮切换为「确认删除」
      clearConversationConfirmStates(deleteButton.closest(".conversation-item"));
      const row = deleteButton.closest(".conversation-item");
      row?.classList.add("confirm-delete");
      deleteButton.hidden = true;
      row.querySelector(".conversation-confirm-btn").hidden = false;
      return;
    }
    const item = event.target.closest(".conversation-item");
    if (!item || item.classList.contains("renaming")) return;
    const id = item.dataset.conversationId;
    if (event.target.closest(".conversation-pin-btn")) {
      toggleConversationPin(id, item);
      return;
    }
    if (event.target.closest(".conversation-rename-btn")) {
      startConversationRename(item);
      return;
    }
    if (event.target.closest(".conversation-category-btn")) {
      toggleCategoryChoice(item);
      return;
    }
    if (event.target.closest(".conversation-matter-btn")) {
      convertConversationToMatter(id, item.dataset.conversationTitle || "对话");
      return;
    }
    if (event.target.closest(".conversation-export-btn")) {
      exportConversation(id, item.dataset.conversationTitle || "对话");
      return;
    }
    const openButton = event.target.closest(".conversation-item-open");
    if (!openButton) return;
    if (sending) return window.toast("回答生成中，请稍后再切换对话");
    clearConversationConfirmStates();
    loadConversation(openButton.dataset.conversationId);
  });

  if (conversationCategories) {
    conversationCategories.addEventListener("click", (event) => {
      const chip = event.target.closest(".conversation-cat-chip");
      if (!chip) return;
      categoryFilter = chip.dataset.value;
      renderConversationList();
    });
  }

  function toggleCategoryChoice(row) {
    const existing = row.querySelector(".conversation-category-row");
    if (existing) {
      row.querySelector(".conversation-category-btn")?.setAttribute("aria-expanded", "false");
      existing.remove();
      return;
    }
    row.querySelector(".conversation-category-btn")?.setAttribute("aria-expanded", "true");
    const chips = document.createElement("div");
    chips.className = "conversation-category-row";
    const id = row.dataset.conversationId;
    const clear = document.createElement("button");
    clear.type = "button";
    clear.className = "conversation-cat-chip";
    clear.textContent = "清除";
    clear.setAttribute("aria-label", "清除会话分类");
    clear.addEventListener("click", () => setConversationCategory(id, row, ""));
    chips.appendChild(clear);
    CATEGORY_LABELS.forEach((label) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "conversation-cat-chip";
      chip.textContent = label;
      chip.addEventListener("click", () => setConversationCategory(id, row, label));
      chips.appendChild(chip);
    });
    row.appendChild(chips);
  }

  async function setConversationCategory(id, row, category) {
    try {
      const res = await fetch(`${API_BASE}/api/conversations/${encodeURIComponent(id)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ category }),
      });
      if (!res.ok) throw new Error("category request failed");
      const current = conversations.find((item) => item.id === id);
      if (current) current.category = category || "";
      renderConversationList();
      window.toast(category ? `已分类为「${category}」` : "已清除分类");
    } catch {
      window.toast("分类保存失败，请稍后重试");
    }
  }

  async function convertConversationToMatter(id, title) {
    window.toast("正在从对话整理案情…");
    try {
      const res = await fetch(
        `${API_BASE}/api/conversations/${encodeURIComponent(id)}/intake`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (res.status === 401) {
        window.dispatchEvent(new CustomEvent("auth:request-login"));
        return;
      }
      if (!res.ok) {
        window.toast(data.error || "案情整理失败，可在工作台手动填写");
        return;
      }
      document.getElementById("tab-button-matter").click();
      window.dispatchEvent(new CustomEvent("matter:prefill", { detail: { intake: data.intake } }));
      window.toast(`已从「${title.slice(0, 16)}」整理案情，请确认后生成处理路径`);
    } catch {
      window.toast("网络错误，请稍后重试");
    }
  }

  async function toggleConversationPin(id, row) {
    const current = conversations.find((item) => item.id === id);
    const nextPinned = !(current && current.pinned);
    try {
      const res = await fetch(`${API_BASE}/api/conversations/${encodeURIComponent(id)}/pin`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pinned: nextPinned }),
      });
      if (!res.ok) throw new Error("pin request failed");
      if (current) current.pinned = nextPinned;
      conversations.sort((a, b) => (b.pinned ? 1 : 0) - (a.pinned ? 1 : 0));
      renderConversationList();
      window.toast(nextPinned ? "已置顶" : "已取消置顶");
    } catch {
      window.toast("操作失败，请稍后重试");
    }
  }

  function startConversationRename(row) {
    if (row.classList.contains("renaming")) return;
    const id = row.dataset.conversationId;
    const current = conversations.find((item) => item.id === id);
    const titleEl = row.querySelector(".conversation-item-title");
    if (!titleEl || !current) return;
    const input = document.createElement("input");
    input.type = "text";
    input.className = "conversation-rename-input";
    input.maxLength = 60;
    input.value = current.title;
    input.setAttribute("aria-label", "对话标题");
    row.classList.add("renaming");
    titleEl.replaceWith(input);
    input.focus();
    input.select();
    let settled = false;
    const finish = async (save) => {
      if (settled) return;
      settled = true;
      const value = input.value.trim();
      if (!save || !value || value === current.title) {
        renderConversationList();
        return;
      }
      try {
        const res = await fetch(`${API_BASE}/api/conversations/${encodeURIComponent(id)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: value }),
        });
        if (!res.ok) throw new Error("rename request failed");
        current.title = value;
        if (conversationId === id) chatTitle.textContent = value;
      } catch {
        window.toast("重命名失败，请稍后重试");
      }
      renderConversationList();
    };
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        finish(true);
      } else if (event.key === "Escape") {
        finish(false);
      }
    });
    input.addEventListener("blur", () => finish(true));
  }

  async function exportConversation(id, title) {
    try {
      const res = await fetch(`${API_BASE}/api/conversations/${encodeURIComponent(id)}/export`);
      if (!res.ok) throw new Error("export request failed");
      const blob = await res.blob();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = `${title.slice(0, 40)}.md`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(link.href), 5000);
      window.toast("对话已导出为 Markdown");
    } catch {
      window.toast("导出失败，请稍后重试");
    }
  }

  function clearConversationConfirmStates(exceptRow = null) {
    conversationList.querySelectorAll(".conversation-item.confirm-delete").forEach((row) => {
      if (row === exceptRow) return;
      row.classList.remove("confirm-delete");
      row.querySelector(".conversation-confirm-btn")?.setAttribute("hidden", "");
      row.querySelector(".conversation-delete-btn")?.removeAttribute("hidden");
    });
  }

  citationsToggle.addEventListener("click", () => {
    const open = !citationsRail.classList.contains("open");
    if (open) {
      if (activeCitations.length) {
        showCitations(activeCitations);
        track("citation_open", { via: "panel_toggle" });
      } else showSourceList();
    }
    setCitationsOpen(open);
  });
  citationsClose.addEventListener("click", () => setCitationsOpen(false));

  // ---- 上下文统计条：摘要+尾部字符 / 压缩阈值；达到阈值后端自动压缩 ----
  const contextMeter = document.getElementById("context-meter");
  const contextMeterLabel = document.getElementById("context-meter-label");
  const contextMeterFill = document.getElementById("context-meter-fill");
  function updateContextMeter(context) {
    if (!contextMeter || !context || !context.budget) return;
    const ratio = Math.min(1, (context.total_chars || 0) / context.budget);
    const fmt = (tokens) => (tokens >= 1000 ? `${(tokens / 1000).toFixed(1)}k` : String(tokens));
    contextMeterLabel.textContent = `上下文 ${fmt(context.tokens_est || 0)} / ${fmt(context.budget_tokens || 0)}`;
    contextMeterLabel.classList.toggle("compacted", Boolean(context.compacted));
    contextMeterFill.style.width = `${Math.round(ratio * 100)}%`;
    contextMeter.classList.toggle("warn", ratio >= 0.7 && ratio < 0.92);
    contextMeter.classList.toggle("hot", ratio >= 0.92);
    contextMeter.hidden = false;
  }
  window.updateContextMeter = updateContextMeter;

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
    track("citation_open", { via: "inline_ref" });
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
    if (isNewConversation) track("qa_ask", { new_conversation: true });
    else track("qa_ask", { new_conversation: false });
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
        } else if (event.type === "context") {
          updateContextMeter(event);
        } else if (event.type === "delta") {
          stream.push(event.text);
        } else if (event.type === "result") {
          conversationId = event.conversation_id || conversationId;
          resultEvent = event;
          stream.finish(event);
          track("answer_received", {
            grounded: Boolean(event.grounded),
            citations: (event.citations || []).length,
            online: String(event.backend || "").includes("pkulaw"),
          });
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

  // ---- 欢迎屏吉祥物追视：鼠标左右分区切换朝向动画（带滞回防抖） ----
  // 三张透明 WebP 叠放，仅切透明度；素材缺失时停留在正面，不影响页面。
  const heroMascotZone = { current: "front" };
  window.addEventListener("pointermove", (e) => {
    heroMascotZone.x = e.clientX / Math.max(1, window.innerWidth);
  }, { passive: true });
  let heroRaf = 0, heroLast = 0;
  function heroMascotFrame(now) {
    heroRaf = requestAnimationFrame(heroMascotFrame);
    if (now - heroLast < 80) return;
    heroLast = now;
    const mascot = messagesEl.querySelector(".welcome-mascot");
    if (!mascot || document.hidden) return;
    const x = heroMascotZone.x ?? 0.5;
    let zone = heroMascotZone.current;
    if (x < 0.35) zone = "left";
    else if (x > 0.65) zone = "right";
    else if (zone === "left" && x < 0.5) zone = "left";       // 滞回：进入后需过中线才回正面
    else if (zone === "right" && x > 0.5) zone = "right";
    else zone = "front";
    if (zone !== heroMascotZone.current) {
      heroMascotZone.current = zone;
      mascot.dataset.zone = zone;
    }
  }
  if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
    heroRaf = requestAnimationFrame(heroMascotFrame);
  }

  function startNewConversation() {
    conversationId = "";
    chatTitle.textContent = "新对话";
    messagesEl.innerHTML = welcomeTemplate;
    clearCitations();
    if (contextMeter) contextMeter.hidden = true;   // 新对话无历史，隐藏上下文条
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
      (!categoryFilter
        || (categoryFilter === "__none__" ? !item.category : item.category === categoryFilter))
      && (!query || item.title.toLocaleLowerCase("zh-CN").includes(query)
      || String(item.preview || "").toLocaleLowerCase("zh-CN").includes(query))
    ));
    renderCategoryChips();
    conversationList.replaceChildren();
    filtered.forEach((item) => {
      const row = document.createElement("div");
      row.className = "conversation-item";
      row.classList.toggle("active", item.id === conversationId);
      row.classList.toggle("pinned", Boolean(item.pinned));
      row.dataset.conversationId = item.id;
      row.dataset.conversationTitle = item.title;

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
      time.textContent = formatConversationTime(time.dateTime)
        + (item.category ? ` · ${item.category}` : "");
      openButton.append(title, time);

      const actions = document.createElement("div");
      actions.className = "conversation-actions";
      const pinButton = document.createElement("button");
      pinButton.type = "button";
      pinButton.className = "conversation-pin-btn";
      pinButton.setAttribute("aria-pressed", String(Boolean(item.pinned)));
      pinButton.setAttribute("aria-label",
        item.pinned ? `取消置顶：${item.title}` : `置顶：${item.title}`);
      pinButton.title = item.pinned ? "取消置顶" : "置顶";
      pinButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 4.75h6l-.7 5.2 3 3.3H6.7l3-3.3L9 4.75Z"/><path d="M12 13.25V19.2"/></svg>';
      // 窄侧栏放不下 6 个按钮：其余操作收进"更多"弹出菜单
      const moreButton = document.createElement("button");
      moreButton.type = "button";
      moreButton.className = "conversation-more-btn";
      moreButton.setAttribute("aria-label", `更多操作：${item.title}`);
      moreButton.setAttribute("aria-expanded", "false");
      moreButton.title = "更多操作";
      moreButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 6.01v.01M12 12v.01M12 17.99v.01"/></svg>';
      actions.append(pinButton, moreButton);

      const morePop = document.createElement("div");
      morePop.className = "conversation-more-pop";
      const categoryButton = document.createElement("button");
      categoryButton.type = "button";
      categoryButton.className = "conversation-category-btn";
      categoryButton.setAttribute("aria-label", `设置分类：${item.title}`);
      categoryButton.setAttribute("aria-expanded", "false");
      categoryButton.title = "设置分类";
      categoryButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4.75 6.75h14.5M4.75 12h14.5M4.75 17.25h9"/></svg>';
      const renameButton = document.createElement("button");
      renameButton.type = "button";
      renameButton.className = "conversation-rename-btn";
      renameButton.setAttribute("aria-label", `重命名：${item.title}`);
      renameButton.title = "重命名";
      renameButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4.75 19.25h4l10-10-4-4-10 10v4Z"/><path d="m13.75 6.25 4 4"/></svg>';
      const matterButton = document.createElement("button");
      matterButton.type = "button";
      matterButton.className = "conversation-matter-btn";
      matterButton.setAttribute("aria-label", `整理成事项档案：${item.title}`);
      matterButton.title = "整理成事项档案";
      matterButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 4.75h10a1.25 1.25 0 0 1 1.25 1.25v13L12 16.5l-6.25 2.5v-13A1.25 1.25 0 0 1 7 4.75Z"/></svg>';
      const exportButton = document.createElement("button");
      exportButton.type = "button";
      exportButton.className = "conversation-export-btn";
      exportButton.setAttribute("aria-label", `导出为 Markdown：${item.title}`);
      exportButton.title = "导出为 Markdown";
      exportButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4.75v10m0 0 3.5-3.5M12 14.75l-3.5-3.5"/><path d="M5 19.25h14"/></svg>';
      morePop.append(categoryButton, renameButton, matterButton, exportButton);
      // 弹出菜单带文字标签，避免只有一排看不懂的符号
      const popLabels = new Map([
        [categoryButton, "设置分类"], [renameButton, "重命名"],
        [matterButton, "整理成事项"], [exportButton, "导出 Markdown"],
      ]);
      popLabels.forEach((label, button) => {
        const text = document.createElement("span");
        text.textContent = label;
        button.appendChild(text);
      });

      const deleteButton = document.createElement("button");
      deleteButton.type = "button";
      deleteButton.className = "conversation-delete-btn";
      deleteButton.dataset.conversationId = item.id;
      deleteButton.dataset.conversationTitle = item.title;
      deleteButton.setAttribute("aria-label", `删除对话：${item.title}`);
      deleteButton.title = "删除对话";
      deleteButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4.75h6V7m3 0-1 13H7L6 7m4 4v5m4-5v5"/></svg>';

      const confirmButton = document.createElement("button");
      confirmButton.type = "button";
      confirmButton.className = "conversation-confirm-btn";
      confirmButton.dataset.conversationId = item.id;
      confirmButton.dataset.conversationTitle = item.title;
      confirmButton.hidden = true;
      confirmButton.textContent = "确认删除";

      row.append(openButton, actions, morePop, deleteButton, confirmButton);
      conversationList.appendChild(row);
    });
    conversationTotal.textContent = conversations.length ? String(conversations.length) : "";
    conversationEmpty.textContent = query ? "没有匹配的对话"
      : categoryFilter ? "该分类下还没有对话" : "还没有保存的对话";
    conversationEmpty.hidden = filtered.length > 0;
  }

  function renderCategoryChips() {
    if (!conversationCategories) return;
    const used = CATEGORY_LABELS.filter((label) =>
      conversations.some((item) => item.category === label));
    if (!used.length) {
      conversationCategories.hidden = true;
      conversationCategories.replaceChildren();
      return;
    }
    conversationCategories.hidden = false;
    conversationCategories.replaceChildren();
    const make = (value, label) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "conversation-cat-chip";
      chip.dataset.value = value;
      chip.setAttribute("aria-pressed", String(categoryFilter === value));
      chip.textContent = label;
      conversationCategories.appendChild(chip);
    };
    make("", "全部");
    make("__none__", "未分类");
    used.forEach((label) => make(label, label));
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
    button.disabled = true;
    const row = button.closest(".conversation-item");
    row?.setAttribute("aria-busy", "true");
    const resetConfirm = () => {
      button.disabled = false;
      row?.removeAttribute("aria-busy");
      row?.classList.remove("confirm-delete");
      row?.querySelector(".conversation-delete-btn")?.removeAttribute("hidden");
      button.setAttribute("hidden", "");
    };
    try {
      const res = await fetch(`${API_BASE}/api/conversations/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
      if (res.status === 401) {
        resetConfirm();
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
      resetConfirm();
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
          renderAnswer(el, { ...message, message_id: message.id },
            { updateCitationRail: false, shouldScroll: false });
          if (message.citations && message.citations.length) latestCitations = message.citations;
        }
      });
      setAvailableCitations(latestCitations);
      updateContextMeter(data.context);
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

    if (event.message_id) appendFeedbackBar(el, event.message_id, event.feedback);

    if (updateCitationRail) {
      setAvailableCitations(el.citationRefs);
      suggestCourtForAnswer(el, event.content || "");
    }

    if (shouldScroll) scrollToBottom();
  }

  // ---- 问答 → 庭审直连：回答完成后按内容推荐可练手的模拟案例 ----
  let courtSuggestCache = new Map();

  async function suggestCourtForAnswer(el, content) {
    const text = content.slice(0, 500);
    if (!text.trim()) return;
    const cacheKey = text.slice(0, 60);
    let cases = courtSuggestCache.get(cacheKey);
    if (cases === undefined) {
      try {
        const res = await fetch(`${API_BASE}/api/court/suggest`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
        if (!res.ok) throw new Error("suggest request failed");
        cases = (await res.json()).cases || [];
      } catch {
        cases = [];
      }
      courtSuggestCache.set(cacheKey, cases);
      if (courtSuggestCache.size > 40) courtSuggestCache.clear();
    }
    if (!cases.length || !el.isConnected) return;
    const bar = document.createElement("div");
    bar.className = "court-suggest";
    const label = document.createElement("span");
    label.className = "court-suggest-label";
    label.textContent = "用这类问题练手：";
    bar.appendChild(label);
    cases.forEach((suggested) => {
      const link = document.createElement("button");
      link.type = "button";
      link.className = "court-suggest-btn";
      link.textContent = `${suggested.title} · ${suggested.theme}`;
      link.addEventListener("click", () => {
        track("qa_to_court", { case_id: suggested.case_id });
        document.getElementById("tab-button-court").click();
        window.courtOpenCase?.(suggested.case_id);
      });
      bar.appendChild(link);
    });
    el.appendChild(bar);
    scrollToBottom();
  }

  // ---- 回答反馈：有帮助 / 没帮助（没帮助可选原因并补充说明） ----
  const FEEDBACK_REASONS = [
    { id: "inaccurate", label: "内容不准确" },
    { id: "outdated", label: "依据过时" },
    { id: "irrelevant", label: "答非所问" },
    { id: "unclear", label: "看不懂" },
    { id: "verbose", label: "太冗长" },
    { id: "other", label: "其他" },
  ];

  function appendFeedbackBar(el, messageId, savedRating) {
    const bar = document.createElement("div");
    bar.className = "msg-feedback";
    bar.dataset.messageId = messageId;
    el.appendChild(bar);
    if (savedRating) {
      renderFeedbackDone(bar, savedRating, false);
      return;
    }
    renderFeedbackChoice(bar);
  }

  function renderFeedbackChoice(bar) {
    bar.replaceChildren();
    const caption = document.createElement("span");
    caption.className = "feedback-caption";
    caption.textContent = "这个回答有帮助吗？";
    const helpful = document.createElement("button");
    helpful.type = "button";
    helpful.className = "feedback-btn";
    helpful.textContent = "有帮助";
    helpful.setAttribute("aria-pressed", "false");
    helpful.addEventListener("click", () => submitFeedback(bar, "helpful"));
    const unhelpful = document.createElement("button");
    unhelpful.type = "button";
    unhelpful.className = "feedback-btn";
    unhelpful.textContent = "没帮助";
    unhelpful.setAttribute("aria-pressed", "false");
    unhelpful.addEventListener("click", () => renderFeedbackForm(bar));
    bar.append(caption, helpful, unhelpful);
  }

  function renderFeedbackForm(bar) {
    bar.replaceChildren();
    const form = document.createElement("form");
    form.className = "feedback-form";
    form.noValidate = true;
    const caption = document.createElement("p");
    caption.className = "feedback-form-caption";
    caption.textContent = "没有帮助的原因是？（可补充说明）";
    form.appendChild(caption);
    const options = document.createElement("div");
    options.className = "feedback-reasons";
    options.setAttribute("role", "radiogroup");
    options.setAttribute("aria-label", "没有帮助的原因");
    FEEDBACK_REASONS.forEach((reason, index) => {
      const label = document.createElement("label");
      label.className = "feedback-reason";
      const input = document.createElement("input");
      input.type = "radio";
      input.name = `feedback-reason-${bar.dataset.messageId}`;
      input.value = reason.id;
      if (index === FEEDBACK_REASONS.length - 1) input.checked = true;
      label.append(input, document.createTextNode(reason.label));
      options.appendChild(label);
    });
    form.appendChild(options);
    const textarea = document.createElement("textarea");
    textarea.rows = 2;
    textarea.maxLength = 200;
    textarea.className = "feedback-comment";
    textarea.setAttribute("aria-label", "补充说明（可选）");
    textarea.placeholder = "补充说明（可选，200 字以内）";
    form.appendChild(textarea);
    const actions = document.createElement("div");
    actions.className = "feedback-actions";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "feedback-btn";
    cancel.textContent = "取消";
    cancel.addEventListener("click", () => renderFeedbackChoice(bar));
    const submit = document.createElement("button");
    submit.type = "submit";
    submit.className = "feedback-btn primary";
    submit.textContent = "提交反馈";
    actions.append(cancel, submit);
    form.appendChild(actions);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const checked = form.querySelector("input[type=radio]:checked");
      submitFeedback(bar, "unhelpful", checked ? checked.value : "other",
        textarea.value.trim());
    });
    bar.appendChild(form);
    options.querySelector("input")?.focus();
  }

  function renderFeedbackDone(bar, rating, allowChange = true) {
    bar.replaceChildren();
    const note = document.createElement("span");
    note.className = "feedback-done";
    note.textContent = rating === "helpful" ? "已记录：有帮助" : "已记录：没帮助";
    bar.appendChild(note);
    if (allowChange) {
      const change = document.createElement("button");
      change.type = "button";
      change.className = "feedback-btn";
      change.textContent = "修改";
      change.addEventListener("click", () => renderFeedbackChoice(bar));
      bar.appendChild(change);
    }
  }

  async function submitFeedback(bar, rating, reason = "", comment = "") {
    const messageId = bar.dataset.messageId;
    const button = bar.querySelector(".feedback-btn.primary, .feedback-btn");
    if (button) button.disabled = true;
    try {
      const res = await fetch(`${API_BASE}/api/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message_id: messageId, rating, reason, comment }),
      });
      if (!res.ok) throw new Error("feedback request failed");
      renderFeedbackDone(bar, rating);
      track("feedback_submitted", { rating, reason });
    } catch {
      renderFeedbackChoice(bar);
      window.toast("反馈提交失败，请稍后重试");
    }
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

  // ---- 来源效力标识：现行有效 / 已被修改 / 废止或失效 / 未确认 ----
  const VALIDITY_CLASS = {
    current: "ok", amended: "warn", repealed: "bad", unknown: "unknown",
  };

  function renderValidity(validity) {
    if (!validity || !validity.status) return null;
    const row = document.createElement("div");
    row.className = "validity-row";
    const badge = document.createElement("span");
    badge.className = `validity-badge ${VALIDITY_CLASS[validity.status] || "unknown"}`;
    badge.textContent = validity.label || "效力未确认";
    row.appendChild(badge);
    const bits = [];
    if (validity.authority) bits.push(validity.authority);
    if (validity.published) bits.push(`公布 ${validity.published}`);
    if (validity.effective) bits.push(`施行 ${validity.effective}`);
    if (validity.region) bits.push(validity.region === "地方" ? "适用：地方性" : "适用：全国性");
    if (validity.document_number) bits.push(validity.document_number);
    if (bits.length) {
      const meta = document.createElement("span");
      meta.className = "validity-meta";
      meta.textContent = bits.join(" · ");
      row.appendChild(meta);
    }
    if (validity.note) {
      const note = document.createElement("p");
      note.className = "validity-note";
      note.textContent = validity.note;
      row.appendChild(note);
    }
    return row;
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
        <p class="citation-source">${escapeHtml(source)}</p>
      `;
      const validity = renderValidity(c.validity);
      if (validity) item.appendChild(validity);
      const excerpt = document.createElement("p");
      excerpt.className = "excerpt";
      excerpt.textContent = c.excerpt || "暂无可展示的原文片段";
      item.appendChild(excerpt);
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
      const validity = source.validity;
      meta.textContent = `${source.chunk_count} 段`
        + (validity && validity.label ? ` · ${validity.label}` : "");
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

  // ---- 弹窗焦点管理：打开时锁定 Tab 循环，关闭后回到触发元素 ----
  const FOCUSABLE_SELECTOR = [
    "a[href]", "button:not([disabled])", "input:not([disabled])",
    "select:not([disabled])", "textarea:not([disabled])",
    '[tabindex]:not([tabindex="-1"])',
  ].join(",");

  function trapFocus(modal) {
    const opener = document.activeElement instanceof Element ? document.activeElement : null;
    const onKeyDown = (event) => {
      if (event.key !== "Tab") return;
      const focusable = Array.from(modal.querySelectorAll(FOCUSABLE_SELECTOR))
        .filter((el) => el.offsetParent !== null || el === document.activeElement);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    modal.addEventListener("keydown", onKeyDown);
    const firstField = modal.querySelector(FOCUSABLE_SELECTOR);
    if (firstField) firstField.focus();
    return function release() {
      modal.removeEventListener("keydown", onKeyDown);
      if (opener && document.contains(opener)) opener.focus();
    };
  }

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

  let authReleaseFocus = null;

  function openAuth(mode = "login", hint = "") {
    setAuthMode(mode, hint);
    authModal.hidden = false;
    authBackdrop.hidden = false;
    if (authReleaseFocus) authReleaseFocus();
    authReleaseFocus = trapFocus(authModal);
    authUsername.focus();
  }

  function closeAuth() {
    authModal.hidden = true;
    authBackdrop.hidden = true;
    authPassword.value = "";
    if (authReleaseFocus) {
      authReleaseFocus();
      authReleaseFocus = null;
    }
  }

  function renderAuthArea() {
    authArea.replaceChildren();
    if (authUser) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "auth-chip auth-chip-btn";
      chip.textContent = authUser.username;
      chip.setAttribute("aria-label", `打开账户设置：${authUser.username}`);
      chip.addEventListener("click", () => openAccountModal());
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

  // ---- 账户与数据面板：修改密码 / 导出数据 / 注销账号 ----
  const accountModal = document.getElementById("account-modal");
  const accountBackdrop = document.getElementById("account-backdrop");
  const accountClose = document.getElementById("account-close");
  const accountUsername = document.getElementById("account-username");
  const passwordForm = document.getElementById("password-form");
  const passwordCurrent = document.getElementById("password-current");
  const passwordNew = document.getElementById("password-new");
  const passwordConfirm = document.getElementById("password-confirm");
  const passwordHint = document.getElementById("password-hint");
  const passwordSubmit = document.getElementById("password-submit");
  const exportButton = document.getElementById("account-export");
  const deleteForm = document.getElementById("account-delete-form");
  const deleteConfirmInput = document.getElementById("account-delete-confirm");
  const deletePasswordInput = document.getElementById("account-delete-password");
  const deleteSubmit = document.getElementById("account-delete-submit");
  let accountReleaseFocus = null;

  function openAccountModal() {
    if (!authUser) return;
    accountUsername.textContent = authUser.username;
    accountModal.hidden = false;
    accountBackdrop.hidden = false;
    [passwordHint].forEach((el) => { el.hidden = true; });
    passwordForm.reset();
    deleteForm.reset();
    if (accountReleaseFocus) accountReleaseFocus();
    accountReleaseFocus = trapFocus(accountModal);
  }

  function closeAccountModal() {
    accountModal.hidden = true;
    accountBackdrop.hidden = true;
    if (accountReleaseFocus) {
      accountReleaseFocus();
      accountReleaseFocus = null;
    }
  }

  accountClose.addEventListener("click", closeAccountModal);
  accountBackdrop.addEventListener("click", closeAccountModal);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !accountModal.hidden) closeAccountModal();
  });

  passwordForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    passwordHint.hidden = true;
    const current = passwordCurrent.value;
    const next = passwordNew.value;
    if (next.length < 6) {
      passwordHint.textContent = "新密码至少 6 位";
      passwordHint.hidden = false;
      return;
    }
    if (next !== passwordConfirm.value) {
      passwordHint.textContent = "两次输入的新密码不一致";
      passwordHint.hidden = false;
      return;
    }
    passwordSubmit.disabled = true;
    try {
      const res = await fetch(`${API_BASE}/api/auth/password`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ current_password: current, new_password: next }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        passwordHint.textContent = data.error || "修改失败，请重试";
        passwordHint.hidden = false;
        return;
      }
      passwordForm.reset();
      window.toast("密码已更新，其它设备已退出登录");
    } catch {
      passwordHint.textContent = "网络错误，请重试";
      passwordHint.hidden = false;
    } finally {
      passwordSubmit.disabled = false;
    }
  });

  exportButton.addEventListener("click", async () => {
    exportButton.disabled = true;
    try {
      const res = await fetch(`${API_BASE}/api/account/export`);
      if (!res.ok) throw new Error("export request failed");
      const blob = await res.blob();
      const disposition = res.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename="?([^";]+)"?/);
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = match ? match[1] : "faxiao-export.json";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(link.href), 5000);
      track("account_export", {});
      window.toast("数据导出已开始下载");
    } catch {
      window.toast("导出失败，请稍后重试");
    } finally {
      exportButton.disabled = false;
    }
  });

  deleteForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (deleteConfirmInput.value.trim() !== "DELETE") {
      window.toast("请先输入 DELETE 确认");
      deleteConfirmInput.focus();
      return;
    }
    if (!window.confirm("确定要永久注销账号并删除全部数据吗？此操作不可恢复。")) return;
    deleteSubmit.disabled = true;
    try {
      const res = await fetch(`${API_BASE}/api/account/delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          confirm: "DELETE",
          password: deletePasswordInput.value,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        window.toast(data.error || "注销失败，请重试");
        return;
      }
      window.toast("账号已注销，数据已删除");
      setTimeout(() => location.reload(), 800);
    } catch {
      window.toast("网络错误，请重试");
    } finally {
      deleteSubmit.disabled = false;
    }
  });

  // ---- 设置与诊断：检索增强开关 + 运行日志 ----
  const settingsButton = document.getElementById("settings-button");
  const settingsDrawer = document.getElementById("settings-drawer");
  const settingsBackdrop = document.getElementById("settings-backdrop");
  const settingsClose = document.getElementById("settings-close");
  const settingsFeatures = document.getElementById("settings-features");
  const settingsLog = document.getElementById("settings-log");
  const settingsFeedback = document.getElementById("settings-feedback");
  const settingsEvents = document.getElementById("settings-events");
  const logsRefresh = document.getElementById("logs-refresh");
  const feedbackRefresh = document.getElementById("feedback-refresh");
  const eventsRefresh = document.getElementById("events-refresh");
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
      loadFeedbackPanel();
      loadEventsPanel();
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

  // ---- 管理员：回答反馈与产品事件统计 ----
  const FEEDBACK_REASON_LABELS = {
    inaccurate: "内容不准确", outdated: "依据过时", irrelevant: "答非所问",
    unclear: "看不懂", verbose: "太冗长", other: "其他",
  };

  async function loadFeedbackPanel() {
    if (!settingsFeedback) return;
    settingsFeedback.replaceChildren(renderSettingNotice("正在读取反馈…"));
    try {
      const res = await fetch(`${API_BASE}/api/feedback`);
      if (res.status === 403) {
        settingsFeedback.replaceChildren(renderSettingNotice("回答反馈仅管理员可查看"));
        return;
      }
      if (!res.ok) throw new Error("feedback request failed");
      const data = await res.json();
      settingsFeedback.replaceChildren();
      const helpful = (data.stats || {}).helpful || 0;
      const unhelpful = (data.stats || {}).unhelpful || 0;
      const total = helpful + unhelpful;
      const summary = document.createElement("p");
      summary.className = "settings-hint";
      summary.textContent = total
        ? `有帮助 ${helpful} · 没帮助 ${unhelpful} · 有帮助率 ${Math.round(helpful * 100 / total)}%`
        : "还没有收到反馈";
      settingsFeedback.appendChild(summary);
      (data.recent || []).slice(0, 20).forEach((item) => {
        const line = document.createElement("div");
        line.className = "log-line feedback-line";
        const head = document.createElement("div");
        head.className = "feedback-line-head";
        const tag = document.createElement("span");
        tag.className = `feedback-tag ${item.rating === "helpful" ? "ok" : "bad"}`;
        tag.textContent = item.rating === "helpful" ? "有帮助" : "没帮助";
        const reason = document.createElement("span");
        reason.className = "feedback-reason-tag";
        reason.textContent = FEEDBACK_REASON_LABELS[item.reason] || "";
        const time = document.createElement("span");
        time.className = "log-time";
        time.textContent = (item.created_at || "").replace("T", " ").slice(0, 16);
        head.append(tag, reason, time);
        line.appendChild(head);
        if (item.question) {
          const question = document.createElement("p");
          question.className = "feedback-q";
          question.textContent = `问：${item.question}`;
          line.appendChild(question);
        }
        if (item.comment) {
          const comment = document.createElement("p");
          comment.className = "feedback-q";
          comment.textContent = `用户说明：${item.comment}`;
          line.appendChild(comment);
        }
        settingsFeedback.appendChild(line);
      });
    } catch {
      settingsFeedback.replaceChildren(renderSettingNotice("反馈读取失败，请稍后重试"));
    }
  }

  async function loadEventsPanel() {
    if (!settingsEvents) return;
    settingsEvents.replaceChildren(renderSettingNotice("正在读取事件统计…"));
    try {
      const res = await fetch(`${API_BASE}/api/events/summary`);
      if (res.status === 403) {
        settingsEvents.replaceChildren(renderSettingNotice("事件统计仅管理员可查看"));
        return;
      }
      if (!res.ok) throw new Error("events summary request failed");
      const data = await res.json();
      settingsEvents.replaceChildren();
      const caption = document.createElement("p");
      caption.className = "settings-hint";
      caption.textContent = `近 ${data.days || 30} 天关键事件（次数 · 独立用户）`;
      settingsEvents.appendChild(caption);
      const events = data.events || [];
      if (!events.length) {
        settingsEvents.appendChild(renderSettingNotice("暂无事件数据"));
        return;
      }
      events.forEach((item) => {
        const line = document.createElement("div");
        line.className = "log-line";
        const label = document.createElement("span");
        label.className = "log-msg";
        label.textContent = `${EVENT_LABELS[item.name] || item.name}：${item.count} 次 · ${item.users} 人`;
        line.appendChild(label);
        settingsEvents.appendChild(line);
      });
    } catch {
      settingsEvents.replaceChildren(renderSettingNotice("事件统计读取失败，请稍后重试"));
    }
  }

  const EVENT_LABELS = {
    qa_ask: "发起提问",
    answer_received: "收到回答",
    citation_open: "查看法律原文",
    feedback_submitted: "提交反馈",
    court_case_open: "打开案例",
    court_start: "开始庭审",
    court_complete: "完成庭审",
    court_review: "查看复盘",
    module_switch: "跨模块切换",
    account_export: "导出数据",
  };

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
  feedbackRefresh?.addEventListener("click", loadFeedbackPanel);
  eventsRefresh?.addEventListener("click", loadEventsPanel);
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
