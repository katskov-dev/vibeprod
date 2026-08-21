/* opencode Vibeprod frontend */
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
    if (r.status === 401) {
      location.href = '/login';
      throw new Error('Не авторизован');
    }
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

function openImageLightbox(src, name = '') {
  const root = $('#modal-root');
  let dl = src;
  try {
    const u = new URL(src, window.location.origin);
    if (u.pathname.startsWith('/api/files/content')) {
      u.searchParams.set('download', 'true');
      dl = u.toString();
    }
  } catch {}
  root.innerHTML = `
    <div class="fixed inset-0 bg-black/80 z-50 flex flex-col" id="imgbox">
      <div class="flex items-center justify-between gap-3 px-4 py-2.5 bg-neutral-900/95 border-b border-neutral-800 shrink-0" id="imgbox-bar">
        <div class="text-sm text-neutral-300 truncate">${esc(name || new URL(src, window.location.origin).hostname)}</div>
        <div class="flex items-center gap-2 shrink-0">
          <a href="${esc(dl)}" download class="px-3 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium no-underline">Скачать</a>
          <a href="${esc(src)}" target="_blank" rel="noopener" class="px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-sm no-underline">Открыть в новой вкладке</a>
          <button class="text-neutral-400 hover:text-white text-2xl leading-none px-2">&times;</button>
        </div>
      </div>
      <div class="flex-1 flex items-center justify-center p-4 min-h-0 cursor-zoom-out" id="imgbox-stage">
        <img src="${esc(src)}" alt="" class="max-w-full max-h-full object-contain rounded-lg shadow-2xl">
      </div>
    </div>`;
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  const close = () => {
    root.innerHTML = '';
    document.removeEventListener('keydown', onKey);
  };
  const bar = $('#imgbox-bar');
  if (bar) bar.addEventListener('click', (e) => e.stopPropagation());
  $('#imgbox').onclick = close;
  document.addEventListener('keydown', onKey);
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

const VIEW_TITLES = {
  home: 'Начать работу', sessions: 'Сессии', issues: 'Issues', files: 'Файлы',
  agents: 'Агенты', providers: 'Провайдеры', 'mcp-catalog': 'MCP', skills: 'Скиллы',
  webhooks: 'Вебхуки', outwebhooks: 'Исходящие', schedules: 'Расписания',
  channels: 'Каналы', projects: 'Настройки проекта', ssh: 'SSH',
};

function setNavOpen(open) {
  const sb = $('#sidebar');
  const ov = $('#nav-overlay');
  if (!sb) return;
  sb.classList.toggle('-translate-x-full', !open);
  if (ov) ov.classList.toggle('hidden', !open);
}

async function loadProjectMenu() {
  try { projectsCache = await api.get('/api/projects'); } catch { projectsCache = []; }
  const stored = localStorage.getItem('vibeprod-project');
  if (stored !== null && projectsCache.some(p => String(p.id) === stored)) {
    currentProject = stored;
  } else {
    currentProject = projectsCache.length ? String(projectsCache[0].id) : null;
    if (currentProject) localStorage.setItem('vibeprod-project', currentProject);
    else localStorage.removeItem('vibeprod-project');
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
    if (currentProject) localStorage.setItem('vibeprod-project', String(currentProject));
    else localStorage.removeItem('vibeprod-project');
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
  else if (currentView === 'issues') renderIssues();
  else if (currentView === 'agents') renderAgents();
  else if (currentView === 'providers') renderProviders();
  else if (currentView === 'files') renderFiles();
  else if (currentView === 'mcp-catalog') renderCatalog();
  else if (currentView === 'skills') renderSkills();
  else if (currentView === 'webhooks') renderAutomation('webhooks');
  else if (currentView === 'outwebhooks') renderAutomation('outwebhooks');
  else if (currentView === 'schedules' || currentView === 'automation') renderAutomation('schedules');
  else if (currentView === 'channels') renderChannels();
  else if (currentView === 'ssh') renderSsh();
  else if (currentView === 'projects') renderProjects();
}

function showView(view, arg) {
  currentView = view;
  $$('.nav-btn').forEach(b => {
    const active = b.dataset.view === view;
    b.classList.toggle('bg-neutral-800', active);
    b.classList.toggle('hover:bg-neutral-800/60', !active);
  });
  const titleEl = $('#mobile-view-title');
  if (titleEl) titleEl.textContent = VIEW_TITLES[view] || '';
  setNavOpen(false);
  window.history.pushState({ view, arg }, '', `#${view}${arg ? '/' + arg : ''}`);
  if (view === 'home') renderHome();
  else if (view === 'sessions') renderSessions(arg);
  else if (view === 'issues') renderIssues();
  else if (view === 'agents') renderAgents();
  else if (view === 'providers') renderProviders();
  else if (view === 'files') renderFiles();
  else if (view === 'mcp-catalog') renderCatalog();
  else if (view === 'skills') renderSkills();
  else if (view === 'webhooks') renderAutomation('webhooks');
  else if (view === 'outwebhooks') renderAutomation('outwebhooks');
  else if (view === 'schedules' || view === 'automation') renderAutomation('schedules');
  else if (view === 'channels') renderChannels(arg);
  else if (view === 'ssh') renderSsh(arg);
  else if (view === 'projects') renderProjects();
}
$('#nav').onclick = (e) => {
  const btn = e.target.closest('.nav-btn');
  if (btn) showView(btn.dataset.view);
};
$('#nav-toggle').onclick = () => setNavOpen(true);
$('#nav-overlay').onclick = () => setNavOpen(false);
$('#project-trigger').onclick = (e) => {
  e.stopPropagation();
  setProjectMenu($('#project-menu').classList.contains('hidden'));
};
$('#project-add').onclick = () => { setProjectMenu(false); projectModal(null, { selectAfterCreate: true }); };
$('#project-manage').onclick = () => { setProjectMenu(false); showView('projects'); };
document.addEventListener('click', (e) => {
  const mdImg = e.target.closest('.md img');
  if (mdImg && !mdImg.closest('a')) {
    e.preventDefault();
    const fig = mdImg.closest('.md-img');
    const nameEl = fig ? fig.querySelector('.md-img-name') : null;
    openImageLightbox(mdImg.src, nameEl ? nameEl.textContent : '');
    return;
  }
  const mdLink = e.target.closest('.md a');
  if (mdLink) {
    let u;
    try { u = new URL(mdLink.href, window.location.origin); } catch { u = null; }
    if (u && u.pathname.startsWith('/api/files/content') && /\.(html?|xhtml?|htm)$/i.test(u.searchParams.get('path') || '')) {
      e.preventDefault();
      const path = u.searchParams.get('path') || '';
      openFilePreview({ name: path, url: mdLink.href, size: 0, content_type: 'text/html' });
      return;
    }
  }
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
  let agents = [];
  try {
    const [g, a] = await Promise.all([
      api.get('/api/guardian'),
      api.get('/api/agents' + projQuery()),
    ]);
    info = g; agents = a;
  } catch {}
  const options = [
    ...(info.ready ? [{ v: info.agent_id, l: info.name, tag: 'оператор' }] : []),
    ...agents.map(a => ({ v: a.id, l: a.name, tag: a.mode === 'subagent' ? 'subagent' : '' })),
  ];
  const optLabel = (o) => `${esc(o.l)}${o.tag ? ` · ${esc(o.tag)}` : ''}`;
  main.innerHTML = `
    <div class="relative h-full overflow-y-auto">
      <div class="pointer-events-none absolute inset-0 overflow-hidden">
        <div class="absolute -top-40 left-1/2 -translate-x-1/2 w-[46rem] h-[28rem] rounded-full bg-sky-500/10 blur-3xl"></div>
        <div class="absolute top-1/3 -left-28 w-96 h-96 rounded-full bg-indigo-500/10 blur-3xl"></div>
        <div class="absolute top-1/4 -right-28 w-96 h-96 rounded-full bg-fuchsia-500/10 blur-3xl"></div>
      </div>
      <div class="relative max-w-2xl mx-auto px-6 pt-24 pb-16 flex flex-col items-center">
        <div class="w-12 h-12 rounded-2xl bg-gradient-to-br from-sky-500 to-indigo-600 flex items-center justify-center mb-6 shadow-lg shadow-sky-500/25">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z"></path>
            <path d="M19 15l.9 2.1L22 18l-2.1.9L19 21l-.9-2.1L16 18l2.1-.9L19 15z"></path>
          </svg>
        </div>
        <div class="text-4xl font-bold tracking-tight text-center bg-gradient-to-r from-white via-sky-200 to-indigo-300 bg-clip-text text-transparent">Чем помочь сегодня?</div>
        <div class="text-sm text-neutral-400 mt-3 text-center max-w-lg">Опишите задачу — агент сам настроит проект: создаст агентов, подключит MCP и скиллы, добавит провайдеров, вебхуки и расписания.</div>
        ${info.ready ? '' : `
        <div class="w-full mt-6 px-4 py-3 rounded-xl border border-amber-900/60 bg-amber-950/40 text-amber-300 text-sm text-center">
          Агент-оператор не найден — перезапустите сервер.
        </div>`}
        <div class="w-full mt-8">
          <div id="home-composer" class="rounded-2xl border border-neutral-700/80 bg-neutral-900/90 shadow-2xl shadow-black/40 backdrop-blur transition-colors focus-within:border-sky-500/70">
            <textarea id="home-prompt" rows="2" placeholder="Опишите, что нужно сделать… Например: создай агента для проверки сайта с подключённым playwright"
              class="w-full bg-transparent px-5 pt-5 pb-3 text-[15px] leading-relaxed resize-none focus:outline-none placeholder:text-neutral-600"></textarea>
            <div class="flex items-center justify-between gap-3 px-3 pb-3">
              <div class="relative shrink-0">
                <span class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 w-1.5 h-1.5 rounded-full bg-sky-400"></span>
                <select name="agent_id" ${options.length ? '' : 'disabled'}
                  class="appearance-none bg-neutral-800/80 border border-neutral-700 hover:border-neutral-600 rounded-xl pl-6 pr-8 py-2 text-sm text-neutral-200 cursor-pointer focus:outline-none focus:border-sky-500 disabled:opacity-40">
                  ${options.map(o => `<option value="${o.v}" ${options[0] && o.v === options[0].v ? 'selected' : ''}>${optLabel(o)}</option>`).join('') || '<option>нет агентов</option>'}
                </select>
                <span class="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-neutral-500 text-[10px]">▾</span>
              </div>
              <button id="home-go" class="px-5 py-2 rounded-xl bg-gradient-to-r from-sky-500 to-indigo-500 hover:from-sky-400 hover:to-indigo-400 text-sm font-semibold shadow-lg shadow-sky-600/25 transition-all ${options.length ? '' : 'opacity-40 pointer-events-none'}">Начать</button>
            </div>
          </div>
          <div id="home-error" class="text-sm text-red-400 mt-3 text-center"></div>
          <div class="text-[11px] text-neutral-600 text-center mt-3">Enter — отправить · Shift+Enter — новая строка</div>
        </div>
        <div class="mt-12 w-full">
          <div class="text-[10px] uppercase tracking-widest text-neutral-600 mb-3 text-center">Попробуйте</div>
          <div class="flex flex-wrap justify-center gap-2" id="home-examples">
            ${HOME_EXAMPLES.map(e => `<button class="home-ex px-3.5 py-2 rounded-full border border-neutral-800 bg-neutral-900/60 hover:border-sky-700 hover:bg-neutral-800/70 hover:text-neutral-100 text-xs text-neutral-400 transition-colors" data-t="${esc(e)}">${esc(e)}</button>`).join('')}
          </div>
        </div>
      </div>
    </div>`;
  const prompt = $('#home-prompt');
  const err = $('#home-error');
  const autoGrow = (ta) => {
    ta.style.height = 'auto';
    ta.style.height = Math.min(ta.scrollHeight, 220) + 'px';
  };
  $$('.home-ex', main).forEach(b => b.onclick = () => {
    prompt.value = b.dataset.t;
    prompt.focus();
    autoGrow(prompt);
  });
  prompt.addEventListener('input', () => autoGrow(prompt));
  prompt.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      $('#home-go').click();
    }
  });
  const go = $('#home-go');
  go.onclick = async () => {
    const text = prompt.value.trim();
    if (!text) { err.textContent = 'Введите промпт'; prompt.focus(); return; }
    if (!options.length) { err.textContent = 'Нет агентов — создайте агента в разделе «Агенты»'; return; }
    go.disabled = true;
    err.textContent = '';
    try {
      const sel = $('[name=agent_id]', main);
      const s = await api.post('/api/sessions', {
        agent_id: sel ? +sel.value : info.agent_id,
        title: text.slice(0, 60),
        prompt: text,
        source: !sel || sel.value === String(info.agent_id) ? 'guardian' : 'manual',
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
          ${s.source === 'agent' ? '<span class="px-1.5 py-0.5 rounded bg-emerald-900/60 text-xs shrink-0">вызов агента</span>' : ''}
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

function rewriteFileLinks(html) {
  if (typeof DOMPurify === 'undefined') return html;
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  const base = window.location.origin;
  tpl.content.querySelectorAll('a[href], img[src]').forEach((node) => {
    const attr = node.hasAttribute('href') ? 'href' : 'src';
    let u;
    try { u = new URL(node.getAttribute(attr), base); } catch { return; }
    if (u.pathname.startsWith('/api/files/content')) {
      node.setAttribute(attr, base + u.pathname + u.search);
    }
  });
  decorateImages(tpl.content, base);
  return tpl.innerHTML;
}

function decorateImages(root, base) {
  root.querySelectorAll('img[src]').forEach((img) => {
    if (img.closest('a')) return;
    let u;
    try { u = new URL(img.getAttribute('src'), base); } catch { return; }
    let name = '';
    let statUrl = '';
    if (u.pathname.startsWith('/api/files/content')) {
      const path = u.searchParams.get('path') || '';
      name = path.split('/').pop() || u.hostname;
      const p = new URLSearchParams(u.search);
      p.set('path', path);
      statUrl = `${base}/api/files/stat?${p.toString()}`;
    } else {
      name = u.hostname;
    }
    const fig = document.createElement('figure');
    fig.className = 'md-img';
    img.replaceWith(fig);
    fig.appendChild(img);
    const cap = document.createElement('figcaption');
    cap.innerHTML = `<span class="md-img-name" title="${esc(name)}">${esc(name)}</span>`;
    if (statUrl) {
      const sizeEl = document.createElement('span');
      sizeEl.className = 'md-img-size';
      cap.appendChild(sizeEl);
      fetch(statUrl)
        .then((r) => (r.ok ? r.json() : null))
        .then((d) => { if (d && d.size != null) sizeEl.textContent = fmtSize(d.size); })
        .catch(() => {});
    }
    fig.appendChild(cap);
  });
}

function renderMarkdown(text) {
  const src = String(text ?? '')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .replace(/^\n+|\n+$/g, '');
  if (typeof marked !== 'undefined' && typeof DOMPurify !== 'undefined') {
    try {
      marked.setOptions({ gfm: true, breaks: true });
      return rewriteFileLinks(DOMPurify.sanitize(marked.parse(src)));
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
      : `<div class="max-w-[85%] group">
           <div class="bg-neutral-900 border border-neutral-800 rounded-2xl rounded-bl-sm px-3 py-2 text-sm">
             <div data-content class="space-y-2"></div>
           </div>
           <div class="flex mt-0.5">
             <button class="msg-actions text-neutral-500 hover:text-neutral-200 text-xs leading-none px-1 py-0.5 rounded" data-copy="message" title="Скопировать">⧉</button>
           </div>
         </div>`;
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

function filePutPreviewCard(tool, state) {
  if (state.status !== 'completed') return '';
  let out = state.output;
  if (typeof out === 'string') {
    try { out = JSON.parse(out); } catch { return ''; }
  }
  if (!out || typeof out !== 'object' || !out.url || !out.path) return '';
  if (!/\.(html?|xhtml?|htm)$/i.test(out.path)) return '';
  return `
    <div class="file-preview-card group mt-1.5 flex items-center gap-3 rounded-lg border border-neutral-700/70 bg-neutral-900/60 px-3 py-2 cursor-pointer hover:border-sky-600 transition-colors"
         data-file-preview data-url="${esc(out.url)}" data-name="${esc(out.path)}" data-size="${out.size ?? ''}" data-type="text/html">
      <span class="text-lg shrink-0">🖥️</span>
      <span class="min-w-0 flex-1">
        <span class="mono text-xs text-sky-300 truncate block">${esc(out.path)}</span>
        <span class="text-[10px] text-neutral-500">HTML · ${out.size ? fmtSize(out.size) : 'предпросмотр'}</span>
      </span>
      <span class="shrink-0 px-2.5 py-1 rounded-md bg-sky-600 group-hover:bg-sky-500 text-xs font-medium">Предпросмотр</span>
    </div>`;
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
  const previewCard = /(_file_put|_upload_file)$/.test(tool) ? filePutPreviewCard(tool, state) : '';
  return `<div>${previewCard}<details class="compact-card tool-card border border-neutral-800/70 rounded-md px-1.5 py-0.5 text-[11px] bg-neutral-900/40">
    <summary>
      <span class="chev text-neutral-600 text-[9px]">▸</span>
      <span class="mono text-sky-400 shrink-0">${esc(tool)}</span>
      <span class="${stCls} shrink-0">${esc(st)}</span>
      ${title ? `<span class="text-neutral-600 truncate">${esc(title)}</span>` : ''}
    </summary>
    <pre class="mt-0.5 pt-0.5 border-t border-neutral-800/60 mono text-[10px] text-neutral-400 max-h-40 overflow-y-auto">${esc(JSON.stringify(input, null, 2))}${output !== null ? '\n\n→ ' + esc(typeof output === 'string' ? output : JSON.stringify(output, null, 2)) : ''}</pre>
  </details></div>`;
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
      if (msg.type === 'status') {
        setStatus(msg.status, msg.error);
        if (['completed', 'failed', 'aborted', 'expired'].includes(msg.status)) generating = false;
      }
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
      if (['completed', 'failed', 'aborted', 'expired'].includes(s.status)) {
        const inp = $('#chat-input');
        if (inp) inp.placeholder = 'Продолжить сессию… (Enter — отправить)';
      }
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
      const r = await api.post(`/api/sessions/${activeSessionId}/continue`, { text });
      if (r.restarted) {
        setStatus('starting');
        toast('Воркер перезапускается — сообщение уйдёт после старта');
      }
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
    const card = e.target.closest('[data-file-preview]');
    if (card) {
      e.preventDefault();
      e.stopPropagation();
      openFilePreview({
        name: card.dataset.name,
        url: card.dataset.url,
        size: +card.dataset.size || 0,
        content_type: card.dataset.type || 'text/html',
      });
      return;
    }
    const btn = e.target.closest('[data-copy]');
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    let text = '';
    if (btn.dataset.copy === 'reason') {
      const details = btn.closest('details');
      const src = details ? $('[data-reason-text]', details) : null;
      text = src ? src.textContent : '';
    } else if (btn.dataset.copy === 'message') {
      const wrap = btn.closest('[data-msg]');
      const parts = wrap ? $$('[data-p]', wrap) : [];
      text = parts.map(p => p.dataset.raw || p.textContent).filter(Boolean).join('\n\n');
    }
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
  el.innerHTML = `<div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">${data.map(a => `
    <div class="rounded-xl border border-neutral-800 hover:border-neutral-700 p-5 flex flex-col">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <div class="font-semibold text-lg truncate">
            ${esc(a.name)}
            ${a.is_default ? '<span class="text-xs text-sky-400">default</span>' : ''}
          </div>
          ${a.description ? `<div class="text-sm text-neutral-500 line-clamp-2 mt-0.5">${esc(a.description)}</div>` : ''}
        </div>
        <div class="flex gap-1 text-xs shrink-0">
          <button class="edit px-2 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800" data-id="${a.id}" title="изменить">✎</button>
          <button class="del px-2 py-1.5 rounded-lg border border-red-900 text-red-400 hover:bg-red-950" data-id="${a.id}" title="удалить">✕</button>
        </div>
      </div>
      <div class="mt-auto pt-4">
        <button class="write w-full px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium" data-id="${a.id}">Написать</button>
      </div>
    </div>`).join('')}</div>`;
  $$('.edit', el).forEach(b => b.onclick = () => agentModal(+b.dataset.id));
  $$('.del', el).forEach(b => b.onclick = async () => {
    if (!confirm('Удалить агента?')) return;
    await api.del(`/api/agents/${b.dataset.id}`);
    await refreshAgentsList();
  });
  $$('.write', el).forEach(b => b.onclick = () => writeAgent(+b.dataset.id));
}

async function writeAgent(agentId) {
  let a;
  try { a = await api.get(`/api/agents/${agentId}`); } catch { return; }
  openModal(`Написать агенту «${a.name}»`, `
    ${formArea('Промпт', 'prompt', '', 6, 'Что нужно сделать…')}`,
    async (close) => {
      const f = readForm($('#modal-body'));
      if (!f.prompt.trim()) throw new Error('Промпт обязателен');
      const s = await api.post('/api/sessions', { agent_id: agentId, prompt: f.prompt, project_id: currentProject || undefined });
      close();
      showView('sessions', s.id);
    });
}

async function agentModal(agentId) {
  const isEdit = !!agentId;
  const a = isEdit ? await api.get(`/api/agents/${agentId}`) : {
    name: '', description: '', mode: 'primary', model: 'deepseek/deepseek-chat',
    temperature: '', system_prompt: '', permission: '"allow"', is_default: false,
    memory: '', memory_enabled: true, mcp: [], skills: [], calls: [],
  };
  const skills = await api.get('/api/skills');
  let allAgents = [];
  try { allAgents = await api.get('/api/agents' + projQuery()); } catch {}
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
      <button id="tab-memory" class="px-3 py-1.5 rounded-lg hover:bg-neutral-800/60">Память</button>
      <button id="tab-mcp" class="px-3 py-1.5 rounded-lg hover:bg-neutral-800/60">MCP (${a.mcp.length})</button>
      <button id="tab-skills" class="px-3 py-1.5 rounded-lg hover:bg-neutral-800/60">Скиллы (${a.skills.length})</button>
      <button id="tab-calls" class="px-3 py-1.5 rounded-lg hover:bg-neutral-800/60">Вызовы (${a.calls.length})</button>
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
    <div id="tab-memory-pane" class="hidden">
      <label class="flex items-center gap-2 text-sm mb-3">
        <input type="checkbox" name="memory_enabled" ${a.memory_enabled ? 'checked' : ''} class="accent-sky-600">
        <span class="text-neutral-400 text-xs">Включить память агента (инструменты memory_get/memory_set и контекст между сессиями)</span>
      </label>
      ${formArea('Память (долговременный текст агента — что он помнит между задачами)', 'memory', a.memory || '', 10)}
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
    </div>
    <div id="tab-calls-pane" class="hidden">
      <div class="text-xs text-neutral-500 mb-3">Отмеченных агентов этот агент сможет вызвать инструментами agent_call_list/agent_run — каждый запустится отдельной сессией со своим workspace, инструментами и памятью, результат вернётся вызывающему.</div>
      <div id="calls-checkbox" class="space-y-2">${allAgents.filter(x => !isEdit || x.id !== agentId).map(x => `
        <label class="flex items-center gap-2 text-sm">
          <input type="checkbox" data-call="${x.id}" class="accent-emerald-600" ${a.calls.some(c => c.id === x.id) ? 'checked' : ''}>
          <span class="mono text-xs text-sky-300">${esc(x.name)}</span>
          <span class="text-xs text-neutral-500">— ${esc(x.description || '')}</span>
        </label>`).join('') || '<div class="text-neutral-600 text-sm">Других агентов пока нет — создайте их, чтобы этот агент мог их вызывать.</div>'}
      </div>
    </div>`;
  openModal(isEdit ? `Агент: ${a.name}` : 'Новый агент', body, async (close) => {
    const f = { ...readForm($('#tab-general-pane')), ...readForm($('#tab-system-pane')), ...readForm($('#tab-memory-pane')) };
    f.is_default = $('[name="is_default"]', $('#tab-general-pane')).checked;
    f.memory_enabled = $('[name="memory_enabled"]', $('#tab-memory-pane')).checked;
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
    const callIds = $$('#calls-checkbox [data-call]:checked').map(el => +el.dataset.call);
    await api.put(`/api/agents/${agentId}/calls`, { target_ids: callIds });
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
  const tabs = { 'tab-general': 'tab-general-pane', 'tab-system': 'tab-system-pane', 'tab-memory': 'tab-memory-pane', 'tab-mcp': 'tab-mcp-pane', 'tab-skills': 'tab-skills-pane', 'tab-calls': 'tab-calls-pane' };
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

/* ---------- issues ---------- */
const ISSUE_STATUSES = {
  open: { label: 'открыт', cls: 'bg-sky-900/60 text-sky-300 border-sky-800' },
  in_progress: { label: 'в работе', cls: 'bg-purple-900/60 text-purple-300 border-purple-800' },
  done: { label: 'готово', cls: 'bg-emerald-900/60 text-emerald-300 border-emerald-800' },
};
let issuesCache = [];
const issueFilters = { q: '', tag: '', tab: 'open' };

async function renderIssues() {
  const main = $('#main');
  main.innerHTML = `
    <div class="h-full flex flex-col">
      <div class="flex items-center justify-between px-6 py-4 border-b border-neutral-800">
        <div>
          <div class="text-lg font-semibold">Issues</div>
          <div class="text-xs text-neutral-500">Трекер задач проекта: агенты заводят сюда issues инструментами vibeprod (issue_create), вы видите их здесь.</div>
        </div>
        <button id="new-issue" class="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium">Новый issue</button>
      </div>
      <div class="flex flex-wrap items-center gap-2 px-6 py-3 border-b border-neutral-800">
        <input id="issue-search" placeholder="Поиск по названию, описанию, тегам…" value="${esc(issueFilters.q)}"
          class="w-72 bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:border-sky-600">
        <select id="issue-tag-filter" class="bg-neutral-800 border border-neutral-700 rounded-lg px-2 py-1.5 text-sm">
          <option value="">все теги</option>
          ${[...new Set(issuesCache.flatMap(i => i.tags || []))].sort().map(t => `<option value="${esc(t)}" ${issueFilters.tag === t ? 'selected' : ''}>${esc(t)}</option>`).join('')}
        </select>
        <span id="issue-count" class="text-xs text-neutral-500"></span>
      </div>
      <div class="flex items-center gap-1 px-6 pt-3 border-b border-neutral-800">
        <button id="issue-tab-open" class="issue-tab px-4 py-2 rounded-t-lg text-sm font-medium border-b-2">Открытые <span id="issue-tab-open-count" class="text-neutral-500 font-normal"></span></button>
        <button id="issue-tab-closed" class="issue-tab px-4 py-2 rounded-t-lg text-sm font-medium border-b-2">Закрытые <span id="issue-tab-closed-count" class="text-neutral-500 font-normal"></span></button>
      </div>
      <div class="flex-1 overflow-y-auto p-6" id="issues-list"></div>
    </div>`;
  $('#new-issue').onclick = () => issueModal(null);
  $('#issue-search').oninput = (e) => { issueFilters.q = e.target.value.trim(); renderIssuesList(); };
  $('#issue-tag-filter').onchange = (e) => { issueFilters.tag = e.target.value; renderIssuesList(); };
  const setTab = (tab) => {
    issueFilters.tab = tab;
    $$('.issue-tab').forEach(b => {
      const active = (b.id === `issue-tab-${tab}`);
      b.classList.toggle('text-white', active);
      b.classList.toggle('text-neutral-500', !active);
      b.classList.toggle('border-sky-500', active);
      b.classList.toggle('border-transparent', !active);
      b.classList.toggle('hover:text-neutral-200', !active);
    });
    renderIssuesList();
  };
  $('#issue-tab-open').onclick = () => setTab('open');
  $('#issue-tab-closed').onclick = () => setTab('closed');
  setTab(issueFilters.tab);
  await refreshIssues();
}

async function refreshIssues() {
  try { issuesCache = await api.get('/api/issues' + projQuery()); } catch (e) { return; }
  const openCount = issuesCache.filter(i => i.status !== 'done').length;
  const closedCount = issuesCache.length - openCount;
  const oc = $('#issue-tab-open-count');
  const cc = $('#issue-tab-closed-count');
  if (oc) oc.textContent = openCount ? `· ${openCount}` : '';
  if (cc) cc.textContent = closedCount ? `· ${closedCount}` : '';
  renderIssuesList();
  const tagSel = $('#issue-tag-filter');
  if (tagSel) {
    const tags = [...new Set(issuesCache.flatMap(i => i.tags || []))].sort();
    tagSel.innerHTML = `<option value="">все теги</option>` + tags.map(t => `<option value="${esc(t)}" ${issueFilters.tag === t ? 'selected' : ''}>${esc(t)}</option>`).join('');
  }
}

function renderIssuesList() {
  const el = $('#issues-list');
  if (!el) return;
  const q = issueFilters.q.toLowerCase();
  const rows = issuesCache.filter(i => {
    if (issueFilters.tab === 'open' && i.status === 'done') return false;
    if (issueFilters.tab === 'closed' && i.status !== 'done') return false;
    if (issueFilters.tag && !(i.tags || []).includes(issueFilters.tag)) return false;
    if (q) {
      const hay = `${i.title} ${i.description || ''} ${(i.tags || []).join(' ')}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  const count = $('#issue-count');
  if (count) count.textContent = `показано ${rows.length} из ${issuesCache.length}`;
  if (!rows.length) {
    el.innerHTML = `<div class="text-neutral-500 text-sm">Issues нет. Создайте вручную или попросите агента: инструменты issue_create / issue_list / issue_update.</div>`;
    return;
  }
  el.innerHTML = rows.map(i => {
    const st = ISSUE_STATUSES[i.status] || ISSUE_STATUSES.open;
    return `
    <div class="rounded-xl border border-neutral-800 hover:border-neutral-700 p-4 mb-3">
      <div class="flex items-start justify-between gap-3">
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="px-2 py-0.5 rounded border text-xs ${st.cls}">${st.label}</span>
            <span class="font-semibold">${esc(i.title)}</span>
            ${i.created_by === 'agent' ? '<span class="text-[10px] px-1.5 py-0.5 rounded bg-neutral-800 text-neutral-400" title="заведено агентом">агент</span>' : ''}
          </div>
          <div class="text-xs text-neutral-500 mt-1">#${i.id} · ${esc(i.created_at || '')}${i.updated_at && i.updated_at !== i.created_at ? ` · изменено ${esc(i.updated_at)}` : ''}</div>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <select class="issue-status bg-neutral-800 border border-neutral-700 rounded-lg px-2 py-1 text-xs" data-id="${i.id}">
            ${Object.entries(ISSUE_STATUSES).map(([v, s]) => `<option value="${v}" ${i.status === v ? 'selected' : ''}>${s.label}</option>`).join('')}
          </select>
          <button class="edit px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-xs" data-id="${i.id}">изменить</button>
          <button class="del px-3 py-1.5 rounded-lg border border-red-900 text-red-400 hover:bg-red-950 text-xs" data-id="${i.id}">удалить</button>
        </div>
      </div>
      ${i.description ? `<details class="mt-2"><summary class="text-xs text-neutral-400 cursor-pointer select-none">описание</summary><div class="md mt-2 text-sm leading-relaxed whitespace-pre-wrap">${esc(i.description)}</div></details>` : ''}
      ${(i.tags || []).length ? `<div class="flex flex-wrap gap-1.5 mt-2">${i.tags.map(t => `<button class="tag-chip px-2 py-0.5 rounded-full bg-neutral-800 text-xs text-neutral-300 hover:bg-neutral-700" data-tag="${esc(t)}">${esc(t)}</button>`).join('')}</div>` : ''}
    </div>`;
  }).join('');
  $$('.issue-status', el).forEach(sel => sel.onchange = async () => {
    try {
      await api.put(`/api/issues/${sel.dataset.id}`, { status: sel.value });
      toast('Статус обновлён', 'ok');
    } catch (e) { toast(e.message, 'error'); }
    await refreshIssues();
  });
  $$('.tag-chip', el).forEach(chip => chip.onclick = () => {
    issueFilters.tag = chip.dataset.tag;
    const sel = $('#issue-tag-filter');
    if (sel) sel.value = issueFilters.tag;
    renderIssuesList();
  });
  $$('.edit', el).forEach(b => b.onclick = () => issueModal(issuesCache.find(i => i.id === +b.dataset.id)));
  $$('.del', el).forEach(b => b.onclick = async () => {
    if (!confirm('Удалить issue?')) return;
    await api.del(`/api/issues/${b.dataset.id}`);
    await refreshIssues();
  });
}

function issueModal(row) {
  const isEdit = !!row;
  row = row || { title: '', description: '', status: 'open', tags: [] };
  const projectSelect = currentProject ? '' : formSelect('Проект', 'project_id', projectsCache.map(p => ({ v: p.id, l: p.name })), currentProject);
  openModal(isEdit ? `Issue #${row.id}` : 'Новый issue', `
    ${formInput('Название', 'title', row.title, 'Коротко о задаче')}
    ${formArea('Описание', 'description', row.description, 6, 'Контекст, шаги, ожидания…')}
    ${formSelect('Статус', 'status', Object.entries(ISSUE_STATUSES).map(([v, s]) => ({ v, l: s.label })), row.status)}
    ${formInput('Теги (через запятую)', 'tags', (row.tags || []).join(', '), 'баг, рефакторинг')}
    ${projectSelect}`, async (close) => {
      const f = readForm($('#modal-body'));
      if (!f.title.trim()) throw new Error('Название обязательно');
      const payload = { title: f.title, description: f.description, status: f.status, tags: f.tags.split(',').map(t => t.trim()).filter(Boolean) };
      if (isEdit) await api.put(`/api/issues/${row.id}`, payload);
      else {
        if (currentProject) payload.project_id = currentProject;
        await api.post('/api/issues', payload);
      }
      close();
      await refreshIssues();
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
      <div class="flex-1 overflow-y-auto p-6">
        <div id="providers-added"></div>
        <div class="mt-6 border-t border-neutral-800 pt-4">
          <div class="flex items-center justify-between mb-2">
            <div class="text-sm font-semibold">Каталог opencode — все доступные провайдеры</div>
            <div class="flex items-center gap-2">
              <span id="provider-catalog-meta" class="text-xs text-neutral-500"></span>
              <button id="provider-catalog-refresh" class="px-2.5 py-1 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-xs">обновить</button>
            </div>
          </div>
          <input id="provider-catalog-search" placeholder="Поиск по id или названию…" class="mb-3 w-full bg-neutral-800 border border-neutral-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-sky-600">
          <div id="provider-catalog-list" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2"></div>
        </div>
      </div>
    </div>`;
  $('#new-provider').onclick = () => providerModal();
  $('#provider-catalog-refresh').onclick = () => refreshProviderCatalog(true);
  $('#provider-catalog-search').oninput = renderProviderCatalog;
  await refreshProviders();
  await refreshProviderCatalog();
}

async function refreshProviders() {
  let data;
  try { data = await api.get('/api/providers' + projQuery()); } catch (e) { return; }
  window._addedProviderIds = data.map(p => p.id);
  const el = $('#providers-added');
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
  if ($('#provider-catalog-list')) renderProviderCatalog();
}

/* ---------- каталог провайдеров opencode ---------- */
let providerCatalog = [];
let providerCatalogLoaded = false;

async function refreshProviderCatalog(forceRefresh) {
  const el = $('#provider-catalog-list');
  if (!el) return;
  if (!forceRefresh && providerCatalogLoaded) { renderProviderCatalog(); return; }
  el.innerHTML = `<div class="col-span-full text-neutral-500 text-sm">Загружаем каталог opencode… (первый раз поднимается probe-контейнер, может занять до минуты)</div>`;
  $('#provider-catalog-meta').textContent = '';
  try {
    const cat = await api.get('/api/providers/available' + (forceRefresh ? '?refresh=1' : ''));
    providerCatalog = cat.providers || [];
    providerCatalogLoaded = true;
    $('#provider-catalog-meta').textContent = `${cat.count} провайдеров · opencode ${esc(cat.version)} · ${esc(cat.fetched_at)}${cat.stale ? ' · кэш устарел' : ''}`;
  } catch (e) {
    el.innerHTML = `<div class="col-span-full text-red-400 text-sm">Не удалось загрузить каталог: ${esc(e.message)}</div>`;
    return;
  }
  renderProviderCatalog();
}

function renderProviderCatalog() {
  const el = $('#provider-catalog-list');
  if (!el || !providerCatalogLoaded) return;
  const q = ($('#provider-catalog-search')?.value || '').trim().toLowerCase();
  const added = new Set(window._addedProviderIds || []);
  const items = providerCatalog.filter(p =>
    !q || (p.id || '').toLowerCase().includes(q) || (p.name || '').toLowerCase().includes(q));
  el.innerHTML = items.length
    ? items.map(p => `
      <div class="rounded-lg border border-neutral-800 hover:border-neutral-700 px-3 py-2">
        <div class="flex items-center justify-between gap-2">
          <div class="min-w-0">
            <div class="mono text-sky-300 text-xs truncate">${esc(p.id)}</div>
            <div class="text-xs text-neutral-400 truncate">${esc(p.name || '')}</div>
          </div>
          ${added.has(p.id)
            ? '<span class="text-[11px] px-2 py-0.5 rounded bg-emerald-900/50 text-emerald-400 shrink-0">добавлен</span>'
            : `<button class="catalog-add px-2.5 py-1 rounded-lg border border-sky-800 text-sky-300 hover:bg-sky-950 text-[11px] shrink-0" data-id="${esc(p.id)}" data-name="${esc(p.name || '')}">добавить</button>`}
        </div>
        <div class="text-[11px] text-neutral-500 mt-1 truncate">моделей: ${p.models.length}${p.default_model ? ` · default: ${esc(p.default_model)}` : ''}</div>
      </div>`).join('')
    : '<div class="col-span-full text-neutral-500 text-sm">Ничего не найдено.</div>';
  $$('.catalog-add', el).forEach(b => b.onclick = () => providerModal(null, { id: b.dataset.id, label: b.dataset.name }));
}

function providerModal(p, prefill) {
  const isEdit = !!(p && p.env_var !== undefined);
  const pid = p ? p.id : (prefill ? prefill.id : '');
  const label = p ? p.label : (prefill ? prefill.label : '');
  const known = api.get('/api/providers/available')
    .then(c => (c.providers || []).map(x => ({ id: x.id, name: x.name })))
    .catch(() => api.get('/api/providers/known').then(ids => ids.map(id => ({ id }))).catch(() => []));
  Promise.all([known, currentProject ? Promise.resolve([]) : api.get('/api/projects').catch(() => [])]).then(([list, projects]) => {
    const projSel = currentProject
      ? ''
      : formSelect('Проект', 'project_id', projects.map(x => ({ v: x.id, l: x.name })), isEdit ? p.project_id : (projects[0] || {}).id);
    const body = `
      ${isEdit ? '' : formInput('ID провайдера', 'id', pid, 'deepseek', 'text', 'list="provider-ids"')}
      <datalist id="provider-ids">${list.map(x => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('')}</datalist>
      ${formInput('Название (необязательно)', 'label', label)}
      ${projSel}
      ${formInput('API-ключ', 'api_key', '', isEdit ? 'оставьте пустым, чтобы не менять' : 'sk-…', 'password')}
      <label class="flex items-center gap-2 text-sm mb-3">
        <input type="checkbox" name="enabled" ${isEdit ? (p.enabled ? 'checked' : '') : 'checked'} class="accent-sky-600">
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

/* ---------- files ---------- */
let filesPrefix = '';

function fmtSize(n) {
  if (n == null) return '';
  if (n < 1024) return `${n} Б`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} КБ`;
  return `${(n / 1024 / 1024).toFixed(1)} МБ`;
}

function filesBreadcrumbs() {
  const el = $('#files-breadcrumbs');
  if (!el) return;
  const parts = filesPrefix ? filesPrefix.split('/') : [];
  const crumbs = [`<button class="crumb px-1.5 py-0.5 rounded hover:bg-neutral-800 mono text-xs ${parts.length === 0 ? 'text-sky-300' : 'text-neutral-400'}" data-path="">Файлы</button>`];
  let acc = '';
  parts.forEach((p, i) => {
    acc = acc ? `${acc}/${p}` : p;
    crumbs.push('<span class="text-neutral-600 text-xs">/</span>');
    crumbs.push(`<button class="crumb px-1.5 py-0.5 rounded hover:bg-neutral-800 mono text-xs ${i === parts.length - 1 ? 'text-sky-300' : 'text-neutral-400'}" data-path="${esc(acc)}">${esc(p)}</button>`);
  });
  el.innerHTML = crumbs.join('');
  $$('.crumb', el).forEach(b => b.onclick = () => { filesPrefix = b.dataset.path || ''; syncFilesFolderInput(); refreshFiles(); });
}

function syncFilesFolderInput() {
  const input = $('#files-folder');
  if (input) input.value = filesPrefix;
}

async function renderFiles() {
  const main = $('#main');
  main.innerHTML = `
    <div class="h-full flex flex-col">
      <div class="flex items-center justify-between px-6 py-4 border-b border-neutral-800">
        <div>
          <div class="text-lg font-semibold">Файлы проекта</div>
          <div class="text-xs text-neutral-500">Хранилище MinIO: файлы и скриншоты проекта. Скриншоты загружает агент через MCP «files».</div>
        </div>
        <div class="flex items-center gap-2">
          <input id="files-folder" placeholder="папка (необязательно)" class="w-40 bg-neutral-800 border border-neutral-700 rounded-lg px-2.5 py-1.5 text-xs focus:outline-none focus:border-sky-600">
          <button id="files-upload" class="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium">Загрузить</button>
        </div>
      </div>
      <div class="flex-1 overflow-y-auto p-6">
        <div id="files-breadcrumbs" class="flex items-center flex-wrap gap-0.5 mb-3"></div>
        <div id="files-list"></div>
      </div>
    </div>`;
  $('#files-upload').onclick = () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.onchange = () => {
      if (input.files[0]) uploadProjectFile(input.files[0], $('#files-folder').value);
    };
    input.click();
  };
  syncFilesFolderInput();
  filesBreadcrumbs();
  await refreshFiles();
}

async function uploadProjectFile(file, folder) {
  if (!currentProject) return toast('Сначала выберите проект', 'error');
  const fd = new FormData();
  fd.append('file', file);
  if ((folder || '').trim()) fd.append('folder', folder.trim());
  try {
    const r = await fetch(`/api/files?project_id=${currentProject}`, { method: 'POST', body: fd });
    if (r.status === 401) { location.href = '/login'; throw new Error('Не авторизован'); }
    if (!r.ok) {
      let detail = r.statusText;
      try { detail = (await r.json()).detail || detail; } catch {}
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    toast(`Файл ${file.name} загружен`, 'ok');
    await refreshFiles();
  } catch (e) {
    toast(e.message, 'error');
  }
}

function closeFilePreview() {
  const r = $('#file-preview-root');
  if (r) r.remove();
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeFilePreview();
});

function openFilePreview(f) {
  const abs = new URL(f.url, window.location.origin).toString();
  const isImage = (f.content_type || '').startsWith('image/');
  const isHtml = (f.content_type || '').includes('text/html') || /\.(html?|xhtml?|htm)$/i.test(f.name);
  closeFilePreview();
  const root = document.createElement('div');
  root.id = 'file-preview-root';
  root.innerHTML = `
    <div class="fixed inset-0 bg-black/70 z-50 flex items-center justify-center p-6" id="file-preview-overlay">
      <div class="w-full max-w-4xl bg-neutral-900 border border-neutral-800 rounded-xl shadow-2xl flex flex-col max-h-[90vh]">
        <div class="flex items-center justify-between px-4 py-2.5 border-b border-neutral-800 gap-3">
          <div class="mono text-sm text-sky-300 truncate">${esc(f.name)}</div>
          <div class="flex items-center gap-2 shrink-0">
            <span class="text-xs text-neutral-500">${f.size ? fmtSize(f.size) : ''}</span>
            <a href="${esc(abs)}" target="_blank" rel="noopener" class="px-2.5 py-1 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-xs">открыть в новой вкладке</a>
            <button class="pv-copy px-2.5 py-1 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-xs">копировать ссылку</button>
            <button class="pv-dl px-2.5 py-1 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-xs">скачать</button>
            <button id="file-preview-close" class="text-neutral-500 hover:text-neutral-200 text-xl leading-none px-1">&times;</button>
          </div>
        </div>
        <div class="flex-1 overflow-auto p-4 flex items-center justify-center">
          ${isImage
            ? `<img src="${esc(abs)}" class="max-w-full max-h-[75vh] object-contain rounded-lg">`
            : isHtml
              ? `<iframe src="${esc(abs)}" sandbox="allow-scripts allow-forms allow-modals allow-popups" class="w-full h-[75vh] bg-white rounded-lg border border-neutral-800"></iframe>`
              : `<div class="text-center py-10">
                   <div class="text-4xl mb-3">📄</div>
                   <div class="text-sm text-neutral-400">Предпросмотр недоступен для этого типа файла</div>
                   <div class="text-xs text-neutral-600 mt-1">${esc(f.content_type || '')}</div>
                 </div>`}
        </div>
      </div>
    </div>`;
  document.body.appendChild(root);
  $('#file-preview-overlay').onclick = (e) => { if (e.target.id === 'file-preview-overlay') closeFilePreview(); };
  $('#file-preview-close').onclick = closeFilePreview;
  $('.pv-copy', root).onclick = async () => {
    try {
      await navigator.clipboard.writeText(abs);
      toast('Ссылка скопирована', 'ok');
    } catch {
      toast('Не удалось скопировать', 'error');
    }
  };
  $('.pv-dl', root).onclick = () => {
    const a = document.createElement('a');
    a.href = abs + (abs.includes('?') ? '&' : '?') + 'download=1';
    a.download = f.name.split('/').pop();
    a.click();
  };
}

async function refreshFiles() {
  const el = $('#files-list');
  if (!el) return;
  if (!currentProject) {
    el.innerHTML = `<div class="text-neutral-500 text-sm">Выберите проект в левом верхнем углу — файлы хранятся отдельно для каждого проекта.</div>`;
    return;
  }
  let data;
  try { data = await api.get(`/api/files?project_id=${currentProject}&prefix=${encodeURIComponent(filesPrefix)}`); } catch (e) { return; }
  if (!data.storage_ok) {
    el.innerHTML = `
      <div class="rounded-xl border border-yellow-900 p-5 mb-4">
        <div class="font-semibold text-yellow-400">Хранилище MinIO недоступно</div>
        <div class="text-xs text-neutral-500 mt-1">Поднимите MinIO: <span class="mono">docker compose up -d minio</span> (или задайте VIBEPROD_S3_* и перезапустите брокер), затем обновите страницу.</div>
      </div>`;
    return;
  }
  const files = data.files || [];
  const folders = data.folders || [];
  if (!files.length && !folders.length) {
    el.innerHTML = `<div class="text-neutral-500 text-sm">${filesPrefix ? `Папка «${esc(filesPrefix)}» пуста.` : 'Файлов пока нет. Загрузите первый — или подключите агенту MCP «files» и скилл «screenshot-to-files», чтобы агент складывал сюда скриншоты.'}</div>`;
    return;
  }
  const folderHtml = folders.length ? `<div class="text-[10px] uppercase tracking-wider text-neutral-600 px-2 mb-1">Папки</div>` + folders.map(d => `
    <button class="folder w-full flex items-center gap-2 px-2 py-1.5 rounded hover:bg-neutral-800/60 text-left" data-path="${esc(d.path)}">
      <span class="text-xs w-4 text-center shrink-0">📁</span>
      <span class="mono text-xs text-sky-300 truncate flex-1">${esc(d.name)}</span>
      <span class="text-neutral-600 text-[10px] shrink-0">▸</span>
    </button>`).join('') : '';
  const fileHtml = files.length ? (folders.length ? `<div class="text-[10px] uppercase tracking-wider text-neutral-600 px-2 mb-1 mt-2">Файлы</div>` : '') + files.map(f => {
    const isImage = (f.content_type || '').startsWith('image/');
    const abs = new URL(f.url, window.location.origin).toString();
    return `
      <div class="flex items-center gap-2 px-2 py-1 rounded hover:bg-neutral-800/60 group">
        ${isImage
          ? `<button class="preview shrink-0 cursor-pointer" data-name="${esc(f.name)}"><img src="${esc(abs)}" class="w-5 h-5 object-cover rounded border border-neutral-700" loading="lazy"></button>`
          : `<button class="preview w-5 h-5 shrink-0 cursor-pointer rounded border border-neutral-700 bg-neutral-900 flex items-center justify-center text-neutral-500 text-[7px] mono" data-name="${esc(f.name)}">${esc((f.name.split('.').pop() || 'file').slice(0, 4))}</button>`}
        <button class="preview text-left cursor-pointer min-w-0 flex-1" data-name="${esc(f.name)}" title="${esc(f.name)}">
          <span class="mono text-xs text-sky-300 truncate hover:underline block">${esc(f.name)}</span>
        </button>
        <span class="text-[10px] text-neutral-500 shrink-0" title="${f.last_modified ? esc(f.last_modified) : ''}">${fmtSize(f.size)}</span>
        <button class="preview msg-actions text-neutral-500 hover:text-neutral-200 text-xs leading-none px-1 shrink-0" data-name="${esc(f.name)}" title="Открыть">⤢</button>
        <button class="copy msg-actions text-neutral-500 hover:text-neutral-200 text-xs leading-none px-1 shrink-0" data-url="${esc(abs)}" title="Скопировать ссылку">⧉</button>
        <button class="del msg-actions text-neutral-500 hover:text-red-400 text-xs leading-none px-1 shrink-0" data-name="${esc(f.name)}" title="Удалить">✕</button>
      </div>`;
  }).join('') : '';
  el.innerHTML = folderHtml + fileHtml;
  $$('.folder', el).forEach(b => b.onclick = () => {
    filesPrefix = b.dataset.path || '';
    syncFilesFolderInput();
    filesBreadcrumbs();
    refreshFiles();
  });
  $$('.preview', el).forEach(b => b.onclick = () => {
    const row = files.find(f => f.name === b.dataset.name);
    if (row) openFilePreview(row);
  });
  $$('.copy', el).forEach(b => b.onclick = async () => {
    try {
      await navigator.clipboard.writeText(b.dataset.url);
      toast('Ссылка скопирована', 'ok');
    } catch {
      toast('Не удалось скопировать', 'error');
    }
  });
  $$('.del', el).forEach(b => b.onclick = async () => {
    if (!confirm(`Удалить ${b.dataset.name}?`)) return;
    try {
      await api.del(`/api/files?project_id=${currentProject}&path=${encodeURIComponent(b.dataset.name)}`);
      toast('Файл удалён', 'ok');
      await refreshFiles();
    } catch (e) {
      toast(e.message, 'error');
    }
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
          <button id="auto-tab-out" class="auto-tab px-3 py-1.5 rounded-lg ${tab === 'outwebhooks' ? 'bg-neutral-800' : 'hover:bg-neutral-800/60'}">Исходящие</button>
          <button id="auto-tab-schedules" class="auto-tab px-3 py-1.5 rounded-lg ${tab === 'schedules' ? 'bg-neutral-800' : 'hover:bg-neutral-800/60'}">Расписания</button>
        </div>
      </div>
      <div class="flex-1 overflow-y-auto p-6" id="automation-content"></div>
    </div>`;
  $('#auto-tab-webhooks').onclick = () => showView('webhooks');
  $('#auto-tab-out').onclick = () => showView('outwebhooks');
  $('#auto-tab-schedules').onclick = () => showView('schedules');
  const content = $('#automation-content');
  if (tab === 'schedules') {
    content.innerHTML = schedulesTabHtml();
    bindNewSched();
    await refreshSchedules();
  } else if (tab === 'outwebhooks') {
    await refreshOutWebhooks(content);
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

function parseCronToSimple(cron) {
  // Обратная к buildCronFromSimple: cron → значения полей простого режима.
  const s = { freq: 'day', interval: '30', minute: '0', time: '09:00', dom: '1', dow: [], ok: true };
  const parts = String(cron || '').trim().split(/\s+/);
  if (parts.length !== 5) { s.ok = false; return s; }
  const [min, hour, dom, mon, dow] = parts;
  const pad = (n) => String(n).padStart(2, '0');
  const m = min.match(/^\*\/(\d+)$/);
  if (m && hour === '*' && dom === '*' && mon === '*' && dow === '*') {
    s.freq = 'minute'; s.interval = m[1]; return s;
  }
  if (/^\d+$/.test(min) && hour === '*' && dom === '*' && mon === '*' && dow === '*') {
    s.freq = 'hour'; s.minute = min; return s;
  }
  if (/^\d+$/.test(min) && /^\d+$/.test(hour) && dom === '*' && mon === '*' && dow === '*') {
    s.freq = 'day'; s.time = `${pad(hour)}:${pad(min)}`; return s;
  }
  if (/^\d+$/.test(min) && /^\d+$/.test(hour) && dom === '*' && mon === '*' && dow !== '*') {
    s.freq = 'week'; s.time = `${pad(hour)}:${pad(min)}`;
    s.dow = dow.split(',').filter(d => DOW_RU[d]);
    return s;
  }
  if (/^\d+$/.test(min) && /^\d+$/.test(hour) && /^\d+$/.test(dom) && mon === '*' && dow === '*') {
    s.freq = 'month'; s.time = `${pad(hour)}:${pad(min)}`; s.dom = dom; return s;
  }
  s.ok = false;
  return s;
}

function schedModal(agents, s) {
  s = s || { agent_id: agents[0].id, title: '', prompt: '', cron: '0 9 * * *', timezone: 'Europe/Moscow', enabled: true };
  const simple = parseCronToSimple(s.cron);
  const dowCheckboxes = Object.entries(DOW_RU).map(([v, l]) => `
    <label class="flex items-center gap-1.5 text-xs">
      <input type="checkbox" name="sched-dow" value="${v}" ${simple.dow.includes(v) ? 'checked' : ''} class="accent-sky-600">
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
      ], simple.freq)}
      <div data-freq="minute" class="hidden mb-3">
        ${formInput('Интервал, минут', 'sched-interval', simple.interval, '30')}
      </div>
      <div data-freq="hour" class="hidden mb-3">
        ${formInput('Минута часа (0–59)', 'sched-minute', simple.minute, '0')}
      </div>
      <div data-freq="day" class="hidden mb-3">
        ${formInput('Время', 'sched-time', simple.time, '', 'time')}
      </div>
      <div data-freq="week" class="hidden mb-3">
        <div class="text-xs text-neutral-400 mb-1.5">Дни недели</div>
        <div class="flex flex-wrap gap-3 mb-3">${dowCheckboxes}</div>
        ${formInput('Время', 'sched-time', simple.time, '', 'time')}
      </div>
      <div data-freq="month" class="hidden mb-3">
        <div class="grid grid-cols-2 gap-3">
          ${formInput('День месяца (1–31)', 'sched-dom', simple.dom, '1')}
          ${formInput('Время', 'sched-time', simple.time, '', 'time')}
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
  $('#mode-simple', mbody).onclick = () => {
    const cronVal = $('[name="cron"]', $('#mode-cron-pane', mbody)).value.trim();
    const si = parseCronToSimple(cronVal);
    if (si.ok) {
      $('[name="sched-freq"]', mbody).value = si.freq;
      $('[name="sched-interval"]', mbody).value = si.interval;
      $('[name="sched-minute"]', mbody).value = si.minute;
      $$('[name="sched-time"]', mbody).forEach(el => { el.value = si.time; });
      $('[name="sched-dom"]', mbody).value = si.dom;
      $$('[name="sched-dow"]', mbody).forEach(cb => { cb.checked = si.dow.includes(cb.value); });
    }
    setMode(true);
    updateFields();
  };
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
  // Распознанный cron открываем в простом режиме с его значениями,
  // нестандартный — в CRON-режиме, чтобы не затирать его дефолтами.
  setMode(simple.ok);
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

/* ---------- out webhooks ---------- */
const OUT_EVENT_LABELS = {
  'session.created': 'сессия создана',
  'session.started': 'сессия запущена',
  'session.completed': 'сессия завершена',
  'session.failed': 'сессия упала',
  'session.expired': 'сессия истекла (TTL)',
  'schedule.fired': 'сработало расписание',
  'webhook.received': 'получен входящий webhook',
  'webhook.test': 'тест',
};

const DELIVERY_META = {
  pending: { label: 'в очереди', cls: 'bg-neutral-700' },
  retrying: { label: 'ретрай', cls: 'bg-yellow-700 animate-pulse' },
  success: { label: 'доставлено', cls: 'bg-emerald-700' },
  failed: { label: 'ошибка', cls: 'bg-red-700' },
};
function deliveryBadge(status) {
  const m = DELIVERY_META[status] || DELIVERY_META.pending;
  return `<span class="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium ${m.cls}">${m.label}</span>`;
}

async function refreshOutWebhooks(content) {
  let data;
  try { data = await api.get('/api/out-webhooks' + projQuery()); } catch (e) { return; }
  content.innerHTML = `
    <div class="flex items-center justify-between mb-4">
      <div class="text-xs text-neutral-500 max-w-lg">Исходящий webhook — брокер сам шлёт POST-уведомления о событиях (запуск и завершение сессий, срабатывание расписаний) на ваш URL. Тело: {"event", "timestamp", "data"}. При заданном секрете каждый запрос подписан: X-Vibeprod-Signature (HMAC-SHA256).</div>
      <button id="new-out" class="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium shrink-0 ml-4">Новый webhook</button>
    </div>
    <div id="out-list"></div>`;
  $('#new-out').onclick = () => outWebhookModal();
  const list = $('#out-list');
  if (!data.length) {
    list.innerHTML = `<div class="text-neutral-500 text-sm">Исходящих webhook-ов нет. Создайте — и внешние системы будут получать события брокера.</div>`;
    return;
  }
  list.innerHTML = data.map(w => `
    <div class="rounded-xl border border-neutral-800 hover:border-neutral-700 p-5 mb-3">
      <div class="flex items-start justify-between">
        <div class="min-w-0">
          <div class="font-semibold flex items-center gap-2 flex-wrap">
            <span>${esc(w.name || w.url)}</span>
            ${w.enabled ? '<span class="text-xs px-2 py-0.5 rounded bg-emerald-900/50">вкл</span>' : '<span class="text-xs px-2 py-0.5 rounded bg-neutral-800 text-neutral-500">выкл</span>'}
            ${w.has_secret ? '<span class="text-xs px-2 py-0.5 rounded bg-yellow-900/50">с подписью</span>' : ''}
          </div>
          <div class="mono text-xs text-neutral-500 mt-1 break-all">${esc(w.url)}</div>
        </div>
        <div class="flex gap-2 text-xs shrink-0 ml-3">
          <button class="test px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800" data-id="${w.id}">тест</button>
          <button class="log px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800" data-id="${w.id}">журнал</button>
          <button class="edit px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800" data-id="${w.id}">изменить</button>
          <button class="del px-3 py-1.5 rounded-lg border border-red-900 text-red-400 hover:bg-red-950" data-id="${w.id}">удалить</button>
        </div>
      </div>
      <div class="flex flex-wrap gap-2 mt-3 text-xs">
        ${(w.events || []).map(e => `<span class="px-2 py-1 rounded bg-neutral-800">${esc(OUT_EVENT_LABELS[e] || e)}</span>`).join('')}
        ${w.last_delivery_at ? `<span class="px-2 py-1 rounded bg-neutral-800">доставка: ${esc(w.last_delivery_at)}</span>` : ''}
        ${w.last_delivery_status ? deliveryBadge(w.last_delivery_status) : ''}
      </div>
    </div>`).join('');
  $$('.test', list).forEach(b => b.onclick = async () => {
    try {
      const r = await api.post(`/api/out-webhooks/${b.dataset.id}/test`);
      toast(r.ok ? `Доставлено (HTTP ${r.delivery.http_status})` : `Ошибка доставки: ${r.delivery.error || 'HTTP ' + r.delivery.http_status}`, r.ok ? 'ok' : 'error');
    } catch (e) { toast(e.message, 'error'); }
    await refreshOutWebhooks($('#automation-content'));
  });
  $$('.log', list).forEach(b => b.onclick = () => {
    const row = data.find(w => w.id === +b.dataset.id);
    outDeliveriesModal(row);
  });
  $$('.edit', list).forEach(b => b.onclick = () => {
    const row = data.find(w => w.id === +b.dataset.id);
    outWebhookModal(row);
  });
  $$('.del', list).forEach(b => b.onclick = async () => {
    if (!confirm('Удалить исходящий webhook?')) return;
    await api.del(`/api/out-webhooks/${b.dataset.id}`);
    await refreshOutWebhooks($('#automation-content'));
  });
}

async function outWebhookModal(w) {
  const isEdit = !!w;
  w = w || { name: '', url: '', events: ['session.completed', 'session.failed'], secret: '', enabled: true };
  const projects = currentProject ? [] : await api.get('/api/projects').catch(() => []);
  const projSel = currentProject
    ? ''
    : formSelect('Проект', 'project_id', projects.map(x => ({ v: x.id, l: x.name })), isEdit ? w.project_id : (projects[0] || {}).id);
  const eventBoxes = Object.entries(OUT_EVENT_LABELS).filter(([v]) => v !== 'webhook.test').map(([v, l]) => `
    <label class="flex items-center gap-1.5 text-xs">
      <input type="checkbox" class="evcb accent-sky-600" data-ev="${v}" ${(w.events || []).includes(v) ? 'checked' : ''}>
      <span class="text-neutral-400">${l}</span>
    </label>`).join('');
  openModal(isEdit ? `Исходящий webhook: ${w.name || w.url}` : 'Новый исходящий webhook', `
    ${formInput('Название (необязательно)', 'name', w.name, 'Мой сервис')}
    ${formInput('URL', 'url', w.url, 'https://example.com/hooks/vibeprod')}
    ${projSel}
    <div class="text-xs text-neutral-400 mb-2">События:</div>
    <div class="grid grid-cols-2 gap-1.5 mb-3">${eventBoxes}</div>
    <div class="grid grid-cols-2 gap-3">
      ${formInput('Секрет подписи (необязательно)', 'secret', '', isEdit ? 'оставьте пустым, чтобы не менять' : '', 'password')}
      <label class="flex items-center gap-2 text-sm mb-3 mt-5">
        <input type="checkbox" name="enabled" ${w.enabled ? 'checked' : ''} class="accent-sky-600">
        <span class="text-neutral-400 text-xs">включено</span>
      </label>
    </div>
    ${isEdit ? `<div class="text-xs text-neutral-500 mb-2">Секрет: ${w.has_secret ? 'задан' : 'нет'}</div>` : ''}`,
    async (close) => {
      const f = readForm($('#modal-body'));
      if (!f.url.trim()) throw new Error('URL обязателен');
      f.events = $$('.evcb', $('#modal-body')).filter(c => c.checked).map(c => c.dataset.ev);
      if (!f.events.length) throw new Error('Выберите хотя бы одно событие');
      f.enabled = $('[name="enabled"]', $('#modal-body')).checked;
      if (currentProject) f.project_id = currentProject;
      if (isEdit) await api.put(`/api/out-webhooks/${w.id}`, f);
      else await api.post('/api/out-webhooks', f);
      close();
      const content = $('#automation-content');
      if (content) await refreshOutWebhooks(content);
    });
}

async function outDeliveriesModal(w) {
  const root = $('#modal-root');
  root.innerHTML = `
    <div class="fixed inset-0 bg-black/60 z-40 flex items-center justify-center p-4" id="modal-overlay">
      <div class="bg-neutral-900 border border-neutral-800 rounded-xl w-full max-w-3xl max-h-[90vh] flex flex-col shadow-2xl">
        <div class="flex items-center justify-between px-5 py-3 border-b border-neutral-800">
          <div class="font-semibold truncate">Журнал доставок: ${esc(w.name || w.url)}</div>
          <button id="modal-close" class="text-neutral-500 hover:text-neutral-200 text-xl leading-none">&times;</button>
        </div>
        <div class="p-5 overflow-y-auto" id="deliveries-body"><div class="text-neutral-500 text-sm">загрузка…</div></div>
        <div class="px-5 py-3 border-t border-neutral-800 flex justify-end gap-2">
          <button id="deliveries-refresh" class="px-4 py-2 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-sm">Обновить</button>
          <button id="deliveries-close" class="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium">Закрыть</button>
        </div>
      </div>
    </div>`;
  const close = () => root.innerHTML = '';
  $('#deliveries-close').onclick = close;
  $('#modal-close').onclick = close;
  $('#modal-overlay').onclick = (e) => { if (e.target.id === 'modal-overlay') close(); };
  const load = async () => {
    let rows;
    try { rows = await api.get(`/api/out-webhooks/${w.id}/deliveries`); } catch (e) { toast(e.message, 'error'); return; }
    const body = $('#deliveries-body');
    if (!body) return;
    if (!rows.length) { body.innerHTML = '<div class="text-neutral-500 text-sm">Доставок ещё не было.</div>'; return; }
    body.innerHTML = rows.map(d => `
      <div class="rounded-lg border border-neutral-800 p-3 mb-2">
        <div class="flex items-center justify-between gap-2 flex-wrap">
          <div class="text-sm flex items-center gap-2 flex-wrap">
            <span class="mono text-sky-300 text-xs">${esc(d.event)}</span>
            ${deliveryBadge(d.status)}
            ${d.http_status ? `<span class="text-xs text-neutral-500">HTTP ${d.http_status}</span>` : ''}
          </div>
          <div class="flex items-center gap-2 text-xs">
            <span class="text-neutral-500">${esc(d.started_at || '')}${d.attempts ? ` · попыток: ${d.attempts}` : ''}</span>
            ${d.status === 'failed' ? `<button class="retry px-2.5 py-1 rounded-lg border border-neutral-700 hover:bg-neutral-800" data-id="${d.id}">повторить</button>` : ''}
          </div>
        </div>
        ${d.error ? `<div class="text-xs text-red-400 mt-1 break-all">${esc(d.error)}</div>` : ''}
        ${d.payload && d.payload.data && d.payload.data.title ? `<div class="text-xs text-neutral-500 mt-1 truncate">${esc(String(d.payload.data.title))}</div>` : ''}
      </div>`).join('');
    $$('.retry', body).forEach(b => b.onclick = async () => {
      await api.post(`/api/out-webhooks/${w.id}/deliveries/${b.dataset.id}/retry`);
      toast('Повторная доставка запущена', 'ok');
      setTimeout(load, 1500);
    });
  };
  $('#deliveries-refresh').onclick = load;
  await load();
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
        <div class="text-sm text-neutral-400 mb-4">Токен бота выдаёт @BotFather (/newbot). Первое сообщение в чате запускает агента по умолчанию проекта, дальше переписка продолжает ту же сессию. Команды бота: /agents, /agent N, /new, /abort, /status, /link, /chatid.</div>
        ${formInput('Токен бота', 'token', '', cfg.has_token ? 'оставьте пустым, чтобы не менять' : '123456:ABC…', 'password')}
        ${cfg.has_token ? `<div class="text-xs text-neutral-500 mb-3">Токен задан (…${esc(cfg.token_tail || '')}).</div>` : ''}
        ${formInput('Разрешённые user id (необязательно, через запятую)', 'allowed_users', cfg.allowed_users || '', '111,222')}
        ${formInput('URL веб-интерфейса для команды /link (необязательно)', 'web_url', cfg.web_url || '', 'http://host:8000')}
        ${formInput('Чат для уведомлений о фоновых запусках (необязательно, id сообщит бот: /chatid)', 'notify_chat_id', cfg.notify_chat_id || '', '123456789')}
        ${formSelect('Уведомления о запусках по расписанию и вебхукам', 'notify_mode', [
          { v: 'all', l: 'Всегда' },
          { v: 'errors', l: 'Только при ошибке' },
        ], cfg.notify_mode || 'all')}
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

/* ---------- ssh ---------- */
async function renderSsh(arg) {
  if (arg) return renderSshServer(arg);
  const main = $('#main');
  main.innerHTML = `
    <div class="h-full flex flex-col">
      <div class="flex items-center justify-between px-6 py-4 border-b border-neutral-800">
        <div>
          <div class="text-lg font-semibold">SSH</div>
          <div class="text-xs text-neutral-500">Серверы с белым списком команд: агент выполняет только разрешённые команды и читает логи запусков (MCP «ssh» из каталога).</div>
        </div>
        <button id="ssh-new" class="px-4 py-2 rounded-lg bg-sky-600 hover:bg-sky-500 text-sm font-medium">＋ Сервер</button>
      </div>
      <div class="flex-1 overflow-y-auto p-6" id="ssh-list"></div>
    </div>`;
  $('#ssh-new').onclick = () => sshServerModal();
  await refreshSsh();
}

async function refreshSsh() {
  let data;
  try { data = await api.get('/api/ssh/servers' + projQuery()); } catch (e) { return; }
  const el = $('#ssh-list');
  if (!el) return;
  if (!data.length) {
    el.innerHTML = `<div class="text-neutral-500 text-sm">Серверов нет. Добавьте сервер, разрешите команды и нажмите «Проверить подключение» — после этого MCP «ssh» сможет выполнять их.</div>`;
    return;
  }
  el.innerHTML = data.map(s => `
    <div class="rounded-xl border border-neutral-800 hover:border-neutral-700 p-5 mb-3">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <div class="font-semibold flex items-center gap-2 flex-wrap">
            <span class="mono text-sky-300">${esc(s.name)}</span>
            <span class="mono text-xs text-neutral-400">${esc(s.username)}@${esc(s.host)}:${esc(s.port)}</span>
            ${s.enabled ? '' : '<span class="text-xs px-2 py-0.5 rounded bg-neutral-800 text-neutral-500">выключен</span>'}
            ${s.host_key_fingerprint
              ? `<span class="text-xs px-2 py-0.5 rounded bg-emerald-900/50" title="ключ хоста сохранён">ключ хоста ✓</span>`
              : '<span class="text-xs px-2 py-0.5 rounded bg-amber-900/50" title="ключ хоста не сохранён">хост не проверен</span>'}
            <span class="text-xs px-2 py-0.5 rounded bg-neutral-800">${s.has_key ? 'ключ' : 'пароль'}</span>
          </div>
          ${s.host_key_fingerprint ? `<div class="text-xs mono text-neutral-500 mt-1">${esc(s.host_key_fingerprint)}</div>` : ''}
          ${s.last_error ? `<div class="text-xs text-red-400 mt-1 truncate">${esc(s.last_error.slice(0, 200))}</div>` : ''}
        </div>
        <div class="flex gap-2 text-xs shrink-0 ml-3 flex-wrap justify-end">
          <button class="ssh-cmds px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800" data-id="${s.id}">команды</button>
          <button class="ssh-test px-3 py-1.5 rounded-lg border border-emerald-800 text-emerald-300 hover:bg-emerald-950" data-id="${s.id}">проверить</button>
          <button class="ssh-edit px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800" data-id="${s.id}">изменить</button>
          <button class="ssh-del px-3 py-1.5 rounded-lg border border-red-900 text-red-400 hover:bg-red-950" data-id="${s.id}">удалить</button>
        </div>
      </div>
    </div>`).join('');
  $$('.ssh-cmds', el).forEach(b => b.onclick = () => showView('ssh', b.dataset.id));
  $$('.ssh-edit', el).forEach(b => b.onclick = () => {
    const row = data.find(s => s.id === +b.dataset.id);
    sshServerModal(row);
  });
  $$('.ssh-del', el).forEach(b => b.onclick = async () => {
    if (!confirm('Удалить сервер и все его команды?')) return;
    try { await api.del(`/api/ssh/servers/${b.dataset.id}`); }
    catch (e) { return toast(e.message, 'error'); }
    toast('Удалено', 'ok');
    await refreshSsh();
  });
  $$('.ssh-test', el).forEach(b => b.onclick = async () => {
    b.disabled = true;
    try {
      const r = await api.post(`/api/ssh/servers/${b.dataset.id}/test`);
      toast(`Подключение успешно (${r.fingerprint})`, 'ok');
    } catch (e) {
      if (e.message.includes('ключ хоста изменился')) {
        if (confirm(`${e.message}\n\nПодтвердить замену ключа хоста?`)) {
          try {
            await api.post(`/api/ssh/servers/${b.dataset.id}/test`, { replace_host_key: true });
            toast('Ключ хоста обновлён', 'ok');
          } catch (e2) { toast(e2.message, 'error'); }
        }
      } else {
        toast(e.message, 'error');
      }
    }
    await refreshSsh();
  });
}

function sshServerModal(s) {
  const isEdit = !!s;
  s = s || { name: '', host: '', port: 22, username: '', auth_type: 'key', private_key: '', password: '', enabled: 1 };
  openModal(isEdit ? 'Изменить сервер SSH' : 'Добавить сервер SSH', `
    ${formInput('Имя (для агента)', 'name', s.name, 'prod-db')}
    <div class="grid grid-cols-3 gap-3">
      ${formInput('Хост', 'host', s.host, '192.168.1.10')}
      ${formInput('Порт', 'port', s.port, '22')}
      ${formInput('Пользователь', 'username', s.username, 'deploy')}
    </div>
    ${formSelect('Аутентификация', 'auth_type', [{ v: 'key', l: 'Приватный ключ (PEM)' }, { v: 'password', l: 'Пароль' }], s.auth_type)}
    ${formArea('Приватный ключ (PEM)', 'private_key', '', 5, isEdit ? 'оставьте пустым, чтобы не менять' : '-----BEGIN OPENSSH PRIVATE KEY-----')}
    ${formInput('Пароль', 'password', '', isEdit ? 'оставьте пустым, чтобы не менять' : '', 'password')}
    <label class="flex items-center gap-2 text-sm mb-3">
      <input type="checkbox" name="enabled" ${s.enabled ? 'checked' : ''} class="accent-sky-600">
      <span class="text-neutral-400 text-xs">включено</span>
    </label>
    <div class="text-xs text-neutral-500">После сохранения нажмите «Проверить подключение» — ключ хоста сохранится (TOFU), и агент сможет выполнять команды.</div>`,
    async (close) => {
      const f = readForm($('#modal-body'));
      f.enabled = $('[name="enabled"]', $('#modal-body')).checked;
      f.port = +f.port;
      try {
        if (isEdit) await api.put(`/api/ssh/servers/${s.id}`, f);
        else await api.post('/api/ssh/servers', f);
        close();
        toast('Сохранено', 'ok');
        await refreshSsh();
      } catch (e) { toast(e.message, 'error'); }
    });
}

async function renderSshServer(sid) {
  let servers;
  try { servers = await api.get('/api/ssh/servers' + projQuery()); } catch (e) { return; }
  const s = servers.find(x => String(x.id) === String(sid));
  if (!s) return showView('ssh');
  const main = $('#main');
  main.innerHTML = `
    <div class="h-full flex flex-col">
      <div class="px-6 py-4 border-b border-neutral-800 flex items-center gap-3">
        <button id="ssh-back" class="px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800 text-sm">← SSH</button>
        <div class="text-lg font-semibold">${esc(s.name)}</div>
        <span class="mono text-xs text-neutral-400">${esc(s.username)}@${esc(s.host)}:${esc(s.port)}</span>
        ${s.host_key_fingerprint
          ? `<span class="text-xs px-2 py-0.5 rounded bg-emerald-900/50">ключ хоста ✓</span>`
          : '<span class="text-xs px-2 py-0.5 rounded bg-amber-900/50">хост не проверен</span>'}
        ${s.last_error ? `<span class="text-xs text-red-400 truncate max-w-60">${esc(s.last_error.slice(0, 120))}</span>` : ''}
        <div class="ml-auto flex gap-2">
          <button id="ssh-srv-test" class="px-3 py-1.5 rounded-lg border border-emerald-800 text-emerald-300 hover:bg-emerald-950 text-sm">Проверить подключение</button>
        </div>
      </div>
      <div class="flex-1 overflow-y-auto p-6">
        <div class="flex items-center justify-between mb-3">
          <div class="font-semibold text-sm text-neutral-300">Разрешённые команды</div>
          <button id="ssh-cmd-new" class="px-3 py-1.5 rounded-lg bg-sky-600 hover:bg-sky-500 text-xs font-medium">＋ Команда</button>
        </div>
        <div id="ssh-cmd-list" class="mb-8"></div>
        <div class="font-semibold text-sm text-neutral-300 mb-3">Журнал запусков</div>
        <div id="ssh-runs"></div>
      </div>
    </div>`;
  $('#ssh-back').onclick = () => showView('ssh');
  $('#ssh-srv-test').onclick = async () => {
    const b = $('#ssh-srv-test');
    b.disabled = true;
    try {
      const r = await api.post(`/api/ssh/servers/${sid}/test`);
      toast(`Подключение успешно (${r.fingerprint})`, 'ok');
    } catch (e) {
      if (e.message.includes('ключ хоста изменился')) {
        if (confirm(`${e.message}\n\nПодтвердить замену ключа хоста?`)) {
          try {
            await api.post(`/api/ssh/servers/${sid}/test`, { replace_host_key: true });
            toast('Ключ хоста обновлён', 'ok');
          } catch (e2) { toast(e2.message, 'error'); }
        }
      } else {
        toast(e.message, 'error');
      }
    }
    renderSshServer(sid);
  };
  $('#ssh-cmd-new').onclick = () => sshCommandModal(sid);
  await refreshSshCommands(sid);
  await refreshSshRuns(sid);
}

async function refreshSshCommands(sid) {
  let data;
  try { data = await api.get(`/api/ssh/commands?server_id=${sid}`); } catch (e) { return; }
  const el = $('#ssh-cmd-list');
  if (!el) return;
  if (!data.length) {
    el.innerHTML = `<div class="text-neutral-500 text-sm mb-3">Команд нет. Добавьте шаблон, например: journalctl -u {service} -n {lines} — с regex-валидацией параметров.</div>`;
    return;
  }
  el.innerHTML = data.map(c => `
    <div class="rounded-xl border border-neutral-800 p-4 mb-2">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <div class="font-semibold flex items-center gap-2 flex-wrap">
            <span class="mono text-sky-300">${esc(c.name)}</span>
            ${c.enabled ? '' : '<span class="text-xs px-2 py-0.5 rounded bg-neutral-800 text-neutral-500">выключена</span>'}
            <span class="text-xs px-2 py-0.5 rounded bg-neutral-800">timeout ${esc(c.timeout)}с</span>
          </div>
          ${c.description ? `<div class="text-xs text-neutral-500 mt-1">${esc(c.description)}</div>` : ''}
          <div class="text-xs mono text-neutral-400 mt-1 break-all bg-neutral-900 rounded px-2 py-1">${esc(c.command)}</div>
          ${c.arg_regex ? `<div class="text-xs mono text-neutral-500 mt-1 break-all">regex: ${esc(c.arg_regex)}</div>` : ''}
        </div>
        <div class="flex gap-2 text-xs shrink-0">
          <button class="ssh-cmd-edit px-3 py-1.5 rounded-lg border border-neutral-700 hover:bg-neutral-800" data-id="${c.id}">изменить</button>
          <button class="ssh-cmd-del px-3 py-1.5 rounded-lg border border-red-900 text-red-400 hover:bg-red-950" data-id="${c.id}">удалить</button>
        </div>
      </div>
    </div>`).join('');
  $$('.ssh-cmd-edit', el).forEach(b => b.onclick = () => {
    const row = data.find(c => c.id === +b.dataset.id);
    sshCommandModal(sid, row);
  });
  $$('.ssh-cmd-del', el).forEach(b => b.onclick = async () => {
    if (!confirm('Удалить команду?')) return;
    try { await api.del(`/api/ssh/commands/${b.dataset.id}`); }
    catch (e) { return toast(e.message, 'error'); }
    await refreshSshCommands(sid);
  });
}

function sshCommandModal(sid, c) {
  const isEdit = !!c;
  c = c || { name: '', description: '', command: '', arg_regex: '', timeout: 60, enabled: 1 };
  openModal(isEdit ? 'Изменить команду' : 'Добавить команду', `
    ${formInput('Имя (для агента)', 'name', c.name, 'logs')}
    ${formInput('Описание', 'description', c.description, 'Журнал сервиса')}
    ${formInput('Команда (шаблон с {параметрами})', 'command', c.command, 'journalctl -u {service} -n {lines} --no-pager')}
    ${formInput('Regex параметров (JSON)', 'arg_regex', c.arg_regex, '{"service": "^[a-z0-9-]{1,40}$", "lines": "^[1-9][0-9]{0,3}$"}')}
    ${formInput('Таймаут, сек', 'timeout', c.timeout, '60')}
    <label class="flex items-center gap-2 text-sm mb-3">
      <input type="checkbox" name="enabled" ${c.enabled ? 'checked' : ''} class="accent-sky-600">
      <span class="text-neutral-400 text-xs">включено</span>
    </label>
    <div class="text-xs text-neutral-500">Параметры без regex валидируются строгим набором символов (без пробелов). Каждый параметр экранируется — shell-инъекции невозможны.</div>`,
    async (close) => {
      const f = readForm($('#modal-body'));
      f.enabled = $('[name="enabled"]', $('#modal-body')).checked;
      f.timeout = +f.timeout;
      f.server_id = +sid;
      try {
        if (isEdit) await api.put(`/api/ssh/commands/${c.id}`, f);
        else await api.post('/api/ssh/commands', f);
        close();
        toast('Сохранено', 'ok');
        await refreshSshCommands(sid);
      } catch (e) { toast(e.message, 'error'); }
    });
}

async function refreshSshRuns(sid) {
  let data;
  try { data = await api.get(`/api/ssh/runs?project_id=${currentProject}&server_id=${sid}&limit=30`); } catch (e) { return; }
  const el = $('#ssh-runs');
  if (!el) return;
  if (!data.length) {
    el.innerHTML = `<div class="text-neutral-500 text-sm">Запусков пока нет.</div>`;
    return;
  }
  el.innerHTML = data.map(r => `
    <details class="compact-card rounded-xl border border-neutral-800 p-4 mb-2">
      <summary>
        <span class="chev text-neutral-500 text-xs">▸</span>
        <span class="mono text-xs text-sky-300">${esc(r.command_name || '—')}</span>
        <span class="text-xs ${r.status === 'ok' ? 'text-emerald-400' : 'text-red-400'}">${r.status === 'ok' ? 'ok' : 'ошибка'}</span>
        ${r.exit_code !== null && r.exit_code !== undefined ? `<span class="text-xs text-neutral-500">exit ${esc(r.exit_code)}</span>` : ''}
        <span class="text-xs text-neutral-500">${esc((r.duration_ms ?? '') + ' мс')}</span>
        <span class="text-xs text-neutral-500 ml-auto">${esc(r.started_at)}</span>
      </summary>
      <pre class="mono text-xs text-neutral-300 bg-neutral-900 rounded p-3 mt-2 overflow-x-auto max-h-80 overflow-y-auto">${esc(r.output || '')}</pre>
    </details>`).join('');
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
      ${formInput('URL (streamable HTTP, напр. http://vibeprod-playwright:8931/mcp)', 'url', c.url, 'http://…')}
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
        localStorage.setItem('vibeprod-project', String(created.id));
      }
      close();
      await loadProjectMenu();
      await refreshProjects();
      if (opts.selectAfterCreate) await refreshCurrentView();
    });
}

/* ---------- boot ---------- */
(async function boot() {
  try {
    const st = await fetch('/api/auth').then(r => r.json());
    if (st.enabled) $('#logout-btn').classList.remove('hidden');
  } catch {}
  const lb = $('#logout-btn');
  if (lb) lb.onclick = async () => {
    try { await api.post('/api/logout'); } catch {}
    location.href = '/login';
  };
  await loadProjectMenu();
  try {
    const h = await fetch('/api/sessions').then(r => r.json());
    $('#server-info').textContent = `сессий: ${h.length}`;
  } catch {}
  const [view, arg] = (location.hash.slice(1) || 'home').split('/');
  showView(view, arg);
})();
