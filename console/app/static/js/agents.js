const $ = (id) => document.getElementById(id);

const state = {
  me: null,
  agents: [],
  cartridges: [],
  toolCatalog: {},
  selectedId: null,
  draft: null,
  testChatHistory: [],
  testStreamCtl: null,
  currentTestUserMsg: '',
  currentTestReply: '',
};

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

function csrfHeaders(base = {}) {
  const token = csrfToken();
  return token ? { ...base, 'X-CSRF-Token': token } : base;
}

async function readError(response) {
  try {
    const data = await response.json();
    return data.detail || data.error || `HTTP ${response.status}`;
  } catch (_) {
    try {
      return (await response.text()).slice(0, 240) || `HTTP ${response.status}`;
    } catch {
      return `HTTP ${response.status}`;
    }
  }
}

async function fetchJson(url, options = {}) {
  const method = String(options.method || 'GET').toUpperCase();
  const headers = { Accept: 'application/json', ...(options.headers || {}) };
  const init = { credentials: 'same-origin', ...options, method, headers };

  if (init.body && typeof init.body !== 'string' && !(init.body instanceof FormData)) {
    init.headers['Content-Type'] = init.headers['Content-Type'] || 'application/json';
    init.body = JSON.stringify(init.body);
  }
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    Object.assign(init.headers, csrfHeaders());
  }

  const response = await fetch(url, init);
  if (response.redirected && new URL(response.url).pathname === '/login') {
    throw new Error('UNAUTHENTICATED');
  }
  if (response.status === 401) throw new Error('UNAUTHENTICATED');
  if (response.status === 403) throw new Error('FORBIDDEN');
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

function showAdminOnly(kind = 'FORBIDDEN') {
  const layout = document.querySelector('.layout');
  if (!layout) return;
  const login = kind === 'UNAUTHENTICATED';
  layout.innerHTML = `
    <div style="padding:60px;color:var(--red);font-family:var(--font-mono);letter-spacing:1px">
      ${login ? 'Sesion requerida. Vuelve a iniciar sesion.' : 'Acceso solo para admin.'}
      <div style="margin-top:14px">
        <a class="btn" href="${login ? '/login' : '/'}">${login ? 'IR A LOGIN' : 'VOLVER'}</a>
      </div>
    </div>
  `;
}

async function loadMe() {
  try {
    const data = await fetchJson('/auth/me');
    state.me = data.user || null;
  } catch (_) {
    state.me = null;
  }
  renderUserBar();
}

function renderUserBar() {
  const bar = $('user-bar');
  if (!bar) return;
  bar.replaceChildren();

  if (!state.me) {
    const login = document.createElement('a');
    login.href = '/login';
    login.textContent = 'Iniciar sesión';
    bar.appendChild(login);
    return;
  }

  const email = document.createElement('span');
  const strong = document.createElement('strong');
  strong.textContent = state.me.email || '';
  email.appendChild(strong);

  const role = document.createElement('span');
  role.textContent = String(state.me.role || '').toUpperCase();
  role.style.padding = '1px 6px';
  role.style.border = `1px solid ${state.me.role === 'admin' ? 'var(--amber)' : 'var(--border)'}`;
  role.style.color = state.me.role === 'admin' ? 'var(--amber)' : 'var(--text3)';
  role.style.borderRadius = '2px';

  const logout = document.createElement('a');
  logout.href = '#';
  logout.textContent = 'Salir';
  logout.addEventListener('click', async (event) => {
    event.preventDefault();
    try {
      await fetch('/auth/logout', {
        method: 'POST',
        credentials: 'same-origin',
        headers: csrfHeaders(),
      });
    } finally {
      location.href = '/login';
    }
  });

  bar.append(email, role, logout);
}

async function loadAgents() {
  const data = await fetchJson('/api/agents?include_inactive=true');
  state.agents = data.agents || [];
  renderList();
}

function addCartridgeId(ids, entry) {
  if (!entry) return;
  if (typeof entry === 'string') {
    ids.add(entry);
  } else if (entry.id) {
    ids.add(entry.id);
  } else if (entry.cartridge_id) {
    ids.add(entry.cartridge_id);
  }
}

