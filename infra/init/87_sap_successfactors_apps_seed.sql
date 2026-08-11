-- 87_sap_successfactors_apps_seed.sql
-- Seeds SAP SuccessFactors analytic apps into analytic_apps.
-- analytic_apps (created in 08_workspace_ownership.sql) has NO workspace_id column:
-- apps are workspace-agnostic and scoped by cartridge_id + visibility + the
-- workspace-scoped datasets they reference (datasets table, migration 82).
-- Mirrors 83_sap_hcm_apps_seed.sql / 85_sap_s4hana_apps_seed.sql. HTML is embedded
-- inline; console seed_packaged_apps.py later reconciles from the source files.

INSERT INTO analytic_apps (name, title, html, description, cartridge_id, visibility, datasets_used, updated_at)
VALUES ($seed$sap_successfactors_workforce_overview$seed$, $seed$Workforce Overview$seed$, $seed$<!DOCTYPE html>
<!--
  app_id: sap_successfactors_workforce_overview
  name: Workforce Overview
  description: Dashboard ejecutivo de plantilla. Headcount por departamento, ubicacion y compania, y rotacion mensual (SAP SuccessFactors)
  cartridge: sap_successfactors
  datasets: [sap_successfactors_headcount_by_department, sap_successfactors_headcount_by_location, sap_successfactors_headcount_by_company, sap_successfactors_turnover_by_period]
-->
<html lang="es" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Workforce Overview — SAP SuccessFactors</title>
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
         padding:7px 10px;font-size:12px;min-width:200px}
  button{background:var(--bg2);color:var(--blue);border:1px solid var(--blue);border-radius:6px;
         padding:7px 14px;font-size:11px;font-weight:600;cursor:pointer;letter-spacing:.5px}
  button:hover{background:rgba(124,159,255,.12)}
  .kpis{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:20px}
  .kpi-card{background:var(--bg2);border:1px solid var(--border);border-left:3px solid var(--accent,var(--blue));
            border-radius:8px;padding:16px 20px;min-width:175px;flex:1}
  .kpi-label{font-size:9px;color:var(--text2);letter-spacing:1.5px;font-weight:600;margin-bottom:8px;text-transform:uppercase}
  .kpi-val{font-size:24px;font-weight:700;line-height:1}
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
  <h1>◆ Workforce Overview</h1>
  <div class="subtitle">SAP SuccessFactors · plantilla por departamento, ubicación y compañía · rotación</div>

  <div id="error"></div>

  <div class="controls">
    <label for="company-filter">Compañía</label>
    <select id="company-filter"><option value="">Todas las compañías</option></select>
    <button onclick="resetFilter()">RESET</button>
  </div>

  <div class="kpis" id="kpis"></div>

  <div class="grid">
    <div class="section">
      <div class="section-title">Top 10 departamentos por headcount</div>
      <div class="chart-wrap"><canvas id="chart-dept"></canvas></div>
    </div>
    <div class="section">
      <div class="section-title">Distribución por compañía</div>
      <div class="chart-wrap"><canvas id="chart-company"></canvas></div>
    </div>
    <div class="section">
      <div class="section-title">Top 10 ubicaciones por headcount</div>
      <div class="chart-wrap"><canvas id="chart-location"></canvas></div>
    </div>
    <div class="section">
      <div class="section-title">Rotación mensual (bajas)</div>
      <div class="chart-wrap"><canvas id="chart-turnover"></canvas></div>
    </div>
  </div>

<script>
const COLORS = ['#7c9fff','#3fb950','#d29922','#bc8cff','#39d3ff','#f85149','#db61a2','#56d4dd','#e3b341','#a5d6ff'];
const fmtN = n => n==null ? '—' : Number(n).toLocaleString('es-MX');
const charts = {};
function showError(msg){ document.getElementById('error').innerHTML = `<div class="error">Error cargando datos: ${msg}</div>`; }
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

