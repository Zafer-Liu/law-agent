/* 法晓 · 模拟法庭 Tab：角色选择 + 法庭式布局 + 关键回合交互 */

(function () {
  "use strict";

  const selectEl = document.getElementById("court-select");
  const roleSelectEl = document.getElementById("court-role-select");
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
  const speechZone = speechTextEl.closest(".speech-zone");
  const factsText = document.getElementById("facts-text");
  const disputeList = document.getElementById("dispute-list");
  const evidenceList = document.getElementById("evidence-list");
  const checkpointPanel = document.getElementById("checkpoint-panel");
  const logList = document.getElementById("court-log-list");
  const phaseSteps = document.getElementById("phase-steps");
  const playerRoleBadge = document.getElementById("court-player-role");
  const roleStart = document.getElementById("role-start");
  const courtPause = document.getElementById("court-pause");
  const courtPauseLabel = courtPause.querySelector("span");

  const roleMeta = {
    audience: { name: "旁听观众", badge: "观" },
    juror: { name: "人民陪审员", badge: "陪" },
    prosecutor: { name: "公诉人", badge: "诉" },
    defender: { name: "辩护人", badge: "辩" },
    defendant: { name: "被告人", badge: "被" },
  };
  const playbackTiming = Object.freeze({
    line: { min: 1800, perCharacter: 55, max: 6000 },
    segment: { min: 320, perCharacter: 42, max: 1400 },
    turnHold: { min: 1600, perCharacter: 24, max: 4500 },
    summary: { min: 1800, perCharacter: 45, max: 5000 },
  });

  let currentCaseId = "";
  let selectedRole = "";
  let sessionId = "";
  let caseData = null;
  let pendingCheckpoint = null;      // 等待陪审员输入的检查点事件
  let trialAbortController = null;
  let streamActive = false;
  let trialPaused = false;
  let pauseWaiters = [];
  let statusBeforePause = null;
  let currentTurnText = "";          // 当前发言累计文本（识别证据编号用）
  let currentSpeaker = { role: "", name: "", badge: "" };
  const citedEvidence = new Set();   // 发言中出现过的证据编号
  const markedEvidence = new Set();  // 用户标注的关键证据
  const pollHistory = [];            // [{stage,label}] 预判轨迹
  const roleContributions = [];      // [{stage,title}] 玩家角色发言轨迹

  // ---- 加载案例列表 ----
  fetch("/api/court/cases")
    .then((r) => r.json())
    .then((data) => {
      caseCardsEl.innerHTML = "";
      data.cases.forEach((c) => {
        const card = document.createElement("button");
        card.type = "button";
        card.className = "case-card";
        card.setAttribute("aria-label", `选择案例并挑选角色：${c.title}`);
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

  // ---- 加载案例详情并选择角色 ----
  async function loadCase(caseId) {
    try {
      const res = await fetch(`/api/court/cases/${caseId}`);
      if (!res.ok) { toast("案例加载失败"); return; }
      caseData = await res.json();
      currentCaseId = caseId;

      resetTrialState();
      selectedRole = "";
      selectEl.hidden = true;
      roleSelectEl.hidden = false;
      trialEl.hidden = true;
      deliberationEl.hidden = true;
      verdictEl.hidden = true;
      document.getElementById("role-case-title").textContent = caseData.title + " · " + caseData.subtitle;
      roleSelectEl.querySelectorAll(".role-option").forEach((button) => {
        button.classList.remove("selected");
        button.setAttribute("aria-pressed", "false");
      });
      roleStart.disabled = true;
      roleStart.textContent = "选择角色后开始庭审";
      roleSelectEl.scrollIntoView({ behavior: "smooth", block: "start" });
    } catch {
      toast("网络错误");
    }
  }

  function resetTrialState() {
    setTrialPaused(false);
    setStreamActive(false);
    trialAbortController = null;
    sessionId = "";
    pendingCheckpoint = null;
    currentTurnText = "";
    currentSpeaker = { role: "", name: "", badge: "" };
    citedEvidence.clear();
    markedEvidence.clear();
    pollHistory.length = 0;
    roleContributions.length = 0;
    checkpointPanel.hidden = true;
    checkpointPanel.innerHTML = "";
    logList.innerHTML = "";
    speechName.textContent = "审判长";
    speechBadge.textContent = "审";
    speechTextEl.textContent = "法槌即将落下，庭审马上开始…";
    showSpeechText();
    setPhaseStep("");
    playerRoleBadge.textContent = "";
    document.querySelectorAll(".court-scene .bench").forEach((bench) => {
      bench.classList.remove("speaking", "is-player");
    });
  }

  roleSelectEl.querySelectorAll(".role-option").forEach((button) => {
    button.addEventListener("click", () => {
      selectedRole = button.dataset.role;
      roleSelectEl.querySelectorAll(".role-option").forEach((item) => {
        const active = item === button;
        item.classList.toggle("selected", active);
        item.setAttribute("aria-pressed", String(active));
      });
      roleStart.disabled = false;
      roleStart.textContent = `以${roleMeta[selectedRole].name}身份开始`;
    });
  });

  roleStart.addEventListener("click", () => {
    if (!selectedRole) return;
    roleSelectEl.hidden = true;
    trialEl.hidden = false;
    courtTitle.textContent = caseData.title + " · " + caseData.subtitle;
    courtPhase.textContent = "准备开庭";
    playerRoleBadge.textContent = "你的角色 · " + roleMeta[selectedRole].name;
    applyPlayerRole();
    buildDossierPanel();
    startTrial(currentCaseId);
  });

  document.getElementById("role-back").addEventListener("click", returnToCases);

  function applyPlayerRole() {
    document.querySelectorAll(".court-scene .bench").forEach((bench) => {
      bench.classList.toggle("is-player", bench.dataset.role === selectedRole);
    });
    document.querySelector(".court-scene").classList.toggle("audience-view", selectedRole === "audience");
    const jurorNote = document.querySelector(".juror-bench .bench-name small");
    if (jurorNote) jurorNote.textContent = selectedRole === "juror" ? "你 · 人民陪审员" : "人民陪审员";
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
          <span class="ev-type">${escapeHtml(ev.type)}</span>
          <button type="button" class="ev-star" aria-label="将${escapeHtml(ev.type)}标注为关键证据" aria-pressed="false">☆</button>
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
          toast(`已将“${ev.type}”标注为关键证据`);
        }
      });
      evidenceList.appendChild(card);
    });
  }

  // ---- SSE 读取 ----
  async function readSse(res, handler, options = {}) {
    const pausable = Boolean(options.pausable);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    if (pausable) setStreamActive(true);
    try {
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
          if (pausable) await waitWhilePaused();
          await handler(event);
        }
      }
    } finally {
      if (pausable) setStreamActive(false);
    }
  }

  async function startTrial(caseId) {
    const controller = new AbortController();
    trialAbortController = controller;
    try {
      const res = await fetch(`/api/court/cases/${caseId}/trial`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role: selectedRole }),
        signal: controller.signal,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        toast(err.error || "庭审启动失败");
        return;
      }
      await readSse(res, handleTrialEvent, { pausable: true });
    } catch (err) {
      if (err.name === "AbortError") return;
      toast("庭审连接中断");
    } finally {
      if (trialAbortController === controller) trialAbortController = null;
    }
  }

  // ---- 庭审事件处理 ----
  async function handleTrialEvent(event) {
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
        detectEvidence(event.text);
        setSpeech(event.role, event.name, event.badge, event.text);
        appendLog(event.role, event.name, event.text, event.player);
        await readableDelay(event.text, playbackTiming.line);
        break;

      case "turn_start":
        currentSpeaker = { role: event.role, name: event.name, badge: event.badge };
        currentTurnText = "";
        showSpeechText();
        setSpeaking(event.role, event.name, event.badge);
        speechTextEl.textContent = "";
        showStatus(`${event.name} 正在发言…`);
        break;

      case "delta":
        speechTextEl.textContent += displayEvidenceNames(event.text);
        currentTurnText += event.text;
        detectEvidence(event.text);
        scrollSpeech();
        await readableDelay(event.text, playbackTiming.segment);
        break;

      case "turn_end":
        hideStatus();
        appendLog(currentSpeaker.role, currentSpeaker.name, speechTextEl.textContent);
        await readableDelay(currentTurnText, playbackTiming.turnHold);
        break;

      case "summary":
        setSpeech("clerk", "书记员", "书", `（书记员纪要）${event.text}`);
        appendLog("clerk", "书记员", event.text);
        await readableDelay(event.text, playbackTiming.summary);
        break;

      case "checkpoint":
        pendingCheckpoint = event;
        renderCheckpoint(event);
        break;

      case "resume":
        showSpeechText();
        break;

      case "await_deliberation":
        hideStatus();
        pendingCheckpoint = null;
        courtPhase.textContent = "休庭评议";
        setPhaseStep("deliberation");
        showDeliberation(event.options);
        break;

      case "error":
        hideStatus();
        toast(event.error);
        // 检查点阶段失败：重新显示表单，让用户重试（后端已恢复同一检查点）
        if (pendingCheckpoint) {
          if (pendingCheckpoint.kind === "role_input") {
            roleContributions.pop();
            const playerLines = logList.querySelectorAll(".player-line");
            playerLines[playerLines.length - 1]?.remove();
          } else {
            pollHistory.pop();
          }
          showSpeechAction();
        }
        break;
    }
  }

  function readableDelay(text, timing) {
    const characterCount = Array.from(String(text || "").trim()).length;
    const milliseconds = Math.min(timing.max,
      Math.max(timing.min, characterCount * timing.perCharacter));
    return presentationDelay(milliseconds);
  }

  function presentationDelay(milliseconds) {
    return new Promise((resolve) => setTimeout(resolve, milliseconds));
  }

  function showSpeechText() {
    checkpointPanel.hidden = true;
    speechTextEl.hidden = false;
    speechZone.classList.remove("awaiting-input");
  }

  function showSpeechAction() {
    speechTextEl.hidden = true;
    checkpointPanel.hidden = false;
    speechZone.classList.add("awaiting-input");
    speechZone.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  function setStreamActive(active) {
    streamActive = active;
    courtPause.disabled = !active;
    if (!active) setTrialPaused(false);
  }

  function waitWhilePaused() {
    if (!trialPaused) return Promise.resolve();
    return new Promise((resolve) => pauseWaiters.push(resolve));
  }

  function setTrialPaused(paused) {
    const next = Boolean(paused && streamActive);
    if (trialPaused === next) return;
    trialPaused = next;
    courtPause.setAttribute("aria-pressed", String(next));
    courtPause.classList.toggle("paused", next);
    courtPauseLabel.textContent = next ? "继续庭审" : "暂停庭审";
    courtPause.querySelector(".pause-icon").hidden = next;
    courtPause.querySelector(".play-icon").hidden = !next;
    speechZone.classList.toggle("playback-paused", next);
    if (next) {
      statusBeforePause = { hidden: courtStatus.hidden, text: courtStatusText.textContent };
      showStatus("庭审已暂停，发言进度已保留");
      return;
    }
    const waiting = pauseWaiters;
    pauseWaiters = [];
    waiting.forEach((resolve) => resolve());
    if (statusBeforePause) {
      courtStatusText.textContent = statusBeforePause.text;
      courtStatus.hidden = statusBeforePause.hidden;
      statusBeforePause = null;
    }
  }

  courtPause.addEventListener("click", () => {
    if (!streamActive) return;
    setTrialPaused(!trialPaused);
  });

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
    speechTextEl.textContent = displayEvidenceNames(text);
    scrollSpeech();
  }

  function evidenceName(evidenceId) {
    const evidence = (caseData?.dossier || []).find((item) => item.id === evidenceId);
    return evidence?.type || evidenceId;
  }

  function displayEvidenceNames(text) {
    return String(text || "").replace(/\bE\d+\b/g, (evidenceId) => evidenceName(evidenceId));
  }

  function displayMarkedEvidence() {
    return Array.from(markedEvidence, (evidenceId) => evidenceName(evidenceId)).join("、");
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

  function appendLog(role, name, text, isPlayer = false) {
    const line = document.createElement("div");
    line.className = `log-line ${role || ""}`;
    if (isPlayer) line.classList.add("player-line");
    const b = document.createElement("b");
    b.textContent = name + "：";
    line.appendChild(b);
    line.appendChild(document.createTextNode(displayEvidenceNames(text)));
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
    if (cp.kind === "role_input") {
      renderRoleCheckpoint(cp);
      return;
    }
    setSpeaking("juror", "人民陪审员（你）", "陪");

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
            placeholder="例：监控视频显示夺刀到追刺只有 11 秒，你凭什么认定侵害已经结束？"></textarea>
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
    showSpeechAction();

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

  function renderRoleCheckpoint(cp) {
    setSpeaking(cp.role, `${cp.role_name}（你）`, cp.badge);
    const card = document.createElement("div");
    card.className = "cp-card role-cp-card";
    const optionsHtml = (cp.options || []).map((option) => `
      <button type="button" class="cp-option role-statement-option" data-id="${escapeHtml(option.id)}" aria-pressed="false">
        <span class="cp-opt-label">${escapeHtml(option.label)}</span>
        <span class="cp-opt-detail">${escapeHtml(option.detail)}</span>
        <span class="cp-opt-statement">“${escapeHtml(displayEvidenceNames(option.statement))}”</span>
      </button>`).join("");
    card.innerHTML = `
      <div class="cp-role-head">
        <span class="role-sigil" aria-hidden="true">${escapeHtml(cp.badge)}</span>
        <div><div class="cp-title">${escapeHtml(cp.title)}</div>
        <p class="cp-quote">${escapeHtml(cp.prompt)}</p></div>
      </div>
      <div class="cp-label">选择发言 <small>（预设内容可直接提交）</small></div>
      <div class="cp-options role-statement-options">
        ${optionsHtml}
        <button type="button" class="cp-option custom-statement-option" data-id="custom" aria-pressed="false">
          <span class="cp-opt-label">自定义发言</span>
          <span class="cp-opt-detail">依据卷宗和庭审记录，用你自己的话表达。</span>
        </button>
      </div>
      <div class="cp-question custom-statement" hidden>
        <label class="cp-label" for="cp-statement">你的发言 <small>（10-500 字，将记入庭审记录）</small></label>
        <textarea id="cp-statement" rows="5" maxlength="500"
          placeholder="${escapeHtml(displayEvidenceNames(cp.placeholder))}"></textarea>
        <div class="statement-count"><span id="statement-count">0</span>/500</div>
      </div>
      <button type="button" class="primary-btn" id="cp-submit" disabled>选择一项后继续</button>
    `;
    checkpointPanel.innerHTML = "";
    checkpointPanel.appendChild(card);
    showSpeechAction();
    const textarea = card.querySelector("#cp-statement");
    const submit = card.querySelector("#cp-submit");
    const customArea = card.querySelector(".custom-statement");
    let selected = "";
    card.querySelectorAll(".cp-option").forEach((button) => {
      button.addEventListener("click", () => {
        selected = button.dataset.id;
        card.querySelectorAll(".cp-option").forEach((item) => {
          const active = item === button;
          item.classList.toggle("selected", active);
          item.setAttribute("aria-pressed", String(active));
        });
        const custom = selected === "custom";
        customArea.hidden = !custom;
        submit.disabled = custom ? textarea.value.trim().length < 10 : false;
        submit.textContent = custom ? "提交自定义发言" : "采用这段发言，继续庭审";
        if (custom) textarea.focus();
      });
    });
    textarea.addEventListener("input", () => {
      const length = textarea.value.trim().length;
      card.querySelector("#statement-count").textContent = String(length);
      if (selected === "custom") submit.disabled = length < 10;
    });
    submit.addEventListener("click", () => submitRoleCheckpoint(cp, card, selected));
  }

  async function submitRoleCheckpoint(cp, card, selected) {
    const preset = (cp.options || []).find((option) => option.id === selected);
    const statement = selected === "custom"
      ? card.querySelector("#cp-statement").value.trim()
      : String(preset?.statement || "").trim();
    if (statement.length < 10) { toast("请至少写 10 个字"); return; }
    const submit = card.querySelector("#cp-submit");
    submit.disabled = true;
    submit.textContent = "正在提交…";
    const playerLineCount = logList.querySelectorAll(".player-line").length;
    roleContributions.push({ stage: cp.stage, title: cp.title });
    showSpeechText();
    setSpeech(cp.role, `${cp.role_name}（你）`, cp.badge, statement);
    const controller = new AbortController();
    trialAbortController = controller;
    try {
      const res = await fetch(`/api/court/sessions/${sessionId}/continue`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ statement }),
        signal: controller.signal,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        toast(err.error || "提交失败");
        roleContributions.pop();
        showSpeechAction();
        submit.disabled = false;
        submit.textContent = selected === "custom" ? "提交自定义发言" : "采用这段发言，继续庭审";
        return;
      }
      await readSse(res, handleTrialEvent, { pausable: true });
    } catch (error) {
      if (error.name === "AbortError") return;
      roleContributions.pop();
      const playerLines = logList.querySelectorAll(".player-line");
      if (playerLines.length > playerLineCount) playerLines[playerLines.length - 1]?.remove();
      toast("网络错误");
      showSpeechAction();
      submit.disabled = false;
      submit.textContent = selected === "custom" ? "提交自定义发言" : "采用这段发言，继续庭审";
    } finally {
      if (trialAbortController === controller) trialAbortController = null;
    }
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

    const submit = card.querySelector("#cp-submit");
    submit.disabled = true;
    submit.textContent = "正在提交…";
    showSpeechText();
    const controller = new AbortController();
    trialAbortController = controller;
    try {
      const res = await fetch(`/api/court/sessions/${sessionId}/continue`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        toast(err.error || "提交失败");
        showSpeechAction();
        submit.disabled = false;
        submit.textContent = cp.kind === "mid_review" ? "提交意见，继续庭审" : "记录意见，进入最后陈述";
        return;
      }
      await readSse(res, handleTrialEvent, { pausable: true });
    } catch (error) {
      if (error.name === "AbortError") return;
      toast("网络错误");
      showSpeechAction();
      submit.disabled = false;
      submit.textContent = cp.kind === "mid_review" ? "提交意见，继续庭审" : "记录意见，进入最后陈述";
    } finally {
      if (trialAbortController === controller) trialAbortController = null;
    }
  }

  // ---- 评议 ----
  function trajChip(text, cls) {
    return `<span class="traj-chip ${cls || ""}">${escapeHtml(text)}</span>`;
  }

  function showDeliberation(options) {
    trialEl.hidden = true;
    deliberationEl.hidden = false;
    const isJuror = selectedRole === "juror";
    const isAudience = selectedRole === "audience";
    document.getElementById("deliberation-title").textContent = isJuror
      ? "休庭评议"
      : isAudience ? "庭审结束 · 裁判预测" : "角色任务完成 · 裁判预测";
    document.getElementById("deliberation-intro").textContent = isJuror
      ? "作为人民陪审员，请根据庭审查明的事实与法律，独立作出你的评议。"
      : isAudience
        ? "你已完整旁听控辩双方的发言。现在请根据庭审内容，作出你的裁判预测。"
        : `你已完成${roleMeta[selectedRole].name}的关键发言。现在请暂时跳出诉讼立场，作出最终裁判预测。`;

    // 预判轨迹 + 关键证据标注回顾
    const trajEl = document.getElementById("delib-traj");
    const chips = [];
    pollHistory.forEach((p, i) => {
      chips.push(trajChip(`${i === 0 ? "调查后" : "辩论后"}预判：${p.label}`));
    });
    roleContributions.forEach((item) => {
      chips.push(trajChip(`已提交：${item.title}`));
    });
    if (markedEvidence.size) {
      chips.push(trajChip(`关键证据：${displayMarkedEvidence()}`, "final"));
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
        + (markedEvidence.size ? `（关键证据：${displayMarkedEvidence()}）` : "");

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
            analysisEl.textContent += displayEvidenceNames(event.text);
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
    (event.contributions || []).forEach((item) => {
      chips.push(trajChip(`${item.name}发言已记录`));
    });
    chips.push(trajChip(`你的评议：${event.yours.label}`, "final"));
    const realShort = (event.real.summary || "").replace(/。$/, "");
    chips.push(trajChip(`法院判决：${realShort.length > 22 ? realShort.slice(0, 22) + "…" : realShort}`, "real"));
    el.innerHTML = chips.join('<span class="traj-arrow">→</span>');
    el.hidden = false;
  }

  // ---- 再来一局 ----
  document.getElementById("court-restart").addEventListener("click", () => {
    returnToCases();
  });

  document.getElementById("court-exit").addEventListener("click", () => {
    if (trialAbortController) trialAbortController.abort();
    returnToCases();
  });

  function returnToCases() {
    verdictEl.hidden = true;
    deliberationEl.hidden = true;
    trialEl.hidden = true;
    roleSelectEl.hidden = true;
    selectEl.hidden = false;
    resetTrialState();
    selectedRole = "";
    currentCaseId = "";
    caseData = null;
    selectEl.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function escapeHtml(text) {
    const div = document.createElement("div");
    div.textContent = text;
    return div.innerHTML;
  }
})();