async function loadCartridges() {
  const ids = new Set();
  state.agents.forEach((agent) => addCartridgeId(ids, agent.cartridge_id));

  const sources = await Promise.allSettled([
    fetchJson('/studio/cartridges'),
    fetchJson('/api/cartridges'),
  ]);

  for (const result of sources) {
    if (result.status !== 'fulfilled') continue;
    const rows = result.value.cartridges || [];
    rows.forEach((row) => addCartridgeId(ids, row));
  }

  ids.add('replicon');
  if (state.draft?.cartridge_id) ids.add(state.draft.cartridge_id);
  state.cartridges = [...ids].sort();
  renderCartridgeOptions();
}

function renderCartridgeOptions() {
  const select = $('f-cartridge');
  if (!select) return;
  const current = select.value || state.draft?.cartridge_id || '';
  select.innerHTML = state.cartridges
    .map((id) => `<option value="${escapeHtml(id)}">${escapeHtml(id)}</option>`)
    .join('');
  if (current && state.cartridges.includes(current)) select.value = current;
}

async function loadToolCatalog() {
  try {
    const data = await fetchJson('/api/agents/_tool-catalog');
    state.toolCatalog = data.servers || {};
  } catch (_) {
    state.toolCatalog = {};
  }
  if (state.draft) renderTools();
}

function renderList() {
  const wrap = $('agent-list');
  if (!wrap) return;
  if (!state.agents.length) {
    wrap.innerHTML = '<div style="padding:40px;text-align:center;color:var(--color-text-secondary);font-size:14px">Sin agentes. Crea el primero.</div>';
    return;
  }

  const byCart = {};
  for (const agent of state.agents) {
    (byCart[agent.cartridge_id] ||= []).push(agent);
  }

  const parts = [];
  for (const cart of Object.keys(byCart).sort()) {
    parts.push(`<div class="cart-group">${escapeHtml(cart)}</div><ul class="agent-list">`);
    for (const agent of byCart[cart]) {
      const selected = agent.id === state.selectedId ? 'selected' : '';
      parts.push(`
        <li class="agent-item ${selected}" data-agent-id="${escapeHtml(agent.id)}">
          <div class="agent-row">
            <span class="agent-name">${escapeHtml(agent.name)}</span>
            <span class="agent-slug">${escapeHtml(agent.slug)}</span>
          </div>
          <div class="agent-desc">${escapeHtml(agent.description || '')}</div>
          <div class="agent-meta">
            <span class="badge b-model">${escapeHtml(agent.model || '')}</span>
            ${agent.is_active ? '' : '<span class="badge b-inactive">Inactivo</span>'}
          </div>
        </li>
      `);
    }
    parts.push('</ul>');
  }
  wrap.innerHTML = parts.join('');
}

function bindTabs() {
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((node) => node.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach((node) => node.classList.remove('active'));
      tab.classList.add('active');
      document.querySelector(`.tab-panel[data-panel="${tab.dataset.tab}"]`)?.classList.add('active');
      if (tab.dataset.tab === 'runs') loadRuns();
    });
  });
}

function selectAgent(id) {
  const agent = state.agents.find((item) => item.id === id);
  if (!agent) return;
  state.selectedId = id;
  state.draft = JSON.parse(JSON.stringify(agent));
  hydrateEditor();
  renderList();
  $('btn-test').disabled = false;
  $('btn-del').style.display = '';
  if (isRunsTabActive()) loadRuns();
}

function newAgent() {
  state.selectedId = '_new_';
  state.draft = {
    cartridge_id: state.cartridges[0] || 'replicon',
    slug: '',
    name: '',
    description: '',
    instructions: '',
    personality: '',
    allowed_tools: [],
    rag_filter: {},
    model: 'claude-sonnet-4-6',
    max_tokens: 8192,
    temperature: 0.4,
    extra: {},
    is_active: true,
  };
  renderCartridgeOptions();
  hydrateEditor();
  renderList();
  $('btn-test').disabled = true;
  $('btn-del').style.display = 'none';
  clearRuns('Guarda el agente para ver ejecuciones.');
}