async function fetchJson(url){
  const r = await fetch(url);
  if(!r.ok) throw new Error(url + ' (' + r.status + ')');
  const d = await r.json();
  return Array.isArray(d) ? d : (d.data || []);
}
async function fetchOptional(url){
  try { return await fetchJson(url); }
  catch(e) { console.warn('dataset opcional no disponible', url, e.message); return []; }
}

let DEPT = [], LOC = [], COMP = [], TURN = [];

function hbar(canvasId, labels, values, color){
  if(charts[canvasId]) charts[canvasId].destroy();
  const ctx = document.getElementById(canvasId);
  if(!labels.length){ ctx.parentElement.innerHTML = '<div class="empty">Sin datos</div>'; return; }
  charts[canvasId] = new Chart(ctx, {
    type:'bar',
    data:{ labels, datasets:[{ data:values, backgroundColor:color, borderRadius:4 }] },
    options:{ indexAxis:'y', responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}},
      scales:{ x:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}}, y:{ticks:{color:'#e6edf3'},grid:{display:false}} } }
  });
}

function latestYearTurnover(){
  const years = TURN.map(r=> String(r.termination_month||'').slice(0,4)).filter(Boolean);
  if(!years.length) return 0;
  const max = years.sort().at(-1);
  return TURN.filter(r=> String(r.termination_month||'').startsWith(max)).reduce((s,r)=> s + Number(r.terminations||0), 0);
}

function renderKPIs(compRows){
  const headcount = compRows.reduce((s,r)=> s + Number(r.headcount||0), 0);
  document.getElementById('kpis').innerHTML = `
    <div class="kpi-card" style="--accent:var(--green)">
      <div class="kpi-label">Headcount activo</div>
      <div class="kpi-val">${fmtN(headcount)}</div>
      <div class="kpi-sub">${compRows.length} compañía(s)</div>
    </div>
    <div class="kpi-card" style="--accent:var(--blue)">
      <div class="kpi-label">Departamentos</div>
      <div class="kpi-val">${fmtN(DEPT.length)}</div>
      <div class="kpi-sub">con plantilla activa</div>
    </div>
    <div class="kpi-card" style="--accent:var(--purple)">
      <div class="kpi-label">Ubicaciones</div>
      <div class="kpi-val">${fmtN(LOC.length)}</div>
      <div class="kpi-sub">activas</div>
    </div>
    <div class="kpi-card" style="--accent:var(--red)">
      <div class="kpi-label">Rotación YTD</div>
      <div class="kpi-val">${fmtN(latestYearTurnover())}</div>
      <div class="kpi-sub">bajas (último año)</div>
    </div>`;
}

function renderDeptChart(){
  const top = [...DEPT].sort((a,b)=> Number(b.headcount)-Number(a.headcount)).slice(0,10);
  hbar('chart-dept', top.map(r=> r.department_name || r.department_id || '(sin depto)'), top.map(r=> Number(r.headcount||0)), '#7c9fff');
}

function renderLocationChart(){
  const top = [...LOC].sort((a,b)=> Number(b.headcount)-Number(a.headcount)).slice(0,10);
  hbar('chart-location', top.map(r=> r.location_name || r.location_id || '(sin ubicación)'), top.map(r=> Number(r.headcount||0)), '#39d3ff');
}

function renderCompanyChart(compRows){
  if(charts['chart-company']) charts['chart-company'].destroy();
  const ctx = document.getElementById('chart-company');
  if(!compRows.length){ ctx.parentElement.innerHTML = '<div class="empty">Sin datos</div>'; return; }
  const labels = compRows.map(r=> r.company_name || r.company_id || '(sin compañía)');
  charts['chart-company'] = new Chart(ctx, {
    type:'doughnut',
    data:{ labels, datasets:[{ data:compRows.map(r=> Number(r.headcount||0)), backgroundColor:labels.map((_,i)=>COLORS[i%COLORS.length]), borderColor:'#0d1117', borderWidth:2 }] },
    options:{ responsive:true, maintainAspectRatio:false, plugins:{ legend:{position:'right', labels:{color:'#e6edf3', font:{size:11}}} } }
  });
}

