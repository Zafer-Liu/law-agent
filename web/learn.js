/* 法晓 · 学习地图 Tab：知识点 / 事项 / 庭审的主题化学习导航 */

(function () {
  "use strict";

  const API_BASE = location.pathname.replace(/\/[^/]*$/, "");
  const themesEl = document.getElementById("learn-themes");
  const summaryEl = document.getElementById("learn-summary");
  const emptyEl = document.getElementById("learn-empty");
  const learnTab = document.getElementById("tab-button-learn");
  let loaded = false;

  learnTab.addEventListener("click", () => {
    loadLearnMap();
  });

  async function loadLearnMap() {
    try {
      const res = await fetch(`${API_BASE}/api/learn/map`);
      if (res.status === 401) {
        window.dispatchEvent(new CustomEvent("auth:request-login"));
        return;
      }
      if (!res.ok) throw new Error("learn map request failed");
      const data = await res.json();
      window.trackEvent?.("learn_map_viewed", {
        themes: (data.themes || []).length,
        mastered: (data.summary || {}).mastered || 0,
      });
      renderSummary(data.summary || {});
      renderThemes(data.themes || []);
      loaded = true;
    } catch {
      summaryEl.replaceChildren();
      const notice = document.createElement("p");
      notice.className = "conversation-empty";
      notice.textContent = "学习地图载入失败，请稍后重试。";
      summaryEl.appendChild(notice);
    }
  }

  function renderSummary(summary) {
    summaryEl.replaceChildren();
    const cards = [
      { label: "已掌握法条", value: summary.mastered || 0, cls: "ok" },
      { label: "待复习", value: summary.review || 0, cls: "warn" },
      { label: "事项档案", value: summary.matters || 0, cls: "" },
      { label: `已完成庭审（共 ${summary.cases_total || 0} 案）`, value: summary.courts_done || 0, cls: "" },
    ];
    cards.forEach((item) => {
      const card = document.createElement("div");
      card.className = `learn-stat ${item.cls}`;
      const value = document.createElement("strong");
      value.textContent = String(item.value);
      const label = document.createElement("span");
      label.textContent = item.label;
      card.append(value, label);
      summaryEl.appendChild(card);
    });
  }

  function actionButton(text, className, onClick) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = className;
    button.textContent = text;
    button.addEventListener("click", onClick);
    return button;
  }

  function goCourt(caseId, eventName) {
    window.trackEvent?.(eventName, { case_id: caseId });
    learnTab.parentElement.querySelector('[data-tab="court"]').click();
    window.courtOpenCase?.(caseId);
  }

  function renderThemes(themes) {
    themesEl.replaceChildren();
    emptyEl.hidden = themes.length > 0;
    themes.forEach((theme) => {
      const card = document.createElement("section");
      card.className = "learn-theme";

      const head = document.createElement("header");
      head.className = "learn-theme-head";
      const title = document.createElement("h3");
      title.textContent = theme.name;
      head.appendChild(title);
      const badges = document.createElement("div");
      badges.className = "learn-theme-badges";
      if (theme.mastered) {
        badges.appendChild(statChip(`已掌握 ${theme.mastered}`, "ok"));
      }
      if (theme.review) {
        badges.appendChild(statChip(`待复习 ${theme.review}`, "warn"));
      }
      if (theme.matters) {
        badges.appendChild(statChip(`事项 ${theme.matters}`, ""));
      }
      head.appendChild(badges);
      card.appendChild(head);

      const points = theme.points || [];
      if (points.length) {
        const list = document.createElement("ul");
        list.className = "learn-points";
        points.forEach((point) => {
          const li = document.createElement("li");
          li.className = `learn-point ${point.status}`;
          const dot = document.createElement("span");
          dot.className = "learn-point-dot";
          dot.setAttribute("aria-hidden", "true");
          li.appendChild(dot);
          li.appendChild(document.createTextNode(point.article_ref));
          list.appendChild(li);
        });
        card.appendChild(list);
      }

      const cases = theme.cases || [];
      cases.forEach((item) => {
        const row = document.createElement("div");
        row.className = `learn-case ${item.completed ? "done" : ""}`;
        const info = document.createElement("div");
        info.className = "learn-case-info";
        const name = document.createElement("strong");
        name.textContent = item.title;
        const meta = document.createElement("span");
        meta.textContent = `${item.theme} · ${item.laws_count} 条法条可掌握`
          + (item.completed ? " · 已完成" : "");
        info.append(name, meta);
        row.appendChild(info);
        row.appendChild(actionButton(
          item.completed ? "回看复盘" : "去练手",
          item.completed ? "outline-btn" : "primary-btn",
          () => goCourt(item.case_id, item.completed ? "court_review" : "court_case_open"),
        ));
        card.appendChild(row);
      });

      const actions = document.createElement("div");
      actions.className = "learn-theme-actions";
      actions.appendChild(actionButton("问一个问题", "outline-btn", () => {
        learnTab.parentElement.querySelector('[data-tab="qa"]').click();
        document.getElementById("qa-input")?.focus();
      }));
      actions.appendChild(actionButton("整理一个事项", "outline-btn", () => {
        document.getElementById("tab-button-matter").click();
        document.getElementById("matter-new")?.click();
      }));
      card.appendChild(actions);

      themesEl.appendChild(card);
    });
  }

  function statChip(text, cls) {
    const chip = document.createElement("span");
    chip.className = `learn-chip ${cls}`;
    chip.textContent = text;
    return chip;
  }

  // 登录后如果地图为空（首次进入），预取一次避免切 tab 白屏
  window.addEventListener("auth:changed", () => {
    if (!loaded) return;
    loaded = false;
  });
})();
