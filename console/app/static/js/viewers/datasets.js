// Sprint v1.11 phase 2 — extracted from datasets.html for strict CSP.

function esc(s) { return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;"); }
let allDatasets = [];

function layerBadge(layer) {
  const cls = { silver: 'layer-silver', gold: 'layer-gold' }[layer] || 'layer-silver';
  return `<span class="layer-badge ${cls}">${(layer || 'silver').toUpperCase()}</span>`;
}

function fmt(iso) { return iso ? iso.replace('T', ' ').substring(0, 16) : '—'; }

function render() {
  const fl = document.getElementById('filter-layer').value;
  const fc = document.getElementById('filter-cartridge').value;
  const data = allDatasets.filter(d =>
    (!fl || (d.layer || 'silver') === fl) &&
    (!fc || d.cartridge === fc)
  );

  if (!data.length) {
    document.getElementById('tbody').innerHTML = `<tr><td colspan="7"><div class="empty-state">Sin datasets</div></td></tr>`;
    return;
  }

  document.getElementById('tbody').innerHTML = data.map(d => `
    <tr>
      <td style="font-family:var(--font-mono);color:var(--amber)">${esc(d.name)}</td>
      <td>${layerBadge(d.layer)}</td>
      <td style="font-size:10px;color:var(--text2)">${esc(d.cartridge || "") || "—"}</td>
      <td style="font-size:10px">${esc(d.source_entity || "") || "—"}</td>
      <td style="font-family:var(--font-mono);color:var(--cyan)">${esc(d.column_count || Object.keys(d.column_mapping || {}).length || "") || "—"}</td>
      <td style="font-size:10px">${esc(fmt(d.updated_at || d.created_at))}</td>
      <td><a class="link-btn" href="/viewer/datasets/${encodeURIComponent(d.name || '')}">Ver →</a></td>
    </tr>`).join('');
}

function renderStats() {
  const silver = allDatasets.filter(d => (d.layer || 'silver') === 'silver').length;
  const gold   = allDatasets.filter(d => d.layer === 'gold').length;
  const cartridges = [...new Set(allDatasets.map(d => d.cartridge).filter(Boolean))];

  document.getElementById('stats').innerHTML = `
    <div class="stat"><div class="stat-val">${allDatasets.length}</div><div class="stat-lbl">TOTAL</div></div>
    <div class="stat"><div class="stat-val" style="color:#00e5ff">${silver}</div><div class="stat-lbl">SILVER</div></div>
    <div class="stat"><div class="stat-val" style="color:#ffd700">${gold}</div><div class="stat-lbl">GOLD</div></div>
    <div class="stat"><div class="stat-val">${cartridges.length}</div><div class="stat-lbl">CARTUCHOS</div></div>
  `;

  const sel = document.getElementById('filter-cartridge');
  const prev = sel.value;
  sel.innerHTML = '<option value="">Todos los cartuchos</option>' +
    cartridges.map(c => `<option value="${esc(c)}" ${c === prev ? 'selected' : ''}>${esc(c)}</option>`).join('');
}

async function load() {
  try {
    const r = await fetch('/datasets');
    const d = await r.json();
    allDatasets = d.datasets || [];
    renderStats();
    render();
    document.getElementById('last-update').textContent = 'Actualizado: ' + new Date().toLocaleTimeString();
  } catch (e) {
    document.getElementById('last-update').textContent = 'Error: ' + e.message;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('btn-refresh').addEventListener('click', load);
  document.getElementById('filter-layer').addEventListener('change', render);
  document.getElementById('filter-cartridge').addEventListener('change', render);
  load();
});
