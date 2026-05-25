-- 83_sap_hcm_apps_seed.sql
-- Seeds SAP HCM analytic apps into analytic_apps.
-- analytic_apps (created in 08_workspace_ownership.sql) has NO workspace_id column:
-- apps are workspace-agnostic and scoped by cartridge_id + visibility + the
-- workspace-scoped datasets they reference (datasets table, migration 80).
-- Mirrors the Replicon app seed in 10_replicon_gold_seed.sql. HTML is embedded
-- inline; console seed_packaged_apps.py later reconciles from the source files.

INSERT INTO analytic_apps (name, title, html, description, cartridge_id, visibility, datasets_used, updated_at)
VALUES ($seed$sap_hcm_headcount_dashboard$seed$, $seed$Headcount Dashboard$seed$, $seed$<!DOCTYPE html>
<!--
  app_id: sap_hcm_headcount_dashboard
  name: Headcount Dashboard
  description: Panorama operativo de plantilla activa por departamento, centro de costo y tipo de posicion (SAP HCM)
  cartridge: sap_hcm
  datasets: [headcount_by_department, headcount_by_costcenter, headcount_by_position_type]
-->
<html lang="es" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Headcount Dashboard — SAP HCM</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  :root{
    --bg:#0d1117; --bg2:#161b22; --border:#30363d;
    --text1:#e6edf3; --text2:#8b949e; --text3:#6e7681;
    --blue:#7c9fff; --green:#3fb950; --amber:#d29922; --red:#f85149; --purple:#bc8cff; --cyan:#39d3ff;
  }
  *{box-sizing:border-box}
  body{margin:0;padding:22px;background:var(--bg);color:var(--text1);
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px}
  h1{font-size:18px;margin:0 0 2px;font-weight:700}
  .subtitle{color:var(--text2);font-size:11px;letter-spacing:.04em;margin-bottom:18px}
  .controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:18px}
  .controls label{color:var(--text2);font-size:10px;text-transform:uppercase;letter-spacing:1px;font-weight:600}
  select{background:var(--bg2);color:var(--text1);border:1px solid var(--border);border-radius:6px;
         padding:7px 10px;font-size:12px;min-width:220px}
  button{background:var(--bg2);color:var(--blue);border:1px solid var(--blue);border-radius:6px;
         padding:7px 14px;font-size:11px;font-weight:600;cursor:pointer;letter-spacing:.5px}
  button:hover{background:rgba(124,159,255,.12)}
  .kpis{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:20px}
  .kpi-card{background:var(--bg2);border:1px solid var(--border);border-left:3px solid var(--accent,var(--blue));
            border-radius:8px;padding:16px 20px;min-width:180px}
  .kpi-label{font-size:9px;color:var(--text2);letter-spacing:1.5px;font-weight:600;margin-bottom:8px;text-transform:uppercase}
  .kpi-val{font-size:26px;font-weight:700;line-height:1}
  .kpi-sub{font-size:10px;color:var(--text3);margin-top:6px}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  .section{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px}
  .section.full{grid-column:1 / -1}
  .section-title{font-size:11px;color:var(--text2);text-transform:uppercase;letter-spacing:1px;
                 font-weight:600;margin-bottom:14px}
  .chart-wrap{position:relative;height:300px}
  .empty{padding:28px;text-align:center;color:var(--text3);font-style:italic}
  .error{background:rgba(248,81,73,.12);color:var(--red);border:1px solid var(--red);
         padding:14px;border-radius:6px;margin-bottom:16px}
  @media(max-width:880px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
  <h1>◆ Headcount Dashboard</h1>
  <div class="subtitle">SAP HCM · plantilla activa · ultimo snapshot disponible</div>

  <div id="error"></div>

  <div class="controls">
    <label for="dept-filter">Departamento</label>
    <select id="dept-filter"><option value="">Todos los departamentos</option></select>
    <button onclick="resetFilter()">RESET</button>
  </div>

  <div class="kpis" id="kpis"></div>

  <div class="grid">
    <div class="section">
      <div class="section-title">Top 10 departamentos por headcount</div>
      <div class="chart-wrap"><canvas id="chart-dept"></canvas></div>
    </div>
    <div class="section">
      <div class="section-title">Top 10 centros de costo por headcount (organizacion completa)</div>
      <div class="chart-wrap"><canvas id="chart-cc"></canvas></div>
    </div>
    <div class="section full">
      <div class="section-title">Distribucion por tipo de posicion (organizacion completa)</div>
      <div class="chart-wrap" style="height:320px"><canvas id="chart-pos"></canvas></div>
    </div>
  </div>

<script>
const COLORS = ['#7c9fff','#3fb950','#d29922','#bc8cff','#39d3ff','#f85149','#db61a2','#56d4dd','#e3b341','#a5d6ff'];
const fmtN = n => n==null ? '—' : Number(n).toLocaleString('es-MX');
const charts = {};

function showError(msg){ document.getElementById('error').innerHTML = `<div class="error">Error cargando datos: ${msg}</div>`; }

async function fetchJson(url){
  const r = await fetch(url);
  if(!r.ok) throw new Error(url + ' (' + r.status + ')');
  const d = await r.json();
  return Array.isArray(d) ? d : (d.data || []);
}

// Keep only rows of the most recent snapshot_month (headcount is point-in-time).
function latestSnapshot(rows){
  if(!rows.length) return [];
  const months = rows.map(r => String(r.snapshot_month || '')).filter(Boolean);
  if(!months.length) return rows;
  const max = months.sort().at(-1);
  return rows.filter(r => String(r.snapshot_month || '') === max);
}

let DEPT = [], CC = [], POS = [];

function renderKPIs(deptRows){
  const total = deptRows.reduce((s,r)=> s + Number(r.headcount||0), 0);
  const sel = document.getElementById('dept-filter').value;
  const label = sel ? '1 departamento' : `${deptRows.length} departamentos`;
  document.getElementById('kpis').innerHTML = `
    <div class="kpi-card" style="--accent:var(--green)">
      <div class="kpi-label">Headcount activo${sel ? ' (filtrado)' : ''}</div>
      <div class="kpi-val">${fmtN(total)}</div>
      <div class="kpi-sub">${label}</div>
    </div>
    <div class="kpi-card" style="--accent:var(--blue)">
      <div class="kpi-label">Centros de costo</div>
      <div class="kpi-val">${fmtN(CC.length)}</div>
      <div class="kpi-sub">con plantilla activa</div>
    </div>
    <div class="kpi-card" style="--accent:var(--purple)">
      <div class="kpi-label">Tipos de posicion</div>
      <div class="kpi-val">${fmtN(POS.length)}</div>
      <div class="kpi-sub">grupo / subgrupo</div>
    </div>`;
}

function hbar(canvasId, labels, values, color){
  if(charts[canvasId]) charts[canvasId].destroy();
  const ctx = document.getElementById(canvasId);
  if(!labels.length){ ctx.parentElement.innerHTML = '<div class="empty">Sin datos</div>'; return; }
  charts[canvasId] = new Chart(ctx, {
    type:'bar',
    data:{ labels, datasets:[{ data:values, backgroundColor:color, borderRadius:4 }] },
    options:{
      indexAxis:'y', responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{display:false} },
      scales:{
        x:{ ticks:{color:'#8b949e'}, grid:{color:'#21262d'} },
        y:{ ticks:{color:'#e6edf3'}, grid:{display:false} }
      }
    }
  });
}

