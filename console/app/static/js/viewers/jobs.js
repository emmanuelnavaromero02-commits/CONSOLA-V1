// Sprint v1.11 phase 2 — extracted from jobs.html for strict CSP.

function esc(s) { return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;"); }

let allJobs = [];

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
  const cls = { running: 'badge-running', done: 'badge-done', failed: 'badge-failed' }[s] || 'badge-pending';
  return `<span class="badge ${cls}">${s.toUpperCase()}</span>`;
}

function dur(start, end) {
  if (!start) return '—';
  const s = new Date(start), e = end ? new Date(end) : new Date();
  const sec = Math.round((e - s) / 1000);
  if (sec < 60) return sec + 's';
  return Math.round(sec / 60) + 'm ' + (sec % 60) + 's';
}

function fmt(iso) { return iso ? iso.replace('T', ' ').substring(0, 19) : '—'; }

function renderStats() {
  const running = allJobs.filter(j => j.status === 'running').length;
  const done    = allJobs.filter(j => j.status === 'done').length;
  const failed  = allJobs.filter(j => j.status === 'failed').length;
  const total   = allJobs.reduce((a, j) => {
    const r = (j.result || {});
    return a + (r.record_count || r.total_records || 0);
  }, 0);
  document.getElementById('stats').innerHTML = `
    <div class="stat"><div class="stat-val">${allJobs.length}</div><div class="stat-lbl">TOTAL JOBS</div></div>
    <div class="stat"><div class="stat-val" style="color:var(--amber)">${running}</div><div class="stat-lbl">EN EJECUCION</div></div>
    <div class="stat"><div class="stat-val" style="color:var(--cyan)">${done}</div><div class="stat-lbl">COMPLETADOS</div></div>
    <div class="stat"><div class="stat-val" style="color:var(--red)">${failed}</div><div class="stat-lbl">FALLIDOS</div></div>
    <div class="stat"><div class="stat-val">${total.toLocaleString()}</div><div class="stat-lbl">REGISTROS TOTALES</div></div>
  `;
}

function renderTable() {
  const filter = document.getElementById('filter-status').value;
  const jobs = filter ? allJobs.filter(j => j.status === filter) : allJobs;
  document.getElementById('tbody').innerHTML = jobs.map(j => {
    const args = j.args || {};
    const res  = j.result || {};
    const count = res.record_count || res.total_records || '—';
    return `<tr>
      <td class="mono">${j.job_id}</td>
      <td style="font-size:10px;color:var(--text2)">${j.tool || '—'}</td>
      <td>${args.entity || '(all)'}</td>
      <td style="font-size:10px;color:var(--text2)">${args.mode || '—'}</td>
      <td>${badge(j.status)} <span style="font-size:10px;color:var(--text2)">${j.message || ''}</span></td>
      <td class="mono">${typeof count === 'number' ? count.toLocaleString() : count}</td>
      <td style="font-size:10px">${fmt(j.created_at)}</td>
      <td style="font-size:10px">${dur(j.created_at, j.finished_at)}</td>
      <td><a class="link-btn" href="/viewer/jobs/${j.job_id}">Ver logs →</a></td>
    </tr>`;
  }).join('');
}

async function load() {
  try {
    const r = await fetchWithTimeout('/api/jobs?limit=100');
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    allJobs = d.jobs || [];
    renderStats();
    renderTable();
    document.getElementById('last-update').textContent = 'Actualizado: ' + new Date().toLocaleTimeString();
  } catch (e) {
    document.getElementById('last-update').textContent = 'Error: ' + e.message;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('btn-refresh').addEventListener('click', load);
  document.getElementById('filter-status').addEventListener('change', renderTable);
  load();
  setInterval(() => { if (document.getElementById('auto-refresh').checked) load(); }, 5000);
});
