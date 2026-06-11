// Sprint v1.11 phase 2 — extracted from pipeline.html for strict CSP.
//
// All previous behaviour preserved. Three render paths that used to emit
// `onclick="extractEntity(...)" / "selectDag(...)" / "applyTemplate(...)"`
// inside innerHTML now emit data-* attributes; a small block of event
// delegation at the bottom of the file dispatches clicks to the same
// functions. Static handlers on the static HTML are wired by id.

let pipelineData  = [];
let activeJobs    = {};   // entity → job_id (jobs being polled)
let _autoTimer    = null;
const _initialParams = new URLSearchParams(location.search);
let _cartridge    = _initialParams.get('cartridge') || '';
let _activeTab    = 'pipeline';
let _selectedDag  = null;  // currently selected dag_id in editor
let _airflowPublicUrl = '';
let _vaultConnections = [];
let _selectedConnId = '';

function syncPipelineUrl() {
  const nextParams = new URLSearchParams(location.search);
  nextParams.set('type', 'pipeline');
  nextParams.set('cartridge', _cartridge);
  history.replaceState(null, '', `${location.pathname}?${nextParams.toString()}`);
}

async function activeScopedCartridges() {
  try {
    const r = await fetch('/api/apps', { credentials: 'same-origin' });
    if (!r.ok) return [];
    const d = await r.json();
    return Array.isArray(d.active_scoped_cartridges) ? d.active_scoped_cartridges : [];
  } catch (e) {
    return [];
  }
}

async function loadRuntimeConfig() {
  try {
    const r = await fetch('/api/config', { credentials: 'same-origin' });
    if (!r.ok) return;
    const d = await r.json();
    _airflowPublicUrl = (d.airflow_url || '').replace(/\/+$/, '');
    const afLink = document.getElementById('dag-airflow-link');
    if (afLink && _selectedDag && _selectedDag !== '__new__') {
      afLink.setAttribute('href', airflowDagUrl(_selectedDag));
    }
  } catch (e) {}
}

function airflowFallbackUrl(dagId) {
  const params = new URLSearchParams({type: 'airflow'});
  if (_cartridge) params.set('cartridge', _cartridge);
  if (dagId) params.set('dag_id', dagId);
  return `/viewer?${params.toString()}`;
}

function airflowBaseUrl() {
  if (_airflowPublicUrl) return _airflowPublicUrl;
  const host = window.location.hostname;
  if (host === 'localhost' || host === '127.0.0.1') return `${window.location.protocol}//${host}:8082`;
  return '';
}

function airflowDagUrl(dagId) {
  const base = airflowBaseUrl();
  if (!base) return airflowFallbackUrl(dagId);
  if (!dagId) return base;
  return `${base}/dags/${encodeURIComponent(dagId)}/grid`;
}

function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

function jsonHeaders() {
  const headers = {'Content-Type': 'application/json'};
  const csrf = csrfToken();
  if (csrf) headers['X-CSRF-Token'] = csrf;
  return headers;
}

async function loadCartridgeSelector() {
  try {
    const [cartridgesResp, active] = await Promise.all([
      fetch('/studio/cartridges', {credentials: 'same-origin'}),
      activeScopedCartridges(),
    ]);
    const d = await cartridgesResp.json();
    const list = d.cartridges || [];
    const visibleList = active.length ? list.filter(c => active.includes(c.id)) : list;
    const requested = _cartridge;
    const nextCartridge = requested && (!active.length || active.includes(requested))
      ? requested
      : (visibleList[0]?.id || active[0] || requested || 'sap_successfactors');
    _cartridge = nextCartridge;
    const sel  = document.getElementById('cart-sel');
    const options = visibleList.length ? visibleList : [{id: _cartridge, name: _cartridge}];
    sel.innerHTML = options.map(c =>
      `<option value="${esc(c.id)}" ${c.id === _cartridge ? 'selected' : ''}>${esc(c.name)} (${esc(c.id)})</option>`
    ).join('');
    sel.value = _cartridge;
    syncPipelineUrl();
  } catch(e) {}
}

function onCartridgeChange() {
  const sel = document.getElementById('cart-sel');
  _cartridge = sel.value || 'sap_successfactors';
  syncPipelineUrl();
  activeJobs = {};
  _vaultConnections = [];
  _selectedConnId = '';
  load();
  loadDags();
}

// ── Status helpers ────────────────────────────────────────────────────────────

function dot(status) {
  const map = {fresh:'s-fresh', stale:'s-stale', error:'s-error',
                never:'s-never', running:'s-running', unknown:'s-unknown'};
  return `<span class="${map[status]||'s-unknown'}">●</span>`;
}

function fmt(iso) {
  if (!iso) return '—';
  return iso.replace('T',' ').substring(0, 16);
}