function hydrateEditor() {
  $('editor-empty').style.display = 'none';
  $('editor').style.display = '';
  $('footer-bar').style.display = '';
  $('editor-title').textContent = state.selectedId === '_new_' ? 'Nuevo agente' : `Editar · ${state.draft.name}`;

  renderCartridgeOptions();
  $('f-cartridge').value = state.draft.cartridge_id || state.cartridges[0] || 'replicon';
  $('f-slug').value = state.draft.slug || '';
  $('f-name').value = state.draft.name || '';
  $('f-description').value = state.draft.description || '';
  $('f-active').checked = !!state.draft.is_active;

  $('f-instructions').value = state.draft.instructions || '';
  $('f-personality').value = state.draft.personality || '';

  $('f-model').value = state.draft.model || 'claude-sonnet-4-6';
  $('f-maxtok').value = state.draft.max_tokens || 8192;
  $('f-temp').value = state.draft.temperature != null ? state.draft.temperature : 0.4;

  const kinds = state.draft.rag_filter?.kinds || [];
  document.querySelectorAll('.rag-kind').forEach((checkbox) => {
    checkbox.checked = kinds.includes(checkbox.value);
  });

  renderTools();
  renderVars();

  const schedule = state.draft.extra?.schedule || {};
  $('f-cron').value = schedule.cron || '';
  $('f-tz').value = schedule.tz || '';
  $('f-schedule-prompt').value = schedule.prompt || '';
  $('f-schedule-enabled').checked = schedule.enabled !== false;

  $('save-status').textContent = '';
  $('run-detail').hidden = true;
}

function renderTools() {
  const grid = $('tools-grid');
  if (!grid || !state.draft) return;
  const checked = new Set(state.draft.allowed_tools || []);
  const parts = [];

  for (const server of Object.keys(state.toolCatalog).sort()) {
    parts.push(`<div class="tool-server">${escapeHtml(server)}</div>`);
    for (const tool of state.toolCatalog[server] || []) {
      const full = `${server}__${tool.name}`;
      const id = `tool-${full.replace(/[^\w]/g, '_')}`;
      const isChecked = checked.has(full) ? 'checked' : '';
      const description = String(tool.description || '').slice(0, 200);
      parts.push(`
        <div class="tool-item">
          <input type="checkbox" id="${escapeHtml(id)}" data-tool="${escapeHtml(full)}" ${isChecked}>
          <label for="${escapeHtml(id)}">
            <div>${escapeHtml(tool.name)}</div>
            <div class="tdesc">${escapeHtml(description)}</div>
          </label>
        </div>
      `);
    }
  }

  grid.innerHTML = parts.join('') || '<div style="padding:20px;color:var(--color-text-secondary);font-size:13px">Sin tools disponibles.</div>';
}

function onToolToggle(input) {
  if (!state.draft) return;
  const tool = input.dataset.tool;
  const set = new Set(state.draft.allowed_tools || []);
  if (input.checked) set.add(tool);
  else set.delete(tool);
  state.draft.allowed_tools = [...set];
}

function ensureVariables() {
  state.draft.extra = state.draft.extra || {};
  state.draft.extra.variables = state.draft.extra.variables || {};
  return state.draft.extra.variables;
}

function renderVars() {
  const list = $('vars-list');
  if (!list || !state.draft) return;
  const entries = Object.entries(state.draft.extra?.variables || {});
  list.innerHTML = entries.map(([key, value], index) => `
    <div class="kv-row">
      <input type="text" value="${escapeHtml(key)}" data-i="${index}" data-f="k" placeholder="clave">
      <input type="text" value="${escapeHtml(value)}" data-i="${index}" data-f="v" placeholder="valor">
      <button class="kv-rm" type="button" data-remove-var="${index}">×</button>
    </div>
  `).join('');
}

function addVar() {
  if (!state.draft) return;
  const vars = ensureVariables();
  let key = 'nueva_var';
  let index = 1;
  while (key in vars) key = `nueva_var_${index++}`;
  vars[key] = '';
  renderVars();
}

function removeVar(index) {
  if (!state.draft) return;
  const vars = ensureVariables();
  const keys = Object.keys(vars);
  delete vars[keys[index]];
  renderVars();
}

function onVarChange(input) {
  if (!state.draft) return;
  const index = parseInt(input.dataset.i || '0', 10);
  const field = input.dataset.f;
  const vars = ensureVariables();
  const keys = Object.keys(vars);
  const oldKey = keys[index];
  if (!oldKey) return;

  if (field === 'k') {
    const newKey = input.value;
    if (newKey === oldKey) return;
    const ordered = {};
    keys.forEach((key) => {
      ordered[key === oldKey ? newKey : key] = vars[key];
    });
    state.draft.extra.variables = ordered;
  } else {
    vars[oldKey] = input.value;
  }
}

