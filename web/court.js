/* 法晓 · 模拟法庭 Tab：法庭式布局 + 人民陪审员检查点交互 */

(function () {
  "use strict";

  const selectEl = document.getElementById("court-select");
  const trialEl = document.getElementById("court-trial");
  const deliberationEl = document.getElementById("court-deliberation");
  const verdictEl = document.getElementById("court-verdict");
  const caseCardsEl = document.getElementById("case-cards");
  const courtTitle = document.getElementById("court-title");
  const courtPhase = document.getElementById("court-phase");
  const courtStatus = document.getElementById("court-status");
  const courtStatusText = document.getElementById("court-status-text");
  const speechName = document.getElementById("speech-name");
  const speechBadge = document.getElementById("speech-badge");
  const speechTextEl = document.getElementById("speech-text");
  const factsText = document.getElementById("facts-text");
  const disputeList = document.getElementById("dispute-list");
  const evidenceList = document.getElementById("evidence-list");
  const checkpointPanel = document.getElementById("checkpoint-panel");
  const logList = document.getElementById("court-log-list");
  const phaseSteps = document.getElementById("phase-steps");

  let currentCaseId = "";
  let sessionId = "";
  let caseData = null;
  let pendingCheckpoint = null;      // 等待陪审员输入的检查点事件
  let trialAbortController = null;
  let currentTurnText = "";          // 当前发言累计文本（识别证据编号用）
  let currentSpeaker = { role: "", name: "", badge: "" };
  const citedEvidence = new Set();   // 发言中出现过的证据编号
  const markedEvidence = new Set();  // 用户标注的关键证据
  const pollHistory = [];            // [{stage,label}] 预判轨迹

  // ---- 加载案例列表 ----
  fetch("/api/court/cases")
    .then((r) => r.json())
    .then((data) => {
      caseCardsEl.innerHTML = "";
      data.cases.forEach((c) => {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "case-card";
        card.setAttribute("aria-label", `开始案例：${c.title}`);
        card.innerHTML = `
          <h3>${escapeHtml(c.title)}</h3>
          <div class="subtitle">${escapeHtml(c.subtitle)}</div>
          <div class="theme">${escapeHtml(c.theme)}</div>
          <div class="learning-goal">你将学会：${escapeHtml(c.learning_goal || "分析案件中的关键事实与规则")}</div>
          <div class="charge">指控罪名：${escapeHtml(c.charge)}</div>
          <div class="duration">预计用时 ${escapeHtml(c.duration)}</div>
          <div class="entry-note">${escapeHtml(c.entry_note || "选择后即可开始模拟庭审")}</div>
          <div class="disclaimer">${escapeHtml(c.disclaimer)}</div>
          <span class="case-action">进入法庭</span>
        `;
        card.addEventListener("click", () => loadCase(c.id));
        caseCardsEl.appendChild(card);
      });
    })
    .catch(() => { caseCardsEl.innerHTML = "<p class='muted'>加载案例失败</p>"; });

  // ---- 加载案例详情并开始庭审 ----
  async function loadCase(caseId) {
    try {
      const res = await fetch(`/api/court/cases/${caseId}`);
      if (!res.ok) { toast("案例加载失败"); return; }
      caseData = await res.json();
      currentCaseId = caseId;

      resetTrialState();
      selectEl.hidden = true;
      trialEl.hidden = false;
      deliberationEl.hidden = true;
      verdictEl.hidden = true;
      courtTitle.textContent = caseData.title + " · " + caseData.subtitle;
      courtPhase.textContent = "准备开庭";

      buildDossierPanel();
      startTrial(caseId);
    } catch {
      toast("网络错误");
    }
  }

  function resetTrialState() {
    trialAbortController = null;
    sessionId = "";
    pendingCheckpoint = null;
    currentTurnText = "";
    currentSpeaker = { role: "", name: "", badge: "" };
    citedEvidence.clear();
    markedEvidence.clear();
    pollHistory.length = 0;
    checkpointPanel.hidden = true;
    checkpointPanel.innerHTML = "";
    logList.innerHTML = "";
    speechName.textContent = "审判长";
    speechBadge.textContent = "审";
    speechTextEl.textContent = "法槌即将落下，庭审马上开始…";
    setPhaseStep("");
    document.querySelectorAll(".court-scene .bench").forEach((b) => b.classList.remove("speaking"));
  }

  // ---- 卷宗面板 ----
  function buildDossierPanel() {
    factsText.textContent = caseData.facts;
    disputeList.innerHTML = "";
    (caseData.disputes || []).forEach((d) => {
      const li = document.createElement("li");
      li.textContent = d;
      disputeList.appendChild(li);
    });
    evidenceList.innerHTML = "";
    caseData.dossier.forEach((ev) => {
      const card = document.createElement("div");
      card.className = "evidence-card";
      card.dataset.eid = ev.id;
      card.innerHTML = `
        <div class="ev-head">
          <span class="ev-id">${escapeHtml(ev.id)}</span>
          <span class="ev-type">${escapeHtml(ev.type)}</span>
          <button type="button" class="ev-star" aria-label="标注为关键证据" aria-pressed="false">☆</button>
        </div>
        <div class="ev-body">${escapeHtml(ev.content)}</div>
      `;
      card.querySelector(".ev-head").addEventListener("click", (e) => {
        if (e.target.closest(".ev-star")) return;
        card.classList.toggle("open");
      });
      card.querySelector(".ev-star").addEventListener("click", (event) => {
        const star = event.currentTarget;
        if (markedEvidence.has(ev.id)) {
          markedEvidence.delete(ev.id);
          card.classList.remove("marked");
          star.textContent = "☆";
          star.setAttribute("aria-pressed", "false");
        } else {
          markedEvidence.add(ev.id);
          card.classList.add("marked");
          star.textContent = "★";
          star.setAttribute("aria-pressed", "true");
          toast(`已标注 ${ev.id} 为关键证据`);
        }
      });
      evidenceList.appendChild(card);
    });
  }

  // ---- SSE 读取 ----
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
        handler(event);
      }
    }
  }

  async function startTrial(caseId) {
    const controller = new AbortController();
    trialAbortController = controller;
    try {
      const res = await fetch(`/api/court/cases/${caseId}/trial`, {
        method: "POST", signal: controller.signal,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        toast(err.error || "庭审启动失败");
        return;
      }
      await readSse(res, handleTrialEvent);
    } catch (err) {
      if (err.name === "AbortError") return;
      toast("庭审连接中断");
    } finally {
      if (trialAbortController === controller) trialAbortController = null;
    }
  }

  // ---- 庭审事件处理 ----
  function handleTrialEvent(event) {
    switch (event.type) {
      case "session":
        sessionId = event.session_id;
        break;

      case "phase":
        courtPhase.textContent = event.title;
        setPhaseStep(event.phase);
        break;

      case "line":
        // 审判长程序性台词（纯代码模板，一次性显示）
        setSpeech(event.role, event.name, event.badge, event.text);
        appendLog(event.role, event.name, event.text);
        break;

      case "turn_start":
        currentSpeaker = { role: event.role, name: event.name, badge: event.badge };
        currentTurnText = "";
        setSpeaking(event.role, event.name, event.badge);
        speechTextEl.textContent = "";
        showStatus(`${event.name} 正在发言…`);
        break;

      case "delta":
        speechTextEl.textContent += event.text;
        currentTurnText += event.text;
        detectEvidence(event.text);
        scrollSpeech();
        break;

      case "turn_end":
        hideStatus();
        appendLog(currentSpeaker.role, currentSpeaker.name, speechTextEl.textContent);
        break;

      case "summary":
        setSpeech("clerk", "书记员", "书", `（书记员纪要）${event.text}`);
        appendLog("clerk", "书记员", event.text);
        break;

      case "checkpoint":
        pendingCheckpoint = event;
        renderCheckpoint(event);
        break;

      case "resume":
        pendingCheckpoint = null;
        checkpointPanel.hidden = true;
        break;

      case "await_deliberation":
        hideStatus();
        courtPhase.textContent = "休庭评议";
        setPhaseStep("deliberation");
        showDeliberation(event.options);
        break;

      case "error":
        hideStatus();
        toast(event.error);
        // 检查点阶段失败：重新显示表单，让用户重试（后端已恢复同一检查点）
        if (pendingCheckpoint) checkpointPanel.hidden = false;
        break;
    }
  }

  // ---- 场景渲染 ----
  function setSpeaking(role, name, badge) {
    document.querySelectorAll(".court-scene .bench").forEach((b) => {
      b.classList.toggle("speaking", b.dataset.role === role);
    });
    speechName.textContent = name;
    speechBadge.textContent = badge || "";
  }

  function setSpeech(role, name, badge, text) {
    setSpeaking(role, name, badge);
    speechTextEl.textContent = text;
    scrollSpeech();
  }

  function scrollSpeech() {
    speechTextEl.scrollTop = speechTextEl.scrollHeight;
  }

  function setPhaseStep(phase) {
    // 当前阶段高亮，之前的阶段标记完成
    let before = true;
    phaseSteps.querySelectorAll("li").forEach((li) => {
      const isCurrent = !!phase && li.dataset.phase === phase;
      if (isCurrent) before = false;
      li.classList.toggle("done", before && !isCurrent && !!phase);
      li.classList.toggle("active", isCurrent);
    });
  }

  function detectEvidence(text) {
    const matches = text.match(/E\d+/g) || [];
    matches.forEach((eid) => {
      if (citedEvidence.has(eid)) return;
      citedEvidence.add(eid);
      const card = evidenceList.querySelector(`[data-eid="${eid}"]`);
      if (card) card.classList.add("cited");
    });
  }

  function appendLog(role, name, text) {
    const line = document.createElement("div");
    line.className = `log-line ${role || ""}`;
    const b = document.createElement("b");
    b.textContent = name + "：";
    line.appendChild(b);
    line.appendChild(document.createTextNode(text));
    logList.appendChild(line);
    logList.scrollTop = logList.scrollHeight;
  }

  function showStatus(text) {
    courtStatusText.textContent = text;
    courtStatus.hidden = false;
  }

  function hideStatus() {
    courtStatus.hidden = true;
  }

  // ---- 检查点：阶段性预判与发问 ----
  function renderCheckpoint(cp) {
    setSpeaking("juror", "人民陪审员（你）", "陪");
    speechTextEl.textContent = cp.kind === "mid_review"
      ? "轮到你了——请在下方记录你的阶段性预判，还可以就卷宗证据向控辩双方发问。"
      : "听完控辩双方的对抗，请再次记录你的意见。";

    const card = document.createElement("div");
    card.className = "cp-card";
    const optionsHtml = cp.options.map((opt) => `
      <button type="button" class="cp-option" data-id="${opt.id}">
        <span class="cp-opt-label">${opt.id} · ${escapeHtml(opt.label)}</span>
        <span class="cp-opt-detail">${escapeHtml(opt.detail)}</span>
      </button>`).join("");
    card.innerHTML = `
      <div class="cp-title">审判长征求你的意见</div>
      <p class="cp-quote">“${cp.kind === "mid_review"
        ? "法庭调查结束。请陪审员记录阶段性意见，并可就卷宗证据向控辩双方发问。"
        : "听完控辩双方的对抗，你的判断是否发生了变化？请再次记录你的意见。"}”</p>
      ${cp.previous_label ? `<div class="cp-prev">你在法庭调查后的预判是：<b>${escapeHtml(cp.previous_label)}</b></div>` : ""}
      <div class="cp-label">你的阶段性预判 <small>（必选）</small></div>
      <div class="cp-options">${optionsHtml}</div>
      ${cp.allow_question ? `
        <div class="cp-label">向当事人发问 <small>（可选，会被当庭作答）</small></div>
        <div class="cp-question">
          <textarea id="cp-question" rows="2" maxlength="200"
            placeholder="例：E4 监控显示夺刀到追刺只有 11 秒，你凭什么认定侵害已经结束？"></textarea>
          <div class="cp-target">
            发给：
            <label><input type="radio" name="cp-target" value="prosecutor" checked>公诉人</label>
            <label><input type="radio" name="cp-target" value="defender">辩护人</label>
            <label><input type="radio" name="cp-target" value="defendant">被告人</label>
          </div>
        </div>` : ""}
      <button type="button" class="primary-btn" id="cp-submit">
        ${cp.kind === "mid_review" ? "提交意见，继续庭审" : "记录意见，进入最后陈述"}
      </button>
    `;
    checkpointPanel.innerHTML = "";
    checkpointPanel.appendChild(card);
    checkpointPanel.hidden = false;
    card.scrollIntoView({ behavior: "smooth", block: "center" });

    let selected = "";
    card.querySelectorAll(".cp-option").forEach((btn) => {
      btn.addEventListener("click", () => {
        card.querySelectorAll(".cp-option").forEach((b) => b.classList.remove("selected"));
        btn.classList.add("selected");
        selected = btn.dataset.id;
      });
    });
    card.querySelector("#cp-submit").addEventListener("click", () => submitCheckpoint(cp, card, selected));
  }

  async function submitCheckpoint(cp, card, selected) {
    if (!selected) { toast("请先选择你的阶段性预判"); return; }
    const body = { poll: selected };
    if (cp.allow_question) {
      const q = (card.querySelector("#cp-question")?.value || "").trim();
      if (q) {
        body.question = q.slice(0, 200);
        body.target = card.querySelector('input[name="cp-target"]:checked').value;
      }
    }
    const opt = cp.options.find((o) => o.id === selected);
    pollHistory.push({ stage: cp.kind === "mid_review" ? "investigation" : "debate", label: opt.label });

    checkpointPanel.hidden = true;
    try {
      const res = await fetch(`/api/court/sessions/${sessionId}/continue`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        toast(err.error || "提交失败");
        checkpointPanel.hidden = false;
        return;
      }
      await readSse(res, handleTrialEvent);
    } catch {
      toast("网络错误");
      checkpointPanel.hidden = false;
    }
  }

  // ---- 评议 ----
  function trajChip(text, cls) {
    return `<span class="traj-chip ${cls || ""}">${escapeHtml(text)}</span>`;
  }

  function showDeliberation(options) {
    trialEl.hidden = true;
    deliberationEl.hidden = false;

    // 预判轨迹 + 关键证据标注回顾
    const trajEl = document.getElementById("delib-traj");
    const chips = [];
    pollHistory.forEach((p, i) => {
      chips.push(trajChip(`${i === 0 ? "调查后" : "辩论后"}预判：${p.label}`));
    });
    if (markedEvidence.size) {
      chips.push(trajChip(`关键证据：${Array.from(markedEvidence).join("、")}`, "final"));
    }
    trajEl.innerHTML = chips.join('<span class="traj-arrow">→</span>');
    trajEl.hidden = chips.length === 0;

    const optionsEl = document.getElementById("deliberation-options");
    const yearsEl = document.getElementById("deliberation-years");
    const slider = document.getElementById("years-slider");
    const yearsDisplay = document.getElementById("years-display");
    const yearsMin = document.getElementById("years-min");
    const yearsMax = document.getElementById("years-max");

    optionsEl.innerHTML = "";
    options.forEach((opt) => {
      const label = document.createElement("label");
      label.className = "delib-option";
      label.innerHTML = `
        <input type="radio" name="verdict" value="${opt.id}">
        <span class="option-label">${escapeHtml(opt.label)}</span>
        <div class="option-detail">${escapeHtml(opt.detail)}</div>
      `;
      label.addEventListener("click", () => {
        optionsEl.querySelectorAll(".delib-option").forEach((el) => el.classList.remove("selected"));
        label.classList.add("selected");
        if (opt.convict && opt.years_range) {
          yearsEl.hidden = false;
          slider.min = opt.years_range[0];
          slider.max = opt.years_range[1];
          slider.value = Math.round((opt.years_range[0] + opt.years_range[1]) / 2);
          yearsDisplay.textContent = slider.value;
          yearsMin.textContent = opt.years_range[0] + " 年";
          yearsMax.textContent = opt.years_range[1] + " 年";
        } else {
          yearsEl.hidden = true;
        }
      });
      optionsEl.appendChild(label);
    });

    slider.addEventListener("input", () => {
      yearsDisplay.textContent = slider.value;
    });

    // 提交评议
    const form = document.getElementById("deliberation-form");
    form.onsubmit = async (e) => {
      e.preventDefault();
      const selectedOpt = form.querySelector('input[name="verdict"]:checked');
      if (!selectedOpt) { toast("请选择你的评议意见"); return; }

      const body = { option: selectedOpt.value, marks: Array.from(markedEvidence) };
      const opt = options.find((o) => o.id === selectedOpt.value);
      if (opt && opt.convict) {
        body.years = parseInt(slider.value, 10);
      }

      deliberationEl.hidden = true;
      verdictEl.hidden = false;

      document.getElementById("verdict-yours").textContent =
        opt.label + (opt.convict ? `，量刑建议：${body.years} 年有期徒刑` : "")
        + (markedEvidence.size ? `（关键证据：${Array.from(markedEvidence).join("、")}）` : "");

      try {
        const res = await fetch(`/api/court/sessions/${sessionId}/deliberation`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });

        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          toast(err.error || "提交失败");
          return;
        }

        let analysisEl = document.getElementById("analysis-content");
        analysisEl.textContent = "";
        await readSse(res, (event) => {
          if (event.type === "verdict") {
            document.getElementById("verdict-real").textContent = event.real.summary;
            document.getElementById("verdict-reasoning").textContent = event.real.reasoning;
            document.getElementById("verdict-articles").textContent = event.real.articles.join("；");
            renderVerdictTrajectory(event);
          } else if (event.type === "delta") {
            analysisEl.textContent += event.text;
          } else if (event.type === "error") {
            toast(event.error);
          }
        });
      } catch {
        toast("网络错误");
      }
    };
  }

  function renderVerdictTrajectory(event) {
    const el = document.getElementById("verdict-trajectory");
    const chips = [];
    (event.trajectory || []).forEach((p, i) => {
      chips.push(trajChip(`${i === 0 ? "调查后" : "辩论后"}预判：${p.label}`));
    });
    chips.push(trajChip(`你的评议：${event.yours.label}`, "final"));
    const realShort = (event.real.summary || "").replace(/。$/, "");
    chips.push(trajChip(`法院判决：${realShort.length > 22 ? realShort.slice(0, 22) + "…" : realShort}`, "real"));
    el.innerHTML = chips.join('<span class="traj-arrow">→</span>');
    el.hidden = false;
  }

  // ---- 再来一局 ----
  document.getElementById("court-restart").addEventListener("click", () => {
    verdictEl.hidden = true;
    deliberationEl.hidden = true;
    trialEl.hidden = true;
    selectEl.hidden = false;
    resetTrialState();
  });

  document.getElementById("court-exit").addEventListener("click", () => {
    if (trialAbortController) trialAbortController.abort();
    verdictEl.hidden = true;
    deliberationEl.hidden = true;
    trialEl.hidden = true;
    selectEl.hidden = false;
    resetTrialState();
  });

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }
})();
