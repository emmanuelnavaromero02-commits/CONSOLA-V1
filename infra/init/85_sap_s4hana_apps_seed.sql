-- 85_sap_s4hana_apps_seed.sql
-- Seeds SAP S/4HANA analytic apps into analytic_apps.
-- analytic_apps (created in 08_workspace_ownership.sql) has NO workspace_id column:
-- apps are workspace-agnostic and scoped by cartridge_id + visibility + the
-- workspace-scoped datasets they reference (datasets table, migration 81).
-- Mirrors the SAP HCM app seed in 83_sap_hcm_apps_seed.sql. HTML is embedded
-- inline; console seed_packaged_apps.py later reconciles from the source files.

INSERT INTO analytic_apps (name, title, html, description, cartridge_id, visibility, datasets_used, updated_at)
VALUES ($seed$sap_s4hana_sales_overview$seed$, $seed$Sales Overview$seed$, $seed$<!DOCTYPE html>
<!--
  app_id: sap_s4hana_sales_overview
  name: Sales Overview
  description: Dashboard ejecutivo de ventas. Revenue YTD, top customers, backlog y anomalias de partners (SAP S/4HANA)
  cartridge: sap_s4hana
  datasets: [revenue_by_customer, open_sales_orders, business_partner_anomalies]
-->
<html lang="es" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sales Overview — SAP S/4HANA</title>
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
  table{width:100%;border-collapse:collapse;font-size:12px}
  th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border)}
  th{color:var(--text2);font-size:10px;text-transform:uppercase;letter-spacing:.5px}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  .empty{padding:28px;text-align:center;color:var(--text3);font-style:italic}
  .error{background:rgba(248,81,73,.12);color:var(--red);border:1px solid var(--red);
         padding:14px;border-radius:6px;margin-bottom:16px}
  @media(max-width:880px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
  <h1>◆ Sales Overview</h1>
  <div class="subtitle">SAP S/4HANA · ventas · revenue, backlog y calidad de partners</div>

  <div id="error"></div>

  <div class="controls">
    <label for="month-filter">Desde mes</label>
    <select id="month-filter"><option value="">Todo el periodo</option></select>
    <button onclick="resetFilter()">RESET</button>
  </div>

  <div class="kpis" id="kpis"></div>

  <div class="grid">
    <div class="section full">
      <div class="section-title">Revenue mensual</div>
      <div class="chart-wrap" style="height:300px"><canvas id="chart-revenue"></canvas></div>
    </div>
    <div class="section">
      <div class="section-title">Top 10 clientes por revenue</div>
      <div class="chart-wrap"><canvas id="chart-customers"></canvas></div>
    </div>
    <div class="section">
      <div class="section-title">Backlog abierto por antigüedad</div>
      <div class="chart-wrap"><canvas id="chart-backlog"></canvas></div>
    </div>
    <div class="section full">
      <div class="section-title">Anomalías de business partners por tipo y severidad</div>
      <div id="anomaly-table"></div>
    </div>
  </div>

<script>
const COLORS = ['#7c9fff','#3fb950','#d29922','#bc8cff','#39d3ff','#f85149','#db61a2','#56d4dd','#e3b341','#a5d6ff'];
const fmtN = n => n==null ? '—' : Number(n).toLocaleString('es-MX');
const fmtM = n => n==null ? '—' : '$' + Number(n).toLocaleString('es-MX', {maximumFractionDigits:0});
const charts = {};
function showError(msg){ document.getElementById('error').innerHTML = `<div class="error">Error cargando datos: ${msg}</div>`; }
function esc(s){ return String(s==null?'':s).replace(/[&<>"]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c])); }

async function fetchJson(url){
  const r = await fetch(url);
  if(!r.ok) throw new Error(url + ' (' + r.status + ')');
  const d = await r.json();
  return Array.isArray(d) ? d : (d.data || []);
}

let REV = [], ORD = [], ANOM = [];

function renderKPIs(revRows){
  const revenue = revRows.reduce((s,r)=> s + Number(r.revenue||0), 0);
  const customers = new Set(revRows.map(r=> r.customer_code)).size;
  const backlog = ORD.reduce((s,r)=> s + Number(r.open_value||0), 0);
  document.getElementById('kpis').innerHTML = `
    <div class="kpi-card" style="--accent:var(--green)">
      <div class="kpi-label">Revenue (periodo)</div>
      <div class="kpi-val">${fmtM(revenue)}</div>
      <div class="kpi-sub">${revRows.length} registros cliente-mes</div>
    </div>
    <div class="kpi-card" style="--accent:var(--blue)">
      <div class="kpi-label">Clientes con compras</div>
      <div class="kpi-val">${fmtN(customers)}</div>
      <div class="kpi-sub">en el periodo seleccionado</div>
    </div>
    <div class="kpi-card" style="--accent:var(--amber)">
      <div class="kpi-label">Backlog abierto</div>
      <div class="kpi-val">${fmtM(backlog)}</div>
      <div class="kpi-sub">${fmtN(ORD.reduce((s,r)=>s+Number(r.open_orders||0),0))} pedidos abiertos</div>
    </div>
    <div class="kpi-card" style="--accent:var(--red)">
      <div class="kpi-label">Anomalías de partner</div>
      <div class="kpi-val">${fmtN(ANOM.length)}</div>
      <div class="kpi-sub">registros detectados</div>
    </div>`;
}

function renderRevenueLine(revRows){
  if(charts['chart-revenue']) charts['chart-revenue'].destroy();
  const ctx = document.getElementById('chart-revenue');
  if(!revRows.length){ ctx.parentElement.innerHTML = '<div class="empty">Sin datos de revenue</div>'; return; }
  const agg = {};
  revRows.forEach(r=>{ const m = String(r.revenue_month||''); agg[m] = (agg[m]||0) + Number(r.revenue||0); });
  const months = Object.keys(agg).filter(Boolean).sort();
  charts['chart-revenue'] = new Chart(ctx, {
    type:'line',
    data:{ labels:months, datasets:[{ label:'Revenue', data:months.map(m=>agg[m]), borderColor:'#3fb950', backgroundColor:'#3fb950', tension:.25, fill:false }] },
    options:{ responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}},
      scales:{ x:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}}, y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}} } }
  });
}