function renderTurnoverChart(){
  if(charts['chart-turnover']) charts['chart-turnover'].destroy();
  const ctx = document.getElementById('chart-turnover');
  if(!TURN.length){ ctx.parentElement.innerHTML = '<div class="empty">Sin datos de rotación en la ventana extraída</div>'; return; }
  const agg = {};
  TURN.forEach(r=>{ const m = String(r.termination_month||''); agg[m] = (agg[m]||0) + Number(r.terminations||0); });
  const months = Object.keys(agg).filter(Boolean).sort();
  charts['chart-turnover'] = new Chart(ctx, {
    type:'line',
    data:{ labels:months, datasets:[{ label:'Bajas', data:months.map(m=>agg[m]), borderColor:'#f85149', backgroundColor:'#f85149', tension:.25, fill:false }] },
    options:{ responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}},
      scales:{ x:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}}, y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}} } }
  });
}

function applyFilter(){
  const sel = document.getElementById('company-filter').value;
  const compRows = sel ? COMP.filter(r=> String(r.company_id||r.company_name) === sel) : COMP;
  renderKPIs(compRows);
  renderCompanyChart(compRows);
}
function resetFilter(){ document.getElementById('company-filter').value = ''; applyFilter(); }

async function init(){
  try{
    const [dept, loc, comp, turn] = await Promise.all([
      fetchJson('/api/data/sap_successfactors_headcount_by_department'),
      fetchJson('/api/data/sap_successfactors_headcount_by_location'),
      fetchJson('/api/data/sap_successfactors_headcount_by_company'),
      fetchOptional('/api/data/sap_successfactors_turnover_by_period'),
    ]);
    DEPT = dept; LOC = loc; COMP = comp; TURN = turn;

    const companies = [...new Set(COMP.map(r=> String(r.company_id||r.company_name||'')).filter(Boolean))];
    document.getElementById('company-filter').insertAdjacentHTML('beforeend',
      COMP.map(r=> `<option value="${esc(r.company_id||r.company_name)}">${esc(r.company_name||r.company_id)}</option>`).join(''));
    document.getElementById('company-filter').addEventListener('change', applyFilter);

    renderKPIs(COMP);
    renderDeptChart();
    renderCompanyChart(COMP);
    renderLocationChart();
    renderTurnoverChart();
  }catch(e){ showError(e.message); }
}
init();
</script>
</body>
</html>
$seed$, $seed$Dashboard ejecutivo de plantilla: headcount por departamento, ubicacion y compania, y rotacion mensual.$seed$, $seed$sap_successfactors$seed$, $seed$shared$seed$, $seed${sap_successfactors_headcount_by_department,sap_successfactors_headcount_by_location,sap_successfactors_headcount_by_company,sap_successfactors_turnover_by_period}$seed$::text[], NOW())
ON CONFLICT (name) DO UPDATE
SET title = EXCLUDED.title,
    html = EXCLUDED.html,
    description = EXCLUDED.description,
    cartridge_id = EXCLUDED.cartridge_id,
    visibility = EXCLUDED.visibility,
    datasets_used = EXCLUDED.datasets_used,
    updated_at = EXCLUDED.updated_at;

