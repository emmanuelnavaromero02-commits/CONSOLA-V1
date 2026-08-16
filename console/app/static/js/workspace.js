// Sprint v1.24 (audit B6): extracted from workspace/app/static/workspace.html
// inline <script> block so the workspace shell can ship under a strict
// script-src 'self' CSP. The 31 onclick= handlers in the HTML now bind
// via addEventListener / data-action delegation at the bottom of this file.
//
// Public functions (referenced from data-action handlers) intentionally
// stay in the module top-level scope so they're addressable by name.

function escHtml(s){ return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
function fmt(s){ return s ? String(s).slice(0,16).replace('T',' ') : '—'; }
function csrfToken(){
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}
function csrfHeaders(base = {}){
  const token = csrfToken();
  return token ? { ...base, 'X-CSRF-Token': token } : base;
}
function jsonHeaders(base = {}){
  return csrfHeaders({ 'Content-Type': 'application/json', ...base });
}
function fetchWithTimeout(url, init = {}, timeoutMs = 30000) {
  if (timeoutMs === 0) return fetch(url, init);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, { ...init, signal: init.signal || controller.signal })
    .catch(err => {
      if (err && err.name === 'AbortError') throw new Error('La consulta tardó demasiado');
      throw err;
    })
    .finally(() => clearTimeout(timer));
}

// ── User bar ───────────────────────────────────────────────────────
let ME = null;
async function loadMe() {
  try { const r = await fetch('/auth/me'); const d = await r.json(); ME = d.user; }
  catch { ME = null; }
  const bar = document.getElementById('user-bar');
  if (!ME) { bar.innerHTML = '<a href="/">Recargar</a>'; return; }
  bar.innerHTML = `
    <span class="me"><strong>${escHtml(ME.email)}</strong></span>
    <span class="role-pill ${ME.role==='admin'?'admin':''}">${escHtml(ME.role)}</span>
    <a href="${CONSOLE_URL}/me">Mi perfil</a>
    <a href="#" data-action="logout">Salir</a>`;
}
async function doLogout(){
  await fetch('/auth/logout', { method:'POST', headers: csrfHeaders() });
  location.href = (CONSOLE_URL || '') + '/login';
}
let CONSOLE_URL = '';
async function loadConfig(){
  try { const r = await fetch('/api/config'); const d = await r.json(); CONSOLE_URL = d.console_url || ''; }
  catch { CONSOLE_URL = ''; }
}

// ── Apps ────────────────────────────────────────────────────────────
async function loadApps() {
  const cont = document.getElementById('apps-container');
  cont.innerHTML = '<div class="empty-state">Cargando…</div>';
  try {
    const r = await fetchWithTimeout('/api/apps');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    const apps = d.apps || [];
    document.getElementById('cnt-apps').textContent = apps.length;
    if (!apps.length) {
      cont.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">▦</div>
          <div>Aún no hay apps publicadas.</div>
          <div style="margin-top:8px;color:var(--text2)">Pídele al asistente que genere uno.</div>
        </div>`;
      return;
    }
    cont.innerHTML = `<div class="apps-grid">${apps.map(a => `
      <a class="app-card" href="/apps/${encodeURIComponent(a.name || '')}" target="_blank">
        <div class="app-icon">▦</div>
        <div class="app-title">${escHtml(a.title || a.name)}</div>
        <div class="app-desc">${escHtml(a.description || '—')}</div>
        <div class="app-meta">Actualizado: ${fmt(a.updated_at)}</div>
      </a>`).join('')}</div>`;
  } catch (e) {
    cont.innerHTML = `<div class="empty-state" style="color:var(--red)">Error: ${escHtml(e.message)}</div>`;
  }
}

// ── Chat ────────────────────────────────────────────────────────────
let HISTORY = [];

function onChatKey(ev){
  if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); sendMessage(); }
}
function askExample(btn){
  document.getElementById('chat-input').value = btn.textContent;
  sendMessage();
}
function resetChat(){
  HISTORY = [];
  const h = document.getElementById('chat-history');
  h.innerHTML = `
    <div class="chat-empty">
      <div class="icon">▣</div>
      <div>Nueva conversación.</div>
    </div>`;
}
function clearEmpty(){
  const e = document.getElementById('chat-empty');
  if (e) e.remove();
}

// Minimal markdown: links, bold, code, paragraphs
function renderMarkdown(text){
  const protectedHtml = [];
  const protect = (html) => {
    const token = `\u0000MD${protectedHtml.length}\u0000`;
    protectedHtml.push(html);
    return token;
  };
  // Code blocks first (greedy)
  let html = String(text ?? '').replace(/```([a-z]*)\n?([\s\S]*?)```/g,
    (_, lang, code) => protect(`<pre><code>${escHtml(code)}</code></pre>`));
  // Inline code
  html = html.replace(/`([^`]+)`/g, (_, c) => protect(`<code>${escHtml(c)}</code>`));
  // Links [text](url)
  html = html.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+|\/[^)]*)\)/g,
    (_, t, u) => protect(`<a href="${escHtml(u)}" target="_blank" rel="noopener">${escHtml(t)}</a>`));
  // Bold **text**
  html = html.replace(/\*\*([^*]+)\*\*/g, (_, t) => protect(`<strong>${escHtml(t)}</strong>`));
  // Escape everything that was not generated by the markdown renderer.
  html = escHtml(html);
  protectedHtml.forEach((value, index) => {
    html = html.replaceAll(escHtml(`\u0000MD${index}\u0000`), value);
  });
  // Paragraphs (split on double newline)
  const paras = html.split(/\n\n+/).map(p => `<p>${p.replace(/\n/g, '<br>')}</p>`).join('');
  return paras;
}

