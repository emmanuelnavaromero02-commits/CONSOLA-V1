const OPS = ['>=', '>', '<=', '<', '=', '!='];
let CURRENT_FILTER = 'all';
let SELECTED_ID = null;
let DATASETS_CACHE = null;
let SCHEMA_CACHE = {};
let ME = null;
let USERS = [];

async function loadMe(){
  try {
    const r = await fetch('/auth/me');
    const d = await r.json();
    ME = d.user;
  } catch { ME = null; }
  const bar = document.getElementById('user-bar');
  if (!ME) { bar.innerHTML = `<a href="/login">INICIAR SESIÓN</a>`; return; }
  const adminLink = ME.role === 'admin'
    ? `<a href="/iam?tab=users">USUARIOS</a> · `
    : '';
  const role = String(ME.role || '');
  bar.innerHTML = `
    <span class="me"><strong>${escHtml(ME.email)}</strong></span>
    <span class="role-pill ${role==='admin'?'admin':''}">${escHtml(role.toUpperCase())}</span>
    ${adminLink}
    <a href="/me">MI PERFIL</a>
    <a href="#" data-action="logout">SALIR</a>`;
}
async function doLogout(){
  const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  const csrf = m ? decodeURIComponent(m[1]) : '';
  await fetch('/auth/logout', {
    method: 'POST',
    headers: csrf ? { 'X-CSRF-Token': csrf } : {},
  });
  location.href = '/login';
}

async function loadUsers(){
  try {
    const r = await fetch('/api/users');
    const d = await r.json();
    USERS = d.users || [];
  } catch { USERS = []; }
}
function userLabel(uid){
  if (!uid) return null;
  const u = USERS.find(x => x.id === uid);
  return u ? (u.name || u.email) : `#${uid}`;
}

