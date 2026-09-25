function esc(s) { return String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;"); }
const params = new URLSearchParams(location.search);
let semanticData = null;

function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

function csrfHeaders(base = {}) {
  const token = csrfToken();
  return token ? { ...base, 'X-CSRF-Token': token } : base;
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

async function loadCartridges() {
  try {
    const [serversResp, active] = await Promise.all([
      fetch('/api/mcp/servers', { credentials: 'same-origin' }),
      activeScopedCartridges(),
    ]);
    const d = await serversResp.json();
    const servers = d.servers || [];
    const sel = document.getElementById('cartridge-sel');
    const requested = params.get('cartridge') || '';
    const visibleServers = active.length ? servers.filter(s => active.includes(s.id)) : servers;
    const fallback = visibleServers[0]?.id || active[0] || requested || 'sap_successfactors';
    const cartridge = requested && (!active.length || active.includes(requested)) ? requested : fallback;
    const options = visibleServers.length ? visibleServers : [{id: cartridge}];
    sel.innerHTML = options.map(s => `<option value="${esc(s.id)}" ${s.id === cartridge ? 'selected' : ''}>${esc(s.id)}</option>`).join('');
    sel.value = cartridge;
    loadSemantic();
  } catch (e) {
    loadSemantic();
  }
}

async function loadSemantic() {
  const cartridge = document.getElementById('cartridge-sel').value || params.get('cartridge') || 'sap_successfactors';
  const nextParams = new URLSearchParams(location.search);
  nextParams.set('type', 'semantic');
  nextParams.set('cartridge', cartridge);
  history.replaceState(null, '', `${location.pathname}?${nextParams.toString()}`);
  document.getElementById('main-area').innerHTML = '<div class="empty-state">Cargando modelo semántico...</div>';
  document.getElementById('server-info').innerHTML = '';

  try {
    const r = await fetch(`/api/semantic?cartridge=${encodeURIComponent(cartridge)}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    semanticData = await r.json();

    const srv = semanticData.server || {};
    const healthy = srv.healthy;
    document.getElementById('server-info').innerHTML = `
      <div><div class="meta-lbl">ID</div><div class="meta-val">${esc(srv.id || cartridge)}</div></div>
      <div><div class="meta-lbl">URL</div><div class="meta-val" style="font-size:10px">${esc(srv.url || '—')}</div></div>
      <div><div class="meta-lbl">ESTADO</div><div class="meta-val ${healthy ? 'health-ok' : 'health-err'}">${healthy ? '● HEALTHY' : '● OFFLINE'}</div></div>
      <div><div class="meta-lbl">TOOLS</div><div class="meta-val">${(srv.tools || []).length}</div></div>
    `;

    renderMain(semanticData.entities || {});
  } catch (e) {
    document.getElementById('main-area').innerHTML = `<div class="empty-state" style="color:var(--red)">Error: ${esc(e.message)}</div>`;
  }
}

async function enrichSemantic() {
  const cartridge = document.getElementById('cartridge-sel').value || params.get('cartridge') || 'sap_successfactors';
  const btn = document.getElementById('btn-enrich-semantic');
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Enriqueciendo...';
  }
  try {
    const r = await fetch('/api/semantic/enrich', {
      method: 'POST',
      credentials: 'same-origin',
      headers: csrfHeaders({ 'Content-Type': 'application/json', 'Accept': 'application/json' }),
      body: JSON.stringify({ cartridge, limit: 80 }),
    });
    const payload = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(payload.detail || payload.message || `HTTP ${r.status}`);
    const count = Number(payload.enriched || payload.candidate_count || 0);
    window.alert(count > 0 ? `Catalogo enriquecido: ${count} campos.` : (payload.message || 'No habia campos pendientes.'));
    await loadSemantic();
  } catch (e) {
    window.alert(`No se pudo enriquecer la capa semantica: ${e.message}`);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = '◈ Enriquecer con IA';
    }
  }
}

function modeTags(modes) {
  if (!modes || !modes.length) return '<span class="mode-tag mode-full">full</span>';
  return modes.map(m => `<span class="mode-tag mode-${esc(m)}">${esc(m)}</span>`).join('');
}

function renderMain(entities) {
  const list = Array.isArray(entities) ? entities : (entities.entities || []);
  if (!list.length) {
    document.getElementById('main-area').innerHTML = '<div class="empty-state">Sin entidades en el modelo</div>';
    return;
  }

  const total_fields = list.reduce((a, e) => a + (e.fields || e.columns || []).length, 0);
  const has_watermark = list.filter(e => e.watermark_field || e.watermark).length;

  const watermarks = list
    .filter(e => e.watermark_field || e.watermark)
    .map(e => ({ entity: e.name || e.entity, field: e.watermark_field || e.watermark, value: e.last_watermark || '—' }));

  document.getElementById('main-area').innerHTML = `
    <div class="stat-row">
      <div class="stat"><div class="stat-val">${esc(list.length)}</div><div class="stat-lbl">ENTIDADES</div></div>
      <div class="stat"><div class="stat-val">${total_fields}</div><div class="stat-lbl">CAMPOS TOTAL</div></div>
      <div class="stat"><div class="stat-val" style="color:var(--amber)">${has_watermark}</div><div class="stat-lbl">CON WATERMARK</div></div>
    </div>

    <div style="display:flex;justify-content:flex-end;margin:0 0 12px">
      <button id="btn-enrich-semantic" class="btn btn-primary" type="button">◈ Enriquecer con IA</button>
    </div>

    <div class="card">
      <div class="tab-bar">
        <div class="tab active" id="tab-entities-btn" data-view="entities">ENTIDADES</div>
        <div class="tab" id="tab-watermarks-btn" data-view="watermarks">WATERMARKS</div>
      </div>

      <div id="view-entities">
        <div class="filter-row">
          <input type="text" id="search" placeholder="Buscar entidad...">
          <select id="mode-filter">
            <option value="">Todos los modos</option>
            <option value="full">full</option>
            <option value="delta">delta</option>
            <option value="patch">patch</option>
          </select>
        </div>
        <div class="entity-grid" id="entity-grid">
          ${renderEntityCards(list)}
        </div>
      </div>

      <div id="view-watermarks" style="display:none">
        ${watermarks.length ? `
          <div style="font-size:10px;color:var(--text3);display:flex;gap:8px;padding:4px 0;border-bottom:1px solid var(--border);margin-bottom:4px">
            <span style="min-width:140px">ENTIDAD</span>
            <span style="min-width:120px">CAMPO WATERMARK</span>
            <span>ULTIMO VALOR</span>
          </div>
          ${watermarks.map(w => `
          <div class="watermark-row">
            <span class="wm-entity">${esc(w.entity)}</span>
            <span class="wm-field">${esc(w.field)}</span>
            <span class="wm-value">${esc(w.value)}</span>
          </div>`).join('')}
        ` : '<div class="empty-state">Sin watermarks registrados</div>'}
      </div>
    </div>`;

  window._entityList = list;
}

function renderEntityCards(list) {
  if (!list.length) return '<div class="empty-state" style="grid-column:1/-1">Sin entidades</div>';
  return list.map(e => {
    const name = e.name || e.entity || '?';
    const modes = e.modes || e.extraction_modes || ['full'];
    const fields = e.fields || e.columns || [];
    const pk = e.primary_key || e.pk || '';
    return `
    <div class="entity-card">
      <div class="entity-header">
        <div class="entity-name">${esc(name)}</div>
        <div class="entity-modes">${modeTags(modes)}</div>
      </div>
      ${e.watermark_field || e.watermark ? `<div style="font-size:10px;color:var(--text3);margin-bottom:6px">⏱ watermark: <span style="color:var(--cyan)">${esc(e.watermark_field || e.watermark)}</span></div>` : ''}
      ${pk ? `<div style="font-size:10px;color:var(--text3);margin-bottom:6px">🔑 pk: <span style="color:var(--amber)">${esc(Array.isArray(pk) ? pk.join(', ') : pk)}</span></div>` : ''}
      ${fields.length ? `
        <div style="font-size:9px;color:var(--text3);display:flex;gap:6px;padding:2px 0;border-bottom:1px solid var(--border);margin-bottom:2px">
          <span style="min-width:140px">CAMPO</span><span style="min-width:80px">TIPO</span>
        </div>
        <div class="field-list">
          ${fields.map(f => {
            const fname = typeof f === 'string' ? f : (f.name || f.field || f.column || JSON.stringify(f));
            const ftype = typeof f === 'string' ? '' : (f.type || f.dtype || '');
            const fkey  = typeof f === 'string' ? '' : (f.key || f.pk ? 'KEY' : '');
            return `<div class="field-row">
              <span class="field-name">${esc(fname)}</span>
              <span class="field-type">${esc(ftype)}</span>
              <span class="field-key">${esc(fkey)}</span>
            </div>`;
          }).join('')}
        </div>` : '<div style="font-size:10px;color:var(--text3)">Sin detalle de campos</div>'}
    </div>`;
  }).join('');
}

function filterEntities() {
  const q = document.getElementById('search').value.toLowerCase();
  const mf = document.getElementById('mode-filter').value;
  const list = (window._entityList || []).filter(e => {
    const name = (e.name || e.entity || '').toLowerCase();
    const modes = e.modes || e.extraction_modes || ['full'];
    return (!q || name.includes(q)) && (!mf || modes.includes(mf));
  });
  document.getElementById('entity-grid').innerHTML = renderEntityCards(list);
}

function switchView(view) {
  document.getElementById('view-entities').style.display = view === 'entities' ? 'block' : 'none';
  document.getElementById('view-watermarks').style.display = view === 'watermarks' ? 'block' : 'none';
  document.getElementById('tab-entities-btn').classList.toggle('active', view === 'entities');
  document.getElementById('tab-watermarks-btn').classList.toggle('active', view === 'watermarks');
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('cartridge-sel').addEventListener('change', loadSemantic);
  document.getElementById('btn-reload').addEventListener('click', loadSemantic);

  const main = document.getElementById('main-area');
  main.addEventListener('click', (ev) => {
    if (ev.target.closest('#btn-enrich-semantic')) {
      enrichSemantic();
      return;
    }
    const tab = ev.target.closest('.tab[data-view]');
    if (tab) switchView(tab.dataset.view);
  });
  main.addEventListener('input', (ev) => {
    if (ev.target.id === 'search') filterEntities();
  });
  main.addEventListener('change', (ev) => {
    if (ev.target.id === 'mode-filter') filterEntities();
  });

  loadCartridges();
});