function collectDraft() {
  state.draft.cartridge_id = $('f-cartridge').value;
  state.draft.slug = $('f-slug').value.trim();
  state.draft.name = $('f-name').value.trim();
  state.draft.description = $('f-description').value.trim();
  state.draft.is_active = $('f-active').checked;
  state.draft.instructions = $('f-instructions').value;
  state.draft.personality = $('f-personality').value;
  state.draft.model = $('f-model').value;
  state.draft.max_tokens = parseInt($('f-maxtok').value, 10) || 8192;
  state.draft.temperature = parseFloat($('f-temp').value);

  const kinds = [...document.querySelectorAll('.rag-kind:checked')].map((checkbox) => checkbox.value);
  state.draft.rag_filter = kinds.length ? { kinds } : {};

  const cron = $('f-cron').value.trim();
  const tz = $('f-tz').value.trim();
  const prompt = $('f-schedule-prompt').value.trim();
  const enabled = $('f-schedule-enabled').checked;
  state.draft.extra = state.draft.extra || {};

  if (cron || tz || prompt || !enabled) {
    state.draft.extra.schedule = {};
    if (cron) state.draft.extra.schedule.cron = cron;
    if (tz) state.draft.extra.schedule.tz = tz;
    if (prompt) state.draft.extra.schedule.prompt = prompt;
    if (!enabled) state.draft.extra.schedule.enabled = false;
  } else {
    delete state.draft.extra.schedule;
  }

  return state.draft;
}

async function saveAgent() {
  const payload = collectDraft();
  if (!payload.slug || !payload.name || !payload.instructions) {
    setStatus('Slug, nombre e instrucciones son obligatorios', true);
    return;
  }

  setStatus('Guardando...');
  $('btn-save').disabled = true;
  try {
    const url = state.selectedId === '_new_' ? '/api/agents' : `/api/agents/${encodeURIComponent(state.selectedId)}`;
    const method = state.selectedId === '_new_' ? 'POST' : 'PATCH';
    const saved = await fetchJson(url, { method, body: payload });
    state.selectedId = saved.id;
    await loadAgents();
    await loadCartridges();
    const agent = state.agents.find((item) => item.id === state.selectedId);
    if (agent) {
      state.draft = JSON.parse(JSON.stringify(agent));
      hydrateEditor();
    }
    setStatus('Guardado ' + new Date().toLocaleTimeString());
    if (isRunsTabActive()) loadRuns();
  } catch (error) {
    setStatus('Error: ' + error.message, true);
  } finally {
    $('btn-save').disabled = false;
  }
}

function resetEditor() {
  state.selectedId = null;
  state.draft = null;
  $('editor').style.display = 'none';
  $('editor-empty').style.display = '';
  $('footer-bar').style.display = 'none';
  $('editor-title').textContent = 'Editor';
  $('btn-test').disabled = true;
  clearRuns('Selecciona un agente guardado para ver ejecuciones.');
  renderList();
}

function cancelEdit() {
  if (state.selectedId === '_new_') {
    resetEditor();
    return;
  }
  const agent = state.agents.find((item) => item.id === state.selectedId);
  if (agent) {
    state.draft = JSON.parse(JSON.stringify(agent));
    hydrateEditor();
  }
}

async function deleteAgent() {
  if (state.selectedId === '_new_' || !state.selectedId || !state.draft) return;
  if (!confirm(`Eliminar agente "${state.draft.name}"? Esta accion no se puede deshacer.`)) return;
  try {
    await fetchJson(`/api/agents/${encodeURIComponent(state.selectedId)}`, { method: 'DELETE' });
    resetEditor();
    await loadAgents();
    await loadCartridges();
    setStatus('');
  } catch (error) {
    alert('Error al eliminar: ' + error.message);
  }
}

function setStatus(message, isError = false) {
  const el = $('save-status');
  if (!el) return;
  el.textContent = message;
  el.style.color = isError ? 'var(--red)' : 'var(--text3)';
}

function isRunsTabActive() {
  return document.querySelector('.tab.active')?.dataset.tab === 'runs';
}

function clearRuns(message) {
  $('runs-status').textContent = message;
  $('runs-table').hidden = true;
  $('runs-body').replaceChildren();
  $('run-detail').hidden = true;
}

function fmtDate(value) {
  if (!value) return '—';
  try {
    return new Date(value).toLocaleString();
  } catch {
    return String(value);
  }
}