function reltime(iso) {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso);
  const min  = Math.floor(diff / 60000);
  if (min < 1)   return 'hace un momento';
  if (min < 60)  return `hace ${min}m`;
  const h = Math.floor(min / 60);
  if (h < 24)    return `hace ${h}h`;
  return `hace ${Math.floor(h/24)}d`;
}

function fmtCount(n) {
  return n != null ? Number(n).toLocaleString('es') : '—';
}

function modeTags(modes) {
  return (modes||['full']).map(m =>
    `<span class="mode-tag mode-${m}">${m}</span>`
  ).join('');
}

function layerBadge(layer) {
  return `<span class="layer-badge layer-${layer}">${layer.toUpperCase()}</span>`;
}

// ── Render ────────────────────────────────────────────────────────────────────

function renderStats(rows) {
  const fresh   = rows.filter(r => r.bronze.status === 'fresh').length;
  const stale   = rows.filter(r => r.bronze.status === 'stale').length;
  const never   = rows.filter(r => r.bronze.status === 'never').length;
  const running = Object.keys(activeJobs).length;
  const silverTotal = rows.reduce((a,r) => a + (r.silver||[]).length, 0);
  document.getElementById('stats-bar').innerHTML = `
    <div class="stat"><div class="stat-val">${rows.length}</div><div class="stat-lbl">ENTIDADES</div></div>
    <div class="stat"><div class="stat-val s-fresh">${fresh}</div><div class="stat-lbl">FRESH</div></div>
    <div class="stat"><div class="stat-val s-stale">${stale}</div><div class="stat-lbl">STALE</div></div>
    <div class="stat"><div class="stat-val s-never">${never}</div><div class="stat-lbl">SIN DATOS</div></div>
    <div class="stat"><div class="stat-val s-running">${running}</div><div class="stat-lbl">EXTRAYENDO</div></div>
    <div class="stat"><div class="stat-val">${silverTotal}</div><div class="stat-lbl">DATASETS SILVER</div></div>
    ${renderConnectionPicker()}
  `;
}

function renderConnectionPicker() {
  if (!_vaultConnections.length) return '';
  const options = _vaultConnections.map(conn => {
    const id = String(conn.conn_id || conn.id || '').trim();
    if (!id) return '';
    return `<option value="${esc(id)}" ${id === _selectedConnId ? 'selected' : ''}>${esc(id)}</option>`;
  }).join('');
  if (!options) return '';
  return `<label class="stat" style="min-width:180px">
    <div class="stat-lbl">CONEXIÓN</div>
    <select id="extract-conn-sel" data-action="select-connection"
            style="width:100%;height:28px;margin-top:4px;background:var(--bg2);color:var(--text);border:1px solid var(--border);font-family:var(--font-mono);font-size:11px">
      ${options}
    </select>
  </label>`;
}

function selectedConnectionId() {
  const sel = document.getElementById('extract-conn-sel');
  const value = sel ? sel.value : _selectedConnId;
  return String(value || '').trim();
}

