// Sprint v1.11 phase 2 — extracted from job.html for strict CSP.

function esc(s) { return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;'); }
const jobId = location.pathname.split('/').pop();
let isRunning = true;

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

function badge(s) {
  const cls = { running: 'badge-running', done: 'badge-done', failed: 'badge-failed' }[s] || '';
  return `<span class="badge ${cls}">${s.toUpperCase()}</span>`;
}
function fmt(iso) { return iso ? iso.replace('T', ' ').substring(0, 19) : '—'; }
function dur(s, e) {
  if (!s) return '—';
  const sec = Math.round((new Date(e || Date.now()) - new Date(s)) / 1000);
  return sec < 60 ? sec + 's' : Math.floor(sec / 60) + 'm ' + (sec % 60) + 's';
}

async function loadJob() {
  const r = await fetchWithTimeout(`/api/jobs/${jobId}`, {}, 15000);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const j = await r.json();
  if (j.error) { document.getElementById('meta').innerHTML = `<span style="color:var(--red)">${esc(j.error)}</span>`; return; }

  document.getElementById('page-title').textContent = `JOB ${jobId}`;
  const args = j.args || {};
  document.getElementById('meta').innerHTML = `
    <div class="meta-item"><div class="meta-lbl">STATUS</div><div class="meta-val">${badge(j.status)}</div></div>
    <div class="meta-item"><div class="meta-lbl">ENTIDAD</div><div class="meta-val">${esc(args.entity || "") || "(todas)"}</div></div>
    <div class="meta-item"><div class="meta-lbl">MODO</div><div class="meta-val">${esc(args.mode || "") || "—"}</div></div>
    <div class="meta-item"><div class="meta-lbl">TOOL</div><div class="meta-val" style="font-size:10px">${esc(j.tool || "") || "—"}</div></div>
    <div class="meta-item"><div class="meta-lbl">MENSAJE</div><div class="meta-val" style="color:var(--text2)">${esc(j.message || "") || "—"}</div></div>
    <div class="meta-item"><div class="meta-lbl">INICIO</div><div class="meta-val">${fmt(j.created_at)}</div></div>
    <div class="meta-item"><div class="meta-lbl">FIN</div><div class="meta-val">${fmt(j.finished_at)}</div></div>
    <div class="meta-item"><div class="meta-lbl">DURACION</div><div class="meta-val">${dur(j.created_at, j.finished_at)}</div></div>
    ${j.error ? `<div class="meta-item" style="grid-column:1/-1"><div class="meta-lbl">ERROR</div><div class="meta-val" style="color:var(--red)">${esc(j.error)}</div></div>` : ''}
  `;

  // Progress parsing from message "Progreso X/Y"
  const match = (j.message || '').match(/(\d+)\/(\d+)/);
  if (match && j.status === 'running') {
    const pct = Math.round(parseInt(match[1]) / parseInt(match[2]) * 100);
    document.getElementById('prog-wrap').style.display = 'block';
    document.getElementById('prog-fill').style.width = pct + '%';
  }

  // Entity results
  const res = j.result || {};
  if (res.entities) {
    document.getElementById('entities-card').style.display = 'block';
    document.getElementById('entity-grid').innerHTML = res.entities.map(e => `
      <div class="entity-card">
        <div class="entity-name">${esc(e.entity)}</div>
        ${e.status === 'success'
          ? `<div class="entity-count entity-ok">${(e.record_count || 0).toLocaleString()} <span style="font-size:9px;color:var(--text3)">registros</span></div>`
          : `<div class="entity-count entity-err">ERROR</div><div style="font-size:9px;color:var(--text2)">${esc((e.error || '').substring(0, 60))}</div>`
        }
      </div>`).join('');
  }

  isRunning = j.status === 'running';
}

async function loadLogs() {
  const r = await fetchWithTimeout(`/api/jobs/${jobId}/logs?limit=500`);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const d = await r.json();
  const logs = d.logs || [];
  document.getElementById('log-count').textContent = logs.length + ' entradas';
  document.getElementById('log-container').innerHTML = logs.map(l => `
    <div class="log-entry">
      <span class="log-ts">${esc((l.ts || '').substring(11, 23))}</span>
      <span class="log-entity">${esc(l.entity || "") || "(batch)"}</span>
      <span class="log-level log-level-${esc(l.level)}">${esc(l.level)}</span>
      <span class="log-msg">
        ${esc(l.message)}
        ${l.detail ? `<div class="log-detail">${esc(JSON.stringify(l.detail))}</div>` : ''}
      </span>
    </div>`).join('');
}

async function loadAll() {
  try {
    await Promise.all([loadJob(), loadLogs()]);
  } catch (e) {
    document.getElementById('log-container').innerHTML = `<span style="color:var(--red)">Error: ${esc(e.message)}</span>`;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('btn-refresh').addEventListener('click', loadAll);
  loadAll();
  setInterval(() => {
    if (document.getElementById('auto-refresh').checked && isRunning) loadAll();
  }, 3000);
});