function renderTopCustomers(revRows){
  if(charts['chart-customers']) charts['chart-customers'].destroy();
  const ctx = document.getElementById('chart-customers');
  if(!revRows.length){ ctx.parentElement.innerHTML = '<div class="empty">Sin datos</div>'; return; }
  const agg = {};
  revRows.forEach(r=>{ const c = r.customer_code || '(sin cliente)'; agg[c] = (agg[c]||0) + Number(r.revenue||0); });
  const top = Object.entries(agg).sort((a,b)=> b[1]-a[1]).slice(0,10);
  charts['chart-customers'] = new Chart(ctx, {
    type:'bar',
    data:{ labels:top.map(t=>t[0]), datasets:[{ data:top.map(t=>t[1]), backgroundColor:'#7c9fff', borderRadius:4 }] },
    options:{ indexAxis:'y', responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}},
      scales:{ x:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}}, y:{ticks:{color:'#e6edf3'},grid:{display:false}} } }
  });
}

function renderBacklogChart(){
  if(charts['chart-backlog']) charts['chart-backlog'].destroy();
  const ctx = document.getElementById('chart-backlog');
  if(!ORD.length){ ctx.parentElement.innerHTML = '<div class="empty">Sin pedidos abiertos</div>'; return; }
  const buckets = {'0-30 días':0,'31-60 días':0,'61-90 días':0,'90+ días':0};
  ORD.forEach(r=>{ const d = Number(r.oldest_age_days||0), v = Number(r.open_value||0);
    if(d<=30) buckets['0-30 días']+=v; else if(d<=60) buckets['31-60 días']+=v;
    else if(d<=90) buckets['61-90 días']+=v; else buckets['90+ días']+=v; });
  const labels = Object.keys(buckets);
  charts['chart-backlog'] = new Chart(ctx, {
    type:'doughnut',
    data:{ labels, datasets:[{ data:labels.map(k=>buckets[k]), backgroundColor:labels.map((_,i)=>COLORS[i%COLORS.length]), borderColor:'#0d1117', borderWidth:2 }] },
    options:{ responsive:true, maintainAspectRatio:false,
      plugins:{ legend:{position:'right', labels:{color:'#e6edf3', font:{size:11}}} } }
  });
}