INSERT INTO analytic_apps (name, title, html, description, cartridge_id, visibility, datasets_used, updated_at)
VALUES ($seed$sap_successfactors_talent_health$seed$, $seed$Talent Health$seed$, $seed$<!DOCTYPE html>
<!--
  app_id: sap_successfactors_talent_health
  name: Talent Health
  description: Salud de talento. Anomalias de datos, embudo de reclutamiento y span of control (SAP SuccessFactors)
  cartridge: sap_successfactors
  datasets: [sap_successfactors_employees_anomalies, sap_successfactors_recruitment_funnel, sap_successfactors_manager_hierarchy]
-->
<html lang="es" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Talent Health — SAP SuccessFactors</title>
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
         padding:7px 10px;font-size:12px;min-width:200px}
  button{background:var(--bg2);color:var(--blue);border:1px solid var(--blue);border-radius:6px;
         padding:7px 14px;font-size:11px;font-weight:600;cursor:pointer;letter-spacing:.5px}
  button:hover{background:rgba(124,159,255,.12)}
  .kpis{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:20px}
  .kpi-card{background:var(--bg2);border:1px solid var(--border);border-left:3px solid var(--accent,var(--blue));
            border-radius:8px;padding:16px 20px;min-width:165px;flex:1}
  .kpi-label{font-size:9px;color:var(--text2);letter-spacing:1.5px;font-weight:600;margin-bottom:8px;text-transform:uppercase}
  .kpi-val{font-size:24px;font-weight:700;line-height:1}
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
  <h1>◆ Talent Health</h1>
  <div class="subtitle">SAP SuccessFactors · anomalías · reclutamiento · span of control</div>

  <div id="error"></div>

  <div class="controls">
    <label for="anomaly-filter">Tipo de anomalía</label>
    <select id="anomaly-filter"><option value="">Todas las anomalías</option></select>
    <button onclick="resetFilter()">RESET</button>
  </div>

  <div class="kpis" id="kpis"></div>

  <div class="grid">
    <div class="section">
      <div class="section-title">Anomalías por tipo y severidad</div>
      <div id="anomaly-table"></div>
    </div>
    <div class="section">
      <div class="section-title">Distribución de span of control (reportes directos)</div>
      <div class="chart-wrap"><canvas id="chart-span"></canvas></div>
    </div>
    <div class="section full">
      <div class="section-title">Requisiciones por departamento (total vs abiertas)</div>
      <div class="chart-wrap" style="height:320px"><canvas id="chart-recruit"></canvas></div>
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
async function fetchOptional(url){
  try { return await fetchJson(url); }
  catch(e) { console.warn('dataset opcional no disponible', url, e.message); return []; }
}

let ANOM = [], FUNNEL = [], MGR = [];

function renderKPIs(anomRows){
  const openReq = FUNNEL.reduce((s,r)=> s + Number(r.open_requisitions||0), 0);
  const managers = MGR.filter(r=> Number(r.direct_reports||0) > 0);
  const avgSpan = managers.length ? managers.reduce((s,r)=> s + Number(r.direct_reports||0), 0) / managers.length : null;
  const depths = MGR.map(r=> Number(r.depth||0)).filter(n=> n>=0);
  const avgDepth = depths.length ? depths.reduce((a,b)=>a+b,0)/depths.length : null;
  document.getElementById('kpis').innerHTML = `
    <div class="kpi-card" style="--accent:var(--red)">
      <div class="kpi-label">Anomalías${document.getElementById('anomaly-filter').value ? ' (filtradas)' : ''}</div>
      <div class="kpi-val">${fmtN(anomRows.length)}</div>
      <div class="kpi-sub">registros detectados</div>
    </div>
    <div class="kpi-card" style="--accent:var(--amber)">
      <div class="kpi-label">Requisiciones abiertas</div>
      <div class="kpi-val">${fmtN(openReq)}</div>
      <div class="kpi-sub">${fmtN(FUNNEL.reduce((s,r)=>s+Number(r.requisitions||0),0))} totales</div>
    </div>
    <div class="kpi-card" style="--accent:var(--blue)">
      <div class="kpi-label">Managers</div>
      <div class="kpi-val">${fmtN(managers.length)}</div>
      <div class="kpi-sub">con reportes directos</div>
    </div>
    <div class="kpi-card" style="--accent:var(--purple)">
      <div class="kpi-label">Span promedio</div>
      <div class="kpi-val">${avgSpan==null ? '—' : fmtD(avgSpan)}</div>
      <div class="kpi-sub">reportes / manager</div>
    </div>
    <div class="kpi-card" style="--accent:var(--cyan)">
      <div class="kpi-label">Profundidad org</div>
      <div class="kpi-val">${avgDepth==null ? '—' : fmtD(avgDepth)}</div>
      <div class="kpi-sub">nivel promedio</div>
    </div>`;
}