async function loadRuns() {
  if (!state.selectedId || state.selectedId === '_new_') {
    clearRuns('Guarda el agente para ver ejecuciones.');
    return;
  }

  $('runs-status').textContent = 'Cargando ejecuciones...';
  $('run-detail').hidden = true;
  try {
    const data = await fetchJson(`/api/agents/${encodeURIComponent(state.selectedId)}/runs?limit=30`);
    const runs = data.runs || [];
    if (!runs.length) {
      clearRuns('Sin ejecuciones registradas.');
      return;
    }
    $('runs-table').hidden = false;
    $('runs-status').textContent = `${runs.length} ejecucion(es) recientes.`;
    $('runs-body').innerHTML = runs.map((run) => `
      <tr>
        <td><a href="#" class="run-link" data-run-id="${escapeHtml(run.id)}">#${escapeHtml(run.id)}</a></td>
        <td>${escapeHtml(run.status || '')}</td>
        <td>${escapeHtml(fmtDate(run.started_at))}</td>
        <td>${escapeHtml(fmtDate(run.finished_at))}</td>
        <td>${escapeHtml(run.n_tool_calls ?? 0)}</td>
        <td>${escapeHtml(run.out_chars ?? 0)} chars</td>
      </tr>
    `).join('');
  } catch (error) {
    clearRuns('Error cargando ejecuciones: ' + error.message);
  }
}

async function openRunDetail(runId) {
  const detail = $('run-detail');
  detail.hidden = false;
  detail.innerHTML = '<div class="runs-status">Cargando detalle...</div>';
  try {
    const run = await fetchJson(`/api/agent-runs/${encodeURIComponent(runId)}`);
    detail.innerHTML = `
      <div class="pane-title">RUN #${escapeHtml(run.id)} · ${escapeHtml(run.status || '')}</div>
      <pre>${escapeHtml(JSON.stringify({
        started_at: run.started_at,
        finished_at: run.finished_at,
        input_messages: run.input_messages,
        output_text: run.output_text,
        tool_calls: run.tool_calls,
        error_message: run.error_message,
      }, null, 2))}</pre>
    `;
  } catch (error) {
    detail.innerHTML = `<div class="runs-status" style="color:var(--red)">Error: ${escapeHtml(error.message)}</div>`;
  }
}

function openTestChat() {
  if (!state.selectedId || state.selectedId === '_new_' || !state.draft) return;
  state.testChatHistory = [];
  state.currentTestUserMsg = '';
  state.currentTestReply = '';

  const backdrop = document.createElement('div');
  backdrop.className = 'testchat-backdrop';
  backdrop.id = 'testchat';
  backdrop.innerHTML = `
    <div class="testchat">
      <div class="testchat-head">
        <span class="name">Probando · ${escapeHtml(state.draft.name)}</span>
        <span style="font-size:12px;color:var(--color-text-secondary)">${escapeHtml(state.draft.model)}</span>
        <button class="close" type="button" id="tc-close">×</button>
      </div>
      <div class="testchat-body" id="tc-body"></div>
      <div class="testchat-foot">
        <input id="tc-input" placeholder="Escribe un mensaje...">
        <button class="btn btn-primary" type="button" id="tc-send">ENVIAR</button>
      </div>
    </div>
  `;
  document.body.appendChild(backdrop);
  backdrop.addEventListener('click', (event) => {
    if (event.target === backdrop) closeTestChat();
  });
  $('tc-close').addEventListener('click', closeTestChat);
  $('tc-send').addEventListener('click', sendTest);
  $('tc-input').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') sendTest();
  });
  $('tc-input').focus();
}

function closeTestChat() {
  if (state.testStreamCtl) {
    try { state.testStreamCtl.abort(); } catch (_) {}
  }
  $('testchat')?.remove();
}