function renderDeptChart(deptRows){
  const top = [...deptRows].sort((a,b)=> Number(b.headcount)-Number(a.headcount)).slice(0,10);
  hbar('chart-dept', top.map(r=> r.org_name || r.org_id || '—'), top.map(r=> Number(r.headcount||0)), '#7c9fff');
}

function renderCcChart(){
  const top = [...CC].sort((a,b)=> Number(b.headcount)-Number(a.headcount)).slice(0,10);
  hbar('chart-cc', top.map(r=> r.cost_center || '(sin centro)'), top.map(r=> Number(r.headcount||0)), '#3fb950');
}

function renderPosChart(){
  if(charts['chart-pos']) charts['chart-pos'].destroy();
  const ctx = document.getElementById('chart-pos');
  if(!POS.length){ ctx.parentElement.innerHTML = '<div class="empty">Sin datos</div>'; return; }
  const agg = {};
  POS.forEach(r=>{
    const key = [r.employee_group || '(sin grupo)', r.employee_subgroup].filter(Boolean).join(' / ');
    agg[key] = (agg[key]||0) + Number(r.headcount||0);
  });
  const labels = Object.keys(agg), values = labels.map(k=> agg[k]);
  charts['chart-pos'] = new Chart(ctx, {
    type:'doughnut',
    data:{ labels, datasets:[{ data:values, backgroundColor:labels.map((_,i)=> COLORS[i%COLORS.length]), borderColor:'#0d1117', borderWidth:2 }] },
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{position:'right', labels:{color:'#e6edf3', font:{size:11}}} } }
  });
}

function applyFilter(){
  const sel = document.getElementById('dept-filter').value;
  const deptRows = sel ? DEPT.filter(r=> String(r.org_id||r.org_name) === sel) : DEPT;
  renderKPIs(deptRows);
  renderDeptChart(deptRows);
}
function resetFilter(){ document.getElementById('dept-filter').value = ''; applyFilter(); }

