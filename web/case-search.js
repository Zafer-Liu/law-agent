(() => {
  'use strict';
  const api = `${location.pathname.replace(/\/[^/]*$/, '')}/api/cases`;
  const form = document.getElementById('case-search-form');
  const input = document.getElementById('case-query');
  const submit = document.getElementById('case-search-submit');
  const example = document.getElementById('case-example');
  const status = document.getElementById('case-status');
  const notice = document.getElementById('case-cloud-notice');
  const results = document.getElementById('case-results');
  let busy = false;
  let model = null;
  let configRevision = 0;

  async function call(path, options) {
    const response = await fetch(api + path, options);
    if (response.status === 401) throw new Error('请在右上角登录管理员账号。');
    if (response.status === 403) throw new Error('课程案例库仅供管理员账号检索。');
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || '类案检索暂不可用，请稍后重试。');
    return data;
  }

  async function loadConfig() {
    if (busy) return;
    const revision = ++configRevision;
    model = null;
    submit.disabled = true;
    notice.hidden = true;
    results.replaceChildren();
    status.textContent = '正在检查案例库…';
    try {
      const config = await call('/config');
      if (revision !== configRevision) return;
      if (!config.configured) {
        status.textContent = '案例库尚未配置。请先导入案例向量并配置数据库连接。';
        return;
      }
      model = config.model;
      notice.hidden = !config.cloud;
      submit.disabled = false;
      status.textContent = `当前检索模型 ${model}`;
    } catch (error) {
      if (revision === configRevision) status.textContent = error.message;
    }
  }

  function element(tag, text, className) {
    const el = document.createElement(tag);
    el.textContent = text;
    if (className) el.className = className;
    return el;
  }

  function renderHit(hit) {
    const item = element('li', '', 'case-hit');
    const info = `${hit.cause || '案由未标注'} · 文书 ${hit.case_no} · 余弦相似度 ${hit.score.toFixed(3)}`;
    item.append(element('p', info, 'case-hit-meta'), element('h3', hit.title));
    const details = document.createElement('details');
    details.append(element('summary', '对照事实与裁判'));
    for (const [label, text] of [['案件事实', hit.facts], ['裁判理由', hit.reasoning], ['裁判结果', hit.outcome]]) {
      details.append(element('h4', label), element('p', text || '源文书未提取该字段。'));
    }
    item.append(details);
    return item;
  }

  const tab = document.getElementById('tab-button-cases');
  tab.addEventListener('click', () => { if (!model) loadConfig(); });
  window.addEventListener('auth:changed', () => {
    configRevision += 1;
    model = null;
    busy = false;
    submit.disabled = true;
    example.disabled = false;
    input.readOnly = false;
    input.value = '';
    results.replaceChildren();
    notice.hidden = true;
    if (tab.getAttribute('aria-selected') === 'true') loadConfig();
  });
  example.addEventListener('click', () => {
    input.value = '买卖合同纠纷。供货结束后买方在对账单上签字确认尚欠货款，卖方催收未果，诉请支付货款及逾期付款损失。';
    input.focus();
  });
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const query = input.value.trim();
    if (!model || busy || !query) return;
    const revision = configRevision;
    busy = true;
    submit.disabled = true;
    example.disabled = true;
    input.readOnly = true;
    results.replaceChildren();
    status.textContent = '正在检索相似案例…';
    try {
      const data = await call('/search', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ query }),
      });
      if (revision !== configRevision) return;
      results.replaceChildren(...data.matches.map(renderHit));
      status.textContent = data.matches.length
        ? `${data.matches.length} 条候选 · ${data.model}。请逐条核对争点和证据。`
        : '未找到候选。试着补充案由、争议焦点或关键证据。';
    } catch (error) {
      if (revision === configRevision) status.textContent = error.message;
    } finally {
      if (revision === configRevision) {
        busy = false;
        submit.disabled = false;
        example.disabled = false;
        input.readOnly = false;
      }
    }
  });
})();