async function sendTest() {
  const input = $('tc-input');
  const msg = input.value.trim();
  if (!msg) return;

  state.currentTestUserMsg = msg;
  state.currentTestReply = '';
  input.value = '';
  const body = $('tc-body');
  body.insertAdjacentHTML('beforeend', `
    <div class="msg user">
      <div class="role">USUARIO</div>
      <div class="body">${escapeHtml(msg)}</div>
    </div>
  `);
  body.scrollTop = body.scrollHeight;

  const agentMsg = document.createElement('div');
  agentMsg.className = 'msg agent';
  agentMsg.innerHTML = '<div class="role">AGENTE</div><div class="body"><span style="color:var(--text3)">(pensando...)</span></div>';
  body.appendChild(agentMsg);
  const agentBody = agentMsg.querySelector('.body');

  state.testStreamCtl = new AbortController();
  try {
    const response = await fetch(`/api/agents/${encodeURIComponent(state.selectedId)}/invoke/stream`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: csrfHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ message: msg, history: state.testChatHistory }),
      signal: state.testStreamCtl.signal,
    });
    if (!response.ok) throw new Error(await readError(response));
    if (!response.body) throw new Error('stream no disponible');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n\n');
      buffer = lines.pop();
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const event = JSON.parse(line.slice(6));
        handleStreamEvent(event, agentBody, body);
      }
    }
  } catch (error) {
    if (error.name !== 'AbortError') {
      agentBody.innerHTML = '<span style="color:var(--red)">Error: ' + escapeHtml(error.message) + '</span>';
    }
  } finally {
    state.testStreamCtl = null;
    if (isRunsTabActive()) loadRuns();
  }
}

function handleStreamEvent(event, agentBody, body) {
  if (event.type === 'tool_use') {
    const item = document.createElement('div');
    item.className = 'msg tool';
    item.textContent = `→ ${event.tool} (${event.server})`;
    body.insertBefore(item, agentBody.parentElement);
    body.scrollTop = body.scrollHeight;
  } else if (event.type === 'tool_result') {
    const item = document.createElement('div');
    item.className = 'msg tool';
    item.style.color = 'var(--green)';
    item.textContent = `   ← ${event.summary || 'ok'}`;
    body.insertBefore(item, agentBody.parentElement);
  } else if (event.type === 'text') {
    state.currentTestReply = event.text || '';
    agentBody.textContent = state.currentTestReply || '(sin respuesta)';
    body.scrollTop = body.scrollHeight;
  } else if (event.type === 'done') {
    state.testChatHistory.push({ role: 'user', content: state.currentTestUserMsg });
    if (state.currentTestReply) {
      state.testChatHistory.push({ role: 'assistant', content: state.currentTestReply });
    }
  } else if (event.type === 'error') {
    agentBody.innerHTML = '<span style="color:var(--red)">Error: ' + escapeHtml(event.message || '') + '</span>';
  }
}

function bindStaticHandlers() {
  $('btn-refresh-agents').addEventListener('click', async () => {
    await loadAgents();
    await loadCartridges();
  });
  $('btn-new-agent').addEventListener('click', newAgent);
  $('btn-test').addEventListener('click', openTestChat);
  $('btn-add-var').addEventListener('click', addVar);
  $('btn-del').addEventListener('click', deleteAgent);
  $('btn-cancel').addEventListener('click', cancelEdit);
  $('btn-save').addEventListener('click', saveAgent);
  $('btn-refresh-runs').addEventListener('click', loadRuns);

  $('agent-list').addEventListener('click', (event) => {
    const item = event.target.closest('[data-agent-id]');
    if (item) selectAgent(item.dataset.agentId);
  });

  $('tools-grid').addEventListener('change', (event) => {
    if (event.target.matches('input[type="checkbox"][data-tool]')) {
      onToolToggle(event.target);
    }
  });

  $('vars-list').addEventListener('input', (event) => {
    if (event.target.matches('input[data-i][data-f]')) onVarChange(event.target);
  });
  $('vars-list').addEventListener('click', (event) => {
    const button = event.target.closest('[data-remove-var]');
    if (button) removeVar(parseInt(button.dataset.removeVar || '0', 10));
  });

  $('runs-body').addEventListener('click', (event) => {
    const link = event.target.closest('[data-run-id]');
    if (!link) return;
    event.preventDefault();
    openRunDetail(link.dataset.runId);
  });
}

async function init() {
  bindStaticHandlers();
  bindTabs();
  await loadMe();
  if (!state.me) {
    showAdminOnly('UNAUTHENTICATED');
    return;
  }
  if (state.me.role !== 'admin') {
    showAdminOnly('FORBIDDEN');
    return;
  }

  try {
    await loadAgents();
    await Promise.all([loadCartridges(), loadToolCatalog()]);
  } catch (error) {
    if (error.message === 'UNAUTHENTICATED' || error.message === 'FORBIDDEN') {
      showAdminOnly(error.message);
      return;
    }
    const wrap = $('agent-list');
    if (wrap) {
      wrap.innerHTML = `<div style="padding:40px;color:var(--red);font-family:var(--font-mono)">Error cargando agentes: ${escapeHtml(error.message)}</div>`;
    }
  }
}

init();
