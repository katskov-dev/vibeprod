/* opencode harness frontend */
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const api = {
  async req(method, url, body) {
    const opts = { method, headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const r = await fetch(url, opts);
    if (!r.ok) {
      let detail = r.statusText;
      try { detail = (await r.json()).detail || detail; } catch {}
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return r.json();
  },
  get: (u) => api.req('GET', u),
  post: (u, b) => api.req('POST', u, b ?? {}),
  put: (u, b) => api.req('PUT', u, b ?? {}),
  del: (u) => api.req('DELETE', u),
};

function toast(msg, kind = 'info') {
  const el = document.createElement('div');
  const colors = { info: 'bg-neutral-800 border-neutral-700', error: 'bg-red-950 border-red-800', ok: 'bg-emerald-950 border-emerald-800' };
  el.className = `px-4 py-2 rounded-lg border text-sm shadow-lg ${colors[kind]}`;
  el.textContent = msg;
  $('#toast-root').appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

const STATUS_META = {
  queued:    { label: 'в очереди', cls: 'bg-neutral-700' },
  starting:  { label: 'стартует',  cls: 'bg-yellow-700 animate-pulse' },
  running:   { label: 'работает',  cls: 'bg-sky-700 animate-pulse' },
  completed: { label: 'завершено', cls: 'bg-emerald-700' },
  failed:    { label: 'ошибка',    cls: 'bg-red-700' },
  aborted:   { label: 'прервано',  cls: 'bg-orange-700' },
  expired:   { label: 'истёк',     cls: 'bg-neutral-600' },
};
function badge(status) {
  const m = STATUS_META[status] || STATUS_META.queued;
  return `<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${m.cls}">${m.label}</span>`;
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* ---------- modal ---------- */
function openModal(title, bodyHtml, onSubmit) {
  const root = $('#modal-root');
  root.innerHTML = `
    <div class="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-4" id="modal-overlay">
      <div class="bg-neutral-900 border border-neutral-800 rounded-xl w-full max-w-2xl max-h-[90vh] flex flex-col shadow-2xl">
        <div class="flex items-center justify-between px-5 py-3 border-b border-neutral-800">
          <div class="font-semibold">${esc(title)}</div>
          <button id="modal-close" class="text-neutral-500 hover:text-neutral-200 text-xl leading-none">&times;</button>
        </div>
        <div class="p-5 overflow-y-auto" id="modal-body">${bodyHtml}</div>
        <div class="px-5 py-3 border-t border-neutral-800 flex justify-end gap-2">
          <button id="modal-cancel" class="px-4 py-2 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-sm">Отмена</button>
          <button id="modal-ok" class="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium">Сохранить</button>
        </div>
      </div>
    </div>`;
  const close = () => root.innerHTML = '';
  $('#modal-close').onclick = close;
  $('#modal-cancel').onclick = close;
  $('#modal-overlay').onclick = (e) => { if (e.target.id === 'modal-overlay') close(); };
  $('#modal-ok').onclick = async () => {
    try { await onSubmit(close); } catch (e) { toast(e.message, 'error'); }
  };
}

function formInput(label, name, value = '', placeholder = '', type = 'text', extra = '') {
  return `<label class="block text-sm mb-3">
    <span class="text-neutral-400 text-xs">${label}</span>
    <input name="${name}" type="${type}" value="${esc(value)}" placeholder="${esc(placeholder)}" ${extra}
      class="mt-1 w-full bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-sky-600">
  </label>`;
}
function formArea(label, name, value = '', rows = 4, placeholder = '') {
  return `<label class="block text-sm mb-3">
    <span class="text-neutral-400 text-xs">${label}</span>
    <textarea name="${name}" rows="${rows}" placeholder="${esc(placeholder)}"
      class="mt-1 w-full bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-sky-600 mono">${esc(value)}</textarea>
  </label>`;
}
function formSelect(label, name, options, value) {
  return `<label class="block text-sm mb-3">
    <span class="text-neutral-400 text-xs">${label}</span>
    <select name="${name}" class="mt-1 w-full bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-sky-600">
      ${options.map(o => `<option value="${esc(o.v)}" ${String(o.v) === String(value) ? 'selected' : ''}>${esc(o.l)}</option>`).join('')}
    </select>
  </label>`;
}
function readForm(rootEl) {
  const out = {};
  $$('[name]', rootEl).forEach(el => { out[el.name] = el.value; });
  return out;
}

/* ---------- nav ---------- */
let currentView = 'sessions';
let currentProject = null;
let projectsCache = [];

async function loadProjectMenu() {
  try { projectsCache = await api.get('/api/projects'); } catch { projectsCache = []; }
  const stored = localStorage.getItem('harness-project');
  if (stored !== null && projectsCache.some(p => String(p.id) === stored)) {
    currentProject = stored;
  } else {
    currentProject = projectsCache.length ? String(projectsCache[0].id) : null;
    if (currentProject) localStorage.setItem('harness-project', currentProject);
    else localStorage.removeItem('harness-project');
  }
  renderProjectMenu();
}

function renderProjectMenu() {
  const labelEl = $('#project-current');
  if (labelEl) {
    labelEl.textContent = currentProject
      ? ((projectsCache.find(p => String(p.id) === String(currentProject)) || {}).name || '—')
      : '—';
  }
  const list = $('#project-menu-list');
  if (!list) return;
  const items = projectsCache.map(p => ({ id: String(p.id), name: p.name }));
  list.innerHTML = items.map(it => `
    <button class="proj-item w-full flex items-center justify-between gap-2 px-3 py-1.5 text-sm text-left hover:bg-neutral-800 ${String(currentProject) === String(it.id) ? 'text-sky-300 bg-neutral-800/50' : ''}" data-id="${it.id}">
      <span class="truncate">${esc(it.name)}</span>
      ${String(currentProject) === String(it.id) ? '<span class="text-sky-400 shrink-0">✓</span>' : ''}
    </button>`).join('') || '<div class="px-3 py-2 text-xs text-neutral-500">проектов нет</div>';
  $$('.proj-item', list).forEach(b => b.onclick = async () => {
    currentProject = b.dataset.id || null;
    if (currentProject) localStorage.setItem('harness-project', String(currentProject));
    else localStorage.removeItem('harness-project');
    renderProjectMenu();
    setProjectMenu(false);
    await refreshCurrentView();
  });
}

function setProjectMenu(open) {
  const menu = $('#project-menu');
  if (!menu) return;
  menu.classList.toggle('hidden', !open);
  const caret = $('#project-caret');
  if (caret) caret.classList.toggle('rotate-180', open);
}

async function refreshCurrentView() {
  if (currentView === 'home') renderHome();
  else if (currentView === 'sessions') renderSessions();
  else if (currentView === 'agents') renderAgents();
  else if (currentView === 'providers') renderProviders();
  else if (currentView === 'mcp-catalog') renderCatalog();
  else if (currentView === 'skills') renderSkills();
  else if (currentView === 'webhooks') renderAutomation('webhooks');
  else if (currentView === 'schedules' || currentView === 'automation') renderAutomation('schedules');
  else if (currentView === 'channels') renderChannels();
  else if (currentView === 'projects') renderProjects();
}

function showView(view, arg) {
  currentView = view;
  $$('.nav-btn').forEach(b => {
    const active = b.dataset.view === view;
    b.classList.toggle('bg-neutral-800', active);
    b.classList.toggle('hover:bg-neutral-800/60', !active);
  });
  window.history.pushState({ view, arg }, '', `#${view}${arg ? '/' + arg : ''}`);
  if (view === 'home') renderHome();
  else if (view === 'sessions') renderSessions(arg);
  else if (view === 'agents') renderAgents();
  else if (view === 'providers') renderProviders();
  else if (view === 'mcp-catalog') renderCatalog();
  else if (view === 'skills') renderSkills();
  else if (view === 'webhooks') renderAutomation('webhooks');
  else if (view === 'schedules' || view === 'automation') renderAutomation('schedules');
  else if (view === 'channels') renderChannels(arg);
  else if (view === 'projects') renderProjects();
}
$('#nav').onclick = (e) => {
  const btn = e.target.closest('.nav-btn');
  if (btn) showView(btn.dataset.view);
};
$('#project-trigger').onclick = (e) => {
  e.stopPropagation();
  setProjectMenu($('#project-menu').classList.contains('hidden'));
};
$('#project-add').onclick = () => { setProjectMenu(false); projectModal(null, { selectAfterCreate: true }); };
$('#project-manage').onclick = () => { setProjectMenu(false); showView('projects'); };
document.addEventListener('click', (e) => {
  const menu = $('#project-menu');
  if (menu && !menu.classList.contains('hidden')) {
    if (!e.target.closest('#project-trigger') && !e.target.closest('#project-menu')) setProjectMenu(false);
  }
  const mmenu = $('#model-menu');
  if (mmenu && !mmenu.classList.contains('hidden') && !e.target.closest('#model-select-wrap')) {
    mmenu.classList.add('hidden');
  }
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') setProjectMenu(false);
});
window.onpopstate = (e) => {
  const [view, arg] = (location.hash.slice(1) || 'home').split('/');
  showView(view, arg);
};

const projQuery = () => currentProject ? `?project_id=${currentProject}` : '';

/* ---------- home (агент-оператор) ---------- */
const HOME_EXAMPLES = [
  'Покажи, как настроен мой проект',
  'Создай агента для работы с документами и подключи ему playwright',
  'Добавь провайдера openai — ключ у меня есть',
  'Настрой вебхук, который запускает утренний отчёт по будням',
  'Создай скилл «перевод договоров» и привяжи его к агенту general',
];

async function renderHome() {
  const main = $('#main');
  let info = { ready: false };
  try { info = await api.get('/api/guardian'); } catch {}
  main.innerHTML = `
    <div class="h-full overflow-y-auto">
      <div class="max-w-2xl mx-auto px-6 py-16 flex flex-col items-start">
        <div class="text-2xl font-semibold mb-1">Что нужно сделать?</div>
        <div class="text-sm text-neutral-500 mb-8">Опишите задачу — агент-оператор сам настроит проект:
          создаст агентов, подключит MCP и скиллы, добавит провайдеров, вебхуки и расписания.
          Ничего удалять без вашего подтверждения он не будет.</div>
        ${info.ready ? '' : `
        <div class="w-full mb-6 px-4 py-3 rounded-lg border border-amber-900/60 bg-amber-950/40 text-amber-300 text-sm">
          Агент-оператор не найден — перезапустите сервер.
        </div>`}
        <textarea id="home-prompt" rows="4" placeholder="Например: создай агента для проверки сайта с подключённым playwright…"
          class="w-full bg-neutral-900 border border-neutral-700 rounded-xl px-4 py-3 text-sm focus:outline-none focus:border-sky-600 resize-none"></textarea>
        <div class="flex items-center gap-3 mt-4">
          <button id="home-go" class="px-5 py-2.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium ${info.ready ? '' : 'opacity-40 pointer-events-none'}">Начать</button>
          <span class="text-xs text-neutral-600">откроется сессия с агентом-оператором</span>
        </div>
        <div id="home-error" class="text-sm text-red-400 mt-3"></div>
        <div class="mt-10 w-full">
          <div class="text-[10px] uppercase tracking-wider text-neutral-600 mb-2">Примеры</div>
          <div class="flex flex-wrap gap-2" id="home-examples">
            ${HOME_EXAMPLES.map(e => `<button class="home-ex px-3 py-1.5 rounded-full border border-neutral-800 hover:border-sky-700 text-xs text-neutral-400 hover:text-neutral-200" data-t="${esc(e)}">${esc(e)}</button>`).join('')}
          </div>
        </div>
      </div>
    </div>`;
  if (!info.ready) return;
  const prompt = $('#home-prompt');
  const err = $('#home-error');
  $$('.home-ex', main).forEach(b => b.onclick = () => {
    prompt.value = b.dataset.t;
    prompt.focus();
  });
  $('#home-go').onclick = async () => {
    const text = prompt.value.trim();
    if (!text) { err.textContent = 'Введите промпт'; prompt.focus(); return; }
    const go = $('#home-go');
    go.disabled = true;
    err.textContent = '';
    try {
      const s = await api.post('/api/sessions', {
        agent_id: info.agent_id,
        title: text.slice(0, 60),
        prompt: text,
        source: 'guardian',
        project_id: currentProject || info.project_id,
      });
      showView('sessions', s.id);
    } catch (e) {
      err.textContent = e.message || 'Не удалось запустить сессию';
      go.disabled = false;
    }
  };
  if (prompt) prompt.focus();
}

/* ---------- sessions ---------- */
async function renderSessions(openId) {
  const main = $('#main');
  main.innerHTML = `
    <div class="h-full flex flex-col">
      <div class="flex items-center justify-between px-6 py-4 border-b border-neutral-800">
        <div class="text-lg font-semibold">Сессии</div>
        <button id="new-session" class="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium">Новая сессия</button>
      </div>
      <div class="flex-1 overflow-y-auto p-6" id="sessions-list"><div class="text-neutral-500 text-sm">загрузка…</div></div>
    </div>`;
  $('#new-session').onclick = async () => {
    const agents = await api.get('/api/agents' + projQuery());
    if (!agents.length) return toast('Сначала создайте агента', 'error');
    openModal('Новая сессия', `
      ${formSelect('Агент', 'agent_id', agents.map(a => ({ v: a.id, l: `${a.name} · ${a.model}` })), agents[0].id)}
      ${formInput('Заголовок (необязательно)', 'title')}
      ${formArea('Промпт', 'prompt', '', 6, 'Что нужно сделать…')}`,
      async (close) => {
        const f = readForm($('#modal-body'));
        if (!f.prompt.trim()) throw new Error('Промпт обязателен');
        const s = await api.post('/api/sessions', { agent_id: +f.agent_id, title: f.title, prompt: f.prompt, project_id: currentProject || undefined });
        close();
        showView('sessions', s.id);
      });
  };
  await refreshSessionsList(openId);
}

async function refreshSessionsList(openId) {
  let data;
  try { data = await api.get('/api/sessions' + projQuery()); } catch (e) { return; }
  const el = $('#sessions-list');
  if (!el) return;
  if (!data.length) {
    el.innerHTML = `<div class="text-neutral-500 text-sm">Сессий пока нет. Создайте первую.</div>`;
    return;
  }
  el.innerHTML = data.map(s => `
    <div class="flex items-center gap-4 px-4 py-3 rounded-xl border border-neutral-800 hover:border-neutral-700 mb-2 cursor-pointer" data-open="${s.id}">
      <div class="flex-1 min-w-0">
        <div class="font-medium truncate flex items-center gap-2">${esc(s.title)}
          ${s.source === 'webhook' ? '<span class="px-1.5 py-0.5 rounded bg-fuchsia-900/60 text-xs shrink-0">webhook</span>' : ''}
          ${s.source === 'schedule' ? '<span class="px-1.5 py-0.5 rounded bg-amber-900/60 text-xs shrink-0">расписание</span>' : ''}
          ${s.source === 'guardian' ? '<span class="px-1.5 py-0.5 rounded bg-sky-900/60 text-xs shrink-0">оператор</span>' : ''}
          ${s.source === 'telegram' ? '<span class="px-1.5 py-0.5 rounded bg-sky-900/60 text-xs shrink-0">telegram</span>' : ''}
        </div>
        <div class="text-xs text-neutral-500">${esc(s.agent_name || '—')} · ${esc(s.model || '')} · ${esc(s.created_at)}</div>
        ${s.error ? `<div class="text-xs text-red-400 truncate mt-0.5" title="${esc(s.error)}">${esc(s.error.slice(0, 140))}</div>` : ''}
      </div>
      <div class="flex items-center gap-3">
        ${badge(s.status)}
        <button class="del text-neutral-600 hover:text-red-400 text-xs" data-del="${s.id}">удалить</button>
      </div>
    </div>`).join('');
  $$('[data-open]', el).forEach(row => row.onclick = () => showView('sessions', row.dataset.open));
  $$('[data-del]', el).forEach(b => b.onclick = async (e) => {
    e.stopPropagation();
    if (!confirm('Удалить сессию и её контейнер?')) return;
    await api.del(`/api/sessions/${b.dataset.del}`);
    await refreshSessionsList();
  });
  if (openId) renderChat(openId);
}

/* ---------- chat ---------- */
const deltaBuffers = new Map();
const msgRoles = new Map();
const rawTexts = new Map();
let generating = false;
let pendingUserBubble = null;

function renderMarkdown(text) {
  const src = String(text ?? '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .replace(/^\n+|\n+$/g, '');
  if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
    try {
      marked.setOptions({ gfm: true, breaks: true });
      return DOMPurify.sanitize(marked.parse(src));
    } catch {}
  }
  return esc(src);
}

function setTextPart(p, pkey, raw) {
  rawTexts.set(pkey, raw);
  p.dataset.raw = raw;
  p.innerHTML = renderMarkdown(raw);
}

function handleEvent(event, ts) {
  const type = event.type;
  const props = event.properties || {};
  if (type === 'message.updated') upsertMessage(props.info);
  else if (type === 'message.part.updated') upsertPart(props.part, true);
  else if (type === 'message.part.delta') appendDelta(props);
  else if (type === 'todo.updated') renderTodos(props.todos);
  else if (type === 'question.asked') upsertQuestion(props, ts);
  else if (type === 'question.replied') markQuestionAnswered(props);
  else if (type === 'question.rejected') markQuestionRejected(props);
  else if (type === 'session.status') {
    const busy = props.status && props.status.type === 'busy';
    generating = busy;
    const el = $('#chat-status');
    if (el && busy) el.innerHTML = badge('running');
  }
  else if (type === 'session.idle') { generating = false; }
  else if (type === 'session.failed' || type === 'session.error') { generating = false; }
  logEvent(type, ts);
}

function logEvent(type, ts) {
  const log = $('#event-log');
  if (!log) return;
  const interesting = type.startsWith('session.') || type.startsWith('file.') || type === 'todo.updated';
  if (!interesting) return;
  const row = document.createElement('div');
  row.className = 'text-xs text-neutral-500 mono break-all mb-1';
  row.textContent = `${ts || ''} ${type}`;
  log.prepend(row);
  if (log.children.length > 100) log.removeChild(log.lastChild);
}

function upsertMessage(info) {
  if (!info) return;
  const el = $('#chat-messages');
  if (!el) return;
  if (info.role !== 'user') return; // assistant-части рендерятся отдельными сообщениями
  msgRoles.set(info.id, 'user');
  const id = info.id;
  let wrap = $(`[data-msg="${id}"]`, el);
  if (!wrap) {
    if (pendingUserBubble && pendingUserBubble.isConnected) {
      wrap = pendingUserBubble;
      wrap.dataset.msg = id;
      pendingUserBubble = null;
    } else {
      wrap = document.createElement('div');
      wrap.dataset.msg = id;
      wrap.className = 'flex justify-end';
      wrap.innerHTML = `<div class="max-w-[80%] bg-sky-900/60 border border-sky-800 rounded-2xl rounded-br-sm px-4 py-2.5 text-sm" data-content></div>`;
      el.appendChild(wrap);
    }
  }
  el.scrollTop = el.scrollHeight;
}

function ensureKindBubble(messageID, kind) {
  const el = $('#chat-messages');
  if (!el) return null;
  const key = `${messageID}:${kind}`;
  let wrap = $(`[data-msg="${key}"]`, el);
  if (!wrap) {
    const subtle = kind.startsWith('tool') || kind.startsWith('reasoning') || kind === 'error';
    wrap = document.createElement('div');
    wrap.dataset.msg = key;
    wrap.dataset.kind = kind;
    wrap.className = 'flex justify-start';
    wrap.innerHTML = subtle
      ? `<div class="max-w-[85%]" data-content></div>`
      : `<div class="max-w-[85%] bg-neutral-900 border border-neutral-800 rounded-2xl rounded-bl-sm px-3 py-2 text-sm space-y-2" data-content></div>`;
    el.appendChild(wrap);
  }
  return wrap;
}

function appendDelta(props) {
  const partID = props.partID;
  const messageID = props.messageID;
  if (!partID || msgRoles.get(messageID) === 'user') return;
  const delta = props.delta || '';
  if (!delta) return;
  const wrap = ensureKindBubble(messageID, 'text');
  if (!wrap) return;
  const content = $('[data-content]', wrap);
  const pkey = `${messageID}:${partID}`;
  let p = $(`[data-p="${pkey}"]`, $('#chat-messages'));
  if (!p) {
    p = document.createElement('div');
    p.dataset.p = pkey;
    p.className = 'md leading-relaxed';
    content.appendChild(p);
  }
  setTextPart(p, pkey, (rawTexts.get(pkey) || '') + delta);
  $('#chat-messages').scrollTop = $('#chat-messages').scrollHeight;
}

function upsertPart(part, replaceText) {
  if (!part || !part.id) return;
  if (msgRoles.get(part.messageID) === 'user') return;
  const el = $('#chat-messages');
  if (!el) return;
  const pkey = `${part.messageID}:${part.id}`;
  if (part.type === 'text') {
    const raw = replaceText || !rawTexts.has(pkey) ? (part.text || '') : rawTexts.get(pkey);
    if (!raw.trim()) {
      const existing = $(`[data-p="${pkey}"]`, el);
      if (existing) existing.closest('[data-msg]').remove();
      rawTexts.delete(pkey);
      return;
    }
    const wrap = ensureKindBubble(part.messageID, 'text');
    if (!wrap) return;
    const content = $('[data-content]', wrap);
    let p = $(`[data-p="${pkey}"]`, el);
    if (!p) {
      p = document.createElement('div');
      p.dataset.p = pkey;
      p.className = 'md leading-relaxed';
      content.appendChild(p);
    }
    setTextPart(p, pkey, raw);
  } else if (part.type === 'tool') {
    const wrap = ensureKindBubble(part.messageID, `tool:${part.id}`);
    if (!wrap) return;
    const content = $('[data-content]', wrap);
    let p = $(`[data-p="${pkey}"]`, el);
    if (!p) {
      p = document.createElement('div');
      p.dataset.p = pkey;
      content.appendChild(p);
    }
    const prev = $('details', p);
    const wasOpen = prev ? prev.open : false;
    p.innerHTML = toolCard(part);
    if (wasOpen) $('details', p).open = true;
  } else if (part.type === 'reasoning') {
    const existing = $(`[data-p="${pkey}"]`, el);
    if (!existing && !(part.text || '').trim()) return;
    const wrap = ensureKindBubble(part.messageID, `reasoning:${part.id}`);
    if (!wrap) return;
    const content = $('[data-content]', wrap);
    let p = $(`[data-p="${pkey}"]`, el);
    if (!p) {
      p = document.createElement('div');
      p.dataset.p = pkey;
      p.innerHTML = reasonCard(part);
      content.appendChild(p);
    } else {
      const details = $('details', p);
      if (details) {
        const text = part.text || '';
        $('[data-reason-text]', details).textContent = text;
        $('[data-reason-len]', details).textContent = text.length ? text.length + ' симв.' : '';
      }
    }
  }
  el.scrollTop = el.scrollHeight;
}

const TOOL_STATUS = { pending: 'ожидание', running: 'выполняется', completed: 'готово', error: 'ошибка' };

function reasonCard(part) {
  const text = part.text || '';
  return `<details class="compact-card reason-card group border border-neutral-800/70 rounded-md px-1.5 py-0.5 text-[11px] bg-neutral-900/40">
    <summary>
      <span class="chev text-neutral-600 text-[9px]">▸</span>
      <span class="text-neutral-500 shrink-0">рассуждение</span>
      <span class="text-neutral-700" data-reason-len>${text.length ? text.length + ' симв.' : ''}</span>
      <button class="msg-actions ml-auto text-neutral-500 hover:text-neutral-200 text-xs leading-none px-1 py-0.5 rounded" data-copy="reason" title="Скопировать">⧉</button>
    </summary>
    <div data-reason-text class="mt-0.5 pt-0.5 border-t border-neutral-800/60 whitespace-pre-wrap mono text-[10px] text-neutral-400">${esc(text)}</div>
  </details>`;
}

function toolCard(part) {
  const tool = part.tool || 'tool';
  const state = part.state || {};
  const st = TOOL_STATUS[state.status] || state.status || '…';
  if (tool === 'question') {
    return `<div class="text-[11px] text-neutral-500 px-1.5 py-0.5">❓ вопрос агенту${state.status === 'completed' ? ' · ✓' : ''}</div>`;
  }
  const input = state.input !== undefined ? state.input : {};
  const output = state.output !== undefined ? state.output : null;
  const title = state.title || null;
  const stCls = state.status === 'completed' ? 'text-emerald-400' : state.status === 'error' ? 'text-red-400' : 'text-neutral-500';
  return `<details class="compact-card tool-card border border-neutral-800/70 rounded-md px-1.5 py-0.5 text-[11px] bg-neutral-900/40">
    <summary>
      <span class="chev text-neutral-600 text-[9px]">▸</span>
      <span class="mono text-sky-400 shrink-0">${esc(tool)}</span>
      <span class="${stCls} shrink-0">${esc(st)}</span>
      ${title ? `<span class="text-neutral-600 truncate">${esc(title)}</span>` : ''}
    </summary>
    <pre class="mt-0.5 pt-0.5 border-t border-neutral-800/60 mono text-[10px] text-neutral-400 max-h-40 overflow-y-auto">${esc(JSON.stringify(input, null, 2))}${output !== null ? '\n\n→ ' + esc(typeof output === 'string' ? output : JSON.stringify(output, null, 2)) : ''}</pre>
  </details>`;
}

function renderTodos(todos) {
  if (!todos) return;
  let panel = $('#todos-panel');
  const side = $('#chat-side');
  if (!side) return;
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'todos-panel';
    side.prepend(panel);
    const log = document.createElement('div');
    log.innerHTML = '<div class="text-xs font-semibold text-neutral-400 mb-2 mt-4">СОБЫТИЯ</div><div id="event-log" class="max-h-64 overflow-y-auto"></div>';
    side.appendChild(log);
  }
  const icons = { completed: '✅', in_progress: '🔄', pending: '⬜' };
  panel.innerHTML = `<div class="text-xs font-semibold text-neutral-400 mb-2">ЗАДАЧИ</div>` + todos.map(t => `
    <div class="flex items-start gap-2 text-xs mb-1.5">
      <span class="mt-0.5">${icons[t.status] || '⬜'}</span>
      <span class="${t.status === 'completed' ? 'text-neutral-500 line-through' : ''}">${esc(t.content)}</span>
    </div>`).join('');
}

/* ---------- вопросы агента ---------- */
const questionStates = new Map(); // requestID -> { req, answers: string[][] }
let questionQueue = []; // порядок ожидающих вопросов
let activeQuestionID = null; // запрос, показанный сейчас вместо поля ввода
let activeQuestionTab = 0;
let activeSessionId = null;

function currentPending() {
  return questionQueue.find(id => {
    const s = questionStates.get(id);
    return s && !s.answered && !s.rejected;
  }) || null;
}

function questionPanelHTML(reqID) {
  const state = questionStates.get(reqID);
  if (!state) return '';
  const req = state.req;
  const n = req.questions.length;
  const tab = Math.min(Math.max(activeQuestionTab, 0), n - 1);
  const q = req.questions[tab];
  const sel = state.answers[tab] || [];
  const custom = q.custom !== false;
  const tabs = n > 1 ? `
      <div class="flex gap-0.5 mb-2 border-b border-neutral-800">
        ${req.questions.map((qq, qi) => `
          <button class="q-tab px-2.5 py-1.5 rounded-t-lg text-xs ${qi === tab ? 'bg-neutral-800 text-sky-300' : 'text-neutral-500 hover:text-neutral-300'}" data-act="tab" data-tab="${qi}">
            ${esc(qq.header || 'Вопрос ' + (qi + 1))}
          </button>`).join('')}
      </div>` : '';
  const options = (q.options || []).map(o => {
    const active = sel.includes(o.label);
    return `<button class="px-3 py-1.5 rounded-lg border text-xs ${active ? 'border-sky-600 text-sky-300 bg-sky-950/40' : 'border-neutral-700 hover:border-sky-700 text-neutral-300'}" data-act="opt" data-label="${esc(o.label)}" title="${esc(o.description || '')}">${esc(o.label)}</button>`;
  }).join('');
  const showFooter = n > 1 || q.multiple === true;
  const footerBtn = !showFooter ? '' : n > 1 ? `
      <button class="px-3 py-1.5 rounded-lg text-xs font-medium ${sel.length ? 'bg-sky-600 hover:bg-sky-500 text-white' : 'bg-neutral-800 text-neutral-600 pointer-events-none'}" data-act="next">${tab === n - 1 ? 'Ответить' : 'Далее'}</button>` : `
      <button class="px-3 py-1.5 rounded-lg text-xs font-medium ${sel.length ? 'bg-sky-600 hover:bg-sky-500 text-white' : 'bg-neutral-800 text-neutral-600 pointer-events-none'}" data-act="send">Ответить</button>`;
  return `
    <div class="q-panel bg-neutral-900 border border-sky-800 rounded-xl px-4 py-3" data-qid="${esc(reqID)}">
      <div class="flex items-center gap-2 mb-1.5">
        <span class="text-sky-400">❓</span>
        <span class="text-xs font-semibold text-sky-300">${n > 1 ? esc(q.header || 'Вопрос ' + (tab + 1)) : 'Вопрос агента'}</span>
        ${n > 1 ? `<span class="text-[11px] text-neutral-500">${tab + 1}/${n}</span>` : ''}
        ${questionQueue.length > 1 ? `<span class="text-[11px] text-neutral-600">· ещё ${questionQueue.length - 1} в очереди</span>` : ''}
        <button class="ml-auto px-2 py-0.5 rounded border border-neutral-700 hover:border-red-800 hover:text-red-400 text-[11px] text-neutral-500" data-act="reject">отклонить</button>
      </div>
      ${tabs}
      <div class="text-sm text-neutral-200 mb-2">${esc(q.question)}</div>
      <div class="flex flex-wrap gap-1.5 mb-2">${options}</div>
      ${showFooter && sel.length ? `<div class="text-[11px] text-sky-400 mb-1.5">выбрано: ${esc(sel.join(', '))}</div>` : ''}
      ${custom ? `
      <div class="flex gap-1.5 mb-2">
        <input class="q-custom flex-1 bg-neutral-800 border border-neutral-700 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-sky-600" placeholder="Свой вариант…">
        <button class="px-2.5 py-1.5 rounded-lg border border-neutral-700 hover:border-sky-600 text-xs" data-act="custom">${n > 1 ? 'Сохранить' : 'Отправить'}</button>
      </div>` : ''}
      ${footerBtn || (n > 1 && tab > 0) ? `
      <div class="flex items-center justify-end gap-2">
        ${n > 1 && tab > 0 ? '<button class="px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-xs" data-act="back">← Назад</button>' : ''}
        ${footerBtn}
      </div>` : ''}
    </div>`;
}

async function sendQuestion(reqID) {
  const state = questionStates.get(reqID);
  if (!state) return;
  try {
    await api.post(`/api/sessions/${activeSessionId}/question/${reqID}/answer`, { answers: state.answers.map(a => a || []) });
  } catch (err) { toast(err.message, 'error'); }
}

function advanceQuestion() {
  const reqID = currentPending();
  if (!reqID) return;
  const state = questionStates.get(reqID);
  const n = state.req.questions.length;
  if (activeQuestionTab < n - 1) {
    activeQuestionTab += 1;
    renderQuestionPanel();
  } else {
    sendQuestion(reqID);
  }
}

function renderQuestionPanel() {
  const area = $('#chat-input-area');
  if (!area) return;
  const reqID = currentPending();
  if (!reqID) {
    activeQuestionID = null;
    activeQuestionTab = 0;
    setupChatInput();
    return;
  }
  activeQuestionID = reqID;
  area.innerHTML = questionPanelHTML(reqID);
  const panel = $('.q-panel', area);
  if (!panel) return;
  panel.onclick = async (e) => {
    const btn = e.target.closest('[data-act]');
    if (!btn) return;
    const act = btn.dataset.act;
    if (act === 'reject') {
      try { await api.post(`/api/sessions/${activeSessionId}/question/${reqID}/reject`); }
      catch (err) { toast(err.message, 'error'); }
      return;
    }
    const state = questionStates.get(reqID);
    if (!state) return;
    const n = state.req.questions.length;
    if (act === 'tab') {
      activeQuestionTab = +btn.dataset.tab;
      renderQuestionPanel();
      return;
    }
    if (act === 'back') {
      activeQuestionTab = Math.max(0, activeQuestionTab - 1);
      renderQuestionPanel();
      return;
    }
    if (act === 'opt') {
      const tab = activeQuestionTab;
      const q = state.req.questions[tab];
      const label = btn.dataset.label;
      if (q.multiple === true) {
        const arr = state.answers[tab] || [];
        state.answers[tab] = arr.includes(label) ? arr.filter(x => x !== label) : [...arr, label];
        renderQuestionPanel();
      } else {
        state.answers[tab] = [label];
        if (n > 1) advanceQuestion(); else sendQuestion(reqID);
      }
      return;
    }
    if (act === 'custom') {
      const input = $('input.q-custom', panel);
      const text = (input ? input.value : '').trim();
      if (!text) return;
      state.answers[activeQuestionTab] = [text];
      if (n > 1) renderQuestionPanel(); else sendQuestion(reqID);
      return;
    }
    if (act === 'next') { advanceQuestion(); return; }
    if (act === 'send') { sendQuestion(reqID); }
  };
  panel.onkeydown = (e) => {
    if (e.key === 'Enter' && e.target && e.target.classList && e.target.classList.contains('q-custom')) {
      const btn = $('button[data-act="custom"]', panel);
      if (btn) btn.click();
    }
  };
  const input = $('input.q-custom', panel);
  if (input) input.focus();
}

function upsertQuestion(req, ts) {
  if (!req || !req.id || !Array.isArray(req.questions)) return;
  let state = questionStates.get(req.id);
  if (!state) {
    state = { req, answers: req.questions.map(() => []) };
    questionStates.set(req.id, state);
  }
  if (!state.answered && !state.rejected && !questionQueue.includes(req.id)) {
    questionQueue.push(req.id);
  }
  if (!activeQuestionID) renderQuestionPanel();
}

function markQuestionAnswered(props) {
  const state = questionStates.get(props.requestID);
  if (!state) return;
  state.answered = (props.answers || []).map(a => a || []);
  questionQueue = questionQueue.filter(id => id !== props.requestID);
  if (activeQuestionID === props.requestID) {
    activeQuestionTab = 0;
    renderQuestionPanel();
  }
}

function markQuestionRejected(props) {
  const state = questionStates.get(props.requestID);
  if (!state) return;
  state.rejected = true;
  questionQueue = questionQueue.filter(id => id !== props.requestID);
  if (activeQuestionID === props.requestID) {
    activeQuestionTab = 0;
    renderQuestionPanel();
  }
}

function renderChat(sessionId) {
  activeSessionId = sessionId;
  questionStates.clear();
  questionQueue = [];
  activeQuestionID = null;
  activeQuestionTab = 0;
  const main = $('#main');
  main.innerHTML = `
    <div class="h-full flex flex-col">
      <div class="flex items-center justify-between px-6 py-3 border-b border-neutral-800">
        <div class="flex items-center gap-3">
          <button id="chat-back" class="text-neutral-500 hover:text-neutral-200 text-lg leading-none">&larr;</button>
          <div>
            <div class="font-semibold" id="chat-title">…</div>
            <div class="text-xs text-neutral-500" id="chat-agent"></div>
          </div>
        </div>
        <div class="flex items-center gap-3">
          <span id="chat-status"></span>
          <button id="chat-abort" class="px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-xs">Прервать</button>
          <button id="chat-restart" class="px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-xs">Перезапустить</button>
        </div>
      </div>
      <div class="flex-1 flex overflow-hidden">
        <div class="flex-1 overflow-y-auto p-6 space-y-2" id="chat-messages"></div>
        <div class="w-72 shrink-0 border-l border-neutral-800 overflow-y-auto p-4 space-y-4 hidden md:block" id="chat-side"></div>
      </div>
      <div class="p-4 border-t border-neutral-800" id="chat-input-area"></div>
    </div>`;
  $('#chat-back').onclick = () => showView('sessions');
  let ws = null;

  setupChatActions();
  setupChatInput();
  $('#chat-abort').onclick = async () => { try { await api.post(`/api/sessions/${sessionId}/abort`); } catch (e) { toast(e.message, 'error'); } };
  $('#chat-restart').onclick = async () => {
    $('#chat-messages').innerHTML = '<div class="text-neutral-500 text-sm">перезапуск воркера…</div>';
    partEls.clear();
    try { await api.post(`/api/sessions/${sessionId}/restart`); } catch (e) { toast(e.message, 'error'); }
  };

  connect();
  async function connect() {
    ws = new WebSocket(`ws://${location.host}/ws/sessions/${sessionId}`);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === 'ping') return;
      if (msg.type === 'status') setStatus(msg.status, msg.error);
      if (msg.type === 'event') handleEvent(msg.event, msg.ts);
      if (msg.type === 'done') {
        generating = false;
        setStatus(msg.status, msg.error);
        if (msg.status === 'completed' && msg.result) renderTranscript(msg.result);
      }
    };
    ws.onclose = () => { setTimeout(connect, 3000); };
  }

  async function loadMeta() {
    try {
      const s = await api.get(`/api/sessions/${sessionId}`);
      $('#chat-title').textContent = s.title;
      $('#chat-agent').textContent = `${s.agent_name} · ${s.model}`;
      setStatus(s.status, s.error);
      if (s.prompt && !$('#chat-messages').children.length) addUserBubble(s.prompt);
      const m = await api.get(`/api/sessions/${sessionId}/messages`);
      if (m.result) {
        renderTranscript(m.result);
        m.events.forEach(ev => {
          try {
            const evt = { type: ev.type, properties: JSON.parse(ev.payload) };
            if (evt.type && evt.type.startsWith('question.')) handleEvent(evt, ev.ts);
          } catch {}
        });
      } else {
        m.events.forEach(ev => { try { handleEvent({ type: ev.type, properties: JSON.parse(ev.payload) }, ev.ts); } catch {} });
      }
      (m.questions || []).forEach(q => upsertQuestion(q));
      renderQuestionPanel();
    } catch (e) { toast(e.message, 'error'); }
  }
  loadMeta();
}

function setupChatInput() {
  const area = $('#chat-input-area');
  if (!area) return;
  area.innerHTML = `
    <div class="flex gap-2">
      <textarea id="chat-input" rows="2" placeholder="Сообщение агенту… (Enter — отправить)"
        class="flex-1 bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-sky-600 resize-none"></textarea>
      <button id="chat-send" class="px-5 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium self-end h-9">Отправить</button>
    </div>`;
  const sendPrompt = async () => {
    const input = $('#chat-input');
    const text = input.value.trim();
    if (!text || generating) return;
    input.value = '';
    addUserBubble(text);
    generating = true;
    try {
      await api.post(`/api/sessions/${activeSessionId}/prompt`, { text });
    } catch (e) { toast(e.message, 'error'); generating = false; }
  };
  $('#chat-send').onclick = sendPrompt;
  $('#chat-input').onkeydown = (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendPrompt(); } };
  const input = $('#chat-input');
  if (input) input.focus();
}