function renderAnomalyTable(){
  const el = document.getElementById('anomaly-table');
  if(!ANOM.length){ el.innerHTML = '<div class="empty">Sin anomalías de partners</div>'; return; }
  const agg = {};
  ANOM.forEach(r=>{ const k = (r.anomaly_type||'(sin tipo)') + '||' + (r.severity||'—'); agg[k] = (agg[k]||0) + 1; });
  const rows = Object.entries(agg).map(([k,c])=>{ const [t,s]=k.split('||'); return {t,s,c}; }).sort((a,b)=> b.c-a.c);
  el.innerHTML = `<table><thead><tr><th>Tipo de anomalía</th><th>Severidad</th><th class="num">Casos</th></tr></thead><tbody>${
    rows.map(r=> `<tr><td>${esc(r.t)}</td><td>${esc(r.s)}</td><td class="num">${fmtN(r.c)}</td></tr>`).join('')
  }</tbody></table>`;
}

function applyFilter(){
  const sel = document.getElementById('month-filter').value;
  const rows = sel ? REV.filter(r=> String(r.revenue_month||'') >= sel) : REV;
  renderKPIs(rows);
  renderRevenueLine(rows);
  renderTopCustomers(rows);
}
function resetFilter(){ document.getElementById('month-filter').value = ''; applyFilter(); }

async function init(){
  try{
    const [rev, ord, anom] = await Promise.all([
      fetchJson('/api/data/revenue_by_customer'),
      fetchJson('/api/data/open_sales_orders'),
      fetchJson('/api/data/business_partner_anomalies'),
    ]);
    REV = rev; ORD = ord; ANOM = anom;

    const months = [...new Set(REV.map(r=> String(r.revenue_month||'')).filter(Boolean))].sort();
    document.getElementById('month-filter').insertAdjacentHTML('beforeend',
      months.map(m=> `<option value="${esc(m)}">${esc(m)}</option>`).join(''));
    document.getElementById('month-filter').addEventListener('change', applyFilter);

    renderKPIs(REV);
    renderRevenueLine(REV);
    renderTopCustomers(REV);
    renderBacklogChart();
    renderAnomalyTable();
  }catch(e){ showError(e.message); }
}
init();
</script>
</body>
</html>
$seed$, $seed$Dashboard ejecutivo de ventas: revenue mensual, top clientes, backlog abierto y anomalias de business partners.$seed$, $seed$sap_s4hana$seed$, $seed$shared$seed$, $seed${revenue_by_customer,open_sales_orders,business_partner_anomalies}$seed$::text[], NOW())
ON CONFLICT (name) DO NOTHING;

INSERT INTO analytic_apps (name, title, html, description, cartridge_id, visibility, datasets_used, updated_at)
VALUES ($seed$sap_s4hana_finance_dashboard$seed$, $seed$Finance Dashboard$seed$, $seed$<!DOCTYPE html>
<!--
  app_id: sap_s4hana_finance_dashboard
  name: Finance Dashboard
  description: Dashboard ejecutivo financiero. Saldo contable, facturas vencidas, mora y gasto en proveedores (SAP S/4HANA)
  cartridge: sap_s4hana
  datasets: [gl_balance_by_account, overdue_billing, purchase_spend_by_supplier]
-->
<html lang="es" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Finance Dashboard — SAP S/4HANA</title>
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
  table{width:100%;border-collapse:collapse;font-size:12px}
  th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--border)}
  th{color:var(--text2);font-size:10px;text-transform:uppercase;letter-spacing:.5px}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  .empty{padding:28px;text-align:center;color:var(--text3);font-style:italic}
  .error{background:rgba(248,81,73,.12);color:var(--red);border:1px solid var(--red);
         padding:14px;border-radius:6px;margin-bottom:16px}
  @media(max-width:880px){.grid{grid-template-columns:1fr}}
