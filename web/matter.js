/* 法晓 · 问题工作台 Tab：结构化案情采集 → 事项档案（分析报告 + 推荐庭审） */

(function () {
  "use strict";

  const API_BASE = location.pathname.replace(/\/[^/]*$/, "");
  const formView = document.getElementById("matter-form-view");
  const resultView = document.getElementById("matter-result-view");
  const resultTitle = document.getElementById("matter-result-title");
  const resultBody = document.getElementById("matter-result-body");
  const form = document.getElementById("matter-form");
  const submitBtn = document.getElementById("matter-submit");
  const listEl = document.getElementById("matter-list");
  const emptyEl = document.getElementById("matter-empty");
  const newBtn = document.getElementById("matter-new");
  const backFormBtn = document.getElementById("matter-back-form");
  const whatInput = document.getElementById("matter-what");
  const goalInput = document.getElementById("matter-goal");

  let matters = [];
  let submitting = false;
  let activeMatterId = "";

  const INTAKE_LABELS = {
    what: "发生了什么", when_where: "时间与地点", parties: "当事人关系",
    amount: "涉及金额", evidence: "已有证据", goal: "你的诉求", urgent: "期限或紧急风险",
  };

  newBtn.addEventListener("click", showForm);
  backFormBtn.addEventListener("click", showForm);
  whatInput.addEventListener("input", () => {
    submitBtn.disabled = whatInput.value.trim().length < 10
      || goalInput.value.trim().length < 2 || submitting;
  });
  goalInput.addEventListener("input", () => {
    submitBtn.disabled = whatInput.value.trim().length < 10
      || goalInput.value.trim().length < 2 || submitting;
  });

  function showForm() {
    activeMatterId = "";
    resultView.hidden = true;
    formView.hidden = false;
    renderMatterList();
    whatInput.focus();
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitting || submitBtn.disabled) return;
    const intake = {
      what: whatInput.value.trim(),
      when_where: document.getElementById("matter-when-where").value.trim(),
      parties: document.getElementById("matter-parties").value.trim(),
      amount: document.getElementById("matter-amount").value.trim(),
      evidence: document.getElementById("matter-evidence").value.trim(),
      goal: goalInput.value.trim(),
      urgent: document.getElementById("matter-urgent").value.trim(),
    };
    submitting = true;
    submitBtn.disabled = true;
    submitBtn.textContent = "正在生成…";
    activeMatterId = "";
    formView.hidden = true;
    resultView.hidden = false;
    resultTitle.textContent = "新的分析";
    resultBody.replaceChildren(renderPending("正在查找相关法律…"));
    window.trackEvent?.("matter_created", {});
    try {
      const res = await fetch(`${API_BASE}/api/matters`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(intake),
      });
      if (res.status === 401) {
        window.dispatchEvent(new CustomEvent("auth:request-login"));
        showForm();
        return;
      }
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        window.toast(err.error || "提交失败，请稍后重试");
        showForm();
        return;
      }
      await readSse(res, handleEvent);
      form.reset();
      submitBtn.disabled = true;
      await loadMatters();
    } catch {
      window.toast("网络错误，请稍后重试");
      showForm();
    } finally {
      submitting = false;
      submitBtn.disabled = false;
      submitBtn.textContent = "生成处理路径";
    }
  });

  async function readSse(res, handler) {
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
        await handler(event);
      }
    }
  }

  async function handleEvent(event) {
    if (event.type === "status") {
      resultBody.replaceChildren(renderPending(event.text));
    } else if (event.type === "result") {
      activeMatterId = event.matter_id;
      window.trackEvent?.("matter_completed", {
        laws: (event.report?.applicable_laws || []).length,
      });
      renderReport(event.matter_id, event.report || {}, event.suggested_cases || []);
      loadMatters();
    } else if (event.type === "error") {
      resultBody.replaceChildren(renderFailed(event.error));
    }
  }

  async function loadMatters() {
    try {
      const res = await fetch(`${API_BASE}/api/matters`);
      if (res.status === 401) {
        matters = [];
        renderMatterList();
        emptyEl.textContent = "登录后可使用问题工作台";
        emptyEl.hidden = false;
        return;
      }
      if (!res.ok) throw new Error("matters request failed");
      matters = (await res.json()).matters || [];
      renderMatterList();
    } catch {
      emptyEl.textContent = "事项列表载入失败";
      emptyEl.hidden = matters.length === 0;
    }
  }

  function renderMatterList() {
    listEl.replaceChildren();
    matters.forEach((item) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "matter-item";
      row.classList.toggle("active", item.id === activeMatterId);
      row.dataset.matterId = item.id;
      const head = document.createElement("span");
      head.className = "matter-item-title";
      head.textContent = item.title;
      const status = document.createElement("span");
      status.className = `matter-item-status ${item.status}`;
      status.textContent = item.status === "ready" ? "已生成"
        : item.status === "failed" ? "生成失败" : "生成中";
      const time = document.createElement("time");
      time.className = "conversation-item-time";
      time.dateTime = item.updated_at || item.created_at;
      time.textContent = (item.updated_at || item.created_at || "").slice(5, 10).replace("-", "/");
      row.append(head, status, time);
      row.addEventListener("click", () => openMatter(item.id));
      listEl.appendChild(row);
    });
    emptyEl.textContent = "还没有提交过事项";
    emptyEl.hidden = matters.length > 0;
  }

  async function openMatter(id) {
    if (submitting) return window.toast("报告生成中，请稍候");
    try {
      const res = await fetch(`${API_BASE}/api/matters/${encodeURIComponent(id)}`);
      if (res.status === 401) {
        window.dispatchEvent(new CustomEvent("auth:request-login"));
        return;
      }
      if (!res.ok) throw new Error("matter detail failed");
      const data = await res.json();
      activeMatterId = id;
      formView.hidden = true;
      resultView.hidden = false;
      window.trackEvent?.("matter_review", {});
      const stored = data.result || {};
      if (data.status === "generating") {
        resultTitle.textContent = data.title;
        resultBody.replaceChildren(renderPending("报告生成中…"));
      } else if (data.status === "failed") {
        resultTitle.textContent = data.title;
        resultBody.replaceChildren(renderFailed(data.error || "生成失败", id));
      } else {
        renderReport(id, stored.report || {}, stored.suggested_cases || [], data);
      }
      renderMatterList();
      resultView.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch {
      window.toast("事项载入失败，请稍后重试");
    }
  }

  // ---- 报告渲染 ----
  function renderPending(text) {
    const box = document.createElement("div");
    box.className = "matter-pending";
    const dot = document.createElement("span");
    dot.className = "typing-indicator";
    dot.setAttribute("aria-hidden", "true");
    dot.textContent = "●●●";
    const label = document.createElement("span");
    label.textContent = text;
    box.append(dot, label);
    return box;
  }

  function renderFailed(message, matterId) {
    const box = document.createElement("div");
    box.className = "matter-failed";
    const text = document.createElement("p");
    text.textContent = message || "报告生成失败。";
    box.appendChild(text);
    if (matterId) {
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "outline-btn";
      retry.textContent = "重新生成";
      retry.addEventListener("click", () => regenerate(matterId));
      box.appendChild(retry);
    }
    return box;
  }

  async function regenerate(matterId) {
    resultBody.replaceChildren(renderPending("正在重新生成…"));
    try {
      const res = await fetch(`${API_BASE}/api/matters/${encodeURIComponent(matterId)}/regenerate`,
        { method: "POST" });
      if (!res.ok) throw new Error("regenerate failed");
      await readSse(res, handleEvent);
      loadMatters();
    } catch {
      window.toast("重新生成失败，请稍后重试");
    }
  }

  function sectionCard(kicker, buildBody) {
    const card = document.createElement("section");
    card.className = "matter-card";
    const head = document.createElement("h3");
    head.textContent = kicker;
    card.appendChild(head);
    buildBody(card);
    return card;
  }

  function bulletList(items, className) {
    const ul = document.createElement("ul");
    ul.className = className || "matter-bullets";
    items.forEach((item) => {
      const li = document.createElement("li");
      li.textContent = item;
      ul.appendChild(li);
    });
    return ul;
  }

  const VALIDITY_CLASS = {
    current: "ok", amended: "warn", repealed: "bad", unknown: "unknown",
  };

  function renderReport(matterId, report, suggestedCases, matterData) {
    resultTitle.textContent = (matterData && matterData.title)
      || (report.fact_summary || "事项档案").slice(0, 40);
    resultBody.replaceChildren();

    // 归类 + 事实摘要
    const summary = sectionCard("问题归类与事实摘要", (card) => {
      const chips = document.createElement("div");
      chips.className = "matter-type-row";
      const type = document.createElement("span");
      type.className = "matter-type-badge";
      type.textContent = report.issue_type || "待归类";
      chips.appendChild(type);
      if (report.fact_summary) chips.appendChild(document.createTextNode(report.fact_summary));
      card.appendChild(chips);
      if (matterData && matterData.intake) {
        const details = document.createElement("details");
        details.className = "matter-intake";
        const summaryEl = document.createElement("summary");
        summaryEl.textContent = "我提交的案情信息";
        details.appendChild(summaryEl);
        const ul = document.createElement("ul");
        Object.entries(matterData.intake).forEach(([key, value]) => {
          if (!value) return;
          const li = document.createElement("li");
          li.textContent = `${INTAKE_LABELS[key] || key}：${value}`;
          ul.appendChild(li);
        });
        details.appendChild(ul);
        card.appendChild(details);
      }
    });
    resultBody.appendChild(summary);

    // 适用依据
    const laws = report.applicable_laws || [];
    resultBody.appendChild(sectionCard(
      `适用依据${laws.length ? `（${laws.length} 条）` : ""}`, (card) => {
        if (!laws.length) {
          const empty = document.createElement("p");
          empty.className = "muted";
          empty.textContent = "本次检索没有找到足以支持的现行依据，请补充信息后重新生成，或换个说法描述问题。";
          card.appendChild(empty);
          return;
        }
        laws.forEach((law) => {
          const item = document.createElement("article");
          item.className = "matter-law";
          const meta = document.createElement("div");
          meta.className = "matter-law-meta";
          const title = document.createElement("strong");
          title.textContent = `${law.title} ${law.article || ""}`.trim();
          meta.appendChild(title);
          if (law.validity && law.validity.status) {
            const badge = document.createElement("span");
            badge.className = `validity-badge ${VALIDITY_CLASS[law.validity.status] || "unknown"}`;
            badge.textContent = law.validity.label || "效力未确认";
            meta.appendChild(badge);
          }
          item.appendChild(meta);
          if (law.reason) {
            const reason = document.createElement("p");
            reason.className = "matter-law-reason";
            reason.textContent = `适用理由：${law.reason}`;
            item.appendChild(reason);
          }
          if (law.excerpt) {
            const excerpt = document.createElement("p");
            excerpt.className = "matter-law-excerpt";
            excerpt.textContent = law.excerpt;
            item.appendChild(excerpt);
          }
          card.appendChild(item);
        });
      }));

    // 行动清单
    const steps = report.action_steps || [];
    resultBody.appendChild(sectionCard("下一步行动清单", (card) => {
      if (!steps.length) {
        const empty = document.createElement("p");
        empty.className = "muted";
        empty.textContent = "暂无建议步骤。";
        card.appendChild(empty);
        return;
      }
      const ol = document.createElement("ol");
      ol.className = "matter-steps";
      steps.forEach((step) => {
        const li = document.createElement("li");
        const strong = document.createElement("strong");
        strong.textContent = step.step || "";
        li.appendChild(strong);
        if (step.detail) {
          li.appendChild(document.createTextNode(step.detail));
        }
        ol.appendChild(li);
      });
      card.appendChild(ol);
    }));

    // 证据清单 + 缺少信息（并排）
    const duo = document.createElement("div");
    duo.className = "matter-duo";
    duo.appendChild(sectionCard("证据清单", (card) => {
      card.appendChild(bulletList(report.evidence_checklist || [], "matter-checklist"));
    }));
    duo.appendChild(sectionCard("仍需确认的信息", (card) => {
      const items = report.missing_info || [];
      if (!items.length) {
        const empty = document.createElement("p");
        empty.className = "muted";
        empty.textContent = "暂无明显缺口。";
        card.appendChild(empty);
        return;
      }
      card.appendChild(bulletList(items));
    }));
    resultBody.appendChild(duo);

    // 风险提醒
    const risks = report.risks || [];
    if (risks.length) {
      resultBody.appendChild(sectionCard("风险与期限提醒", (card) => {
        card.appendChild(bulletList(risks, "matter-risks"));
      }));
    }

    // 推荐模拟庭审
    if (suggestedCases && suggestedCases.length) {
      resultBody.appendChild(sectionCard("推荐模拟庭审 · 用这个案子练手", (card) => {
        const note = document.createElement("p");
        note.className = "muted";
        note.textContent = "以下内置案例与本事项相关，可以在模拟法庭里练习同类争点的判断。";
        card.appendChild(note);
        suggestedCases.forEach((suggested) => {
          const link = document.createElement("button");
          link.type = "button";
          link.className = "outline-btn matter-case-link";
          link.textContent = `${suggested.title} · ${suggested.theme}`;
          link.addEventListener("click", () => {
            window.trackEvent?.("matter_to_court", { case_id: suggested.case_id });
            document.getElementById("tab-button-court").click();
            window.courtOpenCase?.(suggested.case_id);
          });
          card.appendChild(link);
        });
      }));
    }

    // 免责 + 删除
    const footer = document.createElement("div");
    footer.className = "matter-footer";
    if (report.disclaimer) {
      const note = document.createElement("p");
      note.className = "muted";
      note.textContent = report.disclaimer;
      footer.appendChild(note);
    }
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "text-btn matter-delete";
    remove.textContent = "删除此事项";
    remove.addEventListener("click", () => deleteMatter(matterId));
    footer.appendChild(remove);
    resultBody.appendChild(footer);
  }

  async function deleteMatter(matterId) {
    if (!window.confirm("确定删除这个事项档案吗？删除后不可恢复。")) return;
    try {
      const res = await fetch(`${API_BASE}/api/matters/${encodeURIComponent(matterId)}`,
        { method: "DELETE" });
      if (!res.ok) throw new Error("matter delete failed");
      window.toast("事项已删除");
      activeMatterId = "";
      await loadMatters();
      showForm();
    } catch {
      window.toast("删除失败，请稍后重试");
    }
  }

  // 登录状态变化时刷新列表
  window.addEventListener("auth:changed", () => loadMatters());

  // 问答 → 工作台：接收从对话蒸馏出的案情，预填表单待用户确认
  window.addEventListener("matter:prefill", (event) => {
    const intake = event.detail && event.detail.intake;
    if (!intake) return;
    showForm();
    const fields = {
      what: whatInput, goal: goalInput,
      when_where: document.getElementById("matter-when-where"),
      parties: document.getElementById("matter-parties"),
      amount: document.getElementById("matter-amount"),
      evidence: document.getElementById("matter-evidence"),
      urgent: document.getElementById("matter-urgent"),
    };
    Object.entries(fields).forEach(([key, input]) => {
      input.value = String(intake[key] || "");
    });
    submitBtn.disabled = whatInput.value.trim().length < 10
      || goalInput.value.trim().length < 2;
    whatInput.focus();
  });

  loadMatters();
})();