function setupChatActions() {
  const el = $('#chat-messages');
  if (!el) return;
  el.onclick = async (e) => {
    const btn = e.target.closest('[data-copy="reason"]');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    const details = btn.closest('details');
    const src = details ? $('[data-reason-text]', details) : null;
    const text = src ? src.textContent : '';
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      toast('Скопировано', 'ok');
    } catch {
      toast('Не удалось скопировать', 'error');
    }
  };
}

function setStatus(status, error) {
  const el = $('#chat-status');
  if (!el) return;
  el.innerHTML = badge(status);
  if (error) toast(error, 'error');
}

function addUserBubble(text) {
  const el = $('#chat-messages');
  if (!el) return;
  const div = document.createElement('div');
  div.className = 'flex justify-end';
  div.innerHTML = `<div class="md max-w-[80%] bg-sky-900/60 border border-sky-800 rounded-2xl rounded-br-sm px-4 py-2.5 text-sm" data-content>${renderMarkdown(text)}</div>`;
  el.appendChild(div);
  pendingUserBubble = div;
  el.scrollTop = el.scrollHeight;
}


function renderTranscript(messages) {
  const el = $('#chat-messages');
  if (!el) return;
  el.innerHTML = '';
  pendingUserBubble = null;
  msgRoles.clear();
  rawTexts.clear();
  (messages || []).forEach(msg => {
    const info = msg.info || {};
    const role = info.role || 'assistant';
    if (role === 'user') {
      upsertMessage({ id: info.id, role });
      const wrap = $(`[data-msg="${info.id}"]`, el);
      const content = $('[data-content]', wrap);
      (msg.parts || []).forEach(part => {
        if (!part.id || part.type !== 'text') return;
        const pkey = `${info.id}:${part.id}`;
        const p = document.createElement('div');
        p.dataset.p = pkey;
        p.dataset.raw = part.text || '';
        p.className = 'md leading-relaxed';
        p.innerHTML = renderMarkdown(part.text || '');
        rawTexts.set(pkey, part.text || '');
        content.appendChild(p);
      });
      return;
    }
    if (info.error) {
      const wrap = ensureKindBubble(info.id, 'error');
      if (wrap) {
        const err = info.error;
        const text = err && err.data ? err.data.message : err && err.message ? err.message : JSON.stringify(err);
        const p = document.createElement('div');
        p.className = 'text-red-400 text-xs whitespace-pre-wrap';
        p.textContent = String(text || 'ошибка выполнения');
        $('[data-content]', wrap).appendChild(p);
      }
    }
    (msg.parts || []).forEach(part => {
      if (!part.id) return;
      const pkey = `${info.id}:${part.id}`;
      if (part.type === 'text') {
        if (!(part.text || '').trim()) return;
        const wrap = ensureKindBubble(info.id, 'text');
        if (!wrap) return;
        const p = document.createElement('div');
        p.dataset.p = pkey;
        p.dataset.raw = part.text || '';
        p.className = 'md leading-relaxed';
        p.innerHTML = renderMarkdown(part.text || '');
        rawTexts.set(pkey, part.text || '');
        $('[data-content]', wrap).appendChild(p);
      } else if (part.type === 'tool') {
        const wrap = ensureKindBubble(info.id, `tool:${part.id}`);
        if (!wrap) return;
        const p = document.createElement('div');
        p.dataset.p = pkey;
        p.innerHTML = toolCard(part);
        $('[data-content]', wrap).appendChild(p);
      } else if (part.type === 'reasoning') {
        if (!(part.text || '').trim()) return;
        const wrap = ensureKindBubble(info.id, `reasoning:${part.id}`);
        if (!wrap) return;
        const p = document.createElement('div');
        p.dataset.p = pkey;
        p.innerHTML = reasonCard(part);
        $('[data-content]', wrap).appendChild(p);
      }
    });
  });
  el.scrollTop = el.scrollHeight;
}