function renderRow(row) {
  const entity    = row.entity;
  const cartridge = row.cartridge;
  const jobInfo   = activeJobs[entity] || null;
  const isRunning = jobInfo || (row.last_job && row.last_job.status === 'running');

  // Entity status
  let entityStatus = row.bronze.status;
  if (isRunning) entityStatus = 'running';
  if (row.last_job && row.last_job.status === 'failed' && !isRunning) entityStatus = 'error';

  // Entity cell
  let jobHtml = '';
  if (isRunning) {
    const msg = jobInfo?.message || row.last_job?.message || 'Extrayendo...';
    const pct = parseJobPct(msg);
    jobHtml = `
      <div class="job-progress">
        <div class="job-bar"><div class="job-fill" style="width:${pct}%"></div></div>
        <div class="job-msg">${esc(msg)}</div>
      </div>`;
  } else if (row.last_run) {
    // Airflow DAG run (from pipeline_runs table)
    const lr = row.last_run;
    const statusColor = {done:'s-fresh', success:'s-fresh', failed:'s-error'}[lr.status] || 's-unknown';
    const srcBadge = lr.source === 'airflow'
      ? `<span style="color:var(--text3);font-size:8px;font-family:var(--font-mono)">airflow</span>`
      : '';
    const errSnippet = lr.error
      ? `<div style="color:var(--red);font-size:9px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:180px" title="${esc(lr.error)}">${esc(lr.error.substring(0,80))}</div>`
      : '';
    jobHtml = `
      <div class="job-status-row">
        <span class="${statusColor}">●</span>
        <span style="color:var(--text3)">${lr.status} · ${reltime(lr.finished_at)}</span>
        ${srcBadge}
        ${lr.job_id ? `<a href="/viewer/jobs/${lr.job_id}" style="color:var(--text3);font-size:9px;text-decoration:none;">logs →</a>` : ''}
      </div>${errSnippet}`;
  } else if (row.last_job) {
    const j = row.last_job;
    const statusColor = {done:'s-fresh', failed:'s-error', running:'s-running'}[j.status] || 's-unknown';
    jobHtml = `
      <div class="job-status-row">
        <span class="${statusColor}">●</span>
        <span style="color:var(--text3)">${j.status} · ${reltime(j.created_at)}</span>
        ${j.job_id ? `<a href="/viewer/jobs/${j.job_id}" style="color:var(--text3);font-size:9px;text-decoration:none;" title="Ver logs">logs →</a>` : ''}
      </div>`;
  }

  const disabledAttr = isRunning ? 'disabled' : '';
  const btnLabel = isRunning ? '⟳ running' : '► Extract';

  // Sprint v1.11 phase 2: was onclick="extractEntity(...)" — now delegated.
  const entityCell = `
    <div class="cell entity-cell">
      <div class="entity-name">${dot(entityStatus)} ${esc(entity)}</div>
      <div class="entity-meta">${modeTags(row.modes)}${row.watermark ? ` <span style="color:var(--text3)">⏱ ${esc(row.watermark)}</span>` : ''}</div>
      <button class="extract-btn" data-action="extract-entity" data-cartridge="${esc(cartridge)}" data-entity="${esc(entity)}" ${disabledAttr}>${btnLabel}</button>
      ${jobHtml}
    </div>`;

  // Bronze cell
  const b = row.bronze;
  const lr = row.last_run;
  const bronzeCell = `
    <div class="cell" style="position:relative">
      ${b.status === 'error'
        ? `<div class="bronze-date" style="color:var(--red)">${dot('error')} Error</div>
           <div class="bronze-src" style="color:var(--red);font-size:9px;margin-top:2px">${esc((lr?.error||'').substring(0,100))}</div>`
        : b.status !== 'never'
        ? `<div class="bronze-date">${dot(b.status)} ${b.latest_date || '—'}</div>
           <div class="bronze-count">${fmtCount(b.record_count)} rows</div>
           <div class="bronze-src">${esc(b.source)}</div>`
        : `<div class="stage-empty">${dot('never')} sin extracción</div>
           <div class="bronze-src" style="margin-top:4px">${esc(b.source)}</div>`
      }
      <span class="arrow-wrap">▶</span>
    </div>`;

  // Silver cell
  let silverHtml = '';
  if (!row.silver || !row.silver.length) {
    silverHtml = `<div class="stage-empty">${dot('never')} sin datasets</div>`;
  } else {
    silverHtml = row.silver.map(ds => {
      const s = isRunning ? 'running' : ds.status;
      return `
        <div class="ds-node">
          <div class="ds-name">${dot(s)} ${esc(ds.name)}</div>
          <div class="ds-meta">
            ${layerBadge(ds.layer)}
            <span class="count">${fmtCount(ds.row_count)} rows</span>
            ${ds.last_refresh ? `<span class="ts"> · ${reltime(ds.last_refresh)}</span>` : ''}
          </div>
        </div>`;
    }).join('');
  }
  const silverCell = `
    <div class="cell" style="position:relative">
      ${silverHtml}
      ${row.gold && row.gold.length ? '<span class="arrow-wrap">▶</span>' : ''}
    </div>`;

  // Gold cell
  let goldHtml = '';
  if (!row.gold || !row.gold.length) {
    goldHtml = `<div class="stage-empty" style="color:#333">—</div>`;
  } else {
    goldHtml = row.gold.map(ds => {
      const s = isRunning ? 'running' : ds.status;
      return `
        <div class="ds-node">
          <div class="ds-name">${dot(s)} ${esc(ds.name)}</div>
          <div class="ds-meta">
            ${layerBadge(ds.layer)}
            <span class="count">${fmtCount(ds.row_count)} rows</span>
            ${ds.last_refresh ? `<span class="ts"> · ${reltime(ds.last_refresh)}</span>` : ''}
          </div>
        </div>`;
    }).join('');
  }
  const goldCell = `<div class="cell">${goldHtml}</div>`;

  const rowClass = isRunning ? 'pipeline-row cascade-highlight' : 'pipeline-row';
  return `<div class="${rowClass}" data-entity="${esc(entity)}">${entityCell}${bronzeCell}${silverCell}${goldCell}</div>`;
}

function render(rows) {
  renderStats(rows);
  if (!rows.length) {
    document.getElementById('pipeline-table').innerHTML =
      '<div class="loading-row">No se encontraron entidades registradas.</div>';
    return;
  }
  document.getElementById('pipeline-table').innerHTML = rows.map(renderRow).join('');
}

// ── Extract actions ───────────────────────────────────────────────────────────

