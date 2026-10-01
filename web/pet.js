/* 法晓 · 獬豸桌宠：sprite 帧动画 + 眼随鼠标 + 聊天联动 + 成长进度
 *
 * 帧资产 web/pet/*.png（MiniMax 生成，白底统一裁剪）。
 * 与 app.js 通过 window 自定义事件解耦：
 *   pet:ask    { }            —— 用户发出问题
 *   pet:status { text }       —— 检索/生成阶段文案
 *   pet:answer { pet }        —— 回答完成（pet 为后端成长状态）
 *   pet:error  { }            —— 回答失败
 *   pet:pet    { pet }        —— 庭审完成（成长状态）
 */
(function () {
  "use strict";

  const API_BASE = location.pathname.replace(/\/[^/]*$/, "");
  const ASSET = (name) => `${API_BASE}/assets/pet/${name}.png`;

  const FRAMES = ["idle", "blink", "look-left", "look-right",
    "happy", "thinking", "typing", "sleepy"];
  const SLEEP_AFTER_MS = 60000;
  const LOOK_DEADZONE = 90;

  const chatArea = document.querySelector(".chat-area");
  if (!chatArea) return;

  // ---- DOM ----
  const root = document.createElement("div");
  root.className = "pet-root";
  root.innerHTML = `
    <div class="pet-panel" role="dialog" aria-label="獬豸成长面板">
      <div class="pet-panel-head"><strong class="pet-stage-name">幼獬</strong>
        <small class="pet-exp-text">0 经验</small></div>
      <p class="pet-panel-title">初识人间，蹒跚学步</p>
      <div class="pet-bar"><div class="pet-bar-fill" style="width:0%"></div></div>
      <div class="pet-bar-note"><span class="pet-progress-note"></span>
        <span class="pet-next-note"></span></div>
      <div class="pet-stats">
        <div class="pet-stat"><b class="pet-qa-n">0</b><span>完成问答</span></div>
        <div class="pet-stat"><b class="pet-court-n">0</b><span>完成庭审</span></div>
      </div>
      <div class="pet-stages-row"></div>
    </div>
    <div class="pet-bubble" aria-hidden="true"></div>
    <button class="pet-badge" type="button" data-stage="cub"
      aria-label="法晓的獬豸宠物，点击查看成长">
      <div class="pet-sprite"></div>
      <span class="pet-stage-chip">幼獬</span>
    </button>`;
  chatArea.appendChild(root);
  const badge = root.querySelector(".pet-badge");
  const sprite = root.querySelector(".pet-sprite");
  const chip = root.querySelector(".pet-stage-chip");
  const bubble = root.querySelector(".pet-bubble");
  const panel = root.querySelector(".pet-panel");

  // ---- 帧加载 ----
  const loaded = new Set();
  function loadFrame(name) {
    if (loaded.has(name)) return;
    loaded.add(name);
    const img = new Image();
    img.src = ASSET(name);
  }
  loadFrame("idle");
  const idlePreload = () => FRAMES.forEach(loadFrame);
  if ("requestIdleCallback" in window) {
    requestIdleCallback(idlePreload, { timeout: 4000 });
  } else {
    setTimeout(idlePreload, 2000);
  }

  // ---- 状态机 ----
  let state = "idle";
  let blinkTimer = 0;
  let stateTimer = 0;
  let lastActivity = Date.now();
  let generating = false;

  function showFrame(name) { sprite.style.backgroundImage = `url("${ASSET(name)}")`; }
  showFrame("idle");

  function clearTimers() {
    clearTimeout(blinkTimer);
    clearTimeout(stateTimer);
  }

  function setState(next) {
    clearTimers();
    state = next;
    badge.classList.remove("anim-wiggle", "anim-bounce", "anim-shake", "anim-float");
    if (next === "idle") {
      showFrame("idle");
      badge.classList.add("anim-float");
      scheduleBlink();
    } else if (next === "typing") {
      showFrame("typing");
      badge.classList.add("anim-wiggle");
    } else if (next === "thinking") {
      showFrame("thinking");
      badge.classList.add("anim-wiggle");
    } else if (next === "happy") {
      showFrame("happy");
      badge.classList.add("anim-bounce");
      stateTimer = setTimeout(() => setState("idle"), 2200);
    } else if (next === "sleepy") {
      showFrame("sleepy");
    } else if (next === "look-left" || next === "look-right") {
      showFrame(next);
    }
  }

  function scheduleBlink() {
    blinkTimer = setTimeout(() => {
      if (state !== "idle") return;
      showFrame("blink");
      setTimeout(() => { if (state === "idle") showFrame("idle"); }, 170);
      scheduleBlink();
    }, 2400 + Math.random() * 2600);
  }
  setState("idle");

  // ---- 眼随鼠标：方向帧 + 容器微倾 ----
  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let mouse = null;
  let rafPending = false;

  function applyLook() {
    rafPending = false;
    if (!mouse || generating || state === "typing" || state === "happy") return;
    const rect = badge.getBoundingClientRect();
    const cx = rect.left + rect.width / 2;
    const cy = rect.top + rect.height / 2;
    const dx = mouse.x - cx;
    const dy = mouse.y - cy;
    if (Math.hypot(dx, dy) < LOOK_DEADZONE) {
      if (state === "look-left" || state === "look-right") setState("idle");
      sprite.style.transform = "";
      return;
    }
    const dir = dx < 0 ? "look-left" : "look-right";
    if (state === "idle" || state === "look-left" || state === "look-right") {
      setState(dir);
      clearTimers(); // 方向帧不参与眨眼调度
      const tilt = Math.max(-1, Math.min(1, dx / 420)) * 5;
      const lift = Math.max(-1, Math.min(1, dy / 420)) * 3;
      sprite.style.transform = reduceMotion
        ? "" : `rotate(${tilt.toFixed(1)}deg) translateY(${(-lift).toFixed(1)}px)`;
    }
  }

  if (!reduceMotion && matchMedia("(pointer: fine)").matches) {
    window.addEventListener("mousemove", (e) => {
      mouse = { x: e.clientX, y: e.clientY };
      lastActivity = Date.now();
      if (state === "sleepy") setState("idle");
      if (!rafPending) {
        rafPending = true;
        requestAnimationFrame(applyLook);
      }
    }, { passive: true });
  }

  // ---- 打字动画 ----
  const qaInput = document.getElementById("qa-input");
  if (qaInput) {
    let typeTimer = 0;
    qaInput.addEventListener("input", () => {
      lastActivity = Date.now();
      if (generating) return;
      if (state !== "typing") setState("typing");
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

  function renderPet(pet) {
    if (!pet) return;
    chip.textContent = pet.stage_name;
    chip.parentElement.dataset.stage = pet.stage;
    root.querySelector(".pet-stage-name").textContent = pet.stage_name;
    root.querySelector(".pet-panel-title").textContent = pet.stage_title;
    root.querySelector(".pet-exp-text").textContent = `${pet.exp} 经验`;
    root.querySelector(".pet-bar-fill").style.width = `${pet.progress}%`;
    root.querySelector(".pet-progress-note").textContent = `本阶段 ${pet.progress}%`;
    root.querySelector(".pet-next-note").textContent = pet.next_stage_name
      ? `还差 ${pet.next_need} 经验到「${pet.next_stage_name}」` : "已达最高阶段";
    root.querySelector(".pet-qa-n").textContent = pet.qa_count;
    root.querySelector(".pet-court-n").textContent = pet.court_count;
    const row = root.querySelector(".pet-stages-row");
    row.innerHTML = "";
    (pet.stages || []).forEach((s, i) => {
      const dot = document.createElement("span");
      dot.className = "pet-stage-dot" + (i <= pet.stage_index ? " reached" : "");
      dot.title = s.name;
      row.appendChild(dot);
    });
  }

  async function fetchPet() {
    const id = getClientId();
    if (id.length < 8) return;
    try {
      const res = await fetch(`${API_BASE}/api/pet?client_id=${encodeURIComponent(id)}`);
      if (res.ok) {
        const pet = await res.json();
        renderPet(pet);
        stageIndex = pet.stage_index;
      }
    } catch { /* 离线/后端不可用不影响宠物本体 */ }
  }
  fetchPet();

  function stageUp(pet, prevIndex) {
    if (pet && pet.stage_index > prevIndex) {
      setState("happy");
      say(`升级啦！獬豸成长为「${pet.stage_name}」`, 4200);
    }
  }
  let stageIndex = 0;

  // ---- 面板开关 ----
  badge.addEventListener("click", () => {
    const open = panel.classList.toggle("show");
    badge.setAttribute("aria-expanded", String(open));
    if (open) {
      fetchPet();
      setState("happy");
      setTimeout(() => { if (!generating) setState("idle"); }, 1400);
    }
  });
  document.addEventListener("click", (e) => {
    if (!root.contains(e.target)) panel.classList.remove("show");
  });

  // ---- 聊天联动事件（app.js / court.js 派发） ----
  window.addEventListener("pet:ask", () => {
    generating = true;
    lastActivity = Date.now();
    setState("thinking");
  });
  window.addEventListener("pet:status", (e) => {
    if (generating) say(e.detail && e.detail.text ? e.detail.text : "思考中…", 0);
  });
  window.addEventListener("pet:answer", (e) => {
    generating = false;
    hideBubble();
    const pet = e.detail && e.detail.pet;
    if (pet) {
      stageUp(pet, stageIndex);
      stageIndex = pet.stage_index;
      renderPet(pet);
    }
    if (state !== "happy") setState("happy");
    say("回答完成", 2400);
    lastActivity = Date.now();
  });
  window.addEventListener("pet:error", () => {
    generating = false;
    hideBubble();
    clearTimers();
    state = "idle";
    showFrame("idle");
    badge.classList.add("anim-shake");
    setTimeout(() => badge.classList.remove("anim-shake"), 500);
  });
  window.addEventListener("pet:pet", (e) => {
    const pet = e.detail && e.detail.pet;
    if (!pet) return;
    stageUp(pet, stageIndex);
    stageIndex = pet.stage_index;
    renderPet(pet);
  });

  // ---- 位置：跟随 composer-dock 高度 ----
  const dock = chatArea.querySelector(".composer-dock");
  if (dock && "ResizeObserver" in window) {
    const place = () => { root.style.bottom = `${dock.offsetHeight + 12}px`; };
    new ResizeObserver(place).observe(dock);
    place();
  }
})();
