(() => {
  'use strict';

  const byId = (id) => document.getElementById(id);
  const fmtCurrency = (n) => (
    n == null ? '—' : `$${Number(n).toLocaleString('es-MX', {
      minimumFractionDigits: 0,
      maximumFractionDigits: 0,
    })}`
  );
  const fmtPct = (n) => (n == null ? '—' : `${Number(n).toFixed(1)}%`);

  function showError(msg) {
    const el = byId('err');
    el.textContent = msg;
    el.hidden = false;
  }

  function clearError() {
    byId('err').hidden = true;
  }

  function fiscalYearFromMes(mes) {
    if (!mes) return null;
    const [year, month] = String(mes).split('-').map(Number);
    if (!year || !month) return null;
    return month >= 3 ? year : year - 1;
  }

  function addOption(select, value, label) {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label || value;
    select.appendChild(option);
  }

  async function loadOptions() {
    try {
      const response = await fetch('/api/data/pnl_mensual/options?columns=revenue_manager,cliente', {
        credentials: 'same-origin',
      });
      if (!response.ok) throw new Error('No se pudo cargar el catálogo de filtros');
      const opts = await response.json();
      const rmSelect = byId('f-rm');
      const clientSelect = byId('f-cl');
      (opts.revenue_manager || []).sort().forEach((value) => addOption(rmSelect, value));
      (opts.cliente || []).sort().forEach((value) => addOption(clientSelect, value));
    } catch (error) {
      showError(error.message);
    }

    try {
      const response = await fetch('/api/data/pnl_mensual/query', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ filters: {}, limit: 5000, columns: ['mes'] }),
      });
      const rows = await response.json();
      if (!Array.isArray(rows)) return;
      const years = [...new Set(rows.map((row) => fiscalYearFromMes(row.mes)).filter(Boolean))]
        .sort()
        .reverse();
      const fySelect = byId('f-fy');
      years.forEach((year) => addOption(
        fySelect,
        String(year),
        `AF${year} (mar-${year} / feb-${year + 1})`,
      ));
    } catch (_) {
      // Fiscal year is optional; the query still works with the other filters.
    }
  }

  function buildFilters() {
    const filters = {};
    if (byId('f-rm').value) filters.revenue_manager = byId('f-rm').value;
    if (byId('f-fy').value) filters.fiscal_year = Number(byId('f-fy').value);
    if (byId('f-cl').value) filters.cliente = byId('f-cl').value;
    return filters;
  }

  function aggregate(rows) {
    const totals = {
      revenue_usd: 0,
      costo_total: 0,
      margen_bruto_usd: 0,
      horas_facturables: 0,
      horas_totales: 0,
    };
    const byMes = {};
    rows.forEach((row) => {
      totals.revenue_usd += Number(row.revenue_usd || 0);
      totals.costo_total += Number(row.costo_total || 0);
      totals.margen_bruto_usd += Number(row.margen_bruto_usd || 0);
      totals.horas_facturables += Number(row.horas_facturables || 0);
      totals.horas_totales += Number(row.horas_totales || 0);
      if (!row.mes) return;
      byMes[row.mes] = byMes[row.mes] || {
        mes: row.mes,
        revenue_usd: 0,
        costo_total: 0,
        margen_bruto_usd: 0,
      };
      byMes[row.mes].revenue_usd += Number(row.revenue_usd || 0);
      byMes[row.mes].costo_total += Number(row.costo_total || 0);
      byMes[row.mes].margen_bruto_usd += Number(row.margen_bruto_usd || 0);
    });
    totals.margen_pct = totals.revenue_usd ? (totals.margen_bruto_usd / totals.revenue_usd) * 100 : null;
    totals.utilizacion_pct = totals.horas_totales ? (totals.horas_facturables / totals.horas_totales) * 100 : null;
    const months = Object.values(byMes).sort((a, b) => String(a.mes).localeCompare(String(b.mes)));
    months.forEach((month) => {
      month.margen_pct = month.revenue_usd ? (month.margen_bruto_usd / month.revenue_usd) * 100 : null;
    });
    return { totals, months };
  }

  function kpiCard(label, value, tone) {
    const card = document.createElement('div');
    card.className = 'kpi';
    const labelEl = document.createElement('div');
    labelEl.className = 'kpi-label';
    labelEl.textContent = label;
    const valueEl = document.createElement('div');
    valueEl.className = `kpi-val ${tone || ''}`.trim();
    valueEl.textContent = value;
    card.append(labelEl, valueEl);
    return card;
  }

  function renderKpis(totals) {
    const tone = (n) => (n > 0 ? 'pos' : n < 0 ? 'neg' : 'neu');
    const root = byId('kpis');
    root.replaceChildren(
      kpiCard('Revenue', fmtCurrency(totals.revenue_usd), tone(totals.revenue_usd)),
      kpiCard('Costo total', fmtCurrency(totals.costo_total), ''),
      kpiCard('Margen bruto', fmtCurrency(totals.margen_bruto_usd), tone(totals.margen_bruto_usd)),
      kpiCard('% Margen', fmtPct(totals.margen_pct), tone(totals.margen_pct || 0)),
      kpiCard('Utilización', fmtPct(totals.utilizacion_pct), 'neu'),
    );
  }

  function renderTable(months) {
    const wrap = byId('tbl-wrap');
    wrap.replaceChildren();
    if (!months.length) {
      const empty = document.createElement('p');
      empty.textContent = 'Sin filas.';
      wrap.appendChild(empty);
      return;
    }
    const table = document.createElement('table');
    const head = document.createElement('thead');
    const headerRow = document.createElement('tr');
    ['Mes', 'Revenue', 'Costo', 'Margen', '% Margen'].forEach((label, index) => {
      const th = document.createElement('th');
      th.textContent = label;
      if (index === 0) th.className = 'left';
      headerRow.appendChild(th);
    });
    head.appendChild(headerRow);
    const body = document.createElement('tbody');
    months.forEach((month) => {
      const row = document.createElement('tr');
      [
        [month.mes, 'left'],
        [fmtCurrency(month.revenue_usd), ''],
        [fmtCurrency(month.costo_total), ''],
        [fmtCurrency(month.margen_bruto_usd), ''],
        [fmtPct(month.margen_pct), ''],
      ].forEach(([value, className]) => {
        const td = document.createElement('td');
        td.textContent = value;
        if (className) td.className = className;
        row.appendChild(td);
      });
      body.appendChild(row);
    });
    table.append(head, body);
    wrap.appendChild(table);
  }

  function cssColor(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function renderChart(months) {
    const canvas = byId('chart');
    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);
    if (!months.length) return;

    const pad = { top: 20, right: 58, bottom: 42, left: 72 };
    const chartW = width - pad.left - pad.right;
    const chartH = height - pad.top - pad.bottom;
    const maxMoney = Math.max(1, ...months.flatMap((m) => [m.revenue_usd, m.margen_bruto_usd]).map(Number));
    const maxPct = Math.max(100, ...months.map((m) => Number(m.margen_pct || 0)));
    const blue = cssColor('--blue');
    const green = cssColor('--green');
    const amber = cssColor('--amber');
    const text2 = cssColor('--text2');
    const border = cssColor('--border');

    ctx.font = '11px system-ui, sans-serif';
    ctx.strokeStyle = border;
    ctx.fillStyle = text2;
    ctx.lineWidth = 1;
    for (let i = 0; i <= 4; i += 1) {
      const y = pad.top + (chartH / 4) * i;
      ctx.beginPath();
      ctx.moveTo(pad.left, y);
      ctx.lineTo(width - pad.right, y);
      ctx.stroke();
      const value = maxMoney - (maxMoney / 4) * i;
      ctx.fillText(fmtCurrency(value), 8, y + 4);
    }

    const slot = chartW / months.length;
    const barW = Math.min(28, slot / 4);
    const pctPoints = [];

    months.forEach((month, index) => {
      const center = pad.left + slot * index + slot / 2;
      const revH = (Number(month.revenue_usd || 0) / maxMoney) * chartH;
      const marginH = (Number(month.margen_bruto_usd || 0) / maxMoney) * chartH;
      ctx.fillStyle = blue;
      ctx.fillRect(center - barW - 2, pad.top + chartH - revH, barW, revH);
      ctx.fillStyle = green;
      ctx.fillRect(center + 2, pad.top + chartH - marginH, barW, marginH);
      ctx.fillStyle = text2;
      ctx.save();
      ctx.translate(center - 18, height - 10);
      ctx.rotate(-Math.PI / 8);
      ctx.fillText(String(month.mes), 0, 0);
      ctx.restore();
      const pctY = pad.top + chartH - (Number(month.margen_pct || 0) / maxPct) * chartH;
      pctPoints.push([center, pctY]);
    });

    ctx.strokeStyle = amber;
    ctx.lineWidth = 2;
    ctx.beginPath();
    pctPoints.forEach(([x, y], index) => {
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.fillStyle = amber;
    pctPoints.forEach(([x, y]) => {
      ctx.beginPath();
      ctx.arc(x, y, 3, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  async function runQuery() {
    clearError();
    const btn = byId('btn-run');
    btn.disabled = true;
    btn.textContent = 'Consultando...';
    try {
      const response = await fetch('/api/data/pnl_mensual/query', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          filters: buildFilters(),
          limit: 10000,
          columns: ['mes', 'revenue_usd', 'costo_total', 'margen_bruto_usd', 'horas_facturables', 'horas_totales'],
        }),
      });
      if (!response.ok) throw new Error(`Error al consultar el dataset (${response.status})`);
      const rows = await response.json();
      if (!Array.isArray(rows)) throw new Error('Respuesta inesperada del servidor');
      if (!rows.length) {
        byId('placeholder').textContent = 'La consulta no devolvió filas. Ajusta los filtros.';
        byId('placeholder').hidden = false;
        byId('results').hidden = true;
        return;
      }
      const { totals, months } = aggregate(rows);
      renderKpis(totals);
      renderTable(months);
      renderChart(months);
      byId('placeholder').hidden = true;
      byId('results').hidden = false;
    } catch (error) {
      showError(error.message);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Ver resultados';
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    byId('btn-run').addEventListener('click', runQuery);
    loadOptions();
  });
})();