async function init(){
  try{
    const [dept, cc, pos] = await Promise.all([
      fetchJson('/api/data/headcount_by_department'),
      fetchJson('/api/data/headcount_by_costcenter'),
      fetchJson('/api/data/headcount_by_position_type'),
    ]);
    DEPT = latestSnapshot(dept);
    CC = latestSnapshot(cc);
    POS = latestSnapshot(pos);

    const opts = [...DEPT].sort((a,b)=> Number(b.headcount)-Number(a.headcount))
      .map(r=> `<option value="${r.org_id||r.org_name}">${r.org_name || r.org_id}</option>`).join('');
    document.getElementById('dept-filter').insertAdjacentHTML('beforeend', opts);
    document.getElementById('dept-filter').addEventListener('change', applyFilter);

    renderKPIs(DEPT);
    renderDeptChart(DEPT);
    renderCcChart();
    renderPosChart();
  }catch(e){ showError(e.message); }
}
init();
</script>
</body>
</html>
$seed$, $seed$Panorama operativo de plantilla activa por departamento, centro de costo y tipo de posicion.$seed$, $seed$sap_hcm$seed$, $seed$shared$seed$, $seed${headcount_by_department,headcount_by_costcenter,headcount_by_position_type}$seed$::text[], NOW())
ON CONFLICT (name) DO NOTHING;