function escHtml(s){ return String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;'); }
function fmtDate(s){ return s ? String(s).slice(0,10) : '—'; }
function fmtDateTime(s){ return s ? String(s).slice(0,16).replace('T',' ') : '—'; }
function daysUntil(d){
  if(!d) return null;
  const a = new Date(d + 'T00:00:00');
  const b = new Date(); b.setHours(0,0,0,0);
  return Math.round((a - b) / 86400000);
}

async function fetchDatasets(){
  if (DATASETS_CACHE) return DATASETS_CACHE;
  try {
    const r = await fetch('/datasets');
    const d = await r.json();
    DATASETS_CACHE = (d.datasets || []).map(x => ({ name: x.name, layer: x.layer || 'silver' }));
  } catch { DATASETS_CACHE = []; }
  return DATASETS_CACHE;
}
async function fetchSchema(name){
  if (SCHEMA_CACHE[name]) return SCHEMA_CACHE[name];
  try {
    const r = await fetch('/datasets/' + encodeURIComponent(name) + '/schema');
    const d = await r.json();
    SCHEMA_CACHE[name] = d.schema || d.fields || [];
  } catch { SCHEMA_CACHE[name] = []; }
  return SCHEMA_CACHE[name];
}

async function loadList(){
  const params = new URLSearchParams();
  if (CURRENT_FILTER === 'open')    params.set('status','open');
  if (CURRENT_FILTER === 'closed')  params.set('status','closed');
  if (CURRENT_FILTER === 'overdue') params.set('overdue','true');

  const cont = document.getElementById('list-container');
  cont.innerHTML = '<div class="empty-state">Cargando decisiones…</div>';
  try {
    const r = await fetch('/api/decisions?' + params.toString());
    const d = await r.json();
    const rows = d.decisions || [];
    if (!rows.length) {
      cont.innerHTML = `<div class="empty-state">
        ▣ Sin decisiones registradas en este filtro.<br>
        <span style="color:var(--text2)">Pulsa "+ NUEVA DECISIÓN" para registrar la primera.</span>
      </div>`;
      return;
    }
    cont.innerHTML = `
      <table class="dec-table">
        <thead>
          <tr>
            <th>TÍTULO</th>
            <th>RESPONSABLE</th>
            <th>VIS</th>
            <th>COMPROMISO</th>
            <th>STATUS</th>
            <th>OUTCOME</th>
            <th>DÍAS</th>
          </tr>
        </thead>
        <tbody>
          ${rows.map(rowHtml).join('')}
        </tbody>
      </table>`;
    cont.querySelectorAll('tbody tr').forEach(tr => {
      tr.addEventListener('click', () => selectDecision(parseInt(tr.dataset.id, 10)));
    });
  } catch(e) {
    cont.innerHTML = `<div class="empty-state" style="color:var(--red)">Error: ${escHtml(e.message)}</div>`;
  }
}

function rowHtml(r){
  const days = daysUntil(r.commitment_date);
  const status = String(r.status || '');
  let statusBadge = `<span class="badge b-${escHtml(status)}">${escHtml(status.toUpperCase())}</span>`;
  let daysCell = '—';
  if (r.status === 'open' && days !== null) {
    if (days < 0)      { statusBadge = `<span class="badge b-overdue">VENCIDA</span>`; daysCell = `<span style="color:var(--red)">${days}d</span>`; }
    else if (days <= 7) daysCell = `<span style="color:var(--amber)">${days}d</span>`;
    else                daysCell = `<span style="color:var(--text2)">${days}d</span>`;
  }
  let outcomeCell = '—';
  if (r.outcome === 'achieved')      outcomeCell = `<span class="badge b-ok">LOGRADO</span>`;
  else if (r.outcome === 'not_achieved') outcomeCell = `<span class="badge b-fail">NO LOGRADO</span>`;
  const responsableLabel = userLabel(r.assignee_id);
  const responsable = responsableLabel
    ? escHtml(responsableLabel)
    : `<span style="color:var(--text3)">—</span>`;
  const visBadge = r.visibility === 'shared'
    ? `<span class="badge" style="background:var(--info-soft);color:var(--cyan)" title="Visible para todo el equipo">EQUIPO</span>`
    : `<span class="badge" style="background:var(--surface-soft);color:var(--text2)" title="Solo creador, responsable y admins">PRIVADA</span>`;
  return `<tr data-id="${Number(r.id)}" class="${SELECTED_ID === r.id ? 'selected' : ''}">
    <td style="color:var(--text)"><strong>${escHtml(r.title)}</strong></td>
    <td style="color:var(--text2)">${responsable}</td>
    <td>${visBadge}</td>
    <td>${escHtml(fmtDate(r.commitment_date))}</td>
    <td>${statusBadge}</td>
    <td>${outcomeCell}</td>
    <td>${daysCell}</td>
  </tr>`;
}

async function selectDecision(id){
  SELECTED_ID = id;
  document.querySelectorAll('.dec-table tbody tr').forEach(tr => {
    tr.classList.toggle('selected', parseInt(tr.dataset.id,10) === id);
  });
  await renderDetail(id);
  document.getElementById('detail-container').scrollIntoView({behavior:'smooth', block:'start'});
}

async function renderDetail(id){
  const cont = document.getElementById('detail-container');
  cont.innerHTML = '<div class="empty-state">Cargando…</div>';
  let d;
  try {
    const r = await fetch('/api/decisions/' + id);
    if (!r.ok) throw new Error(r.statusText);
    d = await r.json();
  } catch(e) {
    cont.innerHTML = `<div class="empty-state" style="color:var(--red)">Error: ${escHtml(e.message)}</div>`;
    return;
  }
  cont.innerHTML = `
    <div class="detail">
      <div class="detail-head">
        <h2>${escHtml(d.title)}</h2>
        <button class="btn-ghost" data-action="close-detail">CERRAR</button>
        <button class="btn-danger" data-action="delete-decision" data-id="${Number(d.id)}">🗑 ELIMINAR</button>
      </div>
      <div class="detail-meta">
        <div class="meta-item"><span class="meta-lbl">CREADA</span><span class="meta-val">${escHtml(fmtDateTime(d.created_at))}</span></div>
        <div class="meta-item"><span class="meta-lbl">COMPROMISO</span><span class="meta-val">${escHtml(fmtDate(d.commitment_date))}</span></div>
        <div class="meta-item"><span class="meta-lbl">CIERRE</span><span class="meta-val">${escHtml(fmtDateTime(d.closed_at))}</span></div>
        <div class="meta-item"><span class="meta-lbl">STATUS</span><span class="meta-val">${escHtml((d.status||'').toUpperCase())}</span></div>
        <div class="meta-item"><span class="meta-lbl">OUTCOME</span><span class="meta-val">${d.outcome ? escHtml(d.outcome.toUpperCase()) : '—'}</span></div>
      </div>
      <div class="tabs">
        <div class="tab active" data-tab="overview">OVERVIEW</div>
        <div class="tab" data-tab="kpis">KPIs (${(d.kpis||[]).length})</div>
        <div class="tab" data-tab="actions">BITÁCORA (${(d.actions||[]).length})</div>
        <div class="tab" data-tab="followup">SEGUIMIENTO</div>
      </div>
      <div class="tab-body" id="tab-body"></div>
    </div>`;
  cont.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => {
    cont.querySelectorAll('.tab').forEach(x => x.classList.toggle('active', x === t));
    renderTab(t.dataset.tab, d);
  }));
  renderTab('overview', d);
}
function closeDetail(){
  SELECTED_ID = null;
  document.getElementById('detail-container').innerHTML = '';
  loadList();
}