async function extractEntity(cartridge, entity) {
  const body = {mode: 'incremental'};
  const connId = selectedConnectionId();
  if (connId) body.conn_id = connId;
  const r = await fetch(`/api/pipeline/${cartridge}/${entity}/extract`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (d.job_id) {
    activeJobs[entity] = {job_id: d.job_id, message: 'Iniciando...'};
    _pollJob(entity, d.job_id);
    _rerender();
  }
}

async function extractAll() {
  const body = {mode: 'incremental'};
  const connId = selectedConnectionId();
  if (connId) body.conn_id = connId;
  const r = await fetch(`/api/pipeline/${encodeURIComponent(_cartridge)}/extract_all`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (!r.ok) {
    alert(`Extract All falló: ${d.detail || d.error || r.status}`);
    return;
  }
  if (d.triggered && d.triggered.length) {
    d.triggered.forEach(t => {
      const jobId = t.job_id || t.dag_run_id || t.run_id;
      if (jobId) activeJobs[t.entity] = {job_id: jobId, message: 'Batch iniciado...'};
    });
    _rerender();
    setTimeout(load, 3000);
  }
}

// ── Job polling ───────────────────────────────────────────────────────────────

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

function parseJobPct(msg) {
  if (!msg) return 0;
  const m = msg.match(/(\d+)\/(\d+)/);
  return m ? Math.round(parseInt(m[1]) / parseInt(m[2]) * 100) : 30;
}

async function _pollJob(entity, jobId) {
  let done = false;
  while (!done) {
    await _sleep(2000);
    try {
      const r = await fetchWithTimeout(`/api/jobs/${jobId}`, {}, 15000);
      const j = await r.json();
      if (j.status === 'done' || j.status === 'failed') {
        delete activeJobs[entity];
        done = true;
        await load();   // full reload to see Silver updates
      } else {
        if (activeJobs[entity]) {
          activeJobs[entity].message = j.message || 'Extrayendo...';
        }
        _rerender();
      }
    } catch { done = true; }
  }
}

function _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── Data loading ──────────────────────────────────────────────────────────────

async function load() {
  try {
    await loadVaultConnections();
    const r = await fetchWithTimeout(`/api/pipeline?cartridge=${encodeURIComponent(_cartridge)}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    pipelineData = d.pipeline || [];

    // Merge running jobs detected server-side
    pipelineData.forEach(row => {
      if (row.last_job && row.last_job.status === 'running' && !activeJobs[row.entity]) {
        activeJobs[row.entity] = {job_id: row.last_job.job_id, message: row.last_job.message || ''};
        _pollJob(row.entity, row.last_job.job_id);
      }
    });

    render(pipelineData);
    document.getElementById('last-update').textContent =
      'Actualizado: ' + new Date().toLocaleTimeString();
  } catch(e) {
    document.getElementById('last-update').textContent = 'Error: ' + e.message;
  }
}

async function loadVaultConnections() {
  try {
    const r = await fetchWithTimeout(`/api/vault/connections/${encodeURIComponent(_cartridge)}`, {credentials: 'same-origin'});
    if (!r.ok) return;
    const d = await r.json();
    _vaultConnections = d.connections || [];
    if (!_selectedConnId && _vaultConnections.length) {
      _selectedConnId = String(_vaultConnections[0].conn_id || _vaultConnections[0].id || '').trim();
    }
  } catch (e) {}
}

function _rerender() {
  render(pipelineData);
}

function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ── Auto-refresh ──────────────────────────────────────────────────────────────

function startAutoRefresh() {
  if (_autoTimer) clearInterval(_autoTimer);
  _autoTimer = setInterval(() => {
    if (document.getElementById('auto-refresh').checked && !Object.keys(activeJobs).length) {
      load();
    }
  }, 8000);
}

// ── Tab switching ─────────────────────────────────────────────────────────────

function switchTab(tab) {
  _activeTab = tab;
  document.getElementById('view-pipeline').style.display = tab === 'pipeline' ? '' : 'none';
  document.getElementById('view-dags').style.display     = tab === 'dags'     ? '' : 'none';
  document.getElementById('tab-pipeline').classList.toggle('active', tab === 'pipeline');
  document.getElementById('tab-dags').classList.toggle('active',    tab === 'dags');
  if (tab === 'dags') loadDags();
}

// ── DAG editor ────────────────────────────────────────────────────────────────

let _dagsCache = [];

function _showEditor() {
  document.getElementById('dag-empty-state').style.display = 'none';
  document.getElementById('dag-editor-body').style.display = 'flex';
}

async function loadDags() {
  const list = document.getElementById('dag-list');
  list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text3);font-size:11px">Cargando…</div>';
  try {
    const r = await fetch('/api/mcp/invoke', {
      method: 'POST', credentials: 'same-origin',
      headers: jsonHeaders(),
      body: JSON.stringify({ server: 'infra', tool: 'airflow_list_dags', args: {} }),
    });
    const d = await r.json();
    _dagsCache = (d.result?.dags || [])
      .filter(dag => !_cartridge || dag.dag_id.startsWith(_cartridge + '_'))
      .sort((a, b) => a.dag_id.localeCompare(b.dag_id));

    if (!_dagsCache.length) {
      list.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text3);font-size:11px">
        Sin DAGs con prefijo <code>${esc(_cartridge)}_</code></div>`;
      return;
    }
    // Sprint v1.11 phase 2: was onclick="selectDag(...)" — now delegated.
    list.innerHTML = _dagsCache.map(dag => {
      const statusDot = dag.is_paused
        ? `<span style="color:#555">● pausado</span>`
        : `<span class="s-fresh">● activo</span>`;
      return `<div class="dag-sidebar-item ${_selectedDag === dag.dag_id ? 'selected' : ''}"
                   id="dagitem-${esc(dag.dag_id)}"
                   data-action="select-dag"
                   data-dag-id="${esc(dag.dag_id)}">
        <div class="dag-item-id">${esc(dag.dag_id)}</div>
        <div class="dag-item-meta">${statusDot}</div>
      </div>`;
    }).join('');

    // Restore previous selection, or auto-select first DAG
    const toSelect = (_selectedDag && _dagsCache.find(d => d.dag_id === _selectedDag))
      ? _selectedDag
      : _dagsCache[0]?.dag_id;
    if (toSelect) await selectDag(toSelect, false);
  } catch(e) {
    list.innerHTML = `<div style="padding:16px;color:var(--red);font-size:11px">Error: ${esc(e.message)}</div>`;
  }
}

async function selectDag(dagId, reloadList = true) {
  _selectedDag = dagId;
  // Update sidebar highlight
  document.querySelectorAll('.dag-sidebar-item').forEach(el => {
    el.classList.toggle('selected', el.id === `dagitem-${dagId}`);
  });

  // Show editor panel
  _showEditor();

  const textarea = document.getElementById('dag-code-textarea');
  const nameEl   = document.getElementById('dag-editor-name');
  const badgeEl  = document.getElementById('dag-editor-badge');
  const afLink   = document.getElementById('dag-airflow-link');

  nameEl.textContent = dagId;
  afLink.href = airflowDagUrl(dagId);

  const dag = _dagsCache.find(d => d.dag_id === dagId);
  if (dag) {
    badgeEl.innerHTML = dag.is_paused
      ? `<span class="dag-badge dag-paused">pausado</span>`
      : `<span class="dag-badge dag-active">activo</span>`;
  }

  setEditorCode('# Cargando fuente…');
  setDeployMsg('', '');

  // Try to load source from DB (falls back to disk automatically in dag_get_source)
  try {
    const r = await fetch('/api/mcp/invoke', {
      method: 'POST', credentials: 'same-origin',
      headers: jsonHeaders(),
      body: JSON.stringify({
        server: 'infra', tool: 'dag_get_source',
        args: { cartridge_id: _cartridge, dag_id: dagId },
      }),
    });
    const d = await r.json();
    if (d.result?.found && d.result?.source_code) {
      setEditorCode(d.result.source_code);
      return;
    }
  } catch(_) {}

  setEditorCode(
    `# Fuente no encontrada en BD para "${dagId}".\n` +
    `# El asistente puede regenerarla con infra__airflow_create_dag.\n`
  );
}

function newDag() {
  _selectedDag = '__new__';
  document.querySelectorAll('.dag-sidebar-item').forEach(el => el.classList.remove('selected'));
  _showEditor();
  document.getElementById('dag-editor-name').textContent = 'nuevo_dag';
  document.getElementById('dag-editor-badge').innerHTML = '';
  document.getElementById('dag-airflow-link').href = '#';
  setEditorCode(
    `# Nuevo DAG — ${_cartridge}\n` +
    `# dag_id recomendado: ${_cartridge}_<entidad>_<modo>\n\n` +
    `from airflow import DAG\n` +
    `from airflow.operators.python import PythonOperator\n` +
    `from datetime import datetime\n\n` +
    `with DAG(\n` +
    `    dag_id='${_cartridge}_nueva_entidad',\n` +
    `    start_date=datetime(2024, 1, 1),\n` +
    `    schedule=None,\n` +
    `    catchup=False,\n` +
    `    tags=['${_cartridge}'],\n` +
    `) as dag:\n` +
    `    pass\n`
  );
  setDeployMsg('', '');
}

async function deployDag() {
  const existingBtn = document.getElementById('btn-deploy');
  if (existingBtn?.dataset.disabledReason) {
    setDeployMsg(existingBtn.dataset.disabledReason, 'err');
    return;
  }
  const code   = document.getElementById('dag-code-textarea').value.trim();
  if (!code) { setDeployMsg('Sin código', 'err'); return; }

  // Extract dag_id from code (dag_id='...') or use current
  let dagId = _selectedDag === '__new__' ? '' : _selectedDag;
  const m = code.match(/dag_id\s*=\s*['"]([^'"]+)['"]/);
  if (m) dagId = m[1];
  if (!dagId) { setDeployMsg('No se encontró dag_id en el código', 'err'); return; }

  const btn = document.getElementById('btn-deploy');
  btn.disabled = true;
  setDeployMsg('Desplegando…', '');

  try {
    const r = await fetch('/api/mcp/invoke', {
      method: 'POST', credentials: 'same-origin',
      headers: jsonHeaders(),
      body: JSON.stringify({
        server: 'infra', tool: 'airflow_create_dag',
        args: { dag_id: dagId, code, cartridge_id: _cartridge,
                description: `DAG del cartucho ${_cartridge}` },
      }),
    });
    const d = await r.json();
    if (d.result?.created || d.result?.dag_id) {
      setDeployMsg(`✓ Desplegado: ${esc(d.result.created || dagId)} (${d.result.bytes || 0} bytes)`, 'ok');
      // Clear dirty flag — deployed code is now the canonical version
      _deployedCode = code;
      markDirty();
      document.getElementById('dag-editor-name').textContent = dagId;
      _selectedDag = dagId;
      setTimeout(loadDags, 1200);
    } else {
      setDeployMsg(`Error: ${esc(JSON.stringify(d.result || d))}`, 'err');
    }
  } catch(e) {
    setDeployMsg(`Error: ${esc(e.message)}`, 'err');
  }
  btn.disabled = false;
}

// ── Code editor helpers ────────────────────────────────────────────────────────

let _deployedCode = '';   // last code that was successfully deployed (or loaded from disk)

function editorKeydown(e) {
  const ta    = e.target;
  const start = ta.selectionStart;
  const end   = ta.selectionEnd;
  const val   = ta.value;

  // Ctrl+S → deploy
  if ((e.ctrlKey || e.metaKey) && e.key === 's') {
    e.preventDefault();
    deployDag();
    return;
  }

  // Tab → insert 4 spaces (single cursor) or indent selected lines
  if (e.key === 'Tab') {
    e.preventDefault();
    if (start === end) {
      // No selection — insert spaces to next 4-space boundary
      const col     = start - val.lastIndexOf('\n', start - 1) - 1;
      const spaces  = '    '.slice(col % 4) || '    ';
      _insertAt(ta, start, end, spaces, spaces.length);
    } else {
      // Multi-line selection — indent / dedent all touched lines
      const lineStart = val.lastIndexOf('\n', start - 1) + 1;
      const lineEnd   = val.indexOf('\n', end - 1);
      const block     = val.substring(lineStart, lineEnd < 0 ? val.length : lineEnd);
      if (e.shiftKey) {
        const replaced = block.replace(/^    /mg, '');
        _replaceBlock(ta, lineStart, lineEnd < 0 ? val.length : lineEnd, replaced);
      } else {
        const replaced = block.replace(/^/mg, '    ');
        _replaceBlock(ta, lineStart, lineEnd < 0 ? val.length : lineEnd, replaced);
      }
    }
    updateLineNumbers();
    markDirty();
    return;
  }

  // Shift+Tab on single cursor → remove up to 4 spaces from line start
  if (e.key === 'Tab' && e.shiftKey) { return; }  // handled above

  // Enter → auto-indent matching current line + extra indent after ':'
  if (e.key === 'Enter') {
    e.preventDefault();
    const lineStart  = val.lastIndexOf('\n', start - 1) + 1;
    const currentLine = val.substring(lineStart, start);
    const indent      = currentLine.match(/^(\s*)/)[1];
    const extraIndent = currentLine.trimEnd().endsWith(':') ? '    ' : '';
    const insert      = '\n' + indent + extraIndent;
    _insertAt(ta, start, end, insert, insert.length);
    updateLineNumbers();
    markDirty();
  }
}

function _insertAt(ta, start, end, text, cursorOffset) {
  const v = ta.value;
  ta.value = v.substring(0, start) + text + v.substring(end);
  ta.selectionStart = ta.selectionEnd = start + cursorOffset;
}

function _replaceBlock(ta, from, to, text) {
  const v = ta.value;
  ta.value = v.substring(0, from) + text + (to < 0 ? '' : v.substring(to));
  ta.selectionStart = from;
  ta.selectionEnd   = from + text.length;
}

function editorOnInput() {
  updateLineNumbers();
  markDirty();
}

function syncLineScroll() {
  const ta = document.getElementById('dag-code-textarea');
  const ln = document.getElementById('dag-line-numbers');
  if (ln) ln.scrollTop = ta.scrollTop;
}

function updateLineNumbers() {
  const ta    = document.getElementById('dag-code-textarea');
  const ln    = document.getElementById('dag-line-numbers');
  if (!ta || !ln) return;
  const count = (ta.value.match(/\n/g) || []).length + 1;
  // Only redraw if count changed
  const current = ln.querySelectorAll('span').length;
  if (current === count) return;
  ln.innerHTML = Array.from({length: count}, (_, i) =>
    `<span>${i + 1}</span>`
  ).join('');
}

function markDirty() {
  const badge = document.getElementById('dag-dirty-badge');
  const isDirty = document.getElementById('dag-code-textarea').value !== _deployedCode;
  if (badge) badge.style.display = isDirty ? '' : 'none';
}

function setEditorCode(code) {
  const ta = document.getElementById('dag-code-textarea');
  if (ta) ta.value = code;
  _deployedCode = code;
  updateLineNumbers();
  markDirty();
}

function setDeployMsg(msg, type) {
  const el = document.getElementById('deploy-msg');
  el.textContent = msg;
  el.className = 'deploy-msg' + (type === 'ok' ? ' deploy-ok' : type === 'err' ? ' deploy-err' : '');
}

function copyDagCode() {
  const code = document.getElementById('dag-code-textarea').value;
  if (!code) return;
  navigator.clipboard.writeText(code).then(() => setDeployMsg('⎘ Copiado', 'ok'))
    .catch(() => setDeployMsg('Error al copiar', 'err'));
  setTimeout(() => setDeployMsg('', ''), 2000);
}

// ── DAG Templates ─────────────────────────────────────────────────────────────

let _tplOpen = false;

function toggleTemplates() {
  _tplOpen = !_tplOpen;
  const list = document.getElementById('tpl-list');
  const tog  = document.getElementById('tpl-toggle');
  list.style.display = _tplOpen ? '' : 'none';
  tog.textContent    = _tplOpen ? '▼' : '▶';
  if (_tplOpen && list.querySelector('[style*="Cargando"]')) loadTemplates();
}

async function loadTemplates() {
  const list = document.getElementById('tpl-list');
  try {
    const r = await fetch('/api/dag_templates');
    const d = await r.json();
    const tpls = d.templates || [];
    if (!tpls.length) {
      list.innerHTML = '<div style="padding:12px;color:var(--text3);font-size:10px">Sin plantillas.</div>';
      return;
    }
    // Sprint v1.11 phase 2: was onclick="applyTemplate(...)" — now delegated.
    list.innerHTML = tpls.map(t => `
      <div class="tpl-item" data-action="apply-template" data-template-id="${esc(t.id)}">
        <div class="tpl-item-name">◈ ${esc(t.name)}</div>
        <div class="tpl-item-desc">${esc(t.description)}</div>
        <div class="tpl-tags">${(t.tags||[]).map(tag => `<span class="tpl-tag">${esc(tag)}</span>`).join('')}</div>
      </div>`).join('');
  } catch(e) {
    list.innerHTML = `<div style="padding:12px;color:var(--red);font-size:10px">Error: ${esc(e.message)}</div>`;
  }
}

async function applyTemplate(templateId) {
  // Ask for entity name to substitute placeholders
  const entity = prompt('Nombre de la entidad (ej. "ProjectDetail"):',
                        _selectedDag && _selectedDag !== '__new__'
                          ? _selectedDag.replace(_cartridge + '_', '').replace(/_/g, '')
                          : 'MyEntity');
  if (!entity) return;

  try {
    const r = await fetch(
      `/api/dag_templates/${encodeURIComponent(templateId)}` +
      `?cartridge=${encodeURIComponent(_cartridge)}&entity=${encodeURIComponent(entity)}`
    );
    const d = await r.json();
    if (!d.code) { alert('No se pudo cargar la plantilla'); return; }

    setEditorCode(d.code);
    // Show editor if not already visible
    _showEditor();
    document.getElementById('dag-editor-name').textContent =
      `${_cartridge}_${entity.toLowerCase()} (plantilla)`;
    document.getElementById('dag-editor-badge').innerHTML =
      '<span class="dag-badge dag-paused">borrador</span>';
    _selectedDag = '__new__';
    setDeployMsg('Plantilla cargada — revisa los TODO y despliega', 'ok');
    setTimeout(() => setDeployMsg('', ''), 4000);
  } catch(e) {
    alert(`Error al cargar plantilla: ${e.message}`);
  }
}

function sendDagToAssistant() {
  const dagId  = _selectedDag === '__new__' ? 'nuevo_dag' : (_selectedDag || 'dag');
  const source = document.getElementById('dag-code-textarea').value;
  const hasSource = source && !source.startsWith('# Fuente no encontrada');
  const msg = hasSource
    ? `Quiero modificar el DAG \`${dagId}\`. Este es el código actual:\n\n\`\`\`python\n${source}\n\`\`\`\n\n¿Qué cambios quieres hacer?`
    : `Quiero crear o corregir el DAG \`${dagId}\` del cartucho \`${_cartridge}\`. Genera el código completo y despliégalo con infra__airflow_create_dag.`;

  // Try postMessage to parent (embedded in Monitor/Studio)
  if (window.parent && window.parent !== window) {
    window.parent.postMessage({ type: 'dag_to_assistant', message: msg, dagId, source }, '*');
    setDeployMsg('✓ Enviado al asistente', 'ok');
    setTimeout(() => setDeployMsg('', ''), 2500);
    return;
  }
  // Fallback: open Studio via sessionStorage
  const payload = { type: 'dag_edit', dag_id: dagId, source, cartridge: _cartridge };
  sessionStorage.setItem('studio_pending', JSON.stringify(payload));
  window.open('/studio', '_blank');
}

// ── Wiring ────────────────────────────────────────────────────────────────────
// Sprint v1.11 phase 2 — all inline handlers replaced. Static buttons are
// wired by id; dynamically rendered controls go through event delegation
// keyed off data-action / data-* attributes.

function _wireStaticListeners() {
  document.getElementById('cart-sel').addEventListener('change', onCartridgeChange);
  document.getElementById('btn-refresh').addEventListener('click', load);
  document.getElementById('tab-pipeline').addEventListener('click', () => switchTab('pipeline'));
  document.getElementById('tab-dags').addEventListener('click', () => switchTab('dags'));
  document.getElementById('btn-dag-reload').addEventListener('click', loadDags);
  document.getElementById('btn-dag-new').addEventListener('click', newDag);
  document.getElementById('btn-toggle-templates').addEventListener('click', toggleTemplates);
  document.getElementById('btn-deploy').addEventListener('click', deployDag);
  document.getElementById('btn-copy').addEventListener('click', copyDagCode);
  document.getElementById('btn-send-assistant').addEventListener('click', sendDagToAssistant);

  const ta = document.getElementById('dag-code-textarea');
  ta.addEventListener('keydown', editorKeydown);
  ta.addEventListener('input', editorOnInput);
  ta.addEventListener('scroll', syncLineScroll);
}

function _wireDelegation() {
  // Each render emits data-action attributes; this single listener dispatches.
  document.body.addEventListener('click', (ev) => {
    const target = ev.target.closest('[data-action]');
    if (!target) return;
    const action = target.dataset.action;
    if (action === 'extract-entity') {
      extractEntity(target.dataset.cartridge, target.dataset.entity);
    } else if (action === 'extract-all') {
      extractAll();
    } else if (action === 'select-connection') {
      _selectedConnId = target.value || '';
    } else if (action === 'select-dag') {
      selectDag(target.dataset.dagId);
    } else if (action === 'apply-template') {
      applyTemplate(target.dataset.templateId);
    }
  });
}

// v1.43.2 (Frontend R1 hardening): the Deploy DAG button calls
// infra__airflow_create_dag, which is gated by _is_development() in
// mcp-infra. With APP_ENV=production (the new safe default), the
// tool refuses to run and the user sees a confusing "disabled outside
// development" error after clicking. Hide the CTA in production
// instead — surface a clear hint pointing to the runbook.
async function _gateDevModeUI() {
  try {
    const r = await fetch('/api/system/info', {credentials: 'same-origin'});
    if (!r.ok) return;
    const info = await r.json();
    if (!info.dev_mode) {
      const btn = document.getElementById('btn-deploy');
      if (btn) {
        btn.disabled = true;
        btn.dataset.disabledReason = 'Deploy a Airflow deshabilitado en producción: usar CI/CD y la imagen GHCR oficial.';
        btn.title = btn.dataset.disabledReason;
        btn.textContent = 'Deploy deshabilitado';
        btn.style.opacity = '0.5';
        btn.style.cursor = 'not-allowed';
        setDeployMsg(btn.dataset.disabledReason, 'err');
      }
    }
  } catch (e) {
    // /api/system/info unauthenticated path returns 401; in that case
    // we leave the button as-is (login gate is upstream).
  }
}


document.addEventListener('DOMContentLoaded', () => {
  _wireStaticListeners();
  _wireDelegation();
  _gateDevModeUI();

  // Support ?tab=dags[&dag=dag_id] URL params
  const p   = new URLSearchParams(location.search);
  const tab = p.get('tab');
  const dag = p.get('dag');
  loadRuntimeConfig().finally(() => {
    if (tab === 'dags') {
      if (dag) _selectedDag = dag;
      loadCartridgeSelector().then(() => {
        load();
        switchTab('dags');
      });
    } else {
      loadCartridgeSelector().then(() => { load(); });
    }
  });
  startAutoRefresh();
});