/* ---------- agents ---------- */
async function renderAgents() {
  const main = $('#main');
  main.innerHTML = `
    <div class="h-full flex flex-col">
      <div class="flex items-center justify-between px-6 py-4 border-b border-neutral-800">
        <div class="text-lg font-semibold">Агенты</div>
        <div class="flex gap-2">
          <button id="new-skill" class="px-4 py-2 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-sm">Скиллы</button>
          <button id="new-agent" class="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium">Новый агент</button>
        </div>
      </div>
      <div class="flex-1 overflow-y-auto p-6" id="agents-list"></div>
    </div>`;
  $('#new-agent').onclick = () => agentModal();
  $('#new-skill').onclick = () => showView('skills');
  await refreshAgentsList();
}

async function refreshAgentsList() {
  let data;
  try { data = await api.get('/api/agents' + projQuery()); } catch (e) { return; }
  const el = $('#agents-list');
  if (!el) return;
  if (!data.length) {
    el.innerHTML = `<div class="text-neutral-500 text-sm">Агентов нет. Создайте первого — с него начнутся сессии.</div>`;
    return;
  }
  el.innerHTML = data.map(a => `
    <div class="rounded-xl border border-neutral-800 hover:border-neutral-700 p-5 mb-3">
      <div class="flex items-start justify-between">
        <div>
          <div class="font-semibold text-lg flex items-center gap-2">
            ${esc(a.name)}
            ${a.is_default ? '<span class="text-xs text-sky-400">default</span>' : ''}
          </div>
          <div class="text-sm text-neutral-500">${esc(a.description || '')}</div>
        </div>
        <div class="flex gap-2 text-xs">
          <button class="edit px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800" data-id="${a.id}">изменить</button>
          <button class="del px-3 py-1.5 rounded-lg border border-red-900 text-red-400 hover:bg-red-950" data-id="${a.id}">удалить</button>
        </div>
      </div>
      <div class="flex flex-wrap gap-2 mt-3 text-xs">
        <span class="px-2 py-1 rounded bg-neutral-800 mono">${esc(a.model)}</span>
        <span class="px-2 py-1 rounded bg-neutral-800">${esc(a.mode)}</span>
        ${a.temperature != null ? `<span class="px-2 py-1 rounded bg-neutral-800">temp ${esc(a.temperature)}</span>` : ''}
        ${a.variant ? `<span class="px-2 py-1 rounded bg-neutral-800">вариант: ${esc(a.variant)}</span>` : ''}
        ${a.mcp.map(m => `<span class="px-2 py-1 rounded bg-purple-900/50 mono">mcp:${esc(m.name)}</span>`).join('')}
        ${a.skills.map(s => `<span class="px-2 py-1 rounded bg-emerald-900/50 mono">skill:${esc(s.name)}</span>`).join('')}
      </div>
      ${a.system_prompt ? `<pre class="mt-3 text-xs text-neutral-500 mono whitespace-pre-wrap border-t border-neutral-800 pt-2">${esc(a.system_prompt.slice(0, 300))}${a.system_prompt.length > 300 ? '…' : ''}</pre>` : ''}
    </div>`).join('');
  $$('.edit', el).forEach(b => b.onclick = () => agentModal(+b.dataset.id));
  $$('.del', el).forEach(b => b.onclick = async () => {
    if (!confirm('Удалить агента?')) return;
    await api.del(`/api/agents/${b.dataset.id}`);
    await refreshAgentsList();
  });
}