async function renderTab(tab, d){
  const body = document.getElementById('tab-body');
  if (tab === 'overview') {
    const userOpts = ['<option value="">(sin asignar)</option>']
      .concat(USERS.map(u => `<option value="${escHtml(u.id)}" ${d.assignee_id===u.id?'selected':''}>${escHtml(u.name||u.email)}</option>`))
      .join('');
    const ownerLabel = userLabel(d.created_by_id) || '—';
    body.innerHTML = `
      <div class="form-grid">
        <div class="field-row">
          <label>TÍTULO</label>
          <input id="ed-title" value="${escHtml(d.title)}">
        </div>
        <div class="field-row">
          <label>FECHA COMPROMISO</label>
          <input id="ed-commitment" type="date" value="${d.commitment_date ? d.commitment_date.slice(0,10) : ''}">
        </div>
        <div class="field-row" style="grid-column: 1 / span 2">
          <label>DESCRIPCIÓN</label>
          <textarea id="ed-description">${escHtml(d.description)}</textarea>
        </div>
        <div class="field-row">
          <label>RESPONSABLE</label>
          <select id="ed-assignee">${userOpts}</select>
        </div>
        <div class="field-row">
          <label>VISIBILIDAD</label>
          <select id="ed-visibility">
            <option value="private" ${d.visibility==='private'?'selected':''}>private — solo creador, responsable, admins</option>
            <option value="shared"  ${d.visibility==='shared' ?'selected':''}>shared — todo el equipo</option>
          </select>
        </div>
        <div class="field-row">
          <label>STATUS</label>
          <select id="ed-status">
            <option value="open"   ${d.status==='open'?'selected':''}>open</option>
            <option value="closed" ${d.status==='closed'?'selected':''}>closed</option>
          </select>
        </div>
        <div class="field-row">
          <label>OUTCOME</label>
          <select id="ed-outcome">
            <option value=""              ${!d.outcome?'selected':''}>(pendiente)</option>
            <option value="achieved"      ${d.outcome==='achieved'?'selected':''}>achieved · LOGRADO</option>
            <option value="not_achieved"  ${d.outcome==='not_achieved'?'selected':''}>not_achieved · NO LOGRADO</option>
          </select>
        </div>
        <div class="field-row" style="grid-column: 1 / span 2">
          <label>CREADA POR</label>
          <div style="font-size:11px;color:var(--text2);font-family:var(--font-mono)">${escHtml(ownerLabel)} · ${fmtDateTime(d.created_at)}</div>
        </div>
      </div>
      <div style="display:flex; gap:8px; margin-top:8px">
        <button class="btn-primary" data-action="save-overview" data-id="${Number(d.id)}">GUARDAR CAMBIOS</button>
      </div>`;
  } else if (tab === 'kpis') {
    const datasets = await fetchDatasets();
    body.innerHTML = `
      <div id="kpi-list"></div>
      <div style="margin-top:18px">
        <button class="btn-ghost" data-action="add-kpi-row">+ AÑADIR KPI</button>
        <button class="btn-primary" style="margin-left:8px" data-action="save-kpis" data-id="${Number(d.id)}">GUARDAR KPIs</button>
      </div>`;
    window._kpis = JSON.parse(JSON.stringify(d.kpis || []));
    window._kpiDatasets = datasets;
    renderKpiList();
  } else if (tab === 'actions') {
    body.innerHTML = `
      <div class="field-row">
        <label>NUEVA ENTRADA EN BITÁCORA</label>
        <textarea id="ac-text" placeholder="Acción tomada o nota…"></textarea>
      </div>
      <div style="display:flex; gap:8px; margin-bottom:18px">
        <button class="btn-primary" data-action="add-action" data-id="${Number(d.id)}">AÑADIR</button>
      </div>
      <div class="actions-list">
        ${(d.actions||[]).length === 0
          ? '<div class="empty-state">Sin entradas en bitácora.</div>'
          : d.actions.map(a => `
            <div class="action-card">
              <div class="action-meta">
                <span>${fmtDateTime(a.ts)}</span>
                <span class="${a.actor==='agent'?'action-actor-agent':''}">${escHtml(a.actor)}</span>
              </div>
              <div class="action-text">${escHtml(a.action_text)}</div>
              ${a.note ? `<div class="action-note">${escHtml(a.note)}</div>` : ''}
            </div>`).join('')}
      </div>`;
  } else if (tab === 'followup') {
    const fu = d.follow_up_decision_id;
    body.innerHTML = `
      <div style="font-size:12px; color:var(--text2); margin-bottom:14px">
        ${fu
          ? `Esta decisión tiene un seguimiento: <a style="color:var(--amber)" href="#" data-action="select-decision" data-id="${Number(fu)}">decisión #${Number(fu)}</a>`
          : 'Si esta decisión no se logró o requiere continuidad, crea una decisión de seguimiento que herede los KPIs.'}
      </div>
      ${fu
        ? `<button class="btn-ghost" data-action="unlink-followup" data-id="${Number(d.id)}">DESVINCULAR SEGUIMIENTO</button>`
        : `<button class="btn-primary" data-action="create-followup" data-id="${Number(d.id)}">CREAR DECISIÓN DE SEGUIMIENTO</button>`}`;
  }
}

function renderKpiList(){
  const cont = document.getElementById('kpi-list');
  if (!cont) return;
  if (!window._kpis.length) {
    cont.innerHTML = '<div class="kpi-list-empty">Sin KPIs definidos. Añade el primero.</div>';
    return;
  }
  cont.innerHTML = window._kpis.map((k, i) => kpiRowHtml(k, i)).join('');
  window._kpis.forEach((_, i) => bindKpiRow(i));
}

function kpiRowHtml(k, i){
  const datasets = window._kpiDatasets || [];
  const dsOpts = datasets.map(d =>
    `<option value="${escHtml(d.name)}" ${k.dataset===d.name?'selected':''}>${escHtml(d.name)} [${escHtml(d.layer)}]</option>`
  ).join('');
  return `<div class="kpi-row" data-i="${i}">
    <div><label>DATASET</label>
      <select data-f="dataset"><option value="">—</option>${dsOpts}</select>
    </div>
    <div><label>COLUMNA</label>
      <select data-f="column"><option value="">—</option></select>
    </div>
    <div><label>OP</label>
      <select data-f="operator">${OPS.map(o=>`<option ${k.operator===o?'selected':''}>${o}</option>`).join('')}</select>
    </div>
    <div><label>TARGET</label>
      <input data-f="target" value="${escHtml(k.target)}" placeholder="ej: 15">
    </div>
    <div><label>PERIODO</label>
      <input data-f="period" value="${escHtml(k.period)}" placeholder="2026-Q2">
    </div>
    <div><label>ETIQUETA</label>
      <input data-f="label" value="${escHtml(k.label)}" placeholder="Margen mínimo">
    </div>
    <div><button class="btn-danger" data-action="remove-kpi" data-i="${i}">×</button></div>
  </div>`;
}

async function bindKpiRow(i){
  const row = document.querySelector(`.kpi-row[data-i="${i}"]`);
  if (!row) return;
  const k = window._kpis[i];
  const colSel = row.querySelector('[data-f="column"]');
  if (k.dataset) {
    const cols = await fetchSchema(k.dataset);
    colSel.innerHTML = '<option value="">—</option>' + cols.map(c =>
      `<option value="${escHtml(c.name)}" ${k.column===c.name?'selected':''}>${escHtml(c.name)} <${escHtml(c.type)}></option>`
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
          `<option value="${escHtml(c.name)}"><${escHtml(c.type)}> ${escHtml(c.name)}</option>`
        ).join('');
      }
    });
    el.addEventListener('input', () => { k[el.dataset.f] = el.value; });
  });
}

function addKpiRow(){
  window._kpis.push({ dataset:'', column:'', operator:'>=', target:'', period:'', label:'' });
  renderKpiList();
}
function removeKpi(i){
  window._kpis.splice(i, 1);
  renderKpiList();
}

async function saveOverview(id){
  const assigneeRaw = document.getElementById('ed-assignee').value;
  const body = {
    title:           document.getElementById('ed-title').value.trim(),
    description:     document.getElementById('ed-description').value,
    commitment_date: document.getElementById('ed-commitment').value || null,
    status:          document.getElementById('ed-status').value,
    outcome:         document.getElementById('ed-outcome').value || null,
    assignee_id:     assigneeRaw ? parseInt(assigneeRaw, 10) : null,
    visibility:      document.getElementById('ed-visibility').value,
  };
  if (!body.title) { alert('El título no puede estar vacío'); return; }
  const r = await fetch('/api/decisions/' + id, {
    method: 'PATCH', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body)
  });
  if (!r.ok) {
    const err = await r.json().catch(()=>({}));
    alert('Error guardando: ' + (err.detail || r.statusText));
    return;
  }
  await loadList();
  await renderDetail(id);
}

async function saveKpis(id){
  const cleaned = window._kpis.filter(k => k.dataset && k.column);
  const r = await fetch('/api/decisions/' + id, {
    method: 'PATCH', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ kpis: cleaned })
  });
  if (!r.ok) { alert('Error guardando KPIs: ' + r.statusText); return; }
  window._kpis = cleaned;
  await renderDetail(id);
}

async function addAction(id){
  const txt = document.getElementById('ac-text').value.trim();
  if (!txt) return;
  const idempotencyKey = globalThis.crypto?.randomUUID?.() ||
    `decision-action-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const r = await fetch('/api/decisions/' + id + '/actions', {
    method: 'POST', headers: {
      'Content-Type':'application/json',
      'Idempotency-Key': idempotencyKey,
    },
    body: JSON.stringify({ action_text: txt })
  });
  if (!r.ok) { alert('Error: ' + r.statusText); return; }
  await renderDetail(id);
}