function appendMessage(role, text){
  clearEmpty();
  const div = document.createElement('div');
  div.className = `msg ${role}`;
  if (role === 'assistant') {
    div.innerHTML = renderMarkdown(text);
  } else {
    div.textContent = text;
  }
  const h = document.getElementById('chat-history');
  h.appendChild(div);
  h.scrollTop = h.scrollHeight;
  return div;
}

function appendDecisionAction(question, reply){
  const wrap = document.createElement('div');
  wrap.className = 'chat-actions';
  const btn = document.createElement('button');
  btn.className = 'chat-action-btn';
  btn.textContent = 'Convertir en decisión';
  btn.onclick = () => decisionFromChat(question, reply);
  wrap.appendChild(btn);
  const h = document.getElementById('chat-history');
  h.appendChild(wrap);
  h.scrollTop = h.scrollHeight;
}

async function decisionFromChat(question, reply){
  // Build a sane prefill: title from the question, description with both sides
  const cleanReply = reply.replace(/[*_`#>|-]/g, ' ').replace(/\s+/g, ' ').trim();
  const title = question.length > 80 ? question.slice(0, 77).trim() + '…' : question;
  const desc =
    '── Origen (asistente del workspace) ──\n' +
    'Pregunta: ' + question + '\n\n' +
    'Respuesta:\n' + (cleanReply.length > 600 ? cleanReply.slice(0, 600) + '…' : cleanReply);
  // Ensure user dropdown has data before opening the modal
  if (!USERS.length) await loadUsers();
  if (!DECISIONS_LOADED) { DECISIONS_LOADED = true; loadDecisions(); }
  openNewDecision({ title, description: desc });
}
window.decisionFromChat = decisionFromChat;

function makeTrail(){
  clearEmpty();
  const div = document.createElement('div');
  div.className = 'trail';
  const tk = document.createElement('div');
  tk.className = 'trail-row thinking';
  tk.innerHTML = 'Pensando<span class="dot">.</span><span class="dot">.</span><span class="dot">.</span>';
  div.appendChild(tk);
  const h = document.getElementById('chat-history');
  h.appendChild(div);
  h.scrollTop = h.scrollHeight;
  return { container: div, thinking: tk };
}

function trailUse(trail, evt){
  // Drop the "Pensando…" row once the first real step lands
  if (trail.thinking) { trail.thinking.remove(); trail.thinking = null; }
  const row = document.createElement('div');
  row.className = 'trail-row use';
  const argStr = formatArgs(evt.args);
  row.innerHTML = `<span class="trail-arrow">▸</span><span class="tool">${escHtml(evt.tool)}</span>${argStr ? `<span class="args">${escHtml(argStr)}</span>` : ''}`;
  trail.container.appendChild(row);
  const h = document.getElementById('chat-history');
  h.scrollTop = h.scrollHeight;
  return row;
}

function trailResult(trail, evt){
  const row = document.createElement('div');
  row.className = 'trail-row result';
  row.innerHTML = `<span class="trail-arrow">↳</span><span class="summary">${escHtml(evt.summary || 'ok')}</span>`;
  trail.container.appendChild(row);
  const h = document.getElementById('chat-history');
  h.scrollTop = h.scrollHeight;
  return row;
}

function trailDone(trail, totalSteps){
  // Add a small toggle to collapse the trail after the answer arrives
  if (totalSteps === 0) {
    // No tool calls → trail is just the (now-removed) thinking row; drop the empty container
    trail.container.remove();
    return;
  }
  const tog = document.createElement('button');
  tog.className = 'trail-toggle';
  tog.textContent = `▾ ocultar razonamiento (${totalSteps} pasos)`;
  let collapsed = false;
  tog.onclick = () => {
    collapsed = !collapsed;
    trail.container.classList.toggle('collapsed', collapsed);
    tog.textContent = collapsed
      ? `▸ ver razonamiento (${totalSteps} pasos)`
      : `▾ ocultar razonamiento (${totalSteps} pasos)`;
  };
  trail.container.parentNode.insertBefore(tog, trail.container.nextSibling);
}

function formatArgs(args){
  if (!args || typeof args !== 'object') return '';
  const keys = Object.keys(args);
  if (!keys.length) return '';
  const parts = keys.slice(0, 3).map(k => {
    let v = args[k];
    if (typeof v === 'object') v = JSON.stringify(v);
    v = String(v);
    if (v.length > 40) v = v.slice(0, 37) + '…';
    return `${k}=${v}`;
  });
  const more = keys.length > 3 ? ` (+${keys.length - 3})` : '';
  return parts.join(', ') + more;
}

async function sendMessage(){
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;
  const btn = document.getElementById('btn-send');
  input.value = '';
  btn.disabled = true;

  appendMessage('user', text);
  const trail = makeTrail();
  let toolSteps = 0;

  try {
    const r = await fetch('/workspace/chat/stream', {
      method: 'POST',
      headers: jsonHeaders(),
      body: JSON.stringify({ message: text, history: HISTORY }),
    });
    if (!r.ok) {
      trail.container.remove();
      const e = await r.json().catch(()=>({}));
      appendMessage('assistant', `*Error: ${escHtml(e.detail || r.statusText)}*`);
      return;
    }

    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let finalReply = '';
    let finalMessages = null;
    let errored = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split('\n\n');
      buffer = blocks.pop() || '';
      for (const block of blocks) {
        if (!block.trim()) continue;
        const lines = block.split('\n');
        let event = '', data = '';
        for (const line of lines) {
          if      (line.startsWith('event: ')) event = line.slice(7).trim();
          else if (line.startsWith('data: '))  data += line.slice(6);
        }
        if (!data) continue;
        let payload;
        try { payload = JSON.parse(data); } catch { continue; }
        if (event === 'tool_use')      { trailUse(trail, payload);    toolSteps += 1; }
        else if (event === 'tool_result') trailResult(trail, payload);
        else if (event === 'text')        { /* final text — keep, render on done */ }
        else if (event === 'done')        { finalReply = payload.reply || ''; finalMessages = payload.messages; }
        else if (event === 'error')       { errored = payload.message || 'unknown error'; }
      }
    }

    trailDone(trail, toolSteps);

    if (errored) {
      appendMessage('assistant', `*Error: ${escHtml(errored)}*`);
      return;
    }
    const replyText = finalReply || '_(sin respuesta)_';
    appendMessage('assistant', replyText);
    appendDecisionAction(text, replyText);

    if (Array.isArray(finalMessages)) HISTORY = finalMessages;
    else HISTORY = [...HISTORY,
                     { role:'user', content:text },
                     { role:'assistant', content: replyText }];

    if (replyText.includes('/apps/')) loadApps();
  } catch (e) {
    trail.container.remove();
    appendMessage('assistant', `*Error de red: ${escHtml(e.message)}*`);
  } finally {
    btn.disabled = false;
    input.focus();
  }
}

// ── Tabs (left pane) ────────────────────────────────────────────────
function switchTab(tab) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  document.querySelectorAll('.tab-page').forEach(p => p.classList.toggle('active', p.id === 'tab-' + tab));
  if (tab === 'decisions' && !DECISIONS_LOADED) {
    DECISIONS_LOADED = true;
    loadUsers();
    loadDecisions();
  }
}

// ── Decisions ───────────────────────────────────────────────────────
let DECISIONS_LOADED = false;
let DEC_FILTER = 'visible';
let USERS = [];

async function loadUsers() {
  try { const r = await fetch('/api/users'); const d = await r.json(); USERS = d.users || []; }
  catch { USERS = []; }
}
function userLabel(uid) {
  if (!uid) return null;
  const u = USERS.find(x => x.id === uid);
  return u ? (u.name || u.email) : `#${uid}`;
}
function daysUntil(d) {
  if (!d) return null;
  const a = new Date(d + 'T00:00:00');
  const b = new Date(); b.setHours(0,0,0,0);
  return Math.round((a - b) / 86400000);
}
function fmtDate(s){ return s ? String(s).slice(0,10) : '—'; }

function setDecFilter(f) {
  DEC_FILTER = f;
  document.querySelectorAll('.filter-pill').forEach(b => b.classList.toggle('active', b.dataset.filter === f));
  loadDecisions();
}

async function loadDecisions() {
  const cont = document.getElementById('decisions-container');
  cont.innerHTML = '<div class="empty-state">Cargando…</div>';
  const params = new URLSearchParams();
  if (DEC_FILTER === 'mine')    params.set('scope', 'mine');
  if (DEC_FILTER === 'open')    params.set('status', 'open');
  if (DEC_FILTER === 'overdue') params.set('overdue', 'true');
  try {
    const r = await fetch('/api/decisions?' + params.toString());
    const d = await r.json();
    const rows = d.decisions || [];
    document.getElementById('cnt-decisions').textContent = rows.length;
    if (!rows.length) {
      cont.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">▣</div>
          <div>Sin decisiones en este filtro.</div>
          <div style="margin-top:8px;color:var(--text2)">Pulsa "Nueva decisión" para registrar la primera.</div>
        </div>`;
      return;
    }
    cont.innerHTML = `<table class="dec-table">
      <thead><tr>
        <th>Título</th><th>Responsable</th><th>Visibilidad</th><th>Compromiso</th>
        <th>Estado</th><th>Resultado</th><th>Días</th>
      </tr></thead>
      <tbody>${rows.map(decRowHtml).join('')}</tbody>
    </table>`;
    cont.querySelectorAll('tbody tr').forEach(tr => {
      tr.addEventListener('click', () => openDecisionDetail(parseInt(tr.dataset.id, 10)));
    });
  } catch (e) {
    cont.innerHTML = `<div class="empty-state" style="color:var(--red)">Error: ${escHtml(e.message)}</div>`;
  }
}

function decRowHtml(r) {
  const days = daysUntil(r.commitment_date);
  const statusLabel = r.status === 'open' ? 'Abierta' : (r.status === 'closed' ? 'Cerrada' : r.status);
  let statusBadge = `<span class="badge b-${r.status}">${escHtml(statusLabel)}</span>`;
  let daysCell = '—';
  if (r.status === 'open' && days !== null) {
    if (days < 0)      { statusBadge = `<span class="badge b-overdue">Vencida</span>`; daysCell = `<span style="color:var(--red)">${days}d</span>`; }
    else if (days <= 7) daysCell = `<span style="color:var(--amber)">${days}d</span>`;
    else                daysCell = `<span style="color:var(--text2)">${days}d</span>`;
  }
  let outcomeCell = '—';
  if (r.outcome === 'achieved')         outcomeCell = `<span class="badge b-ok">Logrado</span>`;
  else if (r.outcome === 'not_achieved') outcomeCell = `<span class="badge b-fail">No logrado</span>`;
  const responsable = userLabel(r.assignee_id) || `<span style="color:var(--text3)">—</span>`;
  const visBadge = r.visibility === 'shared'
    ? '<span class="badge b-shared" title="Visible para todo el equipo">Equipo</span>'
    : '<span class="badge b-private" title="Solo creador, responsable y admins">Privada</span>';
  return `<tr data-id="${r.id}">
    <td><strong>${escHtml(r.title)}</strong></td>
    <td style="color:var(--text2)">${responsable}</td>
    <td>${visBadge}</td>
    <td>${fmtDate(r.commitment_date)}</td>
    <td>${statusBadge}</td>
    <td>${outcomeCell}</td>
    <td>${daysCell}</td>
  </tr>`;
}

// ── New decision ────────────────────────────────────────────────────
function userOpts(selectedId) {
  return ['<option value="">(sin asignar)</option>']
    .concat(USERS.map(u => `<option value="${u.id}" ${u.id===selectedId?'selected':''}>${escHtml(u.name||u.email)}</option>`))
    .join('');
}

function showModal(html) {
  const host = document.getElementById('modal-host');
  host.style.display = 'block';
  host.innerHTML = `<div class="modal-backdrop" data-action="modal-backdrop">${html}</div>`;
}
function closeModal(){ document.getElementById('modal-host').style.display = 'none'; document.getElementById('modal-host').innerHTML = ''; }
window.closeModal = closeModal;

function openNewDecision(prefill) {
  const meId = ME ? ME.id : null;
  const pTitle = (prefill && prefill.title) || '';
  const pDesc  = (prefill && prefill.description) || '';
  showModal(`
    <div class="modal">
      <div class="modal-head">
        <h3>Nueva decisión</h3>
        <button class="modal-close" data-action="modal-close">×</button>
      </div>
      <div class="modal-body">
        <div class="field"><label>Título</label><input id="nd-title" placeholder="Reducir overhead en proyecto X" value="${escHtml(pTitle)}"></div>
        <div class="field"><label>Descripción</label><textarea id="nd-desc" placeholder="Contexto, motivación, criterio de éxito…">${escHtml(pDesc)}</textarea></div>
        <div class="field-grid">
          <div class="field"><label>Fecha compromiso</label><input id="nd-commit" type="date"></div>
          <div class="field"><label>Responsable</label><select id="nd-assignee">${userOpts(meId)}</select></div>
          <div class="field"><label>Visibilidad</label>
            <select id="nd-vis">
              <option value="private">private — creador, responsable y admins</option>
              <option value="shared">shared — todo el equipo</option>
            </select>
          </div>
        </div>
        <div style="font-size:12px;color:var(--text2);font-family:var(--font-ui);letter-spacing:0">Los KPIs se editan después en el detalle.</div>
      </div>
      <div class="modal-foot">
        <button class="btn-ghost" data-action="modal-close">Cancelar</button>
        <button class="btn-pri" data-action="decision-submit">Crear</button>
      </div>
    </div>`);
  setTimeout(() => document.getElementById('nd-title').focus(), 50);
}
window.openNewDecision = openNewDecision;
window.submitNewDecision = async function() {
  const title = document.getElementById('nd-title').value.trim();
  if (!title) { alert('El título es obligatorio'); return; }
  const aRaw = document.getElementById('nd-assignee').value;
  const r = await fetch('/api/decisions', {
    method: 'POST', headers: jsonHeaders(),
    body: JSON.stringify({
      title,
      description: document.getElementById('nd-desc').value,
      commitment_date: document.getElementById('nd-commit').value || null,
      assignee_id: aRaw ? parseInt(aRaw, 10) : null,
      visibility: document.getElementById('nd-vis').value,
    })
  });
  if (!r.ok) {
    const e = await r.json().catch(()=>({}));
    alert('Error: ' + (e.detail || r.statusText));
    return;
  }
  const d = await r.json();
  closeModal();
  switchTab('decisions');
  loadDecisions();
  openDecisionDetail(d.id);
};

// ── Decision detail ────────────────────────────────────────────────
let CUR_DEC = null;

async function openDecisionDetail(id) {
  try {
    const r = await fetch('/api/decisions/' + id);
    if (!r.ok) throw new Error(r.statusText);
    CUR_DEC = await r.json();
  } catch(e) { alert('Error: ' + e.message); return; }
  renderDetailModal('overview');
}
window.openDecisionDetail = openDecisionDetail;

function renderDetailModal(tab) {
  const d = CUR_DEC;
  const owner = userLabel(d.created_by_id) || '—';
  showModal(`
    <div class="modal">
      <div class="modal-head">
        <h3>${escHtml(d.title)}</h3>
        <button class="modal-close" data-action="modal-close">×</button>
      </div>
      <div class="modal-body">
        <div style="font-size:12px;color:var(--text2);font-family:var(--font-ui);letter-spacing:0;margin-bottom:14px">
          creada por <strong style="color:var(--text2)">${escHtml(owner)}</strong> · ${escHtml((d.created_at||'').slice(0,16).replace('T',' '))}
          ${d.closed_at ? ' · cerrada ' + escHtml(d.closed_at.slice(0,10)) : ''}
        </div>
        <div class="sub-tabs">
          <div class="sub-tab ${tab==='overview'?'active':''}"  data-action="detail-tab" data-detail-tab="overview">Resumen</div>
          <div class="sub-tab ${tab==='kpis'?'active':''}"      data-action="detail-tab" data-detail-tab="kpis">KPIs (${(d.kpis||[]).length})</div>
          <div class="sub-tab ${tab==='actions'?'active':''}"   data-action="detail-tab" data-detail-tab="actions">Bitácora (${(d.actions||[]).length})</div>
        </div>
        <div id="detail-body">${
          tab==='overview' ? detailOverviewHtml(d) :
          tab==='kpis'     ? detailKpisHtml(d) :
                             detailActionsHtml(d)
        }</div>
      </div>
      <div class="modal-foot">
        ${tab==='overview' ? `<button class="btn-danger" data-action="decision-delete" data-id="${d.id}">Eliminar</button>` : ''}
        <div style="flex:1"></div>
        <button class="btn-ghost" data-action="modal-close">Cerrar</button>
        ${tab==='overview' ? `<button class="btn-pri" data-action="decision-save-overview" data-id="${d.id}">Guardar</button>` : ''}
        ${tab==='kpis'     ? `<button class="btn-pri" data-action="decision-save-kpis" data-id="${d.id}">Guardar KPIs</button>` : ''}
      </div>
    </div>`);
  if (tab === 'kpis') initKpiEditor();
}
window.renderDetailModal = renderDetailModal;

function detailOverviewHtml(d) {
  return `
    <div class="field"><label>Título</label><input id="ed-title" value="${escHtml(d.title)}"></div>
    <div class="field"><label>Descripción</label><textarea id="ed-desc">${escHtml(d.description)}</textarea></div>
    <div class="field-grid">
      <div class="field"><label>Fecha compromiso</label>
        <input id="ed-commit" type="date" value="${d.commitment_date ? d.commitment_date.slice(0,10) : ''}">
      </div>
      <div class="field"><label>Responsable</label><select id="ed-assignee">${userOpts(d.assignee_id)}</select></div>
      <div class="field"><label>Estado</label>
        <select id="ed-status">
          <option value="open"   ${d.status==='open'?'selected':''}>open</option>
          <option value="closed" ${d.status==='closed'?'selected':''}>closed</option>
        </select>
      </div>
      <div class="field"><label>Resultado</label>
        <select id="ed-outcome">
          <option value=""             ${!d.outcome?'selected':''}>(pendiente)</option>
          <option value="achieved"     ${d.outcome==='achieved'?'selected':''}>achieved · logrado</option>
          <option value="not_achieved" ${d.outcome==='not_achieved'?'selected':''}>not_achieved · no logrado</option>
        </select>
      </div>
      <div class="field"><label>Visibilidad</label>
        <select id="ed-vis">
          <option value="private" ${d.visibility==='private'?'selected':''}>private — creador, responsable, admins</option>
          <option value="shared"  ${d.visibility==='shared' ?'selected':''}>shared — todo el equipo</option>
        </select>
      </div>
    </div>`;
}

function detailActionsHtml(d) {
  const items = (d.actions || []).map(a => `
    <div class="action-card">
      <div class="action-meta"><span>${escHtml((a.ts||'').slice(0,16).replace('T',' '))}</span><span>${escHtml(a.actor)}</span></div>
      <div style="font-size:12px">${escHtml(a.action_text)}</div>
      ${a.note ? `<div style="font-size:11px;color:var(--text2);margin-top:5px">${escHtml(a.note)}</div>` : ''}
    </div>`).join('');
  return `
    <div class="field"><label>Nueva entrada</label><textarea id="ac-text" placeholder="Acción tomada o nota…"></textarea></div>
    <div style="margin-bottom:14px"><button class="btn-pri" data-action="decision-action-add" data-id="${d.id}">Añadir</button></div>
    <div class="actions-list">${items || '<div class="empty-state" style="padding:20px">Sin entradas</div>'}</div>`;
}

// ── KPI editor ──────────────────────────────────────────────────────
const KPI_OPS = ['>=', '>', '<=', '<', '=', '!='];
let DATASETS = null;            // [{name, layer}, ...]
const SCHEMAS = {};              // {dataset: [{name, type}, ...]}
let WORKING_KPIS = [];           // mutable list while editing

function detailKpisHtml(d) {
  WORKING_KPIS = JSON.parse(JSON.stringify(d.kpis || []));
  return `
    <div id="kpi-list"></div>
    <div style="margin-top:14px">
      <button class="btn-ghost" data-action="kpi-add">Añadir KPI</button>
    </div>
    <div style="font-size:12px;color:var(--text2);font-family:var(--font-ui);margin-top:14px;letter-spacing:0">
      Cada KPI define una métrica medible: dataset → columna → operador → target → periodo.
      El agente futuro evaluará automáticamente estos KPIs en la fecha compromiso.
    </div>`;
}

async function fetchDatasets() {
  if (DATASETS) return DATASETS;
  try {
    const r = await fetchWithTimeout('/api/datasets');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    DATASETS = (d.datasets || []).map(x => ({ name: x.name, layer: x.layer || 'silver' }));
  } catch { DATASETS = []; }
  return DATASETS;
}
async function fetchSchema(name) {
  if (SCHEMAS[name]) return SCHEMAS[name];
  try {
    const r = await fetchWithTimeout('/api/datasets/' + encodeURIComponent(name) + '/schema');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    SCHEMAS[name] = d.schema || d.fields || [];
  } catch { SCHEMAS[name] = []; }
  return SCHEMAS[name];
}

async function initKpiEditor() {
  await fetchDatasets();
  // Pre-warm schema for any dataset already referenced
  for (const k of WORKING_KPIS) {
    if (k.dataset) await fetchSchema(k.dataset);
  }
  renderKpiList();
}

function renderKpiList() {
  const cont = document.getElementById('kpi-list');
  if (!cont) return;
  if (!WORKING_KPIS.length) {
    cont.innerHTML = '<div class="kpi-empty">Sin KPIs definidos. Pulsa "Añadir KPI" para crear el primero.</div>';
    return;
  }
  cont.innerHTML = WORKING_KPIS.map((k, i) => kpiRowHtml(k, i)).join('');
  WORKING_KPIS.forEach((_, i) => bindKpiRow(i));
}

function kpiRowHtml(k, i) {
  const dsOpts = (DATASETS || []).map(d =>
    `<option value="${escHtml(d.name)}" ${k.dataset===d.name?'selected':''}>${escHtml(d.name)} [${d.layer}]</option>`
  ).join('');
  return `<div class="kpi-row" data-i="${i}">
    <div><label>Dataset</label>
      <select data-f="dataset"><option value="">—</option>${dsOpts}</select>
    </div>
    <div><label>Columna</label>
      <select data-f="column"><option value="">—</option></select>
    </div>
    <div><label>OP</label>
      <select data-f="operator">${KPI_OPS.map(o=>`<option ${k.operator===o?'selected':''}>${o}</option>`).join('')}</select>
    </div>
    <div><label>Target</label><input data-f="target" value="${escHtml(k.target||'')}" placeholder="15"></div>
    <div><label>Periodo</label><input data-f="period" value="${escHtml(k.period||'')}" placeholder="2026-Q2"></div>
    <div><label>Etiqueta</label><input data-f="label" value="${escHtml(k.label||'')}" placeholder="Margen mínimo"></div>
    <div><button class="btn-danger" data-action="kpi-remove" data-i="${i}">×</button></div>
  </div>`;
}

async function bindKpiRow(i) {
  const row = document.querySelector(`.kpi-row[data-i="${i}"]`);
  if (!row) return;
  const k = WORKING_KPIS[i];
  const colSel = row.querySelector('[data-f="column"]');
  if (k.dataset) {
    const cols = await fetchSchema(k.dataset);
    colSel.innerHTML = '<option value="">—</option>' + cols.map(c =>
      `<option value="${escHtml(c.name)}" ${k.column===c.name?'selected':''}>${escHtml(c.name)} <${escHtml(c.type||'')}></option>`
    ).join('');
  }
  row.querySelectorAll('[data-f]').forEach(el => {
    el.addEventListener('change', async () => {
      const f = el.dataset.f;
      k[f] = el.value;
      if (f === 'dataset') {
        k.column = '';
        const cols = await fetchSchema(k.dataset);
        colSel.innerHTML = '<option value="">—</option>' + cols.map(c =>
          `<option value="${escHtml(c.name)}"><${escHtml(c.type||'')}> ${escHtml(c.name)}</option>`
        ).join('');
      }
    });
    el.addEventListener('input', () => { k[el.dataset.f] = el.value; });
  });
}

window.addKpiRow = function() {
  WORKING_KPIS.push({ dataset:'', column:'', operator:'>=', target:'', period:'', label:'' });
  renderKpiList();
};
window.removeKpiRow = function(i) {
  WORKING_KPIS.splice(i, 1);
  renderKpiList();
};

window.saveDecisionKpis = async function(id) {
  // Drop incomplete rows
  const cleaned = WORKING_KPIS.filter(k => k.dataset && k.column);
  const r = await fetch('/api/decisions/' + id, {
    method: 'PATCH', headers: jsonHeaders(),
    body: JSON.stringify({ kpis: cleaned })
  });
  if (!r.ok) {
    const e = await r.json().catch(()=>({}));
    alert('Error: ' + (e.detail || r.statusText));
    return;
  }
  const d = await r.json();
  CUR_DEC.kpis = d.kpis || [];
  renderDetailModal('kpis');
};

window.saveDecisionOverview = async function(id) {
  const aRaw = document.getElementById('ed-assignee').value;
  const body = {
    title:           document.getElementById('ed-title').value.trim(),
    description:     document.getElementById('ed-desc').value,
    commitment_date: document.getElementById('ed-commit').value || null,
    status:          document.getElementById('ed-status').value,
    outcome:         document.getElementById('ed-outcome').value || null,
    assignee_id:     aRaw ? parseInt(aRaw, 10) : null,
    visibility:      document.getElementById('ed-vis').value,
  };
  if (!body.title) { alert('El título no puede estar vacío'); return; }
  const r = await fetch('/api/decisions/' + id, {
    method: 'PATCH', headers: jsonHeaders(), body: JSON.stringify(body)
  });
  if (!r.ok) {
    const e = await r.json().catch(()=>({}));
    alert('Error: ' + (e.detail || r.statusText));
    return;
  }
  closeModal();
  loadDecisions();
};

window.addDecisionAction = async function(id) {
  const txt = document.getElementById('ac-text').value.trim();
  if (!txt) return;
  const idempotencyKey = globalThis.crypto?.randomUUID?.() ||
    `decision-action-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const r = await fetch('/api/decisions/' + id + '/actions', {
    method: 'POST', headers: jsonHeaders({
      'Idempotency-Key': idempotencyKey,
    }),
    body: JSON.stringify({ action_text: txt })
  });
  if (!r.ok) { alert('Error: ' + r.statusText); return; }
  // Refetch and re-render the actions tab
  const r2 = await fetch('/api/decisions/' + id);
  CUR_DEC = await r2.json();
  renderDetailModal('actions');
};

window.deleteDecision = async function(id) {
  if (!confirm('¿Eliminar esta decisión y toda su bitácora? No se puede deshacer.')) return;
  const r = await fetch('/api/decisions/' + id, { method: 'DELETE', headers: csrfHeaders() });
  if (!r.ok) { alert('Error: ' + r.statusText); return; }
  closeModal();
  loadDecisions();
};

// ── Boot ────────────────────────────────────────────────────────────
(async () => {
  await loadConfig();
  await loadMe();
  loadApps();
  // Sprint v1.24: chat-input onkeydown was an inline attribute that the
  // strict CSP would block. Bind via addEventListener instead. The
  // textarea is in static markup so it exists by boot time.
  const chatInput = document.getElementById('chat-input');
  if (chatInput) chatInput.addEventListener('keydown', onChatKey);
})();


// ── Sprint v1.24 (audit B6): delegated click handler ─────────────────
// Every clickable surface in the workspace shell carries a data-action
// attribute (the inline onclick= attributes were stripped so the page
// can ship under script-src 'self'). The handler reads data-action and
// dispatches to the matching function — same pattern console adopted
// in v1.11 phase 3.
//
// Single document-level listener (not per-element) because most of the
// targets are added to the DOM later via innerHTML in renderDecisions /
// renderDetailModal — they don't exist at boot time.
document.addEventListener('click', (ev) => {
  const el = ev.target.closest('[data-action]');
  if (!el) return;
  const action = el.dataset.action;
  // Numeric args are stored as data-id / data-i strings; coerce here.
  const id = el.dataset.id ? Number(el.dataset.id) : null;
  const i  = el.dataset.i  ? Number(el.dataset.i)  : null;

  switch (action) {
    // Static, no args
    case 'apps-reload':            loadApps(); break;
    case 'chat-reset':             resetChat(); break;
    case 'chat-example':           askExample(el); break;
    case 'chat-send':              sendMessage(); break;
    case 'decision-new':           openNewDecision(); break;
    case 'decision-submit':        submitNewDecision(); break;
    case 'modal-close':            closeModal(); break;
    case 'modal-backdrop':
      // Original was `if(event.target===this)closeModal()` — only
      // close when the click is on the backdrop itself, not on a
      // child of it. el === backdrop, ev.target === actual click target.
      if (ev.target === el) closeModal();
      break;
    case 'logout':
      ev.preventDefault();
      doLogout();
      break;
    case 'kpi-add':                addKpiRow(); break;
    // Static-arg
    case 'switch-tab':             switchTab(el.dataset.tab); break;
    case 'dec-filter':             setDecFilter(el.dataset.filter); break;
    case 'detail-tab':             renderDetailModal(el.dataset.detailTab); break;
    // Dynamic-arg (interpolated id / index from template literals)
    case 'decision-action-add':    addDecisionAction(id); break;
    case 'decision-delete':        deleteDecision(id); break;
    case 'decision-save-overview': saveDecisionOverview(id); break;
    case 'decision-save-kpis':     saveDecisionKpis(id); break;
    case 'kpi-remove':             removeKpiRow(i); break;
  }
});