INSERT INTO analytic_apps (name, title, html, description, cartridge_id, visibility, datasets_used, updated_at)
VALUES ($seed$sap_hcm_people_quality_dashboard$seed$, $seed$People Quality Dashboard$seed$, $seed$<!DOCTYPE html>
<!--
  app_id: sap_hcm_people_quality_dashboard
  name: People Quality Dashboard
  description: Salud operativa de la plantilla: anomalias de datos, tendencia de ausencias y span of control de managers (SAP HCM)
  cartridge: sap_hcm
  datasets: [employees_anomalies, absence_by_type_and_month, manager_hierarchy]
-->
<html lang="es" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>People Quality Dashboard — SAP HCM</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  :root{
    --bg:#0d1117; --bg2:#161b22; --border:#30363d;
    --text1:#e6edf3; --text2:#8b949e; --text3:#6e7681;
    --blue:#7c9fff; --green:#3fb950; --amber:#d29922; --red:#f85149; --purple:#bc8cff; --cyan:#39d3ff;
  }
  *{box-sizing:border-box}
  body{margin:0;padding:22px;background:var(--bg);color:var(--text1);
       font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;font-size:13px}
  h1{font-size:18px;margin:0 0 2px;font-weight:700}
  .subtitle{color:var(--text2);font-size:11px;letter-spacing:.04em;margin-bottom:18px}
  .controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:18px}
  .controls label{color:var(--text2);font-size:10px;text-transform:uppercase;letter-spacing:1px;font-weight:600}
  select{background:var(--bg2);color:var(--text1);border:1px solid var(--border);border-radius:6px;
         padding:7px 10px;font-size:12px;min-width:220px}
  button{background:var(--bg2);color:var(--blue);border:1px solid var(--blue);border-radius:6px;
         padding:7px 14px;font-size:11px;font-weight:600;cursor:pointer;letter-spacing:.5px}
  button:hover{background:rgba(124,159,255,.12)}
  .kpis{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:20px}
  .kpi-card{background:var(--bg2);border:1px solid var(--border);border-left:3px solid var(--accent,var(--blue));
            border-radius:8px;padding:16px 20px;min-width:175px;flex:1}
  .kpi-label{font-size:9px;color:var(--text2);letter-spacing:1.5px;font-weight:600;margin-bottom:8px;text-transform:uppercase}
  .kpi-val{font-size:26px;font-weight:700;line-height:1}
  .kpi-sub{font-size:10px;color:var(--text3);margin-top:6px}
  .grid{display:grid;grid-template-columns:1fr 1fr;gap:16px}
  .section{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:16px}
  .section.full{grid-column:1 / -1}
  .section-title{font-size:11px;color:var(--text2);text-transform:uppercase;letter-spacing:1px;
                 font-weight:600;margin-bottom:14px}
  .chart-wrap{position:relative;height:300px}
  table{width:100%;border-collapse:collapse;font-size:12px}
  th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border)}
  th{color:var(--text2);font-size:10px;text-transform:uppercase;letter-spacing:.5px}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  .badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10px;font-weight:600}
  .sev-high{background:rgba(248,81,73,.15);color:var(--red)}
  .sev-medium{background:rgba(210,153,34,.15);color:var(--amber)}
  .sev-low{background:rgba(63,185,80,.15);color:var(--green)}
  .empty{padding:28px;text-align:center;color:var(--text3);font-style:italic}
  .error{background:rgba(248,81,73,.12);color:var(--red);border:1px solid var(--red);
         padding:14px;border-radius:6px;margin-bottom:16px}
  @media(max-width:880px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
  <h1>◆ People Quality Dashboard</h1>
  <div class="subtitle">SAP HCM · anomalias · ausencias · span of control</div>

  <div id="error"></div>

  <div class="controls">
    <label for="anomaly-filter">Tipo de anomalia</label>
    <select id="anomaly-filter"><option value="">Todas las anomalias</option></select>
    <button onclick="resetFilter()">RESET</button>
  </div>

  <div class="kpis" id="kpis"></div>

  <div class="grid">
    <div class="section">
      <div class="section-title">Anomalias por tipo y severidad</div>
      <div id="anomaly-table"></div>
    </div>
    <div class="section">
      <div class="section-title">Distribucion de span of control (reportes directos)</div>
      <div class="chart-wrap"><canvas id="chart-span"></canvas></div>
    </div>
    <div class="section full">
      <div class="section-title">Tendencia mensual de ausencias por tipo (dias habiles)</div>
      <div class="chart-wrap" style="height:320px"><canvas id="chart-absence"></canvas></div>
    </div>
  </div>

<script>
const COLORS = ['#7c9fff','#3fb950','#d29922','#bc8cff','#39d3ff','#f85149','#db61a2','#56d4dd','#e3b341','#a5d6ff'];
const fmtN = n => n==null ? '—' : Number(n).toLocaleString('es-MX');
const fmtD = n => n==null ? '—' : Number(n).toLocaleString('es-MX',{minimumFractionDigits:1,maximumFractionDigits:1});
const charts = {};

function showError(msg){ document.getElementById('error').innerHTML = `<div class="error">Error cargando datos: ${msg}</div>`; }
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

async function fetchJson(url){
  const r = await fetch(url);
  if(!r.ok) throw new Error(url + ' (' + r.status + ')');
  const d = await r.json();
  return Array.isArray(d) ? d : (d.data || []);
}

let ANOM = [], ABS = [], MGR = [];

function currentYear(){ return new Date().getFullYear(); }

function renderKPIs(anomRows){
  const yr = String(currentYear());
  const absYtd = ABS.filter(r=> String(r.absence_month||'').startsWith(yr))
                    .reduce((s,r)=> s + Number(r.total_days_workable||0), 0);
  const managers = MGR.filter(r=> Number(r.direct_reports||0) > 0);
  const avgSpan = managers.length
    ? managers.reduce((s,r)=> s + Number(r.direct_reports||0), 0) / managers.length : null;
  document.getElementById('kpis').innerHTML = `
    <div class="kpi-card" style="--accent:var(--red)">
      <div class="kpi-label">Anomalias${document.getElementById('anomaly-filter').value ? ' (filtradas)' : ''}</div>
      <div class="kpi-val">${fmtN(anomRows.length)}</div>
      <div class="kpi-sub">registros detectados</div>
    </div>
    <div class="kpi-card" style="--accent:var(--amber)">
      <div class="kpi-label">Ausencias YTD ${yr}</div>
      <div class="kpi-val">${fmtD(absYtd)}</div>
      <div class="kpi-sub">dias habiles</div>
    </div>
    <div class="kpi-card" style="--accent:var(--blue)">
      <div class="kpi-label">Empleados en jerarquia</div>
      <div class="kpi-val">${fmtN(MGR.length)}</div>
      <div class="kpi-sub">${managers.length} con reportes</div>
    </div>
    <div class="kpi-card" style="--accent:var(--purple)">
      <div class="kpi-label">Span promedio</div>
      <div class="kpi-val">${avgSpan==null ? '—' : fmtD(avgSpan)}</div>
      <div class="kpi-sub">${managers.length ? 'reportes / manager' : 'pendiente HRP1001'}</div>
    </div>`;
}

function renderAnomalyTable(anomRows){
  const el = document.getElementById('anomaly-table');
  if(!anomRows.length){ el.innerHTML = '<div class="empty">Sin anomalias</div>'; return; }
  const agg = {};
  anomRows.forEach(r=>{
    const k = (r.anomaly_type||'(sin tipo)') + '||' + (r.severity||'—');
    agg[k] = (agg[k]||0) + 1;
  });
  const rows = Object.entries(agg).map(([k,c])=>{ const [t,s]=k.split('||'); return {t,s,c}; })
    .sort((a,b)=> b.c - a.c);
  el.innerHTML = `<table><thead><tr><th>Tipo</th><th>Severidad</th><th class="num">Casos</th></tr></thead><tbody>${
    rows.map(r=>{
      const sev = String(r.s).toLowerCase();
      const cls = sev==='high'?'sev-high':sev==='medium'?'sev-medium':sev==='low'?'sev-low':'';
      return `<tr><td>${esc(r.t)}</td><td><span class="badge ${cls}">${esc(r.s)}</span></td><td class="num">${fmtN(r.c)}</td></tr>`;
    }).join('')
  }</tbody></table>`;
}

function renderSpanChart(){
  if(charts['chart-span']) charts['chart-span'].destroy();
  const ctx = document.getElementById('chart-span');
  const managers = MGR.filter(r=> Number(r.direct_reports||0) > 0);
  if(!managers.length){ ctx.parentElement.innerHTML = '<div class="empty">Sin jerarquia de managers (pendiente extraccion HRP1001 / Sbrtr)</div>'; return; }
  const buckets = {'1-3':0,'4-6':0,'7-10':0,'11+':0};
  managers.forEach(r=>{ const n = Number(r.direct_reports||0);
    if(n<=3) buckets['1-3']++; else if(n<=6) buckets['4-6']++; else if(n<=10) buckets['7-10']++; else buckets['11+']++; });
  charts['chart-span'] = new Chart(ctx, {
    type:'bar',
    data:{ labels:Object.keys(buckets), datasets:[{ label:'Managers', data:Object.values(buckets), backgroundColor:'#bc8cff', borderRadius:4 }] },
    options:{ responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}},
      scales:{ x:{ticks:{color:'#e6edf3'},grid:{display:false}}, y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}} } }
  });
}