async function deleteDecision(id){
  if (!confirm('¿Eliminar esta decisión y toda su bitácora? No se puede deshacer.')) return;
  const r = await fetch('/api/decisions/' + id, { method: 'DELETE' });
  if (!r.ok) { alert('Error: ' + r.statusText); return; }
  closeDetail();
}

async function createFollowUp(id){
  const r0 = await fetch('/api/decisions/' + id);
  const orig = await r0.json();
  const newTitle = `[Seguimiento] ${orig.title}`;
  const r = await fetch('/api/decisions', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      title: newTitle,
      description: `Seguimiento de decisión #${id}: ${orig.title}`,
      kpis: orig.kpis || [],
    })
  });
  if (!r.ok) { alert('Error creando seguimiento'); return; }
  const created = await r.json();
  await fetch('/api/decisions/' + id, {
    method: 'PATCH', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ follow_up_decision_id: created.id })
  });
  await loadList();
  selectDecision(created.id);
}
async function unlinkFollowUp(id){
  await fetch('/api/decisions/' + id, {
    method: 'PATCH', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ follow_up_decision_id: null })
  });
  renderDetail(id);
}


function openNewModal(){
  const m = document.getElementById('modal-new');
  const userOpts = ['<option value="">(sin asignar)</option>']
    .concat(USERS.map(u => `<option value="${escHtml(u.id)}" ${ME && ME.id===u.id?'selected':''}>${escHtml(u.name||u.email)}</option>`))
    .join('');
  m.style.display = 'block';
  m.innerHTML = `
    <div class="modal-backdrop" data-action="modal-backdrop">
      <div class="modal">
        <div class="modal-head">
          <h3>NUEVA DECISIÓN</h3>
          <button class="modal-close" data-action="close-new-modal">×</button>
        </div>
        <div class="modal-body">
          <div class="field-row">
            <label>TÍTULO</label>
            <input id="nw-title" placeholder="Reducir overhead en proyecto X">
          </div>
          <div class="field-row">
            <label>DESCRIPCIÓN</label>
            <textarea id="nw-desc" placeholder="Contexto, motivación, criterio de éxito…"></textarea>
          </div>
          <div class="field-row">
            <label>FECHA COMPROMISO</label>
            <input id="nw-commit" type="date">
          </div>
          <div class="field-row">
            <label>RESPONSABLE</label>
            <select id="nw-assignee">${userOpts}</select>
          </div>
          <div class="field-row">
            <label>VISIBILIDAD</label>
            <select id="nw-visibility">
              <option value="private">private — solo creador, responsable y admins</option>
              <option value="shared">shared — todo el equipo</option>
            </select>
          </div>
          <div style="font-size:10px; color:var(--text3); font-family:var(--font-mono); letter-spacing:1px">
            Los KPIs se definen tras crear la decisión, en la pestaña KPIs.
          </div>
        </div>
        <div class="modal-foot">
          <button class="btn-ghost" data-action="close-new-modal">CANCELAR</button>
          <button class="btn-primary" data-action="submit-new">CREAR</button>
        </div>
      </div>
    </div>`;
  setTimeout(() => document.getElementById('nw-title').focus(), 50);
}
function closeNewModal(){ document.getElementById('modal-new').style.display = 'none'; }
async function submitNew(){
  const title = document.getElementById('nw-title').value.trim();
  if (!title) { alert('El título es obligatorio'); return; }
  const assigneeRaw = document.getElementById('nw-assignee').value;
  const r = await fetch('/api/decisions', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      title,
      description: document.getElementById('nw-desc').value,
      commitment_date: document.getElementById('nw-commit').value || null,
      assignee_id:    assigneeRaw ? parseInt(assigneeRaw, 10) : null,
      visibility:     document.getElementById('nw-visibility').value,
    })
  });
  if (!r.ok) {
    const err = await r.json().catch(()=>({}));
    alert('Error: ' + (err.detail || r.statusText));
    return;
  }
  const d = await r.json();
  closeNewModal();
  await loadList();
  selectDecision(d.id);
}


