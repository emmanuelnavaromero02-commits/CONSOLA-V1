// Sprint v1.41.0 — cartridge wizard logic.
//
// All listeners are attached via addEventListener (no inline onclick) so the
// strict CSP introduced in v1.11 keeps blocking inline scripts. User-provided
// strings rendered into the DOM go through escHtml() to prevent XSS — every
// other dynamic field passes through textContent assignment.

const CARTRIDGE_META = {
  replicon: {
    name: 'Replicon',
    description: 'Time tracking, proyectos, empleados y horas.',
  },
  sap_hcm: {
    name: 'SAP HCM',
    description: 'Recursos humanos, empleados, puestos y organización.',
  },
  sap_s4hana: {
    name: 'SAP S/4HANA',
    description: 'Finanzas, logística, cuentas, materiales y asientos.',
  },
  sap_successfactors: {
    name: 'SAP SuccessFactors',
    description: 'Talento, performance, goals, reviews y learning.',
  },
};

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
        <div class="cart-card-title">${escHtml(CARTRIDGE_META[id]?.name || id)}</div>
        <div class="cart-card-id">${escHtml(id)}</div>
        <p class="cart-card-copy">${escHtml(CARTRIDGE_META[id]?.description || 'Cartucho configurable de OMEGA.')}</p>
        <div class="cart-card-actions">
          <button class="cart-btn cart-btn-primary" data-action="schema"  data-cartridge="${escHtml(id)}">Editar credenciales</button>
          <button class="cart-btn" data-action="test"    data-cartridge="${escHtml(id)}">Test connection</button>
          <button class="cart-btn" data-action="entities" data-cartridge="${escHtml(id)}">Entidades</button>
        </div>
      </div>
    `).join('');
  } catch (e) {
    container.innerHTML = `<div class="loading-line">Error: ${escHtml(e.message)}</div>`;
  }
}

function normalizeSchema(data, cartridge) {
  if (Array.isArray(data?.fields)) {
    return {
      name: data.name || CARTRIDGE_META[cartridge]?.name || cartridge,
      description: data.description || CARTRIDGE_META[cartridge]?.description || '',
      fields: data.fields,
      raw: data,
    };
  }
  if (data?.connector && typeof data.connector === 'object') {
    const connector = data.connector;
    const fields = [];
    if (connector.api?.base_url_env) {
      fields.push({
        name: 'base_url',
        type: 'url',
        label: 'Base URL',
        description: connector.api.base_url_env,
        required: true,
      });
    }
    if (connector.auth?.type === 'bearer_token') {
      fields.push({
        name: 'token',
        type: 'password',
        label: 'Bearer token',
        description: connector.auth.env_var || 'API token',
        required: true,
      });
    }
    return {
      name: connector.name || CARTRIDGE_META[cartridge]?.name || cartridge,
      description: connector.description || CARTRIDGE_META[cartridge]?.description || '',
      fields,
      raw: data,
    };
  }
  if (data && typeof data === 'object') {
    const fields = Object.entries(data)
      .filter(([key]) => !['name', 'description'].includes(key))
      .map(([name, spec]) => ({ name, ...(typeof spec === 'object' && spec ? spec : {}) }));
    return {
      name: data.name || CARTRIDGE_META[cartridge]?.name || cartridge,
      description: data.description || CARTRIDGE_META[cartridge]?.description || '',
      fields,
      raw: data,
    };
  }
  return fallbackSchema(cartridge);
}

function fallbackSchema(cartridge) {
  return {
    name: CARTRIDGE_META[cartridge]?.name || cartridge,
    description: CARTRIDGE_META[cartridge]?.description || 'Credenciales del cartucho.',
    fields: [
      { name: 'base_url', type: 'url', label: 'Base URL', required: true },
      { name: 'token', type: 'password', label: 'Bearer token', required: true },
    ],
    raw: {},
  };
}

function inputType(field) {
  const name = String(field.name || '');
  if (field.type === 'number') return 'number';
  if (field.type === 'url') return 'url';
  if (field.type === 'password' || /password|token|secret|api[_-]?key/i.test(name)) return 'password';
  return 'text';
}

function renderField(field) {
  const name = field.name || '';
  const label = field.label || name;
  const required = field.required ? 'required aria-required="true"' : '';
  const description = field.description
    ? `<p class="cart-field-help">${escHtml(field.description)}</p>`
    : '';
  if (field.type === 'boolean') {
    return `
      <label class="cart-field cart-field-check">
        <input type="checkbox" name="${escHtml(name)}" ${field.default ? 'checked' : ''}>
        <span>${escHtml(label)}${field.required ? ' *' : ''}</span>
        ${description}
      </label>
    `;
  }
  if (field.type === 'select' && Array.isArray(field.options)) {
    return `
      <label class="cart-field">
        <span>${escHtml(label)}${field.required ? ' *' : ''}</span>
        ${description}
        <select name="${escHtml(name)}" ${required}>
          <option value="">-- elegir --</option>
          ${field.options.map((opt) => `
            <option value="${escHtml(opt.value)}">${escHtml(opt.label || opt.value)}</option>
          `).join('')}
        </select>
      </label>
    `;
  }
  return `
    <label class="cart-field">
      <span>${escHtml(label)}${field.required ? ' *' : ''}</span>
      ${description}
      <input
        name="${escHtml(name)}"
        type="${inputType(field)}"
        value="${escHtml(field.default ?? '')}"
        autocomplete="new-password"
        ${required}
      >
    </label>
  `;
}

async function showSchema(cartridge) {
  const modal = document.getElementById('schema-modal');
  const body  = document.getElementById('schema-body');
  const title = document.getElementById('schema-title');
  const description = document.getElementById('schema-description');
  title.textContent = `Credenciales — ${CARTRIDGE_META[cartridge]?.name || cartridge}`;
  description.textContent = 'Cargando schema de configuración...';
  body.textContent = 'Cargando…';
  modal.hidden = false;
  try {
    const raw = await fetchJson(`/api/cartridges/${encodeURIComponent(cartridge)}/connector_schema`);
    const schema = normalizeSchema(raw, cartridge);
    description.textContent = schema.description || 'Configura y guarda credenciales cifradas en Vault.';
    const fields = schema.fields.length ? schema.fields : fallbackSchema(cartridge).fields;
    body.innerHTML = `
      <form id="credentials-form" class="credentials-form" data-cartridge="${escHtml(cartridge)}">
        <div class="credentials-grid">
          ${fields.map(renderField).join('')}
        </div>
        <div class="credentials-actions">
          <button class="cart-btn cart-btn-primary" type="submit">Guardar credenciales</button>
          <button class="cart-btn" type="button" data-action="test" data-cartridge="${escHtml(cartridge)}">Probar conexión</button>
          <button class="cart-btn cart-btn-danger" type="button" data-action="delete-credentials" data-cartridge="${escHtml(cartridge)}">Borrar credenciales</button>
        </div>
        <p class="cart-muted">Los valores se guardan cifrados en Vault. El audit log solo recibe nombres de campos, no secretos.</p>
      </form>
      <details class="schema-raw">
        <summary>Ver schema técnico</summary>
        <pre>${escHtml(JSON.stringify(schema.raw, null, 2))}</pre>
      </details>
    `;
  } catch (e) {
    description.textContent = '';
    body.innerHTML = `<div class="loading-line">Error: ${escHtml(e.message)}</div>`;
  }
}

async function saveCredentials(form) {
  const cartridge = form.dataset.cartridge;
  const payload = {};
  for (const field of new FormData(form).entries()) {
    const [key, value] = field;
    const input = form.elements[key];
    if (input?.type === 'checkbox') {
      payload[key] = input.checked;
    } else if (String(value).trim() !== '') {
      payload[key] = String(value).trim();
    }
  }
  if (Object.keys(payload).length === 0) {
    toast('Agrega al menos un campo de credenciales.', 'error');
    return;
  }
  try {
    const data = await fetchJson(
      `/api/cartridges/${encodeURIComponent(cartridge)}/credentials`,
      { method: 'POST', body: JSON.stringify(payload) },
    );
    toast(`${cartridge}: credenciales guardadas (${data.encrypted_count || Object.keys(payload).length} campos).`, 'ok');
  } catch (e) {
    toast(`${cartridge}: no se pudo guardar — ${e.message}`, 'error');
  }
}

async function testConnection(cartridge) {
  try {
    const data = await fetchJson(
      `/api/cartridges/${encodeURIComponent(cartridge)}/test_connection`,
      { method: 'POST' },
    );
    if (data.ok) {
      toast(`${cartridge}: conexión OK (${data.latency_ms || 0} ms) — ${data.message || 'verificada'}`, 'ok');
    } else {
      // Only surface `message`; the full payload may include base_url, hosts
      // or other deployment detail we don't want pasted into the UI.
      toast(`${cartridge}: ${data.message || 'error de conexión'}`, 'error');
    }
  } catch (e) {
    toast(`${cartridge}: ${e.message}`, 'error');
  }
}

async function deleteCredentials(cartridge) {
  if (!confirm(`Borrar credenciales guardadas para ${cartridge}?`)) return;
  try {
    await fetchJson(
      `/api/cartridges/${encodeURIComponent(cartridge)}/credentials`,
      { method: 'DELETE' },
    );
    toast(`${cartridge}: credenciales borradas.`, 'ok');
  } catch (e) {
    toast(`${cartridge}: no se pudieron borrar — ${e.message}`, 'error');
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
  document.body.addEventListener('submit', (ev) => {
    const form = ev.target.closest('#credentials-form');
    if (!form) return;
    ev.preventDefault();
    saveCredentials(form);
  });
  document.body.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-action]');
    if (!btn) return;
    const action    = btn.dataset.action;
    const cartridge = btn.dataset.cartridge;
    if (action === 'close-modal') {
      btn.closest('.modal').hidden = true;
    } else if (action === 'schema')   { showSchema(cartridge); }
    else if (action === 'test')       { testConnection(cartridge); }
    else if (action === 'delete-credentials') { deleteCredentials(cartridge); }
    else if (action === 'entities')   { showEntities(cartridge); }
    else if (action === 'run')        { runEntity(cartridge, btn.dataset.entity, btn.dataset.mode); }
  });
});
