function esc(s) { return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;"); }
const dsName = location.pathname.split('/').pop();
let dsData = null;

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

function showTab(name) {
  document.querySelectorAll('.tab').forEach((t, i) => {
    const names = ['sql', 'mapping', 'lineage', 'preview'];
    t.classList.toggle('active', names[i] === name);
  });
  document.querySelectorAll('.tab-content').forEach(c => {
    c.classList.toggle('active', c.id === `tab-${name}`);
  });
  if (name === 'lineage' && !document.getElementById('lineage-list').dataset.loaded) loadLineage();
}

function layerBadge(layer) {
  const cls = { silver: 'layer-silver', gold: 'layer-gold' }[layer] || 'layer-silver';
  return `<span class="layer-badge ${cls}">${esc((layer || 'silver').toUpperCase())}</span>`;
}

function fmt(iso) { return iso ? iso.replace('T', ' ').substring(0, 16) : '—'; }

async function loadDataset() {
  document.getElementById('page-title').textContent = `DATASET · ${dsName}`;
  try {
    const r = await fetchWithTimeout(`/api/datasets/${dsName}/detail`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    dsData = await r.json();

    document.getElementById('meta').innerHTML = `
      <div><div class="meta-lbl">NOMBRE</div><div class="meta-val" style="color:var(--amber)">${esc(dsData.name || dsName)}</div></div>
      <div><div class="meta-lbl">CAPA</div><div class="meta-val">${layerBadge(dsData.layer)}</div></div>
      <div><div class="meta-lbl">CARTUCHO</div><div class="meta-val">${esc(dsData.cartridge || '—')}</div></div>
      <div><div class="meta-lbl">ENTIDAD FUENTE</div><div class="meta-val">${esc(dsData.source_entity || '—')}</div></div>
      <div><div class="meta-lbl">FECHA FUENTE</div><div class="meta-val">${esc(dsData.source_load_date || '—')}</div></div>
      <div><div class="meta-lbl">BATCH FUENTE</div><div class="meta-val" style="font-size:10px">${esc(dsData.source_batch_id || '—')}</div></div>
    `;

    document.getElementById('sql-def').textContent = dsData.sql || '-- Sin definición SQL';

    const mapping = dsData.column_mapping || {};
    const entries = Object.entries(mapping);
    if (entries.length) {
      document.getElementById('mapping-list').innerHTML = entries.map(([src, biz]) => `
        <div class="mapping-row">
          <span class="field-source">${esc(src)}</span>
          <span class="field-arrow">→</span>
          <span class="field-biz">${esc(biz)}</span>
        </div>`).join('');
    } else {
      document.getElementById('mapping-list').innerHTML = '<div class="empty-state">Sin mapeo de columnas definido</div>';
    }
  } catch (e) {
    document.getElementById('meta').innerHTML = `<span style="color:var(--red)">Error cargando dataset: ${esc(e.message)}</span>`;
  }
}

async function loadLineage() {
  document.getElementById('lineage-list').dataset.loaded = '1';
  document.getElementById('lineage-list').innerHTML = '<div class="empty-state">Cargando lineage...</div>';
  try {
    const r = await fetchWithTimeout(`/api/datasets/${dsName}/lineage`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    const rows = d.lineage || d.result || [];
    if (!rows.length) {
      document.getElementById('lineage-list').innerHTML = '<div class="empty-state">Sin historial de materialización</div>';
      return;
    }
    document.getElementById('lineage-list').innerHTML = `
      <div style="font-size:10px;color:var(--text3);display:flex;gap:12px;padding:4px 0;border-bottom:1px solid var(--border);margin-bottom:4px">
        <span style="width:100px">FECHA</span>
        <span style="width:160px">BATCH ID</span>
        <span style="width:80px;text-align:right">FILAS</span>
        <span style="flex:1">STORAGE URI</span>
      </div>
      ${rows.map(r => `
      <div class="lineage-row">
        <span class="lin-date">${esc(fmt(r.created_at))}</span>
        <span class="lin-batch">${esc(r.source_batch_id || '—')}</span>
        <span class="lin-rows">${r.row_count !== null ? Number(r.row_count).toLocaleString() : '—'}</span>
        <span class="lin-uri" title="${esc(r.storage_uri || '')}">${esc(r.storage_uri || '—')}</span>
      </div>`).join('')}`;
  } catch (e) {
    document.getElementById('lineage-list').innerHTML = `<span style="color:var(--red)">Error: ${esc(e.message)}</span>`;
  }
}

async function loadPreview() {
  document.getElementById('preview-wrap').innerHTML = '<div class="empty-state">Cargando...</div>';
  try {
    const r = await fetchWithTimeout(`/datasets/${dsName}/data?limit=20`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    const rows = d.rows || d.data || [];
    if (!rows.length) {
      document.getElementById('preview-wrap').innerHTML = '<div class="empty-state">Sin datos disponibles</div>';
      return;
    }
    const cols = Object.keys(rows[0]);
    document.getElementById('preview-wrap').innerHTML = `
      <table>
        <thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead>
        <tbody>
          ${rows.map(row => `<tr>${cols.map(c => {
            const v = row[c];
            if (v === null || v === undefined) return `<td style="color:var(--text3);font-style:italic;font-size:10px">NULL</td>`;
            const s = String(v);
            return `<td style="font-size:10px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(s)}">${esc(s.substring(0, 60))}</td>`;
          }).join('')}</tr>`).join('')}
        </tbody>
      </table>`;
  } catch (e) {
    document.getElementById('preview-wrap').innerHTML = `<span style="color:var(--red)">Error: ${esc(e.message)}</span>`;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.tab[data-tab]').forEach(tab => {
    tab.addEventListener('click', () => showTab(tab.dataset.tab));
  });
  document.getElementById('btn-load-preview').addEventListener('click', loadPreview);
  loadDataset();
});