function renderAnomalyTable(anomRows){
  const el = document.getElementById('anomaly-table');
  if(!anomRows.length){ el.innerHTML = '<div class="empty">Sin anomalías</div>'; return; }
  const agg = {};
  anomRows.forEach(r=>{ const k = (r.anomaly_type||'(sin tipo)') + '||' + (r.severity||'—'); agg[k] = (agg[k]||0) + 1; });
  const rows = Object.entries(agg).map(([k,c])=>{ const [t,s]=k.split('||'); return {t,s,c}; }).sort((a,b)=> b.c-a.c);
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
  if(!managers.length){ ctx.parentElement.innerHTML = '<div class="empty">Sin jerarquía de managers</div>'; return; }
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

function renderRecruitChart(){
  if(charts['chart-recruit']) charts['chart-recruit'].destroy();
  const ctx = document.getElementById('chart-recruit');
  if(!FUNNEL.length){ ctx.parentElement.innerHTML = '<div class="empty">Sin requisiciones</div>'; return; }
  const top = [...FUNNEL].sort((a,b)=> Number(b.requisitions)-Number(a.requisitions)).slice(0,10);
  const labels = top.map(r=> r.department || '(sin depto)');
  charts['chart-recruit'] = new Chart(ctx, {
    type:'bar',
    data:{ labels, datasets:[
      { label:'Requisiciones', data:top.map(r=> Number(r.requisitions||0)), backgroundColor:'#7c9fff', borderRadius:4 },
      { label:'Abiertas', data:top.map(r=> Number(r.open_requisitions||0)), backgroundColor:'#d29922', borderRadius:4 },
    ] },
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{position:'top', labels:{color:'#e6edf3', font:{size:11}}} },
      scales:{ x:{ticks:{color:'#8b949e'},grid:{display:false}}, y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}} } }
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
    const [anom, funnel, mgr] = await Promise.all([
      fetchOptional('/api/data/sap_successfactors_employees_anomalies'),
      fetchOptional('/api/data/sap_successfactors_recruitment_funnel'),
      fetchJson('/api/data/sap_successfactors_manager_hierarchy'),
    ]);
    ANOM = anom; FUNNEL = funnel; MGR = mgr;

    const types = [...new Set(ANOM.map(r=> r.anomaly_type).filter(Boolean))].sort();
    document.getElementById('anomaly-filter').insertAdjacentHTML('beforeend',
      types.map(t=> `<option value="${esc(t)}">${esc(t)}</option>`).join(''));
    document.getElementById('anomaly-filter').addEventListener('change', applyFilter);

    renderKPIs(ANOM);
    renderAnomalyTable(ANOM);
    renderSpanChart();
    renderRecruitChart();
  }catch(e){ showError(e.message); }
}
init();
</script>
</body>
</html>
$seed$, $seed$Salud de talento: anomalias de datos, embudo de reclutamiento y span of control de managers.$seed$, $seed$sap_successfactors$seed$, $seed$shared$seed$, $seed${sap_successfactors_employees_anomalies,sap_successfactors_recruitment_funnel,sap_successfactors_manager_hierarchy}$seed$::text[], NOW())
ON CONFLICT (name) DO UPDATE
SET title = EXCLUDED.title,
    html = EXCLUDED.html,
    description = EXCLUDED.description,
    cartridge_id = EXCLUDED.cartridge_id,
    visibility = EXCLUDED.visibility,
    datasets_used = EXCLUDED.datasets_used,
    updated_at = EXCLUDED.updated_at;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('87_sap_successfactors_apps_seed.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
