/* 法晓 · 法律常识 Tab：普法文章卡片索引；正文在独立页 /basics/<key> */

(function () {
  "use strict";

  const API_BASE = location.pathname.replace(/\/[^/]*$/, "");
  const ASK_BRIDGE_KEY = "faxiao_basics_ask";
  const RETURN_KEY = "faxiao_basics_return";
  const tabButton = document.getElementById("tab-button-basics");
  const cardsEl = document.getElementById("basics-cards");
  const errorEl = document.getElementById("basics-error");
  let loaded = false;

  tabButton.addEventListener("click", () => {
    if (!loaded) loadBasics();
  });
  document.getElementById("basics-retry")?.addEventListener("click", loadBasics);

  // 从文章页返回首页时，自动停在法律常识 Tab
  try {
    if (sessionStorage.getItem(RETURN_KEY) === "1") {
      sessionStorage.removeItem(RETURN_KEY);
      tabButton.click();
    }
  } catch { /* 存储不可用就按默认 Tab 处理 */ }

  // 文章页「去问法晓」：带着问题回到首页问答输入框
  handleAskBridge();

  function handleAskBridge() {
    let question = "";
    try {
      question = sessionStorage.getItem(ASK_BRIDGE_KEY) || "";
      sessionStorage.removeItem(ASK_BRIDGE_KEY);
    } catch { return; }
    if (!question) return;
    goTab("qa");
    const input = document.getElementById("qa-input");
    if (!input) return;
    input.value = question;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    setTimeout(() => input.focus(), 0);
  }

  function goTab(key) {
    document.querySelector(`.tabs [data-tab="${key}"]`)?.click();
  }

  async function loadBasics() {
    errorEl.hidden = true;
    try {
      const res = await fetch(`${API_BASE}/api/basics`);
      if (!res.ok) throw new Error("basics request failed");
      const data = await res.json();
      render(data);
      loaded = true;
      window.trackEvent?.("basics_viewed", {
        modules: (data.modules || []).length,
      });
    } catch {
      cardsEl.replaceChildren();
      errorEl.hidden = false;
    }
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function render(data) {
    cardsEl.replaceChildren();
    (data.modules || []).forEach((module) => {
      const card = el("a", "basics-index-card");
      card.href = `${API_BASE}/basics/${encodeURIComponent(module.key)}`;
      // 埋点只记事件与模块；导航离开页面时事件随 pagehide 批量送出
      card.addEventListener("click", () => {
        window.trackEvent?.("basics_open_article", { module: module.key });
      });
      const sigil = el("span", "basics-index-sigil",
        module.sigil || (module.name || "").slice(0, 1));
      const head = el("div", "basics-index-head");
      head.appendChild(el("h3", "", module.name));
      head.appendChild(el("p", "muted", module.brief));
      const metaBits = [`${module.block_count} 个知识块`];
      if (data.updated_at) metaBits.push(`更新于 ${data.updated_at}`);
      const meta = el("p", "basics-index-meta");
      meta.append(metaBits.join(" · "), el("span", "basics-index-more", "阅读全文"));
      card.append(sigil, head, meta);
      cardsEl.appendChild(card);
    });
  }
})();