</style>
</head>
<body>
  <h1>◆ Finance Dashboard</h1>
  <div class="subtitle">SAP S/4HANA · finanzas · saldo contable, cobros y gasto</div>

  <div id="error"></div>

  <div class="controls">
    <label for="company-filter">Compañía (CompanyCode)</label>
    <select id="company-filter"><option value="">Todas las compañías</option></select>
    <button onclick="resetFilter()">RESET</button>
  </div>

  <div class="kpis" id="kpis"></div>

  <div class="grid">
    <div class="section">
      <div class="section-title">Top 10 cuentas contables por saldo absoluto</div>
      <div id="gl-table"></div>
    </div>
    <div class="section">
      <div class="section-title">Top 10 proveedores por gasto</div>
      <div class="chart-wrap"><canvas id="chart-suppliers"></canvas></div>
    </div>
    <div class="section">
      <div class="section-title">Saldo contable por año fiscal</div>
      <div class="chart-wrap"><canvas id="chart-fiscal"></canvas></div>
    </div>
    <div class="section">
      <div class="section-title">Facturas vencidas por antigüedad</div>
      <div id="overdue-table" style="max-height:300px;overflow-y:auto"></div>
    </div>
  </div>

<script>
const COLORS = ['#7c9fff','#3fb950','#d29922','#bc8cff','#39d3ff','#f85149','#db61a2','#56d4dd','#e3b341','#a5d6ff'];
const fmtN = n => n==null ? '—' : Number(n).toLocaleString('es-MX');
const fmtM = n => n==null ? '—' : '$' + Number(n).toLocaleString('es-MX', {maximumFractionDigits:0});
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

let GL = [], OVD = [], SPEND = [];

function latestYearSpend(){
  const years = SPEND.map(r=> String(r.spend_month||'').slice(0,4)).filter(Boolean);
  if(!years.length) return 0;
  const max = years.sort().at(-1);
  return SPEND.filter(r=> String(r.spend_month||'').startsWith(max)).reduce((s,r)=> s + Number(r.total_spend||0), 0);
}

function renderKPIs(glRows){
  const saldo = glRows.reduce((s,r)=> s + Number(r.balance||0), 0);
  const vencido = OVD.reduce((s,r)=> s + Number(r.amount||0), 0);
  const moras = OVD.map(r=> Number(r.days_overdue||0)).filter(n=> n>0);
  const avgMora = moras.length ? moras.reduce((a,b)=>a+b,0)/moras.length : null;
  document.getElementById('kpis').innerHTML = `
    <div class="kpi-card" style="--accent:var(--blue)">
      <div class="kpi-label">Saldo contable</div>
      <div class="kpi-val">${fmtM(saldo)}</div>
      <div class="kpi-sub">${glRows.length} cuentas</div>
    </div>
    <div class="kpi-card" style="--accent:var(--red)">
      <div class="kpi-label">Facturas vencidas</div>
      <div class="kpi-val">${fmtM(vencido)}</div>
      <div class="kpi-sub">${fmtN(OVD.length)} documentos</div>
    </div>
    <div class="kpi-card" style="--accent:var(--amber)">
      <div class="kpi-label">Días promedio de mora</div>
      <div class="kpi-val">${avgMora==null ? '—' : fmtD(avgMora)}</div>
      <div class="kpi-sub">vencimiento estimado a 30 días</div>
    </div>
    <div class="kpi-card" style="--accent:var(--green)">
      <div class="kpi-label">Gasto proveedores (YTD)</div>
      <div class="kpi-val">${fmtM(latestYearSpend())}</div>
      <div class="kpi-sub">último año disponible</div>
    </div>`;
}

function renderGlTable(glRows){
  const el = document.getElementById('gl-table');
  if(!glRows.length){ el.innerHTML = '<div class="empty">Sin saldos contables</div>'; return; }
  const top = [...glRows].sort((a,b)=> Math.abs(Number(b.balance||0)) - Math.abs(Number(a.balance||0))).slice(0,10);
  el.innerHTML = `<table><thead><tr><th>Compañía</th><th>Cuenta</th><th>Año</th><th class="num">Saldo</th></tr></thead><tbody>${
    top.map(r=> `<tr><td>${esc(r.company_code)}</td><td>${esc(r.gl_account)}</td><td>${esc(r.fiscal_year)}</td><td class="num">${fmtM(r.balance)}</td></tr>`).join('')
  }</tbody></table>`;
}