async function agentModal(agentId) {
  const isEdit = !!agentId;
  const a = isEdit ? await api.get(`/api/agents/${agentId}`) : {
    name: '', description: '', mode: 'primary', model: 'deepseek/deepseek-chat',
    temperature: '', system_prompt: '', permission: '"allow"', is_default: false, mcp: [], skills: [],
  };
  const skills = await api.get('/api/skills');
  let provs = [];
  try { provs = await api.get('/api/providers' + projQuery()); } catch {}
  let projects = [];
  if (!currentProject) {
    try { projects = await api.get('/api/projects'); } catch {}
  }
  const projSel = currentProject
    ? ''
    : formSelect('Проект', 'project_id', projects.map(p => ({ v: p.id, l: p.name })), a.project_id ?? (projects[0] || {}).id);
  const body = `
    <div class="flex gap-2 mb-4 text-sm">
      <button id="tab-general" class="px-3 py-1.5 rounded-lg bg-neutral-800">Основное</button>
      <button id="tab-system" class="px-3 py-1.5 rounded-lg hover:bg-neutral-800/60">System-промпт</button>
      <button id="tab-mcp" class="px-3 py-1.5 rounded-lg hover:bg-neutral-800/60">MCP (${a.mcp.length})</button>
      <button id="tab-skills" class="px-3 py-1.5 rounded-lg hover:bg-neutral-800/60">Скиллы (${a.skills.length})</button>
    </div>
    <div id="tab-general-pane">
      ${formInput('Имя (латиница, дефисы)', 'name', a.name, 'my-agent')}
      ${formInput('Описание', 'description', a.description)}
      ${projSel}
      <div class="mb-3">
        <span class="text-neutral-400 text-xs">Провайдер</span>
        <select id="model-provider" class="mt-1 w-full bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-sky-600"></select>
      </div>
      <div class="mb-3">
        <span class="text-neutral-400 text-xs">Модель</span>
        <div class="relative mt-1" id="model-select-wrap">
          <button id="model-trigger" type="button" class="w-full flex items-center justify-between bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-sm hover:border-neutral-600 focus:outline-none">
            <span class="model-label mono text-sky-300 truncate">—</span>
            <span class="text-neutral-500 text-xs">▾</span>
          </button>
          <div id="model-menu" class="hidden absolute left-0 right-0 top-full mt-1 bg-neutral-900 border border-neutral-700 rounded-lg shadow-2xl z-30 overflow-hidden">
            <input id="model-filter" placeholder="Фильтр…" class="w-full bg-neutral-800 border-b border-neutral-700 px-3 py-1.5 text-xs focus:outline-none">
            <div id="model-options" class="max-h-52 overflow-y-auto py-1"></div>
          </div>
        </div>
        <div id="model-state" class="text-xs text-neutral-600 mt-1"></div>
      </div>
      <div class="mb-3 hidden" id="variant-wrap">
        <span class="text-neutral-400 text-xs">Вариант (reasoning effort)</span>
        <select id="model-variant" class="mt-1 w-full bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-sky-600"></select>
      </div>
      <input type="hidden" name="model">
      <input type="hidden" name="variant">
      <div class="grid grid-cols-2 gap-3">
        ${formSelect('Режим', 'mode', [{ v: 'primary', l: 'primary' }, { v: 'subagent', l: 'subagent' }, { v: 'all', l: 'all' }], a.mode)}
        ${formInput('Temperature (необязательно)', 'temperature', a.temperature ?? '', '')}
      </div>
      ${formInput('Permission (JSON)', 'permission', a.permission, '"allow"')}
      <label class="flex items-center gap-2 text-sm mb-3">
        <input type="checkbox" name="is_default" ${a.is_default ? 'checked' : ''} class="accent-sky-600">
        <span class="text-neutral-400 text-xs">Агент по умолчанию</span>
      </label>
    </div>
    <div id="tab-system-pane" class="hidden">
      ${formArea('System-промпт (становится телом agent-файла opencode)', 'system_prompt', a.system_prompt, 12)}
    </div>
    <div id="tab-mcp-pane" class="hidden">
      <div class="text-xs text-neutral-500 mb-2">Каталог — добавьте одной кнопкой:</div>
      <div id="mcp-catalog-list" class="space-y-1.5 mb-3"></div>
      <div class="border-t border-neutral-800 pt-3 mb-2 text-xs text-neutral-500">Свои MCP-серверы (попадут в opencode.json агента). Команда/headers/environment — JSON.</div>
      <div id="mcp-list" class="space-y-2">${a.mcp.map(mcpRow).join('')}</div>
      <button id="add-mcp" class="mt-3 px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-xs">+ MCP-сервер</button>
    </div>
    <div id="tab-skills-pane" class="hidden">
      <div class="text-xs text-neutral-500 mb-3">Скиллы прикрепляются к агенту и попадают в .opencode/skills его воркера.</div>
      <div id="skills-checkbox" class="space-y-2">${skills.map(s => `
        <label class="flex items-center gap-2 text-sm">
          <input type="checkbox" data-skill="${s.id}" class="accent-sky-600" ${a.skills.some(x => x.id === s.id) ? 'checked' : ''}>
          <span class="mono text-xs text-sky-300">${esc(s.name)}</span>
          <span class="text-xs text-neutral-500">— ${esc(s.description)}</span>
        </label>`).join('') || '<div class="text-neutral-600 text-sm">Скиллов пока нет — создайте во вкладке «Скиллы» на странице агентов.</div>'}
      </div>
    </div>`;
  openModal(isEdit ? `Агент: ${a.name}` : 'Новый агент', body, async (close) => {
    const f = { ...readForm($('#tab-general-pane')), ...readForm($('#tab-system-pane')) };
    f.is_default = $('[name="is_default"]', $('#tab-general-pane')).checked;
    f.project_id = currentProject || f.project_id || null;
    const mcp = $$('#mcp-list [data-mcp]').map(el => {
      const d = el.dataset;
      return { id: d.mcpId || undefined, name: $('[name="mcp-name"]', el).value, type: $('[name="mcp-type"]', el).value,
        command: $('[name="mcp-command"]', el).value, url: $('[name="mcp-url"]', el).value,
        headers: $('[name="mcp-headers"]', el).value, environment: $('[name="mcp-env"]', el).value,
        enabled: $('[name="mcp-enabled"]', el).checked };
    });
    const skillIds = $$('#skills-checkbox [data-skill]:checked').map(el => +el.dataset.skill);
    if (isEdit) await api.put(`/api/agents/${agentId}`, f);
    else { const created = await api.post('/api/agents', f); agentId = created.id; }
    for (const m of mcp) {
      if (m.id) await api.put(`/api/agents/${agentId}/mcp/${m.id}`, m);
      else if (m.name) await api.post(`/api/agents/${agentId}/mcp`, m);
    }
    await api.put(`/api/agents/${agentId}/skills`, { skill_ids: skillIds });
    close();
    await refreshAgentsList();
  });
  (function initModelWidget() {
    const pane = $('#tab-general-pane');
    const rawModel = a.model || '';
    const parts = rawModel.includes('/') ? rawModel.split('/') : ['', rawModel];
    let curProvider = parts[0] || '';
    let curModelId = parts[1] || '';
    let curVariant = a.variant || '';
    let modelsForProvider = [];

    const providerList = provs.map(p => ({ id: p.id, label: p.label ? `${p.label} (${p.id})` : p.id }));
    if (curProvider && !providerList.some(p => p.id === curProvider)) {
      providerList.unshift({ id: curProvider, label: `${curProvider} (нет в проекте)` });
    }

    const setModelMenu = (open) => {
      const menu = $('#model-menu');
      if (menu) menu.classList.toggle('hidden', !open);
    };

    function updateModelSummary() {
      const modelInput = $('[name="model"]', pane);
      const variantInput = $('[name="variant"]', pane);
      if (modelInput) modelInput.value = curModelId ? `${curProvider}/${curModelId}` : '';
      if (variantInput) variantInput.value = curVariant;
      const label = $('.model-label', pane);
      if (label) label.textContent = curModelId ? `${curProvider}/${curModelId}` : 'выберите модель';
    }

    function updateVariantSelect() {
      const wrap = $('#variant-wrap');
      const sel = $('#model-variant');
      if (!wrap || !sel) return;
      const m = modelsForProvider.find(x => x.id === curModelId);
      const variants = (m && m.variants) ? m.variants : {};
      const keys = Object.keys(variants);
      if (!keys.length) {
        wrap.classList.add('hidden');
        curVariant = '';
        updateModelSummary();
        return;
      }
      wrap.classList.remove('hidden');
      sel.innerHTML = '<option value="">по умолчанию</option>' +
        keys.map(k => `<option value="${esc(k)}">${esc(k)}${variants[k] && variants[k].reasoningEffort ? ` — reasoning effort: ${esc(variants[k].reasoningEffort)}` : ''}</option>`).join('');
      sel.value = keys.includes(curVariant) ? curVariant : '';
      curVariant = sel.value;
      updateModelSummary();
    }

    function renderModelOptions(filter = '') {
      const box = $('#model-options');
      if (!box) return;
      const q = filter.toLowerCase();
      const opts = modelsForProvider.filter(m => m.id.toLowerCase().includes(q) || (m.name || '').toLowerCase().includes(q));
      box.innerHTML = opts.length ? opts.map(m => `
        <button type="button" data-model="${esc(m.id)}" class="w-full text-left px-3 py-1.5 text-sm hover:bg-neutral-800 ${m.id === curModelId ? 'text-sky-300' : ''}">
          <span class="mono">${esc(m.id)}</span>
          ${m.name ? `<span class="text-neutral-500 text-xs">— ${esc(m.name)}</span>` : ''}
          ${m.variants && Object.keys(m.variants).length ? '<span class="text-neutral-600 text-[10px]">(варианты)</span>' : ''}
        </button>`).join('')
        : '<div class="px-3 py-2 text-xs text-neutral-600">нет моделей — нажмите «загрузить»</div>';
      $$('[data-model]', box).forEach(b => b.onclick = () => {
        curModelId = b.dataset.model;
        updateModelSummary();
        setModelMenu(false);
        updateVariantSelect();
      });
    }

    async function loadModelsForProvider(pid) {
      const state = $('#model-state');
      modelsForProvider = [];
      renderModelOptions();
      $('#variant-wrap').classList.add('hidden');
      let data = null;
      try {
        data = await api.get(`/api/providers/${pid}/models`);
      } catch {}
      if (!data || !(data.models || []).length) {
        if (state) state.textContent = 'модели не загружены — поднимаем opencode (~10–20с)…';
        try {
          data = await api.post(`/api/providers/${pid}/refresh-models`);
        } catch (e) {
          if (state) state.textContent = `ошибка: ${e.message}`;
          return;
        }
        if (!data.ok) {
          if (state) state.textContent = `ошибка: ${data.error || 'провайдер недоступен'}`;
          return;
        }
      }
      const details = data.details || data.model_details || {};
      modelsForProvider = (data.models || []).map(id => ({
        id,
        name: (details[id] || {}).name || '',
        variants: (details[id] || {}).variants || {},
      }));
      if (state) state.textContent = modelsForProvider.length
        ? `${modelsForProvider.length} модел${modelsForProvider.length === 1 ? 'ь' : 'ей'}`
        : 'моделей нет — проверьте ключ провайдера';
      renderModelOptions();
      updateVariantSelect();
    }

    const sel = $('#model-provider');
    sel.innerHTML = providerList.map(p => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join('')
      || '<option value="">нет провайдеров</option>';
    if (curProvider && providerList.some(p => p.id === curProvider)) sel.value = curProvider;
    else if (providerList.length) { sel.value = providerList[0].id; curProvider = providerList[0].id; curModelId = ''; }
    sel.onchange = () => {
      curProvider = sel.value;
      curModelId = '';
      curVariant = '';
      updateModelSummary();
      if (curProvider) loadModelsForProvider(curProvider);
    };
    $('#model-trigger').onclick = (e) => { e.stopPropagation(); setModelMenu($('#model-menu').classList.contains('hidden')); };
    $('#model-filter').oninput = (e) => renderModelOptions(e.target.value);
    $('#model-variant').onchange = (e) => { curVariant = e.target.value; updateModelSummary(); };

    if (!providerList.length) {
      const state = $('#model-state');
      if (state) state.textContent = 'Нет провайдеров в проекте — добавьте в «Настройки → Провайдеры»';
      updateModelSummary();
      return;
    }
    updateModelSummary();
    loadModelsForProvider(curProvider);
  })();
  const tabs = { 'tab-general': 'tab-general-pane', 'tab-system': 'tab-system-pane', 'tab-mcp': 'tab-mcp-pane', 'tab-skills': 'tab-skills-pane' };
  Object.entries(tabs).forEach(([btn, pane]) => {
    $(`#${btn}`).onclick = () => {
      Object.values(tabs).forEach(p => $(`#${p}`).classList.add('hidden'));
      $(`#${pane}`).classList.remove('hidden');
      Object.entries(tabs).forEach(([b, p]) => $(`#${b}`).className = `px-3 py-1.5 rounded-lg ${b === btn ? 'bg-neutral-800' : 'hover:bg-neutral-800/60'}`);
    };
  });
  $('#add-mcp').onclick = () => {
    const div = document.createElement('div');
    div.innerHTML = mcpRow({ name: '', type: 'local', command: '', url: '', headers: '', environment: '', enabled: true });
    $('#mcp-list').appendChild(div.firstElementChild);
  };
  (async () => {
    let catalog = [];
    try { catalog = await api.get('/api/mcp-catalog'); } catch {}
    const listEl = $('#mcp-catalog-list');
    if (!catalog.length) {
      listEl.innerHTML = '<div class="text-neutral-600 text-xs">Каталог пуст.</div>';
      return;
    }
    listEl.innerHTML = catalog.map(c => `
      <div class="flex items-center gap-2 border border-neutral-800 rounded-lg px-2.5 py-1.5">
        <div class="flex-1 min-w-0">
          <div class="text-xs mono text-sky-300 truncate">${esc(c.name)}${c.builtin ? ' <span class="text-neutral-500">· встроенный</span>' : ''}</div>
          <div class="text-xs text-neutral-500 truncate">${esc(c.description || '')}</div>
        </div>
        <button data-cat-attach="${c.id}" class="px-2 py-1 rounded border border-sky-800 text-sky-300 hover:bg-sky-950 text-xs shrink-0">＋</button>
      </div>`).join('');
    $$('[data-cat-attach]', listEl).forEach(b => b.onclick = async () => {
      const c = catalog.find(x => x.id === +b.dataset.catAttach);
      if (!c) return;
      const existing = $$('#mcp-list [data-mcp]').find(el => $(`[name="mcp-name"]`, el).value === c.name);
      if (existing) return toast(`MCP «${c.name}» уже в списке`, 'info');
      if (isEdit) {
        try {
          await api.post(`/api/mcp-catalog/${c.id}/attach`, { agent_id: agentId });
        } catch (e) { toast(e.message, 'error'); return; }
      }
      const div = document.createElement('div');
      div.innerHTML = mcpRow({
        name: c.name, type: c.type, command: c.command || '', url: c.url || '',
        headers: c.headers || '', environment: c.environment || '', enabled: true,
      });
      $('#mcp-list').appendChild(div.firstElementChild);
      toast(`MCP «${c.name}» добавлен${isEdit ? ' агенту' : ' (сохранится вместе с агентом)'}`, 'ok');
    });
  })();
  $('#mcp-list').onclick = (e) => {
    if (e.target.closest('[data-del-mcp]')) e.target.closest('[data-del-mcp]').remove();
    if (e.target.closest('[data-mcp-type]')) {
      const row = e.target.closest('[data-mcp]');
      const isLocal = row.querySelector('[name="mcp-type"]').value === 'local';
      row.querySelectorAll('[data-if="local"]').forEach(x => x.classList.toggle('hidden', !isLocal));
      row.querySelectorAll('[data-if="remote"]').forEach(x => x.classList.toggle('hidden', isLocal));
    }
  };
}

function mcpRow(m) {
  return `<div data-mcp data-mcp-id="${esc(m.id || '')}" class="border border-neutral-800 rounded-lg p-3">
    <div class="flex gap-2 mb-2">
      <input name="mcp-name" value="${esc(m.name)}" placeholder="имя сервера" class="flex-1 bg-neutral-800 border border-neutral-700 rounded px-2 py-1 text-xs mono">
      <select name="mcp-type" class="bg-neutral-800 border border-neutral-700 rounded px-2 py-1 text-xs" data-mcp-type>
        <option value="local" ${m.type === 'local' ? 'selected' : ''}>local</option>
        <option value="remote" ${m.type === 'remote' ? 'selected' : ''}>remote</option>
      </select>
      <label class="flex items-center gap-1 text-xs text-neutral-400"><input type="checkbox" name="mcp-enabled" ${m.enabled ? 'checked' : ''} class="accent-sky-600">вкл</label>
      <button data-del-mcp class="text-neutral-600 hover:text-red-400 text-xs px-1">✕</button>
    </div>
    <div data-if="local" class="${m.type === 'remote' ? 'hidden' : ''}">
      <input name="mcp-command" value="${esc(m.command)}" placeholder='["npx", "-y", "@playwright/mcp"]' class="w-full bg-neutral-800 border border-neutral-700 rounded px-2 py-1 text-xs mono mb-2">
      <input name="mcp-env" value="${esc(m.environment)}" placeholder='{"BROWSER": "chromium"} (JSON)' class="w-full bg-neutral-800 border border-neutral-700 rounded px-2 py-1 text-xs mono">
    </div>
    <div data-if="remote" class="${m.type === 'local' ? 'hidden' : ''}">
      <input name="mcp-url" value="${esc(m.url)}" placeholder="https://mcp.example.com" class="w-full bg-neutral-800 border border-neutral-700 rounded px-2 py-1 text-xs mono mb-2">
      <input name="mcp-headers" value="${esc(m.headers)}" placeholder='{"Authorization": "Bearer {env:TOKEN}"} (JSON)' class="w-full bg-neutral-800 border border-neutral-700 rounded px-2 py-1 text-xs mono">
    </div>
  </div>`;
}

/* ---------- skills ---------- */
async function renderSkills() {
  const main = $('#main');
  main.innerHTML = `
    <div class="h-full flex flex-col">
      <div class="flex items-center justify-between px-6 py-4 border-b border-neutral-800">
        <div>
          <div class="text-lg font-semibold">Скиллы</div>
          <div class="text-xs text-neutral-500">Библиотека скиллов. Прикрепить к агенту — в его карточке, вкладка «Скиллы». Каждый скилл становится SKILL.md в .opencode/skills воркера.</div>
        </div>
        <button id="new-skill" class="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium">Новый скилл</button>
      </div>
      <div class="flex-1 overflow-y-auto p-6" id="skills-list"></div>
    </div>`;
  $('#new-skill').onclick = () => skillModal();
  await refreshSkills();
}

async function refreshSkills() {
  let data;
  try { data = await api.get('/api/skills'); } catch (e) { return; }
  const el = $('#skills-list');
  if (!el) return;
  if (!data.length) {
    el.innerHTML = '<div class="text-neutral-500 text-sm">Скиллов нет. Создайте первый — он станет доступен всем агентам.</div>';
    return;
  }
  el.innerHTML = data.map(s => `
    <div class="rounded-xl border border-neutral-800 hover:border-neutral-700 p-5 mb-3">
      <div class="flex items-start justify-between">
        <div class="min-w-0">
          <div class="font-semibold flex items-center gap-2 flex-wrap">
            <span class="mono text-sky-300">${esc(s.name)}</span>
            <span class="text-xs text-neutral-500">агентов: ${s.agent_count || 0}</span>
          </div>
          <div class="text-sm text-neutral-500 mt-0.5">${esc(s.description || '')}</div>
          ${s.body ? `<details class="mt-2"><summary class="text-xs text-neutral-500 cursor-pointer hover:text-neutral-300">тело SKILL.md</summary><pre class="mt-1 text-xs mono text-neutral-400 whitespace-pre-wrap max-h-56 overflow-y-auto bg-neutral-800/50 rounded-lg p-2">${esc(s.body.slice(0, 3000))}${s.body.length > 3000 ? '…' : ''}</pre></details>` : ''}
        </div>
        <div class="flex gap-2 text-xs shrink-0 ml-3">
          <button class="edit px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800" data-id="${s.id}">изменить</button>
          <button class="del px-3 py-1.5 rounded-lg border border-red-900 text-red-400 hover:bg-red-950" data-id="${s.id}">удалить</button>
        </div>
      </div>
    </div>`).join('');
  $$('.edit', el).forEach(b => b.onclick = () => {
    const row = data.find(s => s.id === +b.dataset.id);
    skillModal(row);
  });
  $$('.del', el).forEach(b => b.onclick = async () => {
    if (!confirm('Удалить скилл? Он открепится от агентов.')) return;
    await api.del(`/api/skills/${b.dataset.id}`);
    await refreshSkills();
  });
}

function skillModal(s) {
  const isEdit = !!s;
  s = s || { name: '', description: '', body: '' };
  openModal(isEdit ? `Скилл: ${s.name}` : 'Новый скилл', `
    ${formInput('Имя (латиница, дефисы)', 'name', s.name, 'my-skill')}
    ${formInput('Описание (когда использовать)', 'description', s.description)}
    ${formArea('Тело SKILL.md', 'body', s.body, 12)}`,
    async (close) => {
      const f = readForm($('#modal-body'));
      if (!f.name.trim()) throw new Error('Имя обязательно');
      if (isEdit) await api.put(`/api/skills/${s.id}`, f);
      else await api.post('/api/skills', f);
      close();
      await refreshSkills();
    });
}

/* ---------- providers ---------- */
async function renderProviders() {
  const main = $('#main');
  main.innerHTML = `
    <div class="h-full flex flex-col">
      <div class="flex items-center justify-between px-6 py-4 border-b border-neutral-800">
        <div>
          <div class="text-lg font-semibold">Провайдеры</div>
          <div class="text-xs text-neutral-500">API-ключи попадают в воркеры как env. «Проверить» поднимает настоящий opencode-контейнер и делает тест-запрос к модели.</div>
        </div>
        <button id="new-provider" class="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium">Добавить провайдера</button>
      </div>
      <div class="flex-1 overflow-y-auto p-6" id="providers-list"></div>
    </div>`;
  $('#new-provider').onclick = () => providerModal();
  await refreshProviders();
}

async function refreshProviders() {
  let data;
  try { data = await api.get('/api/providers' + projQuery()); } catch (e) { return; }
  const el = $('#providers-list');
  if (!el) return;
  if (!data.length) {
    el.innerHTML = `<div class="text-neutral-500 text-sm">Провайдеров нет. Добавьте ключ — без него модели провайдера недоступны воркерам.<br>Ключи также можно оставлять в env брокера (DEEPSEEK_API_KEY и т.п.) — это резервный путь.</div>`;
    return;
  }
  el.innerHTML = data.map(p => `
    <div class="rounded-xl border border-neutral-800 hover:border-neutral-700 p-5 mb-3">
      <div class="flex items-start justify-between">
        <div>
          <div class="font-semibold flex items-center gap-2">
            <span class="mono text-sky-300">${esc(p.id)}</span>
            ${p.label ? `<span class="text-neutral-400 text-sm">${esc(p.label)}</span>` : ''}
            ${p.enabled ? '<span class="text-xs px-2 py-0.5 rounded bg-emerald-900/50">вкл</span>' : '<span class="text-xs px-2 py-0.5 rounded bg-neutral-800 text-neutral-500">выкл</span>'}
          </div>
          <div class="text-xs text-neutral-500 mt-0.5 mono">env: ${esc(p.env_var)}</div>
        </div>
        <div class="flex gap-2 text-xs">
          <button class="check px-3 py-1.5 rounded-lg border border-sky-800 text-sky-300 hover:bg-sky-950" data-id="${p.id}">Проверить</button>
          <button class="edit px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800" data-id="${p.id}">изменить</button>
          <button class="del px-3 py-1.5 rounded-lg border border-red-900 text-red-400 hover:bg-red-950" data-id="${p.id}">удалить</button>
        </div>
      </div>
      <div class="text-xs mt-2">
        ${p.has_key ? `<span class="text-emerald-400">ключ: ${esc(p.key_masked)}</span>` : '<span class="text-neutral-500">ключа нет</span>'}
        ${p.last_check_at ? `<span class="text-neutral-500 ml-3">проверен: ${esc(p.last_check_at)}</span>` : ''}
      </div>
      ${p.models && p.models.length ? `<div class="flex flex-wrap gap-1.5 mt-2">${p.models.slice(0, 12).map(m => `<span class="px-1.5 py-0.5 rounded bg-neutral-800 mono text-xs">${esc(m)}</span>`).join('')}${p.models.length > 12 ? `<span class="text-neutral-500 text-xs">+${p.models.length - 12}</span>` : ''}</div>` : ''}
      ${p.last_gen && p.last_gen.ok ? `<div class="text-xs text-emerald-500 mt-2">тест-запрос ok (${esc(p.last_gen.model)}): «${esc(p.last_gen.reply || '—')}»</div>` : ''}
      ${p.last_gen && !p.last_gen.ok ? `<div class="text-xs text-red-400 mt-2 whitespace-pre-wrap">тест-запрос не прошёл (${esc(p.last_gen.model)}): ${esc(p.last_gen.error)}</div>` : ''}
      ${p.last_check_at && p.last_check_ok === 0 ? `<div class="text-xs text-red-400 mt-2 whitespace-pre-wrap">${esc(p.last_check_error || 'проверка не прошла')}</div>` : ''}
      ${p.last_check_at && p.last_check_ok === 1 ? '<div class="text-xs text-emerald-500 mt-2">провайдер зарегистрирован, модели доступны</div>' : ''}
    </div>`).join('');
  $$('.check', el).forEach(b => b.onclick = async (e) => {
    const btn = b;
    const orig = btn.textContent;
    btn.textContent = 'проверяю… (30-60с)';
    btn.disabled = true;
    try {
      const r = await api.post(`/api/providers/${btn.dataset.id}/check`);
      if (r.ok) {
        const genNote = r.gen
          ? (r.gen.ok ? ` · тест-запрос ok: «${r.gen.reply}»` : ` · тест-запрос не прошёл: ${r.gen.error}`)
          : '';
        toast(`Провайдер ${r.provider}: зарегистрирован, моделей: ${r.models.length}${genNote}`, r.gen && !r.gen.ok ? 'error' : 'ok');
      } else {
        toast(`Провайдер ${r.provider}: ${r.error}`, 'error');
      }
    } catch (err) {
      toast(err.message, 'error');
    }
    btn.textContent = orig;
    btn.disabled = false;
    await refreshProviders();
  });
  $$('.edit', el).forEach(b => b.onclick = async () => {
    const row = data.find(p => p.id === b.dataset.id);
    providerModal(row);
  });
  $$('.del', el).forEach(b => b.onclick = async () => {
    if (!confirm(`Удалить провайдера ${b.dataset.id}?`)) return;
    await api.del(`/api/providers/${b.dataset.id}`);
    await refreshProviders();
  });
}

function providerModal(p) {
  const isEdit = !!p;
  const known = api.get('/api/providers/known').then(k => k).catch(() => []);
  Promise.all([known, currentProject ? Promise.resolve([]) : api.get('/api/projects').catch(() => [])]).then(([list, projects]) => {
    const projSel = currentProject
      ? ''
      : formSelect('Проект', 'project_id', projects.map(x => ({ v: x.id, l: x.name })), p ? p.project_id : (projects[0] || {}).id);
    const body = `
      ${isEdit ? '' : formInput('ID провайдера', 'id', p ? p.id : '', 'deepseek', 'text', 'list="provider-ids"')}
      <datalist id="provider-ids">${list.map(x => `<option value="${esc(x)}">`).join('')}</datalist>
      ${formInput('Название (необязательно)', 'label', p ? p.label : '')}
      ${projSel}
      ${formInput('API-ключ', 'api_key', '', isEdit ? 'оставьте пустым, чтобы не менять' : 'sk-…', 'password')}
      <label class="flex items-center gap-2 text-sm mb-3">
        <input type="checkbox" name="enabled" ${p ? (p.enabled ? 'checked' : '') : 'checked'} class="accent-sky-600">
        <span class="text-neutral-400 text-xs">включено (ключ попадает в воркеры)</span>
      </label>
      ${isEdit ? `<div class="text-xs text-neutral-500 mb-2">Текущий ключ: ${p.has_key ? esc(p.key_masked) : 'нет'}</div>` : ''}`;
    openModal(isEdit ? `Провайдер: ${p.id}` : 'Новый провайдер', body, async (close) => {
      const f = readForm($('#modal-body'));
      f.enabled = $('[name="enabled"]', $('#modal-body')).checked;
      f.project_id = currentProject || f.project_id || null;
      if (isEdit) await api.put(`/api/providers/${p.id}`, f);
      else {
        if (!f.id.trim()) throw new Error('ID обязателен');
        await api.post('/api/providers', f);
      }
      close();
      await refreshProviders();
    });
  });
}

/* ---------- automation ---------- */
async function renderAutomation(tab = 'webhooks') {
  const main = $('#main');
  main.innerHTML = `
    <div class="h-full flex flex-col">
      <div class="px-6 py-4 border-b border-neutral-800">
        <div class="text-lg font-semibold mb-3">Автоматизация</div>
        <div class="flex gap-2 text-sm">
          <button id="auto-tab-webhooks" class="auto-tab px-3 py-1.5 rounded-lg ${tab === 'webhooks' ? 'bg-neutral-800' : 'hover:bg-neutral-800/60'}">Webhook-и</button>
          <button id="auto-tab-schedules" class="auto-tab px-3 py-1.5 rounded-lg ${tab === 'schedules' ? 'bg-neutral-800' : 'hover:bg-neutral-800/60'}">Расписания</button>
        </div>
      </div>
      <div class="flex-1 overflow-y-auto p-6" id="automation-content"></div>
    </div>`;
  $('#auto-tab-webhooks').onclick = () => showView('webhooks');
  $('#auto-tab-schedules').onclick = () => showView('schedules');
  const content = $('#automation-content');
  if (tab === 'schedules') {
    content.innerHTML = schedulesTabHtml();
    bindNewSched();
    await refreshSchedules();
  } else {
    await refreshWebhooks(content);
  }
}

function schedulesTabHtml() {
  return `
    <div class="flex items-center justify-end mb-4">
      <button id="new-sched" class="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium">Новое расписание</button>
    </div>
    <div id="sched-list"></div>`;
}

function bindNewSched() {
  const btn = $('#new-sched');
  if (!btn) return;
  btn.onclick = async () => {
    const agents = await api.get('/api/agents' + projQuery());
    if (!agents.length) return toast('Сначала создайте агента', 'error');
    schedModal(agents);
  };
}

/* ---------- schedules ---------- */
async function renderSchedules() {
  const main = $('#main');
  main.innerHTML = `
    <div class="h-full flex flex-col">
      <div class="px-6 py-4 border-b border-neutral-800">
        <div class="text-lg font-semibold">Расписания</div>
      </div>
      <div class="flex-1 overflow-y-auto p-6">${schedulesTabHtml()}</div>
    </div>`;
  bindNewSched();
  await refreshSchedules();
}

async function refreshSchedules() {
  let data;
  try { data = await api.get('/api/schedules' + projQuery()); } catch (e) { return; }
  const el = $('#sched-list');
  if (!el) return;
  if (!data.length) {
    el.innerHTML = `<div class="text-neutral-500 text-sm">Расписаний нет.</div>`;
    return;
  }
  el.innerHTML = data.map(s => `
    <div class="rounded-xl border border-neutral-800 hover:border-neutral-700 p-5 mb-3">
      <div class="flex items-start justify-between">
        <div>
          <div class="font-semibold">${esc(s.title || 'Без названия')}</div>
          <div class="text-xs text-neutral-500">агент: ${esc(s.agent_name || '—')}</div>
        </div>
        <div class="flex gap-2 text-xs">
          <button class="toggle px-3 py-1.5 rounded-lg border ${s.enabled ? 'border-red-900 text-red-400 hover:bg-red-950' : 'border-emerald-800 text-emerald-400 hover:bg-emerald-950'}" data-id="${s.id}">${s.enabled ? 'Остановить' : 'Включить'}</button>
          <button class="run px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800" data-id="${s.id}" title="Выполнить задание прямо сейчас">выполнить сейчас</button>
          <button class="edit px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800" data-id="${s.id}">изменить</button>
          <button class="del px-3 py-1.5 rounded-lg border border-red-900 text-red-400 hover:bg-red-950" data-id="${s.id}">удалить</button>
        </div>
      </div>
      <pre class="mt-3 text-sm whitespace-pre-wrap text-neutral-300 border-l-2 border-neutral-700 pl-3">${esc(s.prompt.slice(0, 400))}${s.prompt.length > 400 ? '…' : ''}</pre>
      <div class="flex flex-wrap gap-2 mt-3 text-xs">
        <span class="px-2 py-1 rounded bg-neutral-800 mono">${esc(s.cron)}</span>
        <span class="px-2 py-1 rounded bg-neutral-800">${esc(cronHuman(s.cron))}</span>
        <span class="px-2 py-1 rounded bg-neutral-800">tz: ${esc(s.timezone)}</span>
        <span class="px-2 py-1 rounded bg-neutral-800">след: ${esc(s.next_run || '—')}</span>
        ${s.last_run ? `<span class="px-2 py-1 rounded bg-neutral-800">был: ${esc(s.last_run)}</span>` : ''}
        ${s.last_run_status ? badge(s.last_run_status) : ''}
        ${s.enabled ? '<span class="px-2 py-1 rounded bg-emerald-900/50">включено</span>' : '<span class="px-2 py-1 rounded bg-neutral-800 text-neutral-500">выключено</span>'}
      </div>
    </div>`).join('');
  $$('.toggle', el).forEach(b => b.onclick = async () => {
    const row = data.find(s => s.id === +b.dataset.id);
    if (!row) return;
    await api.put(`/api/schedules/${row.id}`, { enabled: !row.enabled });
    toast(row.enabled ? 'Расписание остановлено' : 'Расписание включено', 'ok');
    await refreshSchedules();
  });
  $$('.run', el).forEach(b => b.onclick = async () => { await api.post(`/api/schedules/${b.dataset.id}/run-now`); toast('Задание выполнено', 'ok'); setTimeout(refreshSchedules, 1500); });
  $$('.edit', el).forEach(b => b.onclick = async () => {
    const agents = await api.get('/api/agents' + projQuery());
    const row = data.find(s => s.id === +b.dataset.id);
    schedModal(agents, row);
  });
  $$('.del', el).forEach(b => b.onclick = async () => {
    if (!confirm('Удалить расписание?')) return;
    await api.del(`/api/schedules/${b.dataset.id}`);
    await refreshSchedules();
  });
}

const DOW_RU = { mon: 'пн', tue: 'вт', wed: 'ср', thu: 'чт', fri: 'пт', sat: 'сб', sun: 'вс' };

function cronHuman(cron) {
  if (!cron) return '';
  const parts = String(cron).trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [min, hour, dom, mon, dow] = parts;
  const pad = (n) => String(n).padStart(2, '0');
  const interval = min.match(/^\*\/(\d+)$/);
  if (interval && hour === '*' && dom === '*' && mon === '*' && dow === '*')
    return `каждые ${interval[1]} мин`;
  if (/^\d+$/.test(min) && hour === '*' && dom === '*' && mon === '*' && dow === '*')
    return `каждый час в :${pad(min)}`;
  if (/^\d+$/.test(min) && /^\d+$/.test(hour) && dom === '*' && mon === '*' && dow === '*')
    return `ежедневно в ${pad(hour)}:${pad(min)}`;
  if (/^\d+$/.test(min) && /^\d+$/.test(hour) && dom === '*' && mon === '*' && dow !== '*') {
    const days = dow.split(',').map(d => DOW_RU[d] || d).join(', ');
    return `${days} в ${pad(hour)}:${pad(min)}`;
  }
  if (/^\d+$/.test(min) && /^\d+$/.test(hour) && /^\d+$/.test(dom) && mon === '*' && dow === '*')
    return `${dom}-го числа в ${pad(hour)}:${pad(min)}`;
  return cron;
}

function buildCronFromSimple(body) {
  const freq = $('[name="sched-freq"]', body).value;
  const timeEls = $$('[name="sched-time"]', body);
  const timeEl = timeEls.find(el => !el.closest('.hidden')) || timeEls[0];
  const time = ((timeEl && timeEl.value) || '09:00').split(':');
  let cron;
  if (freq === 'minute') {
    const n = Math.min(1440, Math.max(1, parseInt($('[name="sched-interval"]', body).value, 10) || 30));
    cron = `*/${n} * * * *`;
  } else if (freq === 'hour') {
    const m = Math.min(59, Math.max(0, parseInt($('[name="sched-minute"]', body).value, 10) || 0));
    cron = `${m} * * * *`;
  } else if (freq === 'day') {
    cron = `${time[1] || '0'} ${time[0]} * * *`;
  } else if (freq === 'week') {
    const days = $$('[name="sched-dow"]:checked', body).map(el => el.value).join(',') || '*';
    cron = `${time[1] || '0'} ${time[0]} * * ${days}`;
  } else {
    const dom = Math.min(31, Math.max(1, parseInt($('[name="sched-dom"]', body).value, 10) || 1));
    cron = `${time[1] || '0'} ${time[0]} ${dom} * *`;
  }
  const preview = $('#cron-preview', body);
  if (preview) preview.textContent = cron;
  return cron;
}

function schedModal(agents, s) {
  s = s || { agent_id: agents[0].id, title: '', prompt: '', cron: '0 9 * * *', timezone: 'Europe/Moscow', enabled: true };
  const dowCheckboxes = Object.entries(DOW_RU).map(([v, l]) => `
    <label class="flex items-center gap-1.5 text-xs">
      <input type="checkbox" name="sched-dow" value="${v}" class="accent-sky-600">
      <span class="text-neutral-400">${l}</span>
    </label>`).join('');
  const body = `
    ${formSelect('Агент', 'agent_id', agents.map(a => ({ v: a.id, l: a.name })), s.agent_id)}
    ${formInput('Название', 'title', s.title)}
    ${formArea('Промпт', 'prompt', s.prompt, 5)}
    <div class="flex gap-2 mb-3 text-sm">
      <button id="mode-simple" class="px-3 py-1.5 rounded-lg bg-neutral-800">Простая настройка</button>
      <button id="mode-cron" class="px-3 py-1.5 rounded-lg hover:bg-neutral-800/60">CRON-выражение</button>
    </div>
    <div id="mode-simple-pane" class="border border-neutral-800 rounded-lg p-3 mb-3">
      <div class="flex flex-wrap gap-1.5 mb-3 text-xs">
        <button data-preset="minute:30" class="px-2 py-1 rounded border border-neutral-700 hover:bg-neutral-800">каждые 30 минут</button>
        <button data-preset="hour:0" class="px-2 py-1 rounded border border-neutral-700 hover:bg-neutral-800">каждый час</button>
        <button data-preset="day:09:00" class="px-2 py-1 rounded border border-neutral-700 hover:bg-neutral-800">ежедневно в 09:00</button>
        <button data-preset="week:09:00:mon,tue,wed,thu,fri" class="px-2 py-1 rounded border border-neutral-700 hover:bg-neutral-800">пн–пт в 09:00</button>
        <button data-preset="month:09:00:1" class="px-2 py-1 rounded border border-neutral-700 hover:bg-neutral-800">1-го числа в 09:00</button>
      </div>
      ${formSelect('Частота', 'sched-freq', [
        { v: 'minute', l: 'каждые N минут' },
        { v: 'hour', l: 'каждый час' },
        { v: 'day', l: 'ежедневно' },
        { v: 'week', l: 'по дням недели' },
        { v: 'month', l: 'по числам месяца' },
      ], 'day')}
      <div data-freq="minute" class="hidden mb-3">
        ${formInput('Интервал, минут', 'sched-interval', '30', '30')}
      </div>
      <div data-freq="hour" class="hidden mb-3">
        ${formInput('Минута часа (0–59)', 'sched-minute', '0', '0')}
      </div>
      <div data-freq="day" class="hidden mb-3">
        ${formInput('Время', 'sched-time', '09:00', '', 'time')}
      </div>
      <div data-freq="week" class="hidden mb-3">
        <div class="text-xs text-neutral-400 mb-1.5">Дни недели</div>
        <div class="flex flex-wrap gap-3 mb-3">${dowCheckboxes}</div>
        ${formInput('Время', 'sched-time', '09:00', '', 'time')}
      </div>
      <div data-freq="month" class="hidden mb-3">
        <div class="grid grid-cols-2 gap-3">
          ${formInput('День месяца (1–31)', 'sched-dom', '1', '1')}
          ${formInput('Время', 'sched-time', '09:00', '', 'time')}
        </div>
      </div>
      <div class="text-xs text-neutral-500 mt-1">CRON: <span id="cron-preview" class="mono text-sky-300">${esc(s.cron)}</span> · ${esc(cronHuman(s.cron))}</div>
    </div>
    <div id="mode-cron-pane" class="hidden mb-3">
      ${formInput('Cron-выражение (5 полей: мин час день месяц день_недели)', 'cron', s.cron, '0 9 * * *')}
      <div class="text-xs text-neutral-500">${esc(cronHuman(s.cron)) || '—'}</div>
    </div>
    <div class="grid grid-cols-1 gap-3">
      ${formInput('Таймзона', 'timezone', s.timezone, 'Europe/Moscow')}
    </div>
    <label class="flex items-center gap-2 text-sm mb-3">
      <input type="checkbox" name="enabled" ${s.enabled ? 'checked' : ''} class="accent-sky-600">
      <span class="text-neutral-400 text-xs">включено</span>
    </label>`;
  openModal(s.id ? 'Изменить расписание' : 'Новое расписание', body, async (close) => {
    const mbody = $('#modal-body');
    const f = readForm(mbody);
    f.agent_id = +f.agent_id;
    f.enabled = $('[name="enabled"]', mbody).checked;
    if (!f.prompt.trim()) throw new Error('Промпт обязателен');
    if (!$('#mode-cron-pane', mbody).classList.contains('hidden')) {
      f.cron = $('[name="cron"]', $('#mode-cron-pane', mbody)).value.trim();
    } else {
      f.cron = buildCronFromSimple(mbody);
    }
    if (!f.cron) throw new Error('CRON пуст');
    if (currentProject) f.project_id = currentProject;
    if (s.id) await api.put(`/api/schedules/${s.id}`, f);
    else await api.post('/api/schedules', f);
    close();
    await refreshSchedules();
  });
  const mbody = $('#modal-body');
  const setMode = (simple) => {
    $('#mode-simple-pane', mbody).classList.toggle('hidden', !simple);
    $('#mode-cron-pane', mbody).classList.toggle('hidden', simple);
    $('#mode-simple', mbody).className = `px-3 py-1.5 rounded-lg ${simple ? 'bg-neutral-800' : 'hover:bg-neutral-800/60'}`;
    $('#mode-cron', mbody).className = `px-3 py-1.5 rounded-lg ${simple ? 'hover:bg-neutral-800/60' : 'bg-neutral-800'}`;
  };
  $('#mode-simple', mbody).onclick = () => { setMode(true); buildCronFromSimple(mbody); };
  $('#mode-cron', mbody).onclick = () => setMode(false);
  const updateFields = () => {
    const freq = $('[name="sched-freq"]', mbody).value;
    $$('[data-freq]', mbody).forEach(el => el.classList.toggle('hidden', el.dataset.freq !== freq));
    buildCronFromSimple(mbody);
  };
  $('[name="sched-freq"]', mbody).onchange = updateFields;
  $$('#mode-simple-pane input', mbody).forEach(el => el.addEventListener('input', () => buildCronFromSimple(mbody)));
  $$('#mode-simple-pane input', mbody).forEach(el => el.addEventListener('change', () => buildCronFromSimple(mbody)));
  $$('[data-preset]', mbody).forEach(btn => btn.onclick = () => {
    setMode(true);
    const [freq, time, days] = btn.dataset.preset.split(':');
    $('[name="sched-freq"]', mbody).value = freq;
    if (freq === 'minute') $('[name="sched-interval"]', mbody).value = time;
    if (freq === 'hour') $('[name="sched-minute"]', mbody).value = time;
    if (freq === 'day' || freq === 'week' || freq === 'month') $$('[name="sched-time"]', mbody).forEach(el => { el.value = time; });
    if (freq === 'week') {
      const daysSet = new Set(days.split(','));
      $$('[name="sched-dow"]', mbody).forEach(cb => { cb.checked = daysSet.has(cb.value); });
    }
    if (freq === 'month') $('[name="sched-dom"]', mbody).value = days;
    updateFields();
  });
  setMode(true);
  updateFields();
}

/* ---------- webhooks ---------- */
async function refreshWebhooks(content) {
  let data;
  try { data = await api.get('/api/webhooks' + projQuery()); } catch (e) { return; }
  content.innerHTML = `
    <div class="flex items-center justify-between mb-4">
      <div class="text-xs text-neutral-500 max-w-md">Webhook — внешний POST-запрос, который запускает агента. Тело: {"prompt": "…"} (если пусто — промпт по умолчанию). С wait=&lt;сек&gt; можно дождаться результата.</div>
      <button id="new-webhook" class="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium shrink-0 ml-4">Новый webhook</button>
    </div>
    <div id="webhooks-list"></div>`;
  $('#new-webhook').onclick = () => webhookModal();
  const list = $('#webhooks-list');
  if (!data.length) {
    list.innerHTML = `<div class="text-neutral-500 text-sm">Webhook-ов нет. Создайте — и внешние системы смогут запускать агента POST-запросом.</div>`;
    return;
  }
  list.innerHTML = data.map(w => `
    <div class="rounded-xl border border-neutral-800 hover:border-neutral-700 p-5 mb-3">
      <div class="flex items-start justify-between">
        <div class="min-w-0">
          <div class="font-semibold flex items-center gap-2 flex-wrap">
            <span class="mono text-sky-300">${esc(w.slug)}</span>
            ${w.title ? `<span class="text-neutral-400 text-sm">${esc(w.title)}</span>` : ''}
            ${w.enabled ? '<span class="text-xs px-2 py-0.5 rounded bg-emerald-900/50">вкл</span>' : '<span class="text-xs px-2 py-0.5 rounded bg-neutral-800 text-neutral-500">выкл</span>'}
            ${w.has_secret ? '<span class="text-xs px-2 py-0.5 rounded bg-yellow-900/50">с секретом</span>' : ''}
          </div>
          <div class="text-xs text-neutral-500 mt-0.5">агент: ${esc(w.agent_name || '—')}${w.last_run ? ` · был запуск: ${esc(w.last_run)}` : ''}</div>
          ${w.prompt ? `<pre class="mt-2 text-xs text-neutral-500 whitespace-pre-wrap border-l-2 border-neutral-700 pl-2">${esc(w.prompt.slice(0, 200))}${w.prompt.length > 200 ? '…' : ''}</pre>` : ''}
        </div>
        <div class="flex gap-2 text-xs shrink-0 ml-3">
          <button class="test px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800" data-slug="${esc(w.slug)}">тест</button>
          <button class="edit px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800" data-id="${w.id}">изменить</button>
          <button class="del px-3 py-1.5 rounded-lg border border-red-900 text-red-400 hover:bg-red-950" data-id="${w.id}">удалить</button>
        </div>
      </div>
      <div class="flex items-center gap-2 mt-3">
        <code class="flex-1 mono text-xs bg-neutral-800 border border-neutral-700 rounded px-2 py-1 break-all">POST ${location.origin}/api/webhooks/${esc(w.slug)}/run</code>
        <button class="copy px-2 py-1 rounded border border-neutral-700 hover:bg-neutral-800 text-xs" data-url="${location.origin}/api/webhooks/${esc(w.slug)}/run">копировать</button>
      </div>
    </div>`).join('');
  $$('.test', list).forEach(b => b.onclick = async () => {
    try {
      const r = await api.post(`/api/webhooks/${b.dataset.slug}/run`);
      toast(`Webhook сработал: сессия ${r.session_id} (${r.status})`, 'ok');
    } catch (e) { toast(e.message, 'error'); }
  });
  $$('.copy', list).forEach(b => b.onclick = async () => {
    try {
      await navigator.clipboard.writeText(b.dataset.url);
      toast('Скопировано', 'ok');
    } catch { toast('Не удалось скопировать', 'error'); }
  });
  $$('.edit', list).forEach(b => b.onclick = () => {
    const row = data.find(w => w.id === +b.dataset.id);
    webhookModal(row);
  });
  $$('.del', list).forEach(b => b.onclick = async () => {
    if (!confirm('Удалить webhook?')) return;
    await api.del(`/api/webhooks/${b.dataset.id}`);
    await refreshWebhooks($('#automation-content'));
  });
}

async function webhookModal(w) {
  const isEdit = !!w;
  const agents = await api.get('/api/agents' + projQuery());
  if (!isEdit && !agents.length) return toast('Сначала создайте агента', 'error');
  w = w || { slug: '', title: '', agent_id: agents[0]?.id, prompt: '', secret: '', enabled: true };
  openModal(isEdit ? `Webhook: ${w.slug}` : 'Новый webhook', `
    <div class="grid grid-cols-2 gap-3">
      ${formInput('Slug (латиница, дефисы)', 'slug', w.slug, 'my-hook')}
      ${formInput('Название (необязательно)', 'title', w.title)}
    </div>
    ${formSelect('Агент', 'agent_id', agents.map(a => ({ v: a.id, l: a.name })), w.agent_id)}
    ${formArea('Промпт по умолчанию (если в запросе нет prompt)', 'prompt', w.prompt, 4, 'Что сделать агенту…')}
    <div class="grid grid-cols-2 gap-3">
      ${formInput('Секрет (необязательно)', 'secret', '', isEdit ? 'оставьте пустым, чтобы не менять' : '', 'password')}
      <label class="flex items-center gap-2 text-sm mb-3 mt-5">
        <input type="checkbox" name="enabled" ${w.enabled ? 'checked' : ''} class="accent-sky-600">
        <span class="text-neutral-400 text-xs">включено</span>
      </label>
    </div>
    ${isEdit ? `<div class="text-xs text-neutral-500 mb-2">Секрет: ${w.has_secret ? 'задан' : 'нет'}</div>` : ''}`,
    async (close) => {
      const f = readForm($('#modal-body'));
      f.enabled = $('[name="enabled"]', $('#modal-body')).checked;
      if (!f.slug.trim()) throw new Error('Slug обязателен');
      f.agent_id = +f.agent_id;
      if (currentProject) f.project_id = currentProject;
      if (isEdit) await api.put(`/api/webhooks/${w.id}`, f);
      else await api.post('/api/webhooks', f);
      close();
      const content = $('#automation-content');
      if (content) await refreshWebhooks(content);
    });
}

/* ---------- channels ---------- */
async function renderChannels(arg) {
  if (arg) return renderChannelConfig(arg);
  const main = $('#main');
  main.innerHTML = `
    <div class="h-full flex flex-col">
      <div class="px-6 py-4 border-b border-neutral-800">
        <div class="text-lg font-semibold">Каналы</div>
        <div class="text-xs text-neutral-500">Внешние каналы запуска агентов: мессенджеры, уведомления.</div>
      </div>
      <div class="flex-1 overflow-y-auto p-6" id="channels-list"></div>
    </div>`;
  await refreshChannels();
}

async function refreshChannels() {
  let data;
  try { data = await api.get('/api/channels' + projQuery()); } catch (e) { return; }
  const el = $('#channels-list');
  if (!el) return;
  if (!data.length) {
    el.innerHTML = `<div class="text-neutral-500 text-sm">Каналов нет.</div>`;
    return;
  }
  el.innerHTML = data.map(c => `
    <div class="rounded-xl border border-neutral-800 hover:border-neutral-700 p-5 mb-3 cursor-pointer" data-open-channel="${esc(c.id)}">
      <div class="flex items-start justify-between gap-4">
        <div class="min-w-0">
          <div class="font-semibold flex items-center gap-2 flex-wrap">
            ${esc(c.name)}
            ${!c.configured ? '<span class="text-xs px-2 py-0.5 rounded bg-neutral-800 text-neutral-500">не настроен</span>' : ''}
            ${c.configured && !c.enabled ? '<span class="text-xs px-2 py-0.5 rounded bg-neutral-800 text-neutral-500">выключен</span>' : ''}
            ${c.configured && c.enabled && c.connected
              ? `<span class="text-xs px-2 py-0.5 rounded bg-emerald-900/50">подключён${c.bot_username ? ' как @' + esc(c.bot_username) : ''}</span>`
              : (c.configured && c.enabled ? '<span class="text-xs px-2 py-0.5 rounded bg-amber-900/50">не подключён</span>' : '')}
          </div>
          <div class="text-sm text-neutral-500 mt-1">${esc(c.description)}</div>
          ${c.last_error ? `<div class="text-xs text-red-400 mt-1 truncate">${esc(c.last_error.slice(0, 160))}</div>` : ''}
        </div>
        <button class="cfg px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-xs shrink-0" data-id="${esc(c.id)}">настроить</button>
      </div>
    </div>`).join('');
  $$('[data-open-channel]', el).forEach(e => e.onclick = () => showView('channels', e.dataset.openChannel));
  $$('.cfg', el).forEach(b => b.onclick = (ev) => { ev.stopPropagation(); showView('channels', b.dataset.id); });
}

async function renderChannelConfig(channelId) {
  if (channelId !== 'telegram') return showView('channels');
  let cfg;
  try { cfg = await api.get('/api/telegram' + projQuery()); } catch (e) { return; }
  cfg = cfg || {};
  const main = $('#main');
  main.innerHTML = `
    <div class="h-full flex flex-col">
      <div class="px-6 py-4 border-b border-neutral-800 flex items-center gap-3">
        <button id="channels-back" class="px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-sm">← Каналы</button>
        <div class="text-lg font-semibold">Telegram</div>
        ${cfg.has_token
          ? (cfg.connected
              ? `<span class="text-xs px-2 py-0.5 rounded bg-emerald-900/50">подключён${cfg.bot_username ? ' как @' + esc(cfg.bot_username) : ''}</span>`
              : `<span class="text-xs px-2 py-0.5 rounded bg-amber-900/50">не подключён</span>`)
          : '<span class="text-xs px-2 py-0.5 rounded bg-neutral-800 text-neutral-500">не настроен</span>'}
      </div>
      <div class="flex-1 overflow-y-auto p-6 max-w-2xl">
        <div class="text-sm text-neutral-400 mb-4">Токен бота выдаёт @BotFather (/newbot). Первое сообщение в чате запускает агента по умолчанию проекта, дальше переписка продолжает ту же сессию. Команды бота: /agents, /agent N, /new, /abort, /status, /link.</div>
        ${formInput('Токен бота', 'token', '', cfg.has_token ? 'оставьте пустым, чтобы не менять' : '123456:ABC…', 'password')}
        ${cfg.has_token ? `<div class="text-xs text-neutral-500 mb-3">Токен задан (…${esc(cfg.token_tail || '')}).</div>` : ''}
        ${formInput('Разрешённые user id (необязательно, через запятую)', 'allowed_users', cfg.allowed_users || '', '111,222')}
        ${formInput('URL веб-интерфейса для команды /link (необязательно)', 'web_url', cfg.web_url || '', 'http://host:8000')}
        <label class="flex items-center gap-2 text-sm mb-4">
          <input type="checkbox" name="enabled" ${cfg.enabled ? 'checked' : ''} class="accent-sky-600">
          <span class="text-neutral-400 text-xs">включено</span>
        </label>
        <div class="flex gap-2">
          <button id="tg-save" class="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium">Сохранить</button>
          <button id="tg-test" class="px-4 py-2 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-sm">Проверить токен</button>
          ${cfg.has_token ? '<button id="tg-del" class="px-4 py-2 rounded-lg border border-red-900 text-red-400 hover:bg-red-950 text-sm">Удалить</button>' : ''}
        </div>
        <div id="tg-status" class="mt-4 text-sm"></div>
      </div>
    </div>`;
  $('#channels-back').onclick = () => showView('channels');
  $('#tg-save').onclick = async () => {
    const f = readForm($('#main'));
    f.enabled = $('[name="enabled"]', $('#main')).checked;
    try {
      await api.put('/api/telegram' + projQuery(), f);
      toast('Сохранено', 'ok');
      showView('channels', 'telegram');
    } catch (e) { toast(e.message, 'error'); }
  };
  $('#tg-test').onclick = async () => {
    const token = ($('[name="token"]', $('#main')).value || '').trim();
    if (!token && !cfg.has_token) return toast('Введите токен', 'error');
    try {
      const r = await api.post('/api/telegram/test', { token });
      toast(`Токен валиден: @${r.username}`, 'ok');
    } catch (e) { toast(e.message, 'error'); }
  };
  const delBtn = $('#tg-del');
  if (delBtn) delBtn.onclick = async () => {
    if (!confirm('Удалить настройку Telegram? Бот остановится.')) return;
    await api.del('/api/telegram' + projQuery());
    toast('Удалено', 'ok');
    showView('channels');
  };
  const st = $('#tg-status');
  if (cfg.has_token) {
    st.innerHTML = cfg.connected
      ? `<span class="text-emerald-400">Бот подключён и слушает сообщения.</span>`
      : `<span class="text-amber-400">Бот не подключён${cfg.last_error ? ': ' + esc(cfg.last_error) : ''}.</span>`;
  } else {
    st.innerHTML = `<span class="text-neutral-500">Канал не настроен — сохраните токен, и бот запустится.</span>`;
  }
}

/* ---------- mcp catalog ---------- */
async function renderCatalog() {
  const main = $('#main');
  main.innerHTML = `
    <div class="h-full flex flex-col">
      <div class="flex items-center justify-between px-6 py-4 border-b border-neutral-800">
        <div>
          <div class="text-lg font-semibold">Каталог MCP</div>
          <div class="text-xs text-neutral-500">Переиспользуемые MCP-серверы: свои (local/remote) и docker-сервисы. Добавление к агенту — одной кнопкой в его карточке.</div>
        </div>
        <button id="new-cat" class="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium">Добавить MCP</button>
      </div>
      <div class="flex-1 overflow-y-auto p-6" id="catalog-list"></div>
    </div>`;
  $('#new-cat').onclick = () => catalogModal();
  await refreshCatalog();
}

async function refreshCatalog() {
  let data;
  try { data = await api.get('/api/mcp-catalog'); } catch (e) { return; }
  const el = $('#catalog-list');
  if (!el) return;
  if (!data.length) {
    el.innerHTML = '<div class="text-neutral-500 text-sm">Каталог пуст.</div>';
    return;
  }
  el.innerHTML = data.map(c => `
    <div class="rounded-xl border border-neutral-800 hover:border-neutral-700 p-5 mb-3">
      <div class="flex items-start justify-between">
        <div class="min-w-0">
          <div class="font-semibold flex items-center gap-2 flex-wrap">
            <span class="mono text-sky-300">${esc(c.name)}</span>
            ${c.builtin ? '<span class="text-xs px-2 py-0.5 rounded bg-violet-900/50">встроенный</span>' : ''}
            <span class="text-xs px-2 py-0.5 rounded bg-neutral-800">${esc(c.type)}</span>
            ${c.kind === 'service' ? '<span class="text-xs px-2 py-0.5 rounded bg-neutral-800">docker-сервис</span>' : ''}
          </div>
          <div class="text-sm text-neutral-500 mt-1">${esc(c.description || '')}</div>
          ${c.url ? `<div class="text-xs mono text-neutral-400 mt-1 break-all">${esc(c.url)}</div>` : ''}
          ${c.command ? `<div class="text-xs mono text-neutral-400 mt-1 break-all">${esc(c.command)}</div>` : ''}
          ${c.service_status ? `<div class="text-xs mt-2 ${c.service_status === 'running' ? 'text-emerald-400' : 'text-yellow-400'}">сервис: ${esc(c.service_status)}</div>` : ''}
        </div>
        <div class="flex gap-2 text-xs shrink-0 ml-3">
          <button class="attach px-3 py-1.5 rounded-lg border border-sky-800 text-sky-300 hover:bg-sky-950" data-id="${c.id}" title="Добавить к агенту">＋ в агента</button>
          ${c.kind === 'service' ? `<button class="svc px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800" data-id="${c.id}" data-action="${c.service_status === 'running' ? 'stop' : 'start'}">${c.service_status === 'running' ? 'остановить' : 'запустить'}</button>` : ''}
          ${!c.builtin ? `<button class="edit px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800" data-id="${c.id}">изменить</button>
          <button class="del px-3 py-1.5 rounded-lg border border-red-900 text-red-400 hover:bg-red-950" data-id="${c.id}">удалить</button>` : ''}
        </div>
      </div>
    </div>`).join('');
  $$('.attach', el).forEach(b => b.onclick = async () => {
    const row = data.find(c => c.id === +b.dataset.id);
    const agents = await api.get('/api/agents' + projQuery());
    if (!agents.length) return toast('Сначала создайте агента', 'error');
    openModal(`«${row.name}» → агент`, `
      ${formSelect('Агент', 'agent_id', agents.map(a => ({ v: a.id, l: a.name })), agents[0].id)}`,
      async (close) => {
        const f = readForm($('#modal-body'));
        await api.post(`/api/mcp-catalog/${row.id}/attach`, { agent_id: +f.agent_id });
        close();
        toast(`MCP «${row.name}» добавлен агенту`, 'ok');
      });
  });
  $$('.svc', el).forEach(b => b.onclick = async () => {
    const action = b.dataset.action;
    b.disabled = true;
    try {
      await api.post(`/api/mcp-catalog/${b.dataset.id}/${action}`);
      toast(action === 'start' ? 'Сервис запущен' : 'Сервис остановлен', 'ok');
    } catch (e) {
      toast(e.message, 'error');
    }
    await refreshCatalog();
  });
  $$('.edit', el).forEach(b => b.onclick = () => {
    const row = data.find(c => c.id === +b.dataset.id);
    catalogModal(row);
  });
  $$('.del', el).forEach(b => b.onclick = async () => {
    if (!confirm('Удалить запись из каталога?')) return;
    try { await api.del(`/api/mcp-catalog/${b.dataset.id}`); }
    catch (e) { return toast(e.message, 'error'); }
    await refreshCatalog();
  });
}

function catalogModal(c) {
  const isEdit = !!c;
  c = c || { name: '', description: '', type: 'remote', command: '', url: '', headers: '', environment: '' };
  openModal(isEdit ? `MCP: ${c.name}` : 'Новый MCP в каталоге', `
    ${formInput('Имя (латиница, дефисы)', 'name', c.name, 'my-mcp')}
    ${formInput('Описание', 'description', c.description)}
    ${formSelect('Тип', 'type', [{ v: 'local', l: 'local (команда)' }, { v: 'remote', l: 'remote (URL)' }], c.type)}
    <div data-cat-if="local" class="${c.type === 'remote' ? 'hidden' : ''}">
      ${formInput('Команда (JSON-массив)', 'command', c.command, '["npx", "-y", "some-mcp"]')}
      ${formInput('Environment (JSON)', 'environment', c.environment, '{}')}
    </div>
    <div data-cat-if="remote" class="${c.type === 'local' ? 'hidden' : ''}">
      ${formInput('URL (streamable HTTP, напр. http://harness-playwright:8931/mcp)', 'url', c.url, 'http://…')}
      ${formInput('Headers (JSON)', 'headers', c.headers, '{}')}
    </div>`,
    async (close) => {
      const f = readForm($('#modal-body'));
      if (!f.name.trim()) throw new Error('Имя обязательно');
      if (isEdit) await api.put(`/api/mcp-catalog/${c.id}`, f);
      else await api.post('/api/mcp-catalog', f);
      close();
      await refreshCatalog();
    });
  $('[name="type"]', $('#modal-body')).onchange = (e) => {
    const isLocal = e.target.value === 'local';
    $$('[data-cat-if="local"]', $('#modal-body')).forEach(x => x.classList.toggle('hidden', !isLocal));
    $$('[data-cat-if="remote"]', $('#modal-body')).forEach(x => x.classList.toggle('hidden', isLocal));
  };
}

/* ---------- projects ---------- */
async function renderProjects() {
  const main = $('#main');
  main.innerHTML = `
    <div class="h-full flex flex-col">
      <div class="flex items-center justify-between px-6 py-4 border-b border-neutral-800">
        <div>
          <div class="text-lg font-semibold">Настройки проекта</div>
          <div class="text-xs text-neutral-500">Группируют агентов, провайдеров, расписания и сессии. Выбор в сайдбаре фильтрует все списки.</div>
        </div>
        <button id="new-project" class="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium">Новый проект</button>
      </div>
      <div class="flex-1 overflow-y-auto p-6" id="projects-list"></div>
    </div>`;
  $('#new-project').onclick = () => projectModal();
  await refreshProjects();
}

async function refreshProjects() {
  let data;
  try { data = await api.get('/api/projects'); } catch (e) { return; }
  const el = $('#projects-list');
  if (!el) return;
  el.innerHTML = data.map(p => `
    <div class="rounded-xl border border-neutral-800 hover:border-neutral-700 p-5 mb-3">
      <div class="flex items-start justify-between">
        <div>
          <div class="font-semibold text-lg">${esc(p.name)}</div>
          <div class="text-sm text-neutral-500">${esc(p.description || '')}</div>
        </div>
        <div class="flex gap-2 text-xs">
          <button class="edit px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800" data-id="${p.id}">изменить</button>
          <button class="del px-3 py-1.5 rounded-lg border border-red-900 text-red-400 hover:bg-red-950" data-id="${p.id}">удалить</button>
        </div>
      </div>
      <div class="flex flex-wrap gap-2 mt-3 text-xs text-neutral-400">
        <span class="px-2 py-1 rounded bg-neutral-800">агентов: ${p.agent_count}</span>
        <span class="px-2 py-1 rounded bg-neutral-800">провайдеров: ${p.provider_count}</span>
        <span class="px-2 py-1 rounded bg-neutral-800">расписаний: ${p.schedule_count}</span>
        <span class="px-2 py-1 rounded bg-neutral-800">сессий: ${p.session_count}</span>
      </div>
    </div>`).join('') || '<div class="text-neutral-500 text-sm">Проектов нет.</div>';
  $$('.edit', el).forEach(b => b.onclick = () => {
    const row = data.find(p => p.id === +b.dataset.id);
    projectModal(row);
  });
  $$('.del', el).forEach(b => b.onclick = async () => {
    const row = data.find(p => p.id === +b.dataset.id);
    if (!confirm(`Удалить проект «${row.name}» вместе со всем содержимым? Будут удалены его агенты, провайдеры, расписания и сессии (контейнеры воркеров тоже).`)) return;
    try {
      await api.del(`/api/projects/${b.dataset.id}`);
    } catch (e) {
      toast(e.message, 'error');
      return;
    }
    await loadProjectMenu();
    await refreshProjects();
  });
}

function projectModal(p, opts = {}) {
  const isEdit = !!p;
  openModal(isEdit ? `Проект: ${p.name}` : 'Новый проект', `
    ${formInput('Название', 'name', p ? p.name : '')}
    ${formInput('Описание', 'description', p ? p.description : '')}`,
    async (close) => {
      const f = readForm($('#modal-body'));
      if (!f.name.trim()) throw new Error('Название обязательно');
      let created = null;
      if (isEdit) await api.put(`/api/projects/${p.id}`, f);
      else created = await api.post('/api/projects', f);
      if (created && opts.selectAfterCreate) {
        currentProject = String(created.id);
        localStorage.setItem('harness-project', String(created.id));
      }
      close();
      await loadProjectMenu();
      await refreshProjects();
      if (opts.selectAfterCreate) await refreshCurrentView();
    });
}

/* ---------- boot ---------- */
(async function boot() {
  await loadProjectMenu();
  try {
    const h = await fetch('/api/sessions').then(r => r.json());
    $('#server-info').textContent = `сессий: ${h.length}`;
  } catch {}
  const [view, arg] = (location.hash.slice(1) || 'home').split('/');
  showView(view, arg);
})();
