// Sprint v1.41.0 — cartridge wizard logic.
//
// All listeners are attached via addEventListener (no inline onclick) so the
// strict CSP introduced in v1.11 keeps blocking inline scripts. User-provided
// strings rendered into the DOM go through escHtml() to prevent XSS — every
// other dynamic field passes through textContent assignment.

document.documentElement.dataset.theme = localStorage.getItem('mod-theme') || 'dark';

function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function getCsrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

function toast(message, kind) {
  const node = document.getElementById('toast');
  node.textContent = message;
  node.className = 'toast toast-' + (kind || 'ok');
  node.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { node.hidden = true; }, 5000);
}

async function fetchJson(url, opts = {}) {
  const init = Object.assign({ credentials: 'same-origin' }, opts);
  init.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
  if (init.method && init.method !== 'GET') {
    init.headers['X-CSRF-Token'] = getCsrfToken();
  }
  const r = await fetch(url, init);
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`${r.status} ${body.slice(0, 200)}`);
  }
  return r.json();
}

async function loadCartridges() {
  const container = document.getElementById('cartridges-list');
  try {
    const data = await fetchJson('/api/cartridges');
    const ids = data.cartridges || [];
    if (!ids.length) {
      container.innerHTML = '<div class="loading-line">No hay cartuchos disponibles.</div>';
      return;
    }
    container.innerHTML = ids.map(id => `
      <div class="cart-card">
        <div class="cart-card-title">${escHtml(id)}</div>
        <div class="cart-card-actions">
          <button class="cart-btn" data-action="schema"  data-cartridge="${escHtml(id)}">Configurar</button>
          <button class="cart-btn" data-action="test"    data-cartridge="${escHtml(id)}">Test connection</button>
          <button class="cart-btn" data-action="entities" data-cartridge="${escHtml(id)}">Entidades</button>
        </div>
      </div>
    `).join('');
  } catch (e) {
    container.innerHTML = `<div class="loading-line">Error: ${escHtml(e.message)}</div>`;
  }
}

async function showSchema(cartridge) {
  const modal = document.getElementById('schema-modal');
  const body  = document.getElementById('schema-body');
  const title = document.getElementById('schema-title');
  title.textContent = `Configuración — ${cartridge}`;
  body.textContent = 'Cargando…';
  modal.hidden = false;
  try {
    const schema = await fetchJson(`/api/cartridges/${encodeURIComponent(cartridge)}/connector_schema`);
    body.textContent = JSON.stringify(schema, null, 2);
  } catch (e) {
    body.textContent = `Error: ${e.message}`;
  }
}

async function testConnection(cartridge) {
  try {
    const data = await fetchJson(
      `/api/cartridges/${encodeURIComponent(cartridge)}/test_connection`,
      { method: 'POST' },
    );
    const status = (data.status || '').toLowerCase();
    if (status === 'ok' || status === 'degraded') {
      toast(`${cartridge}: ${status} — ${data.message || 'conexión verificada'}`, 'ok');
    } else {
      // Only surface `message`; the full payload may include base_url, hosts
      // or other deployment detail we don't want pasted into the UI.
      toast(`${cartridge}: ${data.message || 'error de conexión'}`, 'error');
    }
  } catch (e) {
    toast(`${cartridge}: ${e.message}`, 'error');
  }
}

async function showEntities(cartridge) {
  const modal = document.getElementById('entities-modal');
  const tbody = document.getElementById('entities-tbody');
  const title = document.getElementById('entities-title');
  title.textContent = `Entidades — ${cartridge}`;
  tbody.innerHTML = '<tr><td colspan="4">Cargando…</td></tr>';
  modal.hidden = false;
  try {
    const data = await fetchJson(`/api/cartridges/${encodeURIComponent(cartridge)}/entities`);
    const entities = data.entities || [];
    if (!entities.length) {
      tbody.innerHTML = '<tr><td colspan="4">Sin entidades.</td></tr>';
      return;
    }
    tbody.innerHTML = entities.map(e => {
      const lastRun = (e.watermark && e.watermark.value) || '—';
      const name    = escHtml(e.entity || '');
      const mode    = escHtml(e.mode  || 'incremental');
      return `
        <tr>
          <td>${name}</td>
          <td>${mode}</td>
          <td>${escHtml(String(lastRun))}</td>
          <td>
            <button class="cart-btn"
                    data-action="run"
                    data-cartridge="${escHtml(cartridge)}"
                    data-entity="${name}"
                    data-mode="incremental">Incremental</button>
            <button class="cart-btn"
                    data-action="run"
                    data-cartridge="${escHtml(cartridge)}"
                    data-entity="${name}"
                    data-mode="full">Full</button>
          </td>
        </tr>`;
    }).join('');
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="4">Error: ${escHtml(e.message)}</td></tr>`;
  }
}

async function runEntity(cartridge, entity, mode) {
  if (!confirm(`Ejecutar ${mode} de "${entity}" en ${cartridge}?`)) return;
  try {
    const data = await fetchJson(
      `/api/cartridges/${encodeURIComponent(cartridge)}/entities/${encodeURIComponent(entity)}/run?mode=${encodeURIComponent(mode)}`,
      { method: 'POST' },
    );
    toast(`${entity} (${mode}) → ${data.status || 'OK'}`, 'ok');
  } catch (e) {
    toast(`Error: ${e.message}`, 'error');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  loadCartridges();
  document.body.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-action]');
    if (!btn) return;
    const action    = btn.dataset.action;
    const cartridge = btn.dataset.cartridge;
    if (action === 'close-modal') {
      btn.closest('.modal').hidden = true;
    } else if (action === 'schema')   { showSchema(cartridge); }
    else if (action === 'test')       { testConnection(cartridge); }
    else if (action === 'entities')   { showEntities(cartridge); }
    else if (action === 'run')        { runEntity(cartridge, btn.dataset.entity, btn.dataset.mode); }
  });
});
