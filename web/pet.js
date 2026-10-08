/* 法晓 · 獬豸桌宠（3D 版）：Three.js 实时渲染的立体獬豸
 *
 * 程序化建模（无外部模型文件）：圆头 + 金色螺旋独角 + 红色鬃毛围领 + 棕色弯角，
 * 头部/眼球/耳朵/尾巴各自独立运动——
 *   · 眼随鼠标：眼球先动、头部平滑跟上（弹簧阻尼），全身呼吸起伏
 *   · 可拖拽移动（Pointer Events，位置记忆在 localStorage），无卡片透明背景
 *   · 打字：左右扫读式摇头，耳朵竖起
 *   · 生成中：抬头思考、缓慢侧倾
 *   · 完成：原地弹跳、尾巴快摆
 *   · 出错：快速摇头
 *   · 60s 无操作：低头打盹
 *
 * 与 app.js / court.js 的 window 自定义事件契约不变：
 *   pet:ask / pet:status {text} / pet:answer {pet} / pet:error / pet:pet {pet}
 * 成长系统走 /api/pet（六阶段），见 legalweb/app.py。
 * three.js 本地自托管于 assets/vendor/；WebGL 不可用时回退为静态 sprite。
 */
(function () {
  "use strict";

  const API_BASE = location.pathname.replace(/\/[^/]*$/, "");
  const SLEEP_AFTER_MS = 60000;

  const chatArea = document.querySelector(".chat-area");
  if (!chatArea) return;

  // ---- DOM ----
  const root = document.createElement("div");
  root.className = "pet-root";
  root.innerHTML = `
    <div class="pet-panel" id="pet-panel" role="dialog" aria-labelledby="pet-panel-title" hidden>
      <div class="pet-panel-head">
        <div><h2 id="pet-panel-title" class="pet-stage-name">幼獬</h2>
          <small class="pet-exp-text">0 经验</small></div>
        <button class="pet-panel-close" type="button" aria-label="关闭成长面板">×</button>
      </div>
      <div class="pet-panel-status" role="status" hidden>
        <span class="pet-panel-status-text"></span>
        <button class="pet-retry-btn" type="button">重试</button>
      </div>
      <div class="pet-login-note" hidden>
        <p>登录后即可同步成长记录、咨询记忆与模拟法庭进度，数据只保存在你自己的账户里。</p>
        <button class="pet-login-btn" type="button">登录 / 注册</button>
      </div>
      <p class="pet-panel-title">初识人间，蹒跚学步</p>
      <p class="pet-stage-appearance">幼角初生，浅色鬃毛</p>
      <div class="pet-bar" role="progressbar" aria-label="当前成长阶段进度"
        aria-valuemin="0" aria-valuemax="100" aria-valuenow="0">
        <div class="pet-bar-fill"></div></div>
      <div class="pet-bar-note"><span class="pet-progress-note"></span>
        <span class="pet-next-note"></span></div>
      <div class="pet-stats">
        <div class="pet-stat"><b class="pet-qa-n">0</b><span>完成问答</span></div>
        <div class="pet-stat"><b class="pet-court-n">0</b><span>完成庭审</span></div>
      </div>
      <p class="pet-earn-rules">有依据的问答 +2 · 完成模拟庭审 +10</p>
      <div class="pet-stages-row" role="list" aria-label="成长阶段"></div>
      <div class="pet-panel-actions" role="group" aria-label="宠物操作">
        <button class="pet-pat-btn" type="button">摸摸獬豸</button>
        <button class="pet-reset-btn" type="button" hidden>恢复位置</button>
        <button class="pet-memory-clear" type="button" hidden>清空记忆</button>
        <button class="pet-hide-btn" type="button">隐藏宠物</button>
      </div>
      <details class="pet-rewards">
        <summary>最近成长记录</summary>
        <ul class="pet-reward-list"></ul>
        <p class="pet-reward-empty">完成有依据的问答或模拟庭审后，记录会出现在这里。</p>
      </details>
      <details class="pet-memories">
        <summary class="pet-memories-head">獬豸的记忆<small class="pet-memory-count"></small></summary>
        <p class="pet-memory-consent"></p>
        <ul class="pet-memory-list"></ul>
        <p class="pet-memory-empty" hidden>还没有记忆，完成一次问答后獬豸就会记住这次咨询</p>
      </details>
    </div>
    <div class="pet-bubble" role="status" aria-live="polite" aria-atomic="true"></div>
    <button class="pet-badge pet-fallback" type="button" data-stage="cub"
      data-state="idle" data-look="center"
      aria-label="法晓的獬豸宠物，点击查看成长" aria-expanded="false"
      aria-controls="pet-panel">
      <canvas class="pet-canvas" aria-hidden="true"></canvas>
      <span class="pet-stage-chip">幼獬</span>
    </button>`;
  chatArea.appendChild(root);
  const badge = root.querySelector(".pet-badge");
  const canvas = root.querySelector(".pet-canvas");
  const chip = root.querySelector(".pet-stage-chip");
  const bubble = root.querySelector(".pet-bubble");
  const panel = root.querySelector(".pet-panel");
  const panelClose = root.querySelector(".pet-panel-close");
  const progressBar = root.querySelector(".pet-bar");
  const memoryClear = root.querySelector(".pet-memory-clear");
  const panelStatus = root.querySelector(".pet-panel-status");
  const panelStatusText = root.querySelector(".pet-panel-status-text");
  const retryButton = root.querySelector(".pet-retry-btn");
  const patButton = root.querySelector(".pet-pat-btn");
  const resetButton = root.querySelector(".pet-reset-btn");
  const loginNote = root.querySelector(".pet-login-note");

  // ---- 状态机（与渲染层解耦：3D 循环读取这些变量） ----
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let state = "idle";              // idle | typing | thinking | happy | sleepy
  let stateEnteredAt = 0;
  let stateTimer = 0;
  let generating = false;
  let errorShakeUntil = 0;
  let lastActivity = Date.now();
  const look = { x: 0, y: 0 };     // 光标相对宠物中心的归一化偏移（约 -2..2）

  function setState(next) {
    if (state === next) return;
    const previous = state;
    state = next;
    badge.dataset.state = next;
    // 立绘模式下不切换表情精灵（行内 background-image 已锁定阶段立绘）
    stateEnteredAt = performance.now();
    clearTimeout(stateTimer);
    if (previous === "sleepy" && next !== "sleepy") hideBubble();
    if (next === "happy") {
      stateTimer = setTimeout(() => { if (state === "happy") setState("idle"); }, 1600);
    }
  }

  // ---- 眼/头随鼠标（方向计算与渲染无关，回退模式同样可用） ----
  if (matchMedia("(pointer: fine)").matches) {
    window.addEventListener("mousemove", (e) => {
      lastActivity = Date.now();
      if (state === "sleepy") setState("idle");
      const r = badge.getBoundingClientRect();
      look.x = (e.clientX - (r.left + r.width / 2)) / Math.max(window.innerWidth / 2, 1);
      look.y = (e.clientY - (r.top + r.height / 2)) / Math.max(window.innerHeight / 2, 1);
      if (!petDragging) badge.dataset.look = look.x < -0.08 ? "left" : look.x > 0.08 ? "right" : "center";
    }, { passive: true });
  }

  // ---- 打字动画 ----
  const qaInput = document.getElementById("qa-input");
  if (qaInput) {
    let typeTimer = 0;
    qaInput.addEventListener("input", () => {
      lastActivity = Date.now();
      if (generating) return;
      setState("typing");
      clearTimeout(typeTimer);
      typeTimer = setTimeout(() => {
        if (!generating && state === "typing") setState("idle");
      }, 1600);
    });
  }

  // ---- 睡眠 ----
  setInterval(() => {
    if (!generating && Date.now() - lastActivity > SLEEP_AFTER_MS
      && state !== "sleepy") {
      setState("sleepy");
      say("Zzz…", 0);
    }
  }, 5000);

  // ---- 状态气泡 ----
  let bubbleTimer = 0;
  function say(text, ms = 3600) {
    bubble.textContent = text;
    bubble.classList.add("show");
    clearTimeout(bubbleTimer);
    if (ms > 0) bubbleTimer = setTimeout(() => bubble.classList.remove("show"), ms);
  }
  function hideBubble() {
    clearTimeout(bubbleTimer);
    bubble.classList.remove("show");
  }

  // ---- 成长 ----
  function getClientId() {
    try { return localStorage.getItem("faxiao_client_id") || ""; } catch { return ""; }
  }

  const MEMORY_KEY = "faxiao_pet_memory_enabled";
  const ONBOARDING_KEY = "faxiao_pet_onboarding_seen";
  let currentPet = null;
  let petFetchController = null;
  let memoryMutationPending = false;
  let onboardingTimer = 0;
  function isMemoryEnabled() {
    try { return localStorage.getItem(MEMORY_KEY) === "1"; } catch { return false; }
  }

  function formatPetDate(value) {
    const date = new Date(value || "");
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat("zh-CN", {
      month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
    }).format(date);
  }

  function setPanelStatus(kind, text = "") {
    panelStatus.hidden = !kind;
    panelStatus.dataset.kind = kind || "";
    panelStatusText.textContent = text;
    retryButton.hidden = kind !== "error";
  }

  // 阶段动画以当前成长阶段为准；幼獬额外拥有独立的左右朝向循环帧。
  let stageArtIndex = -1;
  let petDragging = false;
  let stageImg = null;
  let stageArtLoading = false;
  let artRequest = 0;
  let requestedStage = -1;
  const directionArt = {};
  let artTimeout = 0;
  let idleLoadHandle = 0;
  let idleLoadKind = "";

  function loadDirections() {
    for (const side of ["left", "right"]) {
      if (directionArt[side]) continue;
      const img = new Image();
      directionArt[side] = img;
      img.className = `pet-video pet-direction pet-direction-${side}`;
      img.alt = "";
      img.setAttribute("aria-hidden", "true");
      img.onload = () => {
        badge.insertBefore(img, chip);
        if (stageArtIndex === 0) updateGaze();
      };
      img.onerror = () => {
        if (badge.dataset.gaze === side) badge.dataset.gaze = "front";
      };
      img.src = `${API_BASE}/assets/pet/hero-look-${side}.webp`;
    }
  }

  function probeStageArt(index) {
    index = Number.isInteger(index) && index >= 0 && index <= 5 ? index : 0;
    if (requestedStage === index) return;
    requestedStage = index;
    const request = ++artRequest;
    clearTimeout(artTimeout);
    stageArtIndex = -1;
    stageArtLoading = true;
    badge.classList.remove("pet-stageart");
    badge.classList.add("pet-fallback");
    badge.dataset.artStage = "";
    badge.dataset.gaze = "front";
    if (stageImg) stageImg.remove();
    stageImg = null;
    if (index === 0 && !reduceMotion) loadDirections();
    cancelScheduled3D();
    const img = new Image();
    img.className = "pet-video";
    img.alt = "";
    img.setAttribute("aria-hidden", "true");
    img.onload = () => {
      if (request !== artRequest) return;
      fx = 0; fy = 0; fry = 0;
      stageImg = img;
      badge.insertBefore(img, badge.firstChild);
      stageArtIndex = index;
      clearTimeout(artTimeout);
      stageArtLoading = false;
      badge.dataset.artStage = String(index);
      badge.classList.add("pet-stageart");
      if (index === 0) updateGaze();
      cancelScheduled3D();
    };
    img.onerror = () => {
      if (request !== artRequest) return;
      clearTimeout(artTimeout);
      requestedStage = -1;
      stageArtLoading = false;
      schedule3D();
    };
    img.src = `${API_BASE}/assets/pet/stage-${index}.webp`;
    artTimeout = setTimeout(() => {
      if (request !== artRequest || !stageArtLoading) return;
      ++artRequest;
      requestedStage = -1;
      stageArtLoading = false;
      schedule3D();
    }, 4000);
  }

  // 幼獬切换真实朝向帧；其余阶段保留轻微跟随，避免套用幼獬的外观。
  const pointerAt = { x: innerWidth / 2, y: innerHeight / 2, seen: false, lastMoved: 0 };
  window.addEventListener("pointermove", (e) => {
    if (e.pointerType && e.pointerType !== "mouse" && e.pointerType !== "pen") return;
    if (!pointerAt.seen || e.clientX !== pointerAt.x || e.clientY !== pointerAt.y) {
      pointerAt.lastMoved = performance.now();
    }
    pointerAt.x = e.clientX; pointerAt.y = e.clientY; pointerAt.seen = true;
    if (stageArtIndex === 0) updateGaze();
  }, { passive: true });
  function updateGaze() {
    if (reduceMotion || petDragging || !pointerAt.seen || stageArtIndex !== 0) return;
    if (performance.now() - pointerAt.lastMoved >= 3000) {
      badge.dataset.gaze = "front";
      return;
    }
    const b = badge.getBoundingClientRect();
    const dx = (pointerAt.x - (b.left + b.width / 2)) / Math.max(window.innerWidth / 2, 1);
    const current = badge.dataset.gaze || "front";
    let next = current;
    if (dx < -0.22 && current !== "left") next = "left";
    else if (dx > 0.22 && current !== "right") next = "right";
    else if (Math.abs(dx) < 0.10) next = "front";
    if (next !== "front" && !directionArt[next]?.naturalWidth) next = "front";
    if (next !== current) badge.dataset.gaze = next;
  }
  let followRAF = 0, followLast = 0, fx = 0, fy = 0, fry = 0;
  function followFrame(now) {
    followRAF = requestAnimationFrame(followFrame);
    if (now - followLast < 33) return;
    followLast = now;
    const img = stageImg;
    if (!img || stageArtIndex < 0 || root.style.display === "none") return;
    if (stageArtIndex === 0) {
      updateGaze();
      return;
    }
    const b = badge.getBoundingClientRect();
    let tx = 0, ty = 0, ry = 0;
    if (!petDragging) {
      const dx = (pointerAt.x - (b.left + b.width / 2)) / (window.innerWidth / 2);
      const dy = (pointerAt.y - (b.top + b.height / 2)) / (window.innerHeight / 2);
      tx = clamp(dx, -1, 1) * 9;
      ty = clamp(dy, -1, 1) * 4;
      ry = clamp(dx, -1, 1) * 24;
    }
    fx += (tx - fx) * 0.1; fy += (ty - fy) * 0.1; fry += (ry - fry) * 0.1;
    img.style.transform =
      `perspective(180px) translate(${fx.toFixed(2)}px, ${fy.toFixed(2)}px) rotateY(${fry.toFixed(2)}deg)`;
  }
  if (!reduceMotion) followRAF = requestAnimationFrame(followFrame);

  function renderPet(pet) {
    if (!pet) return;
    currentPet = pet;
    probeStageArt(pet.stage_index || 0);
    chip.textContent = pet.stage_name;
    chip.parentElement.dataset.stage = pet.stage;
    badge.setAttribute("aria-label", `法晓的獬豸宠物，当前阶段${pet.stage_name}，点击查看成长`);
    root.querySelector(".pet-stage-name").textContent = pet.stage_name;
    root.querySelector(".pet-panel-title").textContent = pet.stage_title;
    root.querySelector(".pet-stage-appearance").textContent = `当前外观 · ${pet.stage_appearance || "随成长逐渐变化"}`;
    root.querySelector(".pet-exp-text").textContent = `${pet.exp} 经验`;
    root.querySelector(".pet-bar-fill").style.setProperty("--pet-progress", pet.progress / 100);
    progressBar.setAttribute("aria-valuenow", String(pet.progress));
    progressBar.setAttribute("aria-valuetext", `本阶段完成 ${pet.progress}%`);
    root.querySelector(".pet-progress-note").textContent = `本阶段 ${pet.progress}%`;
    root.querySelector(".pet-next-note").textContent = pet.next_stage_name
      ? `还差 ${pet.next_need} 经验到「${pet.next_stage_name}」` : "已达最高阶段";
    root.querySelector(".pet-qa-n").textContent = pet.qa_count;
    root.querySelector(".pet-court-n").textContent = pet.court_count;
    const row = root.querySelector(".pet-stages-row");
    row.innerHTML = "";
    (pet.stages || []).forEach((s, i) => {
      const step = document.createElement("span");
      step.className = "pet-stage-step" + (i === pet.stage_index ? " current" : "");
      step.setAttribute("role", "listitem");
      step.setAttribute("aria-label", `${s.name}，${i <= pet.stage_index ? "已到达" : `需要 ${s.need} 经验`}`);
      if (i === pet.stage_index) step.setAttribute("aria-current", "step");
      const dot = document.createElement("span");
      dot.className = "pet-stage-dot" + (i <= pet.stage_index ? " reached" : "");
      dot.setAttribute("aria-hidden", "true");
      const label = document.createElement("span");
      label.className = "pet-stage-label";
      label.textContent = s.name;
      step.append(dot, label);
      row.appendChild(step);
    });
    const rewards = pet.recent_rewards || [];
    const rewardList = root.querySelector(".pet-reward-list");
    rewardList.innerHTML = "";
    rewards.forEach((reward) => {
      const li = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = reward.kind === "court" ? "完成模拟庭审" : "完成有依据的问答";
      const meta = document.createElement("span");
      meta.className = "pet-reward-meta";
      meta.textContent = `+${reward.gain} · ${formatPetDate(reward.created_at)}`;
      li.append(label, meta);
      rewardList.appendChild(li);
    });
    root.querySelector(".pet-reward-empty").hidden = rewards.length > 0;
    // 记忆库（后端按问答蒸馏，textContent 写入防注入）
    const memories = pet.memories || [];
    root.querySelector(".pet-memory-count").textContent =
      memories.length ? `共 ${pet.memory_count} 条` : "";
    root.querySelector(".pet-memory-consent").textContent = isMemoryEnabled()
      ? "已开启：仅记录有法律依据的问答摘要，可随时清空。"
      : "当前关闭：不会继续生成新的咨询记忆。";
    const memList = root.querySelector(".pet-memory-list");
    memList.innerHTML = "";
    memories.forEach((m) => {
      const li = document.createElement("li");
      li.className = "pet-memory-item";
      const copy = document.createElement("span");
      copy.className = "pet-memory-copy";
      copy.textContent = m.content;
      const time = document.createElement("time");
      time.dateTime = m.created_at || "";
      time.textContent = formatPetDate(m.created_at);
      const remove = document.createElement("button");
      remove.className = "pet-memory-delete";
      remove.type = "button";
      remove.textContent = "删除";
      remove.setAttribute("aria-label", `删除记忆：${m.content}`);
      remove.addEventListener("click", () => deleteMemory(m));
      const body = document.createElement("span");
      body.className = "pet-memory-body";
      body.append(copy, time);
      li.append(body, remove);
      memList.appendChild(li);
    });
    const empty = root.querySelector(".pet-memory-empty");
    empty.textContent = isMemoryEnabled()
      ? "还没有记忆；完成一次有依据的问答后会生成摘要。"
      : "可在设置中主动开启咨询记忆。";
    empty.hidden = memories.length > 0;
    memoryClear.hidden = !pet.memory_count;
    setPanelStatus("", "");
    if (petScene) applyStageAppearance(petScene, pet.stage_index);
  }

  async function fetchPet({ showLoading = false } = {}) {
    const id = getClientId();
    if (id.length < 8) {
      setPanelStatus("error", "无法读取浏览器标识，请刷新页面后重试。");
      return null;
    }
    if (petFetchController) petFetchController.abort();
    const controller = new AbortController();
    petFetchController = controller;
    if (showLoading) {
      setPanelStatus("loading", currentPet ? "正在刷新成长记录…" : "正在读取成长记录…");
    }
    try {
      const res = await fetch(`${API_BASE}/api/pet?client_id=${encodeURIComponent(id)}`, {
        signal: controller.signal,
      });
      if (res.status === 401) {
        // 未登录：宠物照常陪伴，面板只展示登录引导
        panel.classList.add("logged-out");
        loginNote.hidden = false;
        setPanelStatus("", "");
        return null;
      }
      if (!res.ok) throw new Error(`pet ${res.status}`);
      const pet = await res.json();
      if (controller.signal.aborted) return null;
      panel.classList.remove("logged-out");
      loginNote.hidden = true;
      renderPet(pet);
      stageIndex = pet.stage_index;
      lastExp = pet.exp;
      maybeShowOnboarding();
      return pet;
    } catch (error) {
      if (error && error.name === "AbortError") return null;
      setPanelStatus("error", "成长记录暂时无法读取，宠物仍可继续陪伴。");
      return null;
    } finally {
      if (petFetchController === controller) petFetchController = null;
    }
  }
  function growthFeedback(pet, prevIndex, gain, action) {
    const upgraded = pet && pet.stage_index > prevIndex;
    setState("happy");
    if (upgraded) {
      chip.classList.remove("milestone");
      // 重新读取布局让连续跨级时也能可靠触发一次里程碑动效。
      void chip.offsetWidth;
      chip.classList.add("milestone");
      setTimeout(() => chip.classList.remove("milestone"), 1500);
      // 立绘模式：进化闪光（泛白放大回弹）
      if (stageArtIndex >= 0) {
        badge.classList.remove("pet-stageup");
        void badge.offsetWidth;
        badge.classList.add("pet-stageup");
        setTimeout(() => badge.classList.remove("pet-stageup"), 1400);
      }
      say(`额角亮起 · 成长为「${pet.stage_name}」${gain ? ` · +${gain} 经验` : ""}`, 4800);
      return;
    }
    say(gain ? `${action} · +${gain} 经验` : action, 2400);
  }
  let stageIndex = 0;
  let lastExp = NaN;             // 经验基线：回答完成时算 +N 经验给气泡

  function markOnboardingSeen() {
    clearTimeout(onboardingTimer);
    onboardingTimer = 0;
    try { localStorage.setItem(ONBOARDING_KEY, "1"); } catch { /* 本次展示仍然有效 */ }
  }

  function maybeShowOnboarding() {
    if (onboardingTimer || root.style.display === "none") return;
    try { if (localStorage.getItem(ONBOARDING_KEY) === "1") return; } catch { /* 继续本次提示 */ }
    onboardingTimer = setTimeout(() => {
      onboardingTimer = 0;
      if (root.style.display === "none" || generating) return;
      say("点我看成长，拖动可换位置", 5200);
      markOnboardingSeen();
    }, 1200);
  }

  const PAT_LINES = {
    cub: ["幼獬蹭了蹭你的手", "它还小，但已经认得你了"],
    dawn: ["独角轻轻亮了一下", "它正努力辨认是非曲直"],
    keen: ["目光炯炯，像是发现了新线索", "它认真听着，也认真陪着"],
    just: ["金色轮廓微微发亮", "守正不阿，也记得温柔"],
    sage: ["鬃毛轻摆，神情从容", "明察秋毫，也不忘耐心"],
    luck: ["祥瑞金辉在身边流转", "它安静地守在你身旁"],
  };
  let patCount = 0;

  function patPet() {
    markOnboardingSeen();
    if (!stageArtLoading) ensure3D();
    setState("happy");
    lastActivity = Date.now();
    const lines = PAT_LINES[currentPet && currentPet.stage] || PAT_LINES.cub;
    say(lines[patCount++ % lines.length], 3000);
  }

  // ---- 显示/隐藏：面板按钮隐藏；设置抽屉经 pet:visibility 事件重新开启 ----
  const HIDDEN_KEY = "faxiao_pet_hidden";
  const POSITION_KEY = "faxiao_pet_pos";
  function setPetVisible(visible) {
    root.style.display = visible ? "" : "none";
    try {
      if (visible) localStorage.removeItem(HIDDEN_KEY);
      else localStorage.setItem(HIDDEN_KEY, "1");
    } catch { /* 存储不可用则只影响本次会话 */ }
    if (visible) {
      if (stageArtIndex < 0 && !stageArtLoading) probeStageArt(stageIndex);
      if (!stageArtLoading) ensure3D();
      setState("happy");
      say("我回来啦！", 2400);
      lastActivity = Date.now();
      fetchPet();
    } else {
      cancelScheduled3D();
      loopRunning = false;
      closePanel(false);
      hideBubble();
    }
  }
  root.querySelector(".pet-hide-btn").addEventListener("click", () => {
    // 走事件总线：pet.js 自己的监听器执行隐藏，app.js 的设置开关同步状态
    window.dispatchEvent(new CustomEvent("pet:visibility", { detail: { visible: false } }));
    if (typeof window.toast === "function") window.toast("獬豸已隐藏，可在右上角设置中重新开启");
  });
  window.addEventListener("pet:visibility", (e) => {
    setPetVisible(!(e.detail && e.detail.visible === false));
  });
  window.addEventListener("pet:memory-preference", () => {
    if (currentPet) renderPet(currentPet);
  });
  let initiallyHidden = false;
  try {
    initiallyHidden = localStorage.getItem(HIDDEN_KEY) === "1";
    if (initiallyHidden) root.style.display = "none";
  } catch { /* 忽略存储异常 */ }
  if (!initiallyHidden) {
    probeStageArt(0);
    fetchPet();
  }

  // ---- 面板开关 ----
  let panelHideTimer = 0;

  /* 面板是 fixed 定位：跟据宠物当前位置自动选「上方优先、放不下翻下方」，
     并整体钳制在视口内——宠物拖到边缘也不会被聊天区裁剪或压到界面外。
     完整卡片不滚动；仅视口高度实在放不下时才兜底出滚动条。 */
  function placePanel() {
    if (panel.hidden) return;
    const MARGIN = 12;
    const vw = window.innerWidth, vh = window.innerHeight;
    panel.style.maxHeight = "";      // 先还原自然高度再测量
    panel.style.overflowY = "";
    const br = badge.getBoundingClientRect();
    const pw = panel.offsetWidth, ph = panel.offsetHeight;
    const below = br.top < MARGIN + ph + 14;   // 上方放不下（留出 14px 间距）→ 放下方
    let left = br.left + br.width / 2 - pw / 2; // 水平居中于宠物，再整体钳制在视口内
    let top = below ? br.bottom + 14 : br.top - ph - 14;
    left = Math.max(MARGIN, Math.min(left, vw - pw - MARGIN));
    top = Math.max(MARGIN, Math.min(top, vh - ph - MARGIN));
    if (ph > vh - 2 * MARGIN) {                // 极矮视口：整体放不下才允许内部滚动
      panel.style.maxHeight = `${vh - 2 * MARGIN}px`;
      panel.style.overflowY = "auto";
      top = MARGIN;
    }
    panel.style.left = `${Math.round(left)}px`;
    panel.style.top = `${Math.round(top)}px`;
    panel.style.transformOrigin = below ? "top center" : "bottom center";
  }

  function closePanel(restoreFocus = false) {
    if (badge.getAttribute("aria-expanded") !== "true") return;
    clearTimeout(panelHideTimer);
    panel.classList.remove("show");
    badge.setAttribute("aria-expanded", "false");
    if (reduceMotion) panel.hidden = true;
    else panelHideTimer = setTimeout(() => {
      if (badge.getAttribute("aria-expanded") === "false") panel.hidden = true;
    }, 210);
    if (restoreFocus) badge.focus();
  }

  function openPanel() {
    clearTimeout(panelHideTimer);
    markOnboardingSeen();
    hideBubble();
    panel.hidden = false;
    placePanel();
    badge.setAttribute("aria-expanded", "true");
    requestAnimationFrame(() => {
      if (badge.getAttribute("aria-expanded") === "true") panel.classList.add("show");
    });
    // 点击后徽章尺寸/位置可能变化（如精灵切换），再校正一次确保面板居中于宠物
    requestAnimationFrame(() => placePanel());
    setTimeout(placePanel, 60);
    fetchPet({ showLoading: true });
    if (!stageArtLoading) ensure3D();
    setState("happy");
    panelClose.focus();
  }

  badge.addEventListener("click", () => {
    if (suppressClick) { suppressClick = false; return; }  // 刚拖拽完，不当点击处理
    if (badge.getAttribute("aria-expanded") !== "true") openPanel();
    else closePanel(true);
  });
  panelClose.addEventListener("click", () => closePanel(true));
  patButton.addEventListener("click", patPet);
  retryButton.addEventListener("click", () => fetchPet({ showLoading: true }));
  // 内容增减（成长记录/记忆刷新）会改变面板高度，跟着重新定位
  if ("ResizeObserver" in window) new ResizeObserver(placePanel).observe(panel);
  document.addEventListener("click", (e) => {
    if (!root.contains(e.target)) closePanel(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && badge.getAttribute("aria-expanded") === "true") {
      e.preventDefault();
      closePanel(true);
    }
  });

  async function deleteMemory(memory) {
    if (memoryMutationPending || !memory || !Number.isInteger(memory.id)) return;
    if (!window.confirm(`确定删除这条咨询记忆吗？\n\n${memory.content}`)) return;
    const id = getClientId();
    if (id.length < 8) return;
    setMemoryMutationPending(true);
    try {
      const res = await fetch(`${API_BASE}/api/pet/memories/${memory.id}`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: id }),
      });
      if (res.status === 401) {
        window.dispatchEvent(new CustomEvent("auth:request-login"));
        throw new Error("unauthorized");
      }
      if (!res.ok) throw new Error("delete failed");
      const result = await res.json();
      await fetchPet();
      if (typeof window.toast === "function") {
        window.toast(result.deleted ? "已删除这条咨询记忆" : "这条记忆已不存在");
      }
    } catch {
      if (typeof window.toast === "function") window.toast("记忆删除失败，请稍后重试");
    } finally {
      setMemoryMutationPending(false);
    }
  }

  function setMemoryMutationPending(pending) {
    memoryMutationPending = pending;
    memoryClear.disabled = pending;
    root.querySelectorAll(".pet-memory-delete").forEach((button) => {
      button.disabled = pending;
    });
  }

  async function clearMemories() {
    if (memoryMutationPending) return;
    if (!window.confirm("确定清空獬豸保存的全部咨询记忆吗？此操作无法撤销。")) return;
    const id = getClientId();
    if (id.length < 8) return;
    setMemoryMutationPending(true);
    try {
      const res = await fetch(`${API_BASE}/api/pet/memories`, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client_id: id }),
      });
      if (res.status === 401) {
        window.dispatchEvent(new CustomEvent("auth:request-login"));
        throw new Error("unauthorized");
      }
      if (!res.ok) throw new Error("delete failed");
      const result = await res.json();
      await fetchPet();
      if (typeof window.toast === "function") {
        window.toast(result.deleted ? `已清空 ${result.deleted} 条咨询记忆` : "当前没有可清空的记忆");
      }
    } catch {
      if (typeof window.toast === "function") window.toast("记忆清空失败，请稍后重试");
    } finally {
      setMemoryMutationPending(false);
    }
  }
  memoryClear.addEventListener("click", clearMemories);
  window.addEventListener("pet:clear-memories", clearMemories);

  // ---- 账户联动：未登录时面板引导登录；登录/切换账户后重拉成长数据 ----
  loginNote.querySelector(".pet-login-btn").addEventListener("click", () => {
    window.dispatchEvent(new CustomEvent("auth:request-login"));
  });
  window.addEventListener("auth:changed", () => { fetchPet(); });

  // ---- 聊天联动事件（app.js / court.js 派发） ----
  window.addEventListener("pet:ask", () => {
    markOnboardingSeen();
    generating = true;
    lastActivity = Date.now();
    setState("thinking");
    say("思考中", 0);                       // 立即出现，之后由 status 事件更新
    bubble.classList.add("thinking");
  });
  window.addEventListener("pet:status", (e) => {
    if (generating) say(e.detail && e.detail.text ? e.detail.text : "思考中…", 0);
  });
  window.addEventListener("pet:answer", (e) => {
    generating = false;
    bubble.classList.remove("thinking");
    hideBubble();
    const pet = e.detail && e.detail.pet;
    let gain = null;
    if (pet && Number.isFinite(pet.exp)) {
      if (Number.isFinite(pet.gained) && pet.gained > 0) gain = pet.gained;
      else if (Number.isFinite(lastExp) && pet.exp > lastExp) gain = pet.exp - lastExp;
      lastExp = pet.exp;
    }
    if (pet) {
      const previousStage = stageIndex;
      stageIndex = pet.stage_index;
      renderPet(pet);
      growthFeedback(pet, previousStage, gain, "回答完成");
    } else {
      setState("happy");
      say("回答完成", 2400);
    }
    lastActivity = Date.now();
    // 仅在用户正在查看面板且主动开启记忆时补拉一次，避免每次回答固定轮询。
    if (badge.getAttribute("aria-expanded") === "true" && isMemoryEnabled()) {
      setTimeout(() => {
        if (badge.getAttribute("aria-expanded") === "true") fetchPet();
      }, 5000);
    }
  });
  window.addEventListener("pet:error", () => {
    generating = false;
    bubble.classList.remove("thinking");
    hideBubble();
    errorShakeUntil = performance.now() + 520;
    setState("idle");
    say("刚刚出了点问题，请稍后再试", 3000);
  });
  window.addEventListener("pet:pet", (e) => {
    const pet = e.detail && e.detail.pet;
    if (!pet) return;
    const previousStage = stageIndex;
    stageIndex = pet.stage_index;
    if (Number.isFinite(pet.exp)) lastExp = pet.exp;
    renderPet(pet);
    const gain = Number.isFinite(pet.gained) && pet.gained > 0 ? pet.gained : null;
    growthFeedback(pet, previousStage, gain, "庭审完成");
  });

  // ---- 位置：fixed 定位（视口坐标系），可拖到屏幕任意位置停留；默认跟随 composer-dock ----
  const dock = chatArea.querySelector(".composer-dock");
  let dragMoved = false;   // 拖拽/恢复过位置后，不再自动跟随 dock 高度
  function clampRoot() {
    const r = root.getBoundingClientRect();
    root.style.left = `${Math.max(0, Math.min(r.left, window.innerWidth - root.offsetWidth))}px`;
    root.style.top = `${Math.max(0, Math.min(r.top, window.innerHeight - root.offsetHeight))}px`;
  }
  function dockBottom() {
    if (!dock) return 150;
    const dr = dock.getBoundingClientRect();
    return Math.max(12, window.innerHeight - dr.bottom + 12);
  }
  if (dock && "ResizeObserver" in window) {
    const place = () => {
      if (!dragMoved) root.style.bottom = `${dockBottom()}px`;
      placePanel();
    };
    new ResizeObserver(place).observe(dock);
    place();
  }

  function resetPosition() {
    dragMoved = false;
    root.style.removeProperty("left");
    root.style.removeProperty("top");
    root.style.removeProperty("right");
    root.style.bottom = `${dockBottom()}px`;
    resetButton.hidden = true;
    placePanel();
    try { localStorage.removeItem(POSITION_KEY); } catch { /* 仅影响持久化 */ }
    setState("happy");
    say("我回到原来的位置啦", 2400);
  }
  resetButton.addEventListener("click", resetPosition);

  try {
    const saved = JSON.parse(localStorage.getItem(POSITION_KEY) || "null");
    if (saved && Number.isFinite(saved.l) && Number.isFinite(saved.t)) {
      dragMoved = true;
      resetButton.hidden = false;
      root.style.right = "auto";
      root.style.bottom = "auto";
      root.style.left = `${saved.l}px`;
      root.style.top = `${saved.t}px`;
      clampRoot();
    }
  } catch { /* 位置记忆损坏则回到默认位置 */ }
  window.addEventListener("resize", () => {
    if (dragMoved) clampRoot();
    else root.style.bottom = `${dockBottom()}px`;
    placePanel();
  });

  // 拖拽：Pointer Events 统一鼠标/触摸；位移 <5px 视为点击（开面板）
  let suppressClick = false;
  {
    let pressed = false, dragging = false, pressX = 0, pressY = 0, baseL = 0, baseT = 0;
    badge.addEventListener("pointerdown", (e) => {
      if (e.pointerType === "mouse" && e.button !== 0) return;
      pressed = true;
      dragging = false;
      suppressClick = false;
      pressX = e.clientX; pressY = e.clientY;
      const r = root.getBoundingClientRect();   // fixed 定位：rect 即视口坐标
      baseL = r.left; baseT = r.top;
      try { badge.setPointerCapture(e.pointerId); } catch { /* 无捕获则仅在徽章内可拖 */ }
    });
    badge.addEventListener("pointermove", (e) => {
      if (!pressed) return;
      const dx = e.clientX - pressX, dy = e.clientY - pressY;
      if (!dragging) {
        if (Math.hypot(dx, dy) < 5) return;
        dragging = true; petDragging = true; suppressClick = true; dragMoved = true;
        resetButton.hidden = false;
        root.style.right = "auto";
        root.style.bottom = "auto";
      }
      root.style.left = `${baseL + dx}px`;
      root.style.top = `${baseT + dy}px`;
      clampRoot();
      placePanel();   // 面板开着拖动时跟着宠物走
      lastActivity = Date.now();
      if (state === "sleepy") setState("idle");
    });
    const endDrag = () => {
      if (dragging) {
        petDragging = false;
        try {
          localStorage.setItem(POSITION_KEY,
            JSON.stringify({ l: root.offsetLeft, t: root.offsetTop }));
        } catch { /* 隐私模式等存储不可用 */ }
      }
      pressed = false;
      dragging = false;
      if (stageArtIndex === 0) updateGaze();
    };
    badge.addEventListener("pointerup", endDrag);
    badge.addEventListener("pointercancel", endDrag);
  }

  /* ================= 3D 渲染层 ================= */

  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  function buildScene(THREE) {
    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({
        canvas, alpha: true, antialias: true,
      });
    } catch { return null; }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.6));

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 30);
    camera.position.set(0, 1.58, 5.0);
    camera.lookAt(0, 1.23, 0);

    scene.add(new THREE.HemisphereLight(0xfff8ee, 0xcbb9a6, 1.2));
    const key = new THREE.DirectionalLight(0xffffff, 1.55);
    key.position.set(2.2, 3.5, 2.5);
    scene.add(key);
    const rim = new THREE.DirectionalLight(0xffe2b8, 0.55);
    rim.position.set(-2.5, 1.6, -2);
    scene.add(rim);

    // 卡通三阶渐变着色
    const grad = new THREE.DataTexture(new Uint8Array([92, 168, 236]), 3, 1, THREE.RedFormat);
    grad.minFilter = grad.magFilter = THREE.NearestFilter;
    grad.needsUpdate = true;
    const toon = (hex, extra) => new THREE.MeshToonMaterial(
      Object.assign({ color: hex, gradientMap: grad }, extra));
    const SP = (r, seg = 20) => new THREE.SphereGeometry(r, seg, Math.max(8, Math.round(seg * 0.7)));
    const M = (geo, mat, x, y, z) => {
      const mesh = new THREE.Mesh(geo, mat);
      mesh.position.set(x, y, z);
      return mesh;
    };

    const whiteM = new THREE.MeshBasicMaterial({ color: 0xffffff });
    const brownM = toon(0x6b4a2f), darkM = toon(0x2e2014), pinkM = toon(0xf0c9b2);

    // ---- 形态配置：宝可梦式进化——每阶段重建轮廓 / 角型 / 鬃毛 / 配色 ----
    // head: 头身比 | torso: [x,y,z] 体形 | horn: 角型 | side: 侧角 | ears: 耳态
    // mane: [主色, 副色, 缩放] | fur: 毛色 | sharp: 锐利眼 | plume: 羽状尾 | pads: 肩甲
    const STAGE_FORMS = [
      { key: "cub",  head: 1.26, torso: [0.98, 0.90, 0.96], horn: "stub",
        side: "none", ears: "droop", mane: [0xc8281e, 0xa81c12, 0.84],
        fur: 0xf6efe2, sharp: false, plume: false, pads: false },
      { key: "dawn", head: 1.10, torso: [1.00, 0.94, 1.04], horn: "spiral",
        side: "small", ears: "up", mane: [0xc8281e, 0xa81c12, 0.94],
        fur: 0xf6efe2, sharp: false, plume: false, pads: false },
      { key: "keen", head: 1.00, torso: [0.92, 1.00, 0.98], horn: "straight",
        side: "long", ears: "tall", mane: [0xd0542c, 0xa83518, 1.0],
        fur: 0xf6efe2, sharp: true, plume: false, pads: false },
      { key: "just", head: 0.96, torso: [1.06, 1.04, 1.00], horn: "blade",
        side: "gold", ears: "tall", mane: [0xa81c12, 0x8f150c, 1.12],
        fur: 0xf3ecdc, sharp: true, plume: true, pads: true },
      { key: "sage", head: 0.96, torso: [1.10, 1.06, 1.02], horn: "crown",
        side: "gold", ears: "tall", mane: [0xe0aa4a, 0xc9962f, 1.16],
        fur: 0xf9f3e3, sharp: true, plume: true, pads: true },
      { key: "luck", head: 0.98, torso: [1.14, 1.10, 1.04], horn: "grand",
        side: "sweep", ears: "tall", mane: [0xf0c060, 0xd9a63e, 1.26],
        fur: 0xfbf6e9, sharp: true, plume: true, pads: true },
    ];

    const P = { THREE, renderer, scene, camera };

    class Spiral extends THREE.Curve {
      getPoint(t, v) {
        const a = t * Math.PI * 5.2;
        const r = 0.028 + t * 0.1;
        return (v || new THREE.Vector3()).set(Math.cos(a) * r, t * 0.58, Math.sin(a) * r);
      }
    }

    function rebuildPet(level) {
      if (P.pet) {
        scene.remove(P.pet);
        P.pet.traverse((o) => { if (o.geometry) o.geometry.dispose(); });
      }
      const cfg = STAGE_FORMS[level];
      // 每个形态独立配色材质，避免发光/换色跨阶段串色
      const furM = toon(cfg.fur), furLightM = toon(0xfdf8ee),
        maneM = toon(cfg.mane[0]), maneDarkM = toon(cfg.mane[1]),
        goldM = toon(0xe2a63d), eyeM = toon(0x2e2014);

      const pet = new THREE.Group();
      scene.add(pet);

      // ---- 身体（坐姿） ----
      const torso = M(SP(0.55, 28), furM, 0, 0.52, 0);
      torso.scale.set(cfg.torso[0], cfg.torso[1], cfg.torso[2]);
      pet.add(torso);
      pet.add(M(SP(0.4, 24), furLightM, 0, 0.46, 0.24));           // 肚皮
      const haunchL = M(SP(0.27), furM, -0.44, 0.3, -0.08);
      const haunchR = haunchL.clone();
      haunchR.position.x = 0.44;
      pet.add(haunchL, haunchR);
      const pawL = M(SP(0.13), furLightM, -0.22, 0.13, 0.42);
      pawL.scale.set(1, 0.72, 1.2);
      const pawR = pawL.clone();
      pawR.position.x = 0.22;
      pet.add(pawL, pawR);

      // 肩甲（执正+）：金色半球压在肩上，成年战士感
      if (cfg.pads) {
        const padL = M(SP(0.2, 14), goldM, -0.5, 0.72, 0.04);
        padL.scale.set(1, 0.8, 1);
        const padR = padL.clone();
        padR.position.x = 0.5;
        pet.add(padL, padR);
      }

      // 尾：幼体绒球 / 成年羽状
      const tail = new THREE.Group();
      tail.position.set(0, 0.58, -0.48);
      pet.add(tail);
      if (cfg.plume) {
        const plume = M(new THREE.ConeGeometry(0.13, 0.55, 10), maneM, 0, 0.16, -0.3);
        plume.rotation.x = -2.35;
        tail.add(plume);
        const tip = M(SP(0.09, 10), maneDarkM, 0, 0.34, -0.5);
        tail.add(tip);
      } else {
        const tailFluff = M(SP(0.15 + level * 0.012, 16), maneM, 0, 0.06, -0.16);
        tailFluff.scale.set(1, 0.9, 1.3);
        tail.add(tailFluff);
      }

      // ---- 头（枢轴在颈部；头身比随阶段变化） ----
      const headG = new THREE.Group();
      headG.position.set(0, 0.98, 0.02);
      headG.scale.setScalar(cfg.head);
      pet.add(headG);
      headG.add(M(SP(0.6, 32), furM, 0, 0.42, 0.04));               // 颅
      const cheekR0 = 0.21 - level * 0.012;                          // 越长大腮绒越小
      const cheekL = M(SP(Math.max(0.13, cheekR0), 14), furM, -0.42, 0.3, 0.26);
      const cheekR = cheekL.clone();
      cheekR.position.x = 0.42;
      headG.add(cheekL, cheekR);
      headG.add(M(SP(0.25, 18), furLightM, 0, 0.28, 0.5));          // 口鼻
      headG.add(M(SP(0.055, 10), darkM, 0, 0.35, 0.72));            // 鼻

      // 眼睛：明辨起收窄成锐利眼；独占材质便于发光
      function makeEye(sx) {
        const g = new THREE.Group();
        g.position.set(0.25 * sx, 0.55, 0.53);
        const ball = M(SP(0.088, 16), eyeM, 0, 0, 0);
        if (cfg.sharp) ball.scale.y = 0.62;
        g.add(ball);
        g.add(M(SP(0.03, 10), whiteM, 0.028, cfg.sharp ? 0.01 : 0.034, 0.062));
        headG.add(g);
        return g;
      }
      const eyeL = makeEye(-1), eyeR = makeEye(1);

      // ---- 独角：按阶段换角型 ----
      let horn;
      if (cfg.horn === "stub" || cfg.horn === "spiral") {
        // 螺旋角：幼獬为萌芽短螺旋，启蒙长成完整螺旋
        const short = cfg.horn === "stub";
        const N = short ? 16 : 30;
        horn = new THREE.InstancedMesh(SP(0.05, 10), goldM, N);
        const m4 = new THREE.Matrix4(), q0 = new THREE.Quaternion(),
          sv = new THREE.Vector3(), pv = new THREE.Vector3();
        for (let i = 0; i < N; i++) {
          const t = i / (N - 1);
          new Spiral().getPoint(t, pv);
          if (short) { pv.y *= 0.5; pv.x *= 0.7; pv.z *= 0.7; }
          sv.setScalar((short ? 0.3 : 0.34) + t * (short ? 0.5 : 0.78));
          m4.compose(pv, q0, sv);
          horn.setMatrixAt(i, m4);
        }
        horn.position.set(0, 0.95, 0.26);
        horn.rotation.x = -0.12;
      } else if (cfg.horn === "straight" || cfg.horn === "blade") {
        // 明辨：直锥角；执正：更长更宽的剑角
        const blade = cfg.horn === "blade";
        horn = new THREE.Mesh(
          new THREE.ConeGeometry(blade ? 0.082 : 0.072, blade ? 0.8 : 0.58, 12), goldM);
        horn.position.set(0, 1.12, 0.22);
        horn.rotation.x = -0.18;
      } else {
        // 神判/祥瑞：三叉王冠角——中柱 + 两侧斜角
        horn = new THREE.Group();
        const big = cfg.horn === "grand";
        const center = M(new THREE.ConeGeometry(0.075, big ? 0.85 : 0.66, 12), goldM, 0, 1.14, 0.2);
        center.rotation.x = -0.14;
        horn.add(center);
        for (const sx of [-1, 1]) {
          const sideCone = M(new THREE.ConeGeometry(0.045, big ? 0.48 : 0.36, 10),
            goldM, 0.22 * sx, 1.0, 0.14);
          sideCone.rotation.z = -0.55 * sx;
          sideCone.rotation.x = -0.1;
          horn.add(sideCone);
        }
      }
      headG.add(horn);

      // ---- 侧弯角：幼獬无，启蒙萌芽，成年转金 ----
      if (cfg.side !== "none") {
        const scale = { small: 0.7, long: 1.0, gold: 1.15, sweep: 1.25 }[cfg.side];
        const mat = (cfg.side === "gold" || cfg.side === "sweep") ? goldM : brownM;
        for (const sx of [-1, 1]) {
          const pts = [
            new THREE.Vector3(0, 0, 0),
            new THREE.Vector3(0.05 * sx, 0.12 * scale, -0.05),
            new THREE.Vector3((cfg.side === "sweep" ? 0.14 : 0.03) * sx,
              0.27 * scale, -0.2 * scale),
          ];
          const tube = new THREE.Mesh(
            new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 12, 0.04 * scale, 7), mat);
          tube.position.set(0.52 * sx, 0.56, -0.06);
          tube.rotation.z = -0.5 * sx;
          headG.add(tube);
        }
      }

      // ---- 耳朵：幼獬垂耳，此后立耳渐高 ----
      function makeEar(sx) {
        const g = new THREE.Group();
        g.position.set(0.38 * sx, 0.92, 0.02);
        const earScale = cfg.ears === "droop" ? 0.8 : cfg.ears === "tall" ? 1.2 : 1;
        const outer = M(SP(0.12, 12), furM, 0, 0.1, 0);
        outer.scale.set(0.7, 1.4 * earScale, 0.45);
        const inner = M(SP(0.07, 10), pinkM, 0, 0.1, 0.05);
        inner.scale.set(0.5, 1.15 * earScale, 0.28);
        g.add(outer, inner);
        g.rotation.z = (cfg.ears === "droop" ? -1.0 : -0.55) * sx;
        headG.add(g);
        return g;
      }
      const earL = makeEar(-1), earR = makeEar(1);

      // ---- 鬃毛围领：环 + 绒球，配色随阶段（红 → 深红 → 金） ----
      const mane = new THREE.Group();
      mane.position.set(0, 0.04, 0);
      headG.add(mane);
      const collar = new THREE.Mesh(new THREE.TorusGeometry(0.5, 0.16, 10, 26), maneM);
      collar.rotation.x = Math.PI / 2 - 0.12;
      collar.scale.set(1.04, 1, 0.96);
      mane.add(collar);
      const fluffN = 12 + level;
      for (let i = 0; i < fluffN; i++) {
        const a = (i / fluffN) * Math.PI * 2 + 0.26;
        const fluff = M(SP(0.17 + (i % 3) * 0.03, 12), i % 2 ? maneDarkM : maneM,
          Math.cos(a) * 0.52, Math.sin(a * 2) * 0.07, Math.sin(a) * 0.5);
        fluff.scale.set(1, 0.95, 0.85);
        mane.add(fluff);
      }
      mane.scale.setScalar(cfg.mane[2]);

      // ---- 阶段特效：金色轮廓光环与旋转光环（透明度由 applyStageAppearance 控制） ----
      const aura = new THREE.Mesh(SP(1.02, 24), new THREE.MeshBasicMaterial({
        color: 0xffd27a, transparent: true, opacity: 0,
        blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.BackSide,
      }));
      aura.position.y = 0.62;
      aura.visible = false;
      pet.add(aura);
      const halo = new THREE.Mesh(new THREE.TorusGeometry(0.92, 0.018, 8, 48),
        new THREE.MeshBasicMaterial({
          color: 0xffc84d, transparent: true, opacity: 0,
          blending: THREE.AdditiveBlending, depthWrite: false,
        }));
      halo.position.y = 1.05;
      halo.rotation.x = Math.PI / 2.4;
      halo.visible = false;
      pet.add(halo);

      Object.assign(P, {
        pet, torso, headG, eyeL, eyeR, tail, earL, earR, horn, mane,
        aura, halo, goldM, eyeM,
        earBaseL: earL.rotation.z, earBaseR: earR.rotation.z,
        level,
      });
    }

    rebuildPet(0);
    P.rebuildPet = rebuildPet;
    return P;
  }


  // ---- 六阶段形态进化：切换阶段时整个模型按 STAGE_FORMS 重建 ----
  function applyStageAppearance(P, index) {
    const level = clamp(Number.isFinite(index) ? index : 0, 0, 5);
    if (P.level !== level && typeof P.rebuildPet === "function") P.rebuildPet(level);

    // 独角金辉：启蒙起随阶段增亮
    P.goldM.emissive.setHex(0xffc23d);
    P.goldM.emissiveIntensity = level >= 1 ? 0.10 + (level - 1) * 0.14 : 0;

    // 目光如炬：明辨起双眼泛琥珀光（eyeM 为眼睛独占材质，不影响鼻头）
    P.eyeM.emissive.setHex(0xffb347);
    P.eyeM.emissiveIntensity = level >= 2 ? 0.35 + (level - 2) * 0.18 : 0;

    // 金色轮廓光环：执正起出现，神判/祥瑞更亮
    const auraOn = level >= 3;
    P.aura.visible = auraOn;
    const auraBase = auraOn ? 0.11 + (level - 2) * 0.07 : 0;
    P.aura.material.opacity = auraBase;
    P.aura.material.userData.baseOpacity = auraBase;

    // 旋转光环：神判浮现、祥瑞全亮
    const haloOn = level >= 4;
    P.halo.visible = haloOn;
    const haloBase = level >= 5 ? 0.85 : 0.4;
    P.halo.material.opacity = haloBase;
    P.halo.material.userData.baseOpacity = haloBase;
  }

  let petScene = null;
  let threeLoadPromise = null;
  let loopRunning = false;

  function startLoop(P) {
    if (loopRunning) return;
    loopRunning = true;
    let headYaw = 0, headPitch = 0, headRoll = 0;
    let last = performance.now();
    let lastFrameAt = 0;
    let nextBlinkAt = performance.now() + 2800;
    let blinkUntil = 0;

    function frame(now) {
      if (!loopRunning) return;
      if (document.hidden || root.style.display === "none" || !canvas.clientWidth) {
        loopRunning = false;
        return;
      }
      requestAnimationFrame(frame);
      const frameInterval = reduceMotion ? 100 : 1000 / 30;
      if (now - lastFrameAt < frameInterval) return;
      lastFrameAt = now;
      const t = now / 1000;
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;

      // 各部位目标
      let ty = 0, tp = 0, tr = 0;       // 头 yaw/pitch/roll
      let ex = 0, ey = 0;               // 眼球偏移
      let breatheAmp = 0.018, breatheSpeed = 1.7;
      let wagSpeed = 2 + stageIndex * 0.22, earFlap = 0.05, hopH = 0, squint = 1;

      // 阶段特效动画：光环缓旋、金辉呼吸（基础值存 userData，避免连乘漂移）
      if (P.halo && P.halo.visible) {
        P.halo.rotation.z += dt * 0.55;
        P.halo.material.opacity =
          (P.halo.material.userData.baseOpacity || 0.4) * (0.88 + 0.12 * Math.sin(t * 2.2));
      }
      if (P.aura && P.aura.visible) {
        P.aura.material.opacity =
          (P.aura.material.userData.baseOpacity || 0.06) * (0.85 + 0.15 * Math.sin(t * 1.6));
      }

      if (state === "sleepy") {
        tp = 0.5; squint = 0.06;
        breatheAmp = 0.032; breatheSpeed = 0.75; wagSpeed = 0;
      } else {
        if (Math.hypot(look.x, look.y) > 0.05) {
          ty = clamp(look.x * 1.9, -0.68, 0.68);
          tp = clamp(look.y * 1.1, -0.26, 0.34);
        }
        // 眼球先行：比头多转一点，产生「细节在动」的层次
        ex = clamp(look.x * 2.1, -1, 1) * 0.065;
        ey = clamp(-look.y * 1.7, -1, 1) * 0.032;

        if (state === "typing") {
          if (!reduceMotion) {
            ty += Math.sin(t * 8.5) * 0.16;
            ex += Math.sin(t * 8.5 + 1.2) * 0.022;
          }
          earFlap = 0.14;
        } else if (state === "thinking") {
          tp += 0.24;
          ex = -0.032; ey = 0.028;
          tr = reduceMotion ? 0.12 : 0.12 + Math.sin(t * 1.3) * 0.05;
          wagSpeed = 0.7;
        } else if (state === "happy") {
          const since = (now - stateEnteredAt) / 1000;
          if (!reduceMotion) {
            hopH = Math.abs(Math.sin(since * Math.PI * 2.6)) * 0.2 * Math.max(0, 1 - since / 1.4);
          }
          earFlap = 0.3; wagSpeed = 7; ey = 0.02;
        }
        if (now < errorShakeUntil && !reduceMotion) ty += Math.sin(t * 34) * 0.26;
      }

      // 平滑趋近（帧率无关阻尼；reduced-motion 直接到位）
      const k = reduceMotion ? 1 : 1 - Math.pow(0.002, dt);
      headYaw += (ty - headYaw) * k;
      headPitch += (tp - headPitch) * k;
      headRoll += (tr - headRoll) * k;
      P.headG.rotation.set(headPitch, headYaw, headRoll, "YXZ");

      // 眼球位移 + 眨眼
      P.eyeL.position.set(-0.25 + ex, 0.55 + ey, 0.53);
      P.eyeR.position.set(0.25 + ex, 0.55 + ey, 0.53);
      let blink = squint;
      if (!reduceMotion && state !== "sleepy") {
        if (now > nextBlinkAt) {
          blinkUntil = now + 150;
          nextBlinkAt = now + 2400 + Math.random() * 2600;
        }
        if (now < blinkUntil) blink = 0.06;
      }
      P.eyeL.scale.y = P.eyeR.scale.y = blink;

      // 呼吸 / 弹跳 / 尾巴 / 耳朵
      const br = reduceMotion ? 0 : Math.sin(t * Math.PI * breatheSpeed) * breatheAmp;
      P.torso.scale.set(1 + br * 0.6, 0.94 - br, 0.96 + br * 0.6);
      P.pet.position.y = hopH;
      P.tail.rotation.y = !reduceMotion && wagSpeed ? Math.sin(t * wagSpeed * 2.4) * 0.5 : 0;
      const flap = reduceMotion ? 0 : Math.sin(t * 3.1) * earFlap;
      P.earL.rotation.z = P.earBaseL + flap;
      P.earR.rotation.z = P.earBaseR - flap;

      P.renderer.render(P.scene, P.camera);
    }
    requestAnimationFrame(frame);
  }

  function cancelScheduled3D() {
    if (!idleLoadHandle) return;
    if (idleLoadKind === "idle" && "cancelIdleCallback" in window) {
      window.cancelIdleCallback(idleLoadHandle);
    } else {
      clearTimeout(idleLoadHandle);
    }
    idleLoadHandle = 0;
    idleLoadKind = "";
  }

  function schedule3D() {
    if (idleLoadHandle || petScene || threeLoadPromise || root.style.display === "none") return;
    if (stageArtIndex >= 0 || stageArtLoading) return;
    const load = () => {
      idleLoadHandle = 0;
      idleLoadKind = "";
      ensure3D();
    };
    if ("requestIdleCallback" in window) {
      idleLoadKind = "idle";
      idleLoadHandle = window.requestIdleCallback(load, { timeout: 1400 });
    } else {
      idleLoadKind = "timeout";
      idleLoadHandle = setTimeout(load, 500);
    }
  }

  // 仅在宠物可见时加载 Three.js；隐藏偏好不再支付下载和 WebGL 初始化成本。
  function ensure3D() {
    cancelScheduled3D();
    if (root.style.display === "none") return Promise.resolve(null);
    if (stageArtIndex >= 0 || stageArtLoading) return Promise.resolve(null);
    if (petScene) {
      startLoop(petScene);
      return Promise.resolve(petScene);
    }
    if (threeLoadPromise) return threeLoadPromise;
    threeLoadPromise = import(`${API_BASE}/assets/vendor/three.module.min.js?v=0186c`)
      .then((m) => {
        if (stageArtIndex >= 0 || stageArtLoading || root.style.display === "none") {
          threeLoadPromise = null;
          return null;
        }
        const P = buildScene(m);
        if (!P) {
          threeLoadPromise = null;
          return null;
        }
        petScene = P;
        applyStageAppearance(P, stageIndex);
        badge.classList.remove("pet-fallback");
        const place = () => {
          const w = badge.clientWidth || 96, h = badge.clientHeight || 96;
          P.renderer.setSize(w, h, false);
          P.camera.aspect = w / h;
          P.camera.updateProjectionMatrix();
        };
        if ("ResizeObserver" in window) new ResizeObserver(place).observe(badge);
        place();
        startLoop(P);
        return P;
      })
      .catch(() => {
        threeLoadPromise = null;
        return null;
      }); // 保持静态 sprite 回退，下一次显式交互允许重试
    return threeLoadPromise;
  }

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) loopRunning = false;
    else if (petScene && root.style.display !== "none") startLoop(petScene);
    else if (root.style.display !== "none") schedule3D();
  });

  canvas.addEventListener("webglcontextlost", (event) => {
    event.preventDefault();
    loopRunning = false;
    badge.classList.add("pet-fallback");
  });
  canvas.addEventListener("webglcontextrestored", () => {
    if (!petScene || root.style.display === "none") return;
    badge.classList.remove("pet-fallback");
    startLoop(petScene);
  });

  badge.addEventListener("pointerenter", ensure3D);
  badge.addEventListener("focus", ensure3D);
})();
