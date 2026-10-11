/* 法晓 · 法律常识文章页：/basics/<key> 渲染正文、诉讼路线图与页脚导航 */

(function () {
  "use strict";

  const API_BASE = location.pathname.replace(/\/basics\/[^/]*$/, "");
  const ASK_BRIDGE_KEY = "faxiao_basics_ask";
  const RETURN_KEY = "faxiao_basics_return";
  const SVG_NS = "http://www.w3.org/2000/svg";

  const moduleKey = decodeURIComponent(
    location.pathname.replace(/\/+$/, "").split("/").pop() || "");
  const bodyEl = document.getElementById("article-body");
  const footEl = document.getElementById("article-foot");
  const errorEl = document.getElementById("article-error");
  const crumbEl = document.getElementById("article-crumb");

  // ---- 埋点：与首页 app.js 相同的静默批量上报，仅登录用户 ----
  let authed = false;
  const eventQueue = [];
  let flushTimer = 0;

  function track(name, props = {}) {
    if (!authed) return;
    eventQueue.push({ name, props });
    if (eventQueue.length >= 10) {
      flushEvents();
      return;
    }
    if (!flushTimer) flushTimer = setTimeout(flushEvents, 3000);
  }

  async function flushEvents() {
    flushTimer = 0;
    if (!eventQueue.length) return;
    const batch = eventQueue.splice(0, 20);
    try {
      await fetch(`${API_BASE}/api/events`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ events: batch }),
        keepalive: true,
      });
    } catch { /* 埋点失败不影响阅读 */ }
  }

  window.addEventListener("pagehide", flushEvents);
  fetch(`${API_BASE}/api/auth/me`)
    .then((res) => (res.ok ? res.json() : { user: null }))
    .then((data) => {
      authed = !!data.user;
      track("basics_article_viewed", { module: moduleKey });
    })
    .catch(() => { /* 未登录不上报 */ });

  // 回到首页时停在法律常识 Tab
  document.querySelectorAll('[data-return="basics"]').forEach((link) => {
    link.addEventListener("click", () => {
      try { sessionStorage.setItem(RETURN_KEY, "1"); } catch { /* 忽略 */ }
    });
  });

  document.getElementById("article-retry")?.addEventListener("click", loadArticle);
  loadArticle();

  async function loadArticle() {
    errorEl.hidden = true;
    try {
      const res = await fetch(`${API_BASE}/api/basics/${encodeURIComponent(moduleKey)}`);
      if (!res.ok) throw new Error("article request failed");
      renderArticle(await res.json());
    } catch {
      bodyEl.replaceChildren();
      footEl.replaceChildren();
      crumbEl.textContent = "…";
      errorEl.hidden = false;
    }
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function refLine(text) {
    const line = el("p", "basics-ref");
    line.appendChild(el("span", "basics-ref-tag", "依据"));
    line.appendChild(document.createTextNode(text));
    return line;
  }

  // 带问题回到首页问答输入框（sessionStorage 桥，见 basics.js）
  function askButton(question) {
    const button = el("button", "basics-ask", "去问法晓");
    button.type = "button";
    button.addEventListener("click", () => {
      // 埋点只记事件与模块，不含问题文本（与全局隐私承诺一致）
      track("basics_ask_clicked", { module: moduleKey });
      try { sessionStorage.setItem(ASK_BRIDGE_KEY, question); } catch { /* 仍回首页 */ }
      location.href = `${API_BASE}/`;
    });
    return button;
  }

  // ---- 文章骨架 ----

  function renderArticle(data) {
    const module = data.module;
    document.title = `${module.name} · 法律常识 · 法晓`;
    crumbEl.textContent = module.name;

    bodyEl.replaceChildren();
    const head = el("header", "article-head");
    head.appendChild(el("span", "basics-sigil",
      module.sigil || (module.name || "").slice(0, 1)));
    const titles = el("div", "article-head-titles");
    titles.appendChild(el("h1", "", module.name));
    if (module.brief) titles.appendChild(el("p", "muted", module.brief));
    if (data.updated_at) {
      titles.appendChild(el("p", "article-meta", `内容整理时间：${data.updated_at}`));
    }
    head.appendChild(titles);
    bodyEl.appendChild(head);

    (module.blocks || []).forEach((block) => bodyEl.appendChild(renderBlock(block)));

    renderFoot(data);
  }

  function renderFoot(data) {
    footEl.replaceChildren();
    if (data.disclaimer) {
      footEl.appendChild(el("p", "article-disclaimer", data.disclaimer));
    }
    const actions = el("div", "article-foot-actions");
    const back = el("a", "outline-btn", "← 回到法律常识");
    back.href = `${API_BASE}/`;
    back.addEventListener("click", () => {
      try { sessionStorage.setItem(RETURN_KEY, "1"); } catch { /* 忽略 */ }
    });
    const ask = el("button", "primary-btn", "问一个问题");
    ask.type = "button";
    ask.addEventListener("click", () => {
      location.href = `${API_BASE}/`;
    });
    actions.append(ask, back);
    footEl.appendChild(actions);
  }

  function renderBlock(block) {
    // 注意：不再把 basics-<type> 拼进外层 class——那会与内容类名
    // （.basics-cards/.basics-notes/.basics-hotlines…）撞车，把整块内容
    // 挤进外层网格的一列里
    const wrap = el("div", "basics-block");
    wrap.dataset.blockType = block.type;
    wrap.appendChild(el("h4", "basics-block-title", block.title));
    if (block.hint) wrap.appendChild(el("p", "basics-hint", block.hint));
    const renderers = {
      route: renderRoute,
      table: renderTable,
      cards: renderCards,
      faq: renderFaq,
      notes: renderNotes,
      hotlines: renderHotlines,
    };
    (renderers[block.type] || (() => el("span")))(block, wrap);
    return wrap;
  }

  // ---- 内容块渲染 ----

  function renderTable(block, wrap) {
    const scroll = el("div", "basics-table-wrap");
    const table = el("table", "basics-table");
    const thead = el("thead");
    const headRow = el("tr");
    (block.columns || []).forEach((title, index) => {
      const th = el("th", "", title);
      if (!index) th.setAttribute("scope", "col");
      headRow.appendChild(th);
    });
    thead.appendChild(headRow);
    table.appendChild(thead);
    const tbody = el("tbody");
    (block.rows || []).forEach((row) => {
      const tr = el("tr");
      row.forEach((cell, index) => {
        if (!index) {
          const th = el("th", "", cell);
          th.setAttribute("scope", "row");
          tr.appendChild(th);
        } else {
          tr.appendChild(el("td", "", cell));
        }
      });
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    scroll.appendChild(table);
    wrap.appendChild(scroll);
  }

  function renderCards(block, wrap) {
    const grid = el("div", "basics-cards");
    (block.items || []).forEach((item) => {
      const card = el("article", "basics-card");
      const head = el("div", "basics-card-head");
      head.appendChild(el("h5", "", item.title));
      if (item.badge) {
        head.appendChild(el("span", `basics-badge ${item.level || ""}`, item.badge));
      }
      card.appendChild(head);
      card.appendChild(el("p", "", item.text));
      if (item.ref || item.ask) {
        const foot = el("div", "basics-card-foot");
        if (item.ref) foot.appendChild(refLine(item.ref));
        if (item.ask) foot.appendChild(askButton(item.ask));
        card.appendChild(foot);
      }
      grid.appendChild(card);
    });
    wrap.appendChild(grid);
  }

  function renderFaq(block, wrap) {
    const list = el("div", "basics-faqs");
    (block.items || []).forEach((item) => {
      const details = el("details", "basics-faq");
      const summary = el("summary", "");
      summary.appendChild(el("strong", "", item.q));
      if (item.ref) summary.appendChild(el("span", "basics-ref-tag", item.ref));
      details.appendChild(summary);
      const body = el("div", "basics-faq-body");
      body.appendChild(el("p", "", item.a));
      if (item.ask) body.appendChild(askButton(item.ask));
      details.appendChild(body);
      list.appendChild(details);
    });
    wrap.appendChild(list);
  }

  function renderNotes(block, wrap) {
    const list = el("ul", "basics-notes");
    (block.items || []).forEach((item) => {
      list.appendChild(el("li", "", item));
    });
    wrap.appendChild(list);
  }

  function renderHotlines(block, wrap) {
    const grid = el("div", "basics-hotlines");
    (block.items || []).forEach((item) => {
      const card = el("article", "basics-hotline");
      card.appendChild(el("strong", "", item.number));
      card.appendChild(el("span", "basics-hotline-name", item.name));
      card.appendChild(el("p", "", item.text));
      grid.appendChild(card);
    });
    wrap.appendChild(grid);
  }

  // ---- 诉讼流程路线图：主线（红）+ 支线（琥珀）----
  // 桌面：网格站点 + SVG 测绘道路；≤768px：CSS 流式纵向布局

  let routeObserver = null;
  let routeFrame = 0;

  function renderRoute(block, wrap) {
    const map = el("div", "basics-map");
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "map-roads");
    svg.setAttribute("aria-hidden", "true");
    map.appendChild(svg);

    // 每站收编入站道路的文字，作为卡片内「→」说明（所有视口统一）；
    // 顶部弧线道路除外——它的标签贴线浮动
    const inLabels = {};
    (block.roads || []).forEach((road) => {
      if (road.label && road.to !== "end" && road.via !== "top") {
        (inLabels[road.to] = inLabels[road.to] || []).push(road.label);
      }
    });

    (block.stations || []).forEach((station, index) => {
      const card = el("article",
        "basics-step-card map-station" + (station.branch ? " branch" : ""));
      card.dataset.key = station.key;
      card.dataset.col = String(station.col);
      card.dataset.row = String(station.row);
      card.style.animationDelay = `${index * 70}ms`;
      const head = el("div", "map-station-head");
      if (station.num) {
        // 支线徽章带「支」前缀，悬停说明编号含义，避免 A/B/C 看不懂
        const badge = el("span",
          "map-badge" + (station.branch ? " branch" : ""),
          station.branch ? `支${station.num}` : station.num);
        badge.title = station.branch
          ? `支线 ${station.num}：特殊情况下才走` : `主线第 ${station.num} 步`;
        head.appendChild(badge);
      }
      head.appendChild(el("strong", "", station.title));
      if (station.tag) head.appendChild(el("span", "map-station-tag", station.tag));
      card.appendChild(head);
      (inLabels[station.key] || []).forEach((text) =>
        card.appendChild(el("p", "map-in-label", `→ ${text}`)));
      card.appendChild(el("p", "", station.text));
      if (station.tips && station.tips.length) {
        const tips = el("ul", "basics-step-tips");
        station.tips.forEach((tip) => tips.appendChild(el("li", "", tip)));
        card.appendChild(tips);
      }
      if (station.ref) card.appendChild(refLine(station.ref));
      map.appendChild(card);
    });

    // 箭头 marker：主线红 / 支线琥珀，随线端自动定向
    const defs = document.createElementNS(SVG_NS, "defs");
    [["map-arrow", "#cf2f24"], ["map-arrow-branch", "#e8912a"]].forEach(([id, color]) => {
      const marker = document.createElementNS(SVG_NS, "marker");
      marker.setAttribute("id", id);
      marker.setAttribute("viewBox", "0 0 10 10");
      marker.setAttribute("refX", "8.5");
      marker.setAttribute("refY", "5");
      marker.setAttribute("markerWidth", "6.5");
      marker.setAttribute("markerHeight", "6.5");
      marker.setAttribute("orient", "auto-start-reverse");
      const tip = document.createElementNS(SVG_NS, "path");
      tip.setAttribute("d", "M0,0 L10,5 L0,10 z");
      tip.setAttribute("fill", color);
      marker.appendChild(tip);
      defs.appendChild(marker);
    });
    svg.appendChild(defs);

    // 起终点伪节点的边（start→首站 / 末站→end）只贡献卡内说明，不画线
    const roadEls = (block.roads || []).filter((road) =>
      road.from !== "start" && road.to !== "end").map((road, index) => {
      const group = document.createElementNS(SVG_NS, "g");
      group.setAttribute("class", "map-road" + (road.branch ? " branch" : ""));
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("class", "map-road-line");
      path.setAttribute("pathLength", "1"); // 供描线入场动画归一化
      path.setAttribute("marker-end",
        road.branch ? "url(#map-arrow-branch)" : "url(#map-arrow)");
      path.style.animationDelay = `${index * 90}ms`;
      group.appendChild(path);
      svg.appendChild(group);
      let labelEl = null;
      if (road.label && road.via === "top") {
        // 只有顶部弧线用贴线浮动标签，其余走卡内「→」说明
        labelEl = el("span", "map-label" + (road.branch ? " branch" : ""), road.label);
        map.appendChild(labelEl);
      }
      return { road, group, labelEl };
    });
    const dotsEl = document.createElementNS(SVG_NS, "g");
    dotsEl.setAttribute("class", "map-dots");
    svg.appendChild(dotsEl);

    wrap.appendChild(map);

    routeObserver?.disconnect();
    const redraw = () => drawMap(map, svg, roadEls, dotsEl);
    routeObserver = new ResizeObserver(() => {
      if (routeFrame) return;
      routeFrame = requestAnimationFrame(() => {
        routeFrame = 0;
        redraw();
      });
    });
    routeObserver.observe(map);
    requestAnimationFrame(redraw);
  }

  function drawMap(map, svg, roadEls, dotsEl) {
    if (window.matchMedia("(max-width: 768px)").matches) {
      // 移动端走 CSS 流式布局：不画道路
      return;
    }
    const frame = svg.getBoundingClientRect();
    if (!frame.width || !frame.height) return;
    svg.setAttribute("viewBox", `0 0 ${frame.width} ${frame.height}`);
    const wide4 = window.matchMedia("(min-width: 1040px)").matches;

    const stationEl = (key) => map.querySelector(`.map-station[data-key="${key}"]`);
    const rel = (node) => {
      const rect = node.getBoundingClientRect();
      return {
        left: rect.left - frame.left, top: rect.top - frame.top,
        width: rect.width, height: rect.height,
      };
    };
    const anchor = (key, edge) => {
      const node = stationEl(key);
      if (!node) return null;
      const r = rel(node);
      if (edge === "top") return { x: r.left + r.width / 2, y: r.top };
      if (edge === "bottom") return { x: r.left + r.width / 2, y: r.top + r.height };
      if (edge === "left") return { x: r.left, y: r.top + r.height / 2 };
      return { x: r.left + r.width, y: r.top + r.height / 2 };
    };
    // 基于 col/row 确定性选连接边：同行为水平左右、跨行为竖直上下
    const roadEdges = (road) => {
      if (road.via === "top") return ["top", "top"];
      const f = stationEl(road.from);
      const t = stationEl(road.to);
      if (!f || !t) return ["right", "left"];
      if (f.dataset.row === t.dataset.row) {
        return Number(f.dataset.col) < Number(t.dataset.col)
          ? ["right", "left"] : ["left", "right"];
      }
      return Number(f.dataset.row) < Number(t.dataset.row)
        ? ["bottom", "top"] : ["top", "bottom"];
    };
    // 箭头回退：终点沿道路方向缩短 k。k 只要大于箭头横向半径的一半即可
    // 避免三角形侧翼被目标卡盖住；取 2px 让箭头尖几乎紧贴卡片边缘
    const pull = (pt, ref, k) => {
      const dx = pt.x - ref.x;
      const dy = pt.y - ref.y;
      const len = Math.hypot(dx, dy) || 1;
      return { x: pt.x - dx / len * k, y: pt.y - dy / len * k };
    };

    // 双向道路分车道：同一对站点互为反向时，先出现的靠左/上、
    // 后出现的靠右/下，两条平行线各带箭头，方向一目了然
    const lane = new Array(roadEls.length).fill(0);
    roadEls.forEach((entry, i) => {
      if (lane[i]) return;
      const rev = roadEls.findIndex((other, j) => j !== i && !lane[j]
        && other.road.from === entry.road.to && other.road.to === entry.road.from);
      if (rev >= 0) { lane[i] = -18; lane[rev] = 18; }
    });

    const dots = [];
    const labelQueue = [];
    const arrowTips = []; // 各线箭头位置：标签必须避让，不能盖住方向指示

    roadEls.forEach(({ road, group, labelEl }, idx) => {
      const isArc = road.via === "top" && wide4;
      const edges = isArc ? ["top", "top"] : roadEdges(road);
      let a = anchor(road.from, edges[0]);
      let b = anchor(road.to, edges[1]);
      if (!a || !b) return;
      // 双向车道错开：竖直道路水平偏移、水平道路竖直偏移
      const shift = lane[idx] || 0;
      if (shift && !isArc) {
        if (edges[0] === "bottom" || edges[0] === "top") {
          a = { ...a, x: a.x + shift };
          b = { ...b, x: b.x + shift };
        } else {
          a = { ...a, y: a.y + shift };
          b = { ...b, y: b.y + shift };
        }
      }
      // 直线箭头：普通道路两点直连；越过中间站点的走顶部折线
      let d;
      let tip = b;
      let peak = 0;
      if (isArc) {
        peak = Math.min(a.y, b.y) - 50;
        tip = pull(b, { x: b.x, y: peak }, 2);
        d = `M ${a.x.toFixed(1)} ${a.y.toFixed(1)}`
          + ` L ${a.x.toFixed(1)} ${peak.toFixed(1)}`
          + ` L ${b.x.toFixed(1)} ${peak.toFixed(1)}`
          + ` L ${tip.x.toFixed(1)} ${tip.y.toFixed(1)}`;
      } else {
        tip = pull(b, a, 2);
        d = `M ${a.x.toFixed(1)} ${a.y.toFixed(1)} L ${tip.x.toFixed(1)} ${tip.y.toFixed(1)}`;
      }
      group.querySelector("path").setAttribute("d", d);
      // 起点画小圆点，终点由箭头承接
      dots.push([a, road.branch]);
      arrowTips.push(tip);
      if (labelEl) {
        // 唯一的浮动标签：顶部弧线挂弧顶上方
        labelQueue.push({ labelEl, point: { x: (a.x + b.x) / 2, y: peak - 14 } });
      }
    });

    dotsEl.replaceChildren();
    dots.forEach(([point, branch]) => {
      const circle = document.createElementNS(SVG_NS, "circle");
      circle.setAttribute("cx", point.x.toFixed(1));
      circle.setAttribute("cy", point.y.toFixed(1));
      circle.setAttribute("r", "4.5");
      if (branch) circle.setAttribute("class", "branch");
      dotsEl.appendChild(circle);
    });
    // 浮动道路标签只在四列宽屏出现；两列及以下由站点卡内的「→」说明承接
    if (wide4) placeLabels(labelQueue, frame, arrowTips);
  }

  // 道路标签放置：标签按行底部固定 y，避让已放标签与各线箭头
  function placeLabels(labelQueue, frame, arrowTips) {
    const placed = [];
    labelQueue.forEach(({ labelEl, point }) => {
      const w = labelEl.offsetWidth || 120;
      const h = labelEl.offsetHeight || 22;
      const clampX = (x) => Math.max(w / 2 + 8, Math.min(frame.width - w / 2 - 8, x));
      const y = point.y;
      // 候选偏移从原位向两侧逐步展开，取第一个不撞标签也不盖箭头的位置
      const fits = (cand) => !placed.some((p) =>
          Math.abs(cand - p.x) < (w + p.w) / 2 + 6 &&
          Math.abs(y - p.y) < (h + p.h) / 2 + 4)
        && !arrowTips.some((t) =>
          Math.abs(cand - t.x) < w / 2 + 16 &&
          Math.abs(y - t.y) < h / 2 + 14);
      let x = clampX(point.x);
      for (const k of [0, 1, -1, 2, -2, 3]) {
        const cand = clampX(point.x + k * (w + 12));
        if (fits(cand)) { x = cand; break; }
      }
      labelEl.style.left = `${Math.round(x)}px`;
      labelEl.style.top = `${Math.round(y)}px`;
      placed.push({ x, y, w, h });
    });
  }
})();