function renderSuppliersChart(){
  if(charts['chart-suppliers']) charts['chart-suppliers'].destroy();
  const ctx = document.getElementById('chart-suppliers');
  if(!SPEND.length){ ctx.parentElement.innerHTML = '<div class="empty">Sin gasto de proveedores</div>'; return; }
  const agg = {};
  SPEND.forEach(r=>{ const s = r.supplier_code || '(sin proveedor)'; agg[s] = (agg[s]||0) + Number(r.total_spend||0); });
  const top = Object.entries(agg).sort((a,b)=> b[1]-a[1]).slice(0,10);
  charts['chart-suppliers'] = new Chart(ctx, {
    type:'bar',
    data:{ labels:top.map(t=>t[0]), datasets:[{ data:top.map(t=>t[1]), backgroundColor:'#3fb950', borderRadius:4 }] },
    options:{ indexAxis:'y', responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}},
      scales:{ x:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}}, y:{ticks:{color:'#e6edf3'},grid:{display:false}} } }
  });
}

function renderFiscalChart(glRows){
  if(charts['chart-fiscal']) charts['chart-fiscal'].destroy();
  const ctx = document.getElementById('chart-fiscal');
  if(!glRows.length){ ctx.parentElement.innerHTML = '<div class="empty">Sin datos</div>'; return; }
  const agg = {};
  glRows.forEach(r=>{ const y = String(r.fiscal_year||'—'); agg[y] = (agg[y]||0) + Number(r.balance||0); });
  const years = Object.keys(agg).sort();
  charts['chart-fiscal'] = new Chart(ctx, {
    type:'bar',
    data:{ labels:years, datasets:[{ label:'Saldo', data:years.map(y=>agg[y]), backgroundColor:'#7c9fff', borderRadius:4 }] },
    options:{ responsive:true, maintainAspectRatio:false, plugins:{legend:{display:false}},
      scales:{ x:{ticks:{color:'#e6edf3'},grid:{display:false}}, y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}} } }
  });
}

function renderOverdueTable(){
  const el = document.getElementById('overdue-table');
  if(!OVD.length){ el.innerHTML = '<div class="empty">Sin facturas vencidas</div>'; return; }
  const top = [...OVD].sort((a,b)=> Number(b.days_overdue||0) - Number(a.days_overdue||0)).slice(0,15);
  el.innerHTML = `<table><thead><tr><th>Factura</th><th>Cliente</th><th class="num">Monto</th><th class="num">Días mora</th></tr></thead><tbody>${
    top.map(r=> `<tr><td>${esc(r.billing_document)}</td><td>${esc(r.customer_code)}</td><td class="num">${fmtM(r.amount)}</td><td class="num">${fmtN(r.days_overdue)}</td></tr>`).join('')
  }</tbody></table>`;
}

function applyFilter(){
  const sel = document.getElementById('company-filter').value;
  const glRows = sel ? GL.filter(r=> String(r.company_code) === sel) : GL;
  renderKPIs(glRows);
  renderGlTable(glRows);
  renderFiscalChart(glRows);
}
function resetFilter(){ document.getElementById('company-filter').value = ''; applyFilter(); }

async function init(){
  try{
    const [gl, ovd, spend] = await Promise.all([
      fetchJson('/api/data/gl_balance_by_account'),
      fetchJson('/api/data/overdue_billing'),
      fetchJson('/api/data/purchase_spend_by_supplier'),
    ]);
    GL = gl; OVD = ovd; SPEND = spend;

    const companies = [...new Set(GL.map(r=> String(r.company_code||'')).filter(Boolean))].sort();
    document.getElementById('company-filter').insertAdjacentHTML('beforeend',
      companies.map(c=> `<option value="${esc(c)}">${esc(c)}</option>`).join(''));
    document.getElementById('company-filter').addEventListener('change', applyFilter);

    renderKPIs(GL);
    renderGlTable(GL);
    renderSuppliersChart();
    renderFiscalChart(GL);
    renderOverdueTable();
  }catch(e){ showError(e.message); }
}
init();
</script>
</body>
</html>
$seed$, $seed$Dashboard ejecutivo financiero: saldo contable, facturas vencidas, mora promedio y gasto en proveedores.$seed$, $seed$sap_s4hana$seed$, $seed$shared$seed$, $seed${gl_balance_by_account,overdue_billing,purchase_spend_by_supplier}$seed$::text[], NOW())
ON CONFLICT (name) DO NOTHING;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('85_sap_s4hana_apps_seed.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