function renderAbsenceChart(){
  if(charts['chart-absence']) charts['chart-absence'].destroy();
  const ctx = document.getElementById('chart-absence');
  if(!ABS.length){ ctx.parentElement.innerHTML = '<div class="empty">Sin datos de ausencias</div>'; return; }
  const months = [...new Set(ABS.map(r=> String(r.absence_month||'')))].filter(Boolean).sort();
  const types = [...new Set(ABS.map(r=> r.absence_type||'—'))];
  const datasets = types.map((t,i)=>({
    label: String(t),
    data: months.map(m=>{
      const row = ABS.find(r=> String(r.absence_month)===m && (r.absence_type||'—')===t);
      return row ? Number(row.total_days_workable||0) : 0;
    }),
    borderColor: COLORS[i%COLORS.length], backgroundColor: COLORS[i%COLORS.length], tension:.25, fill:false
  }));
  charts['chart-absence'] = new Chart(ctx, {
    type:'line',
    data:{ labels:months, datasets },
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{position:'top', labels:{color:'#e6edf3', font:{size:11}}} },
      scales:{ x:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}}, y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}} } }
  });
}

function applyFilter(){
  const sel = document.getElementById('anomaly-filter').value;
  const rows = sel ? ANOM.filter(r=> (r.anomaly_type||'') === sel) : ANOM;
  renderKPIs(rows);
  renderAnomalyTable(rows);
}
function resetFilter(){ document.getElementById('anomaly-filter').value = ''; applyFilter(); }

async function init(){
  try{
    const [anom, abs, mgr] = await Promise.all([
      fetchJson('/api/data/employees_anomalies'),
      fetchJson('/api/data/absence_by_type_and_month'),
      fetchJson('/api/data/manager_hierarchy'),
    ]);
    ANOM = anom; ABS = abs; MGR = mgr;

    const types = [...new Set(ANOM.map(r=> r.anomaly_type).filter(Boolean))].sort();
    document.getElementById('anomaly-filter').insertAdjacentHTML('beforeend',
      types.map(t=> `<option value="${esc(t)}">${esc(t)}</option>`).join(''));
    document.getElementById('anomaly-filter').addEventListener('change', applyFilter);

    renderKPIs(ANOM);
    renderAnomalyTable(ANOM);
    renderSpanChart();
    renderAbsenceChart();
  }catch(e){ showError(e.message); }
}
init();
</script>
</body>
</html>
$seed$, $seed$Salud operativa de la plantilla: anomalias de datos, tendencia de ausencias y span of control de managers.$seed$, $seed$sap_hcm$seed$, $seed$shared$seed$, $seed${employees_anomalies,absence_by_type_and_month,manager_hierarchy}$seed$::text[], NOW())
ON CONFLICT (name) DO NOTHING;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('83_sap_hcm_apps_seed.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