document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('btn-new').addEventListener('click', openNewModal);

  document.querySelectorAll('.filter-btn').forEach(b => {
    b.addEventListener('click', () => {
      document.querySelectorAll('.filter-btn').forEach(x => x.classList.toggle('active', x===b));
      CURRENT_FILTER = b.dataset.filter;
      loadList();
    });
  });

  document.body.addEventListener('click', (ev) => {
    const target = ev.target.closest('[data-action]');
    if (!target) return;
    const action = target.dataset.action;
    if (action === 'logout') {
      ev.preventDefault();
      doLogout();
    } else if (action === 'close-detail') {
      closeDetail();
    } else if (action === 'delete-decision') {
      deleteDecision(Number(target.dataset.id));
    } else if (action === 'save-overview') {
      saveOverview(Number(target.dataset.id));
    } else if (action === 'add-kpi-row') {
      addKpiRow();
    } else if (action === 'save-kpis') {
      saveKpis(Number(target.dataset.id));
    } else if (action === 'add-action') {
      addAction(Number(target.dataset.id));
    } else if (action === 'select-decision') {
      ev.preventDefault();
      selectDecision(Number(target.dataset.id));
    } else if (action === 'unlink-followup') {
      unlinkFollowUp(Number(target.dataset.id));
    } else if (action === 'create-followup') {
      createFollowUp(Number(target.dataset.id));
    } else if (action === 'remove-kpi') {
      removeKpi(Number(target.dataset.i));
    } else if (action === 'modal-backdrop') {
      if (ev.target === target) closeNewModal();
    } else if (action === 'close-new-modal') {
      closeNewModal();
    } else if (action === 'submit-new') {
      submitNew();
    }
  });

  (async () => {
    await Promise.all([loadMe(), loadUsers()]);
    loadList();
  })();
});
