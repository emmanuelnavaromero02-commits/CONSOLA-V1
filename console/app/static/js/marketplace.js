import { renderTopbar } from './home/navigation.js';
import { state } from './home/state.js';

const view = {
  mode: 'catalog',
  products: [],
  installations: [],
  adminInstallations: [],
  access: {},
  accessOpen: null,
  query: '',
  busy: new Set(),
  root: null,
  workspaceId: null,
};

const STATUS_COPY = {
  available: ['Disponible', ''],
  active: ['Activo', 'ok'],
  pending_approval: ['Pendiente de aprobación', 'warn'],
  pending_connection: ['Pendiente de conexión', 'warn'],
  waiting_credentials: ['Requiere credenciales', 'warn'],
  requested: ['Solicitado', 'warn'],
  ready: ['Activo', 'ok'],
  failed: ['Falló', 'off'],
  paused: ['Pausado', 'warn'],
  revoked: ['Revocado', 'off'],
  expired: ['Expirado', 'off'],
  suspended: ['Suspendido', 'off'],
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function esc(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

function csrfHeaders(base = {}) {
  const token = csrfToken();
  return token ? { ...base, 'X-CSRF-Token': token } : base;
}

async function readError(response) {
  try {
    const data = await response.json();
    return data.detail || data.error || `HTTP ${response.status}`;
  } catch (_) {
    return `HTTP ${response.status}`;
  }
}

async function fetchJson(url, options = {}) {
  const method = String(options.method || 'GET').toUpperCase();
  const init = {
    credentials: 'same-origin',
    ...options,
    method,
    headers: { Accept: 'application/json', ...(options.headers || {}) },
  };
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    Object.assign(init.headers, csrfHeaders(init.headers));
  }
  if (view.workspaceId) init.headers['X-Workspace-Id'] = view.workspaceId;
  const response = await fetch(url, init);
  if (response.redirected && new URL(response.url).pathname === '/login') {
    location.href = '/login';
    throw new Error('Sesion requerida');
  }
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

function toast(message, kind = '') {
  let wrap = document.getElementById('market-toasts');
  if (!wrap) {
    wrap = el('div', 'toasts');
    wrap.id = 'market-toasts';
    document.body.appendChild(wrap);
  }
  const node = el('div', `toast ${kind}`.trim(), message);
  wrap.appendChild(node);
  setTimeout(() => node.remove(), 4800);
}

function statusLabel(status) {
  return STATUS_COPY[status] || STATUS_COPY.available;
}

function metricCard(label, value) {
  const node = el('article');
  node.append(el('span', null, label), el('strong', null, String(value)));
  return node;
}

function renderMetrics() {
  const host = document.getElementById('market-metrics');
  if (!host) return;
  const active = view.products.filter((p) => p.access_status === 'active').length;
  const pending = view.products.filter((p) => p.access_status === 'pending_approval').length;
  const apps = view.products.reduce((sum, p) => sum + Number(p.app_count || 0), 0);
  host.replaceChildren(
    metricCard('Cartuchos', view.products.length),
    metricCard('Activos', active),
    metricCard('Solicitudes', pending),
    metricCard('Apps incluidas', apps)
  );
}

function list(items) {
  return `<ul>${(items || []).slice(0, 5).map((item) => `<li>${esc(item)}</li>`).join('')}</ul>`;
}

function actionFor(product) {
  const status = product.access_status || 'available';
  if (status === 'active') return `<a class="btn primary" href="/workspace">Ver resultados</a>`;
  if (status === 'pending_approval') return '<button class="btn secondary" type="button" disabled>Esperando aprobación</button>';
  if (status === 'pending_connection') return '<a class="btn secondary" href="mailto:support@omega.local?subject=Credenciales%20Marketplace">Completar con soporte</a>';
  if (['paused', 'revoked', 'expired', 'suspended'].includes(status)) {
    return '<button class="btn secondary" type="button" disabled>Sin permiso</button><a class="btn secondary" href="mailto:support@omega.local?subject=Soporte%20Marketplace">Contactar soporte</a>';
  }
  if (!product.can_request) {
    return '<a class="btn secondary" href="mailto:support@omega.local?subject=Acceso%20Marketplace">Contactar soporte</a>';
  }
  const busy = view.busy.has(product.cartridge_id);
  return `<button class="btn primary" type="button" data-request="${esc(product.cartridge_id)}" ${busy ? 'disabled' : ''}>${busy ? 'Enviando...' : 'Solicitar activación'}</button>`;
}

function productCard(product) {
  const profile = product.commercial || {};
  const [label, variant] = statusLabel(product.access_status);
  const domains = (profile.data_domains || []).slice(0, 6);
  return `
    <article class="product-card market-product-card">
      <header>
        <div>
          <span class="product-kicker">${esc(profile.plan || product.category || 'Cartucho')}</span>
          <h4>${esc(product.name || product.cartridge_id)}</h4>
          <code>${esc(product.cartridge_id)}</code>
        </div>
        <span class="pill ${variant}">${esc(label)}</span>
      </header>
      <p class="product-headline">${esc(profile.headline || product.description || 'Cartucho empresarial para este workspace.')}</p>
      <div class="product-meta">
        <span>${Number(product.entity_count || 0)} entidades</span>
        <span>${Number(product.dataset_count || 0)} datasets</span>
        <span>${Number(product.app_count || 0)} apps</span>
        ${product.requires_credentials ? '<span>requiere credenciales</span>' : '<span>sin credenciales externas</span>'}
      </div>
      <div class="market-detail-grid">
        <section>
          <h5>Qué entrega</h5>
          ${list(profile.what_it_does)}
        </section>
        <section>
          <h5>Dashboards</h5>
          ${list(profile.dashboards)}
        </section>
        <section>
          <h5>Preguntas</h5>
          ${list(profile.sample_questions)}
        </section>
      </div>
      <div class="product-domains">
        ${domains.map((item) => `<span>${esc(item)}</span>`).join('')}
      </div>
      <footer class="card-actions">
        ${actionFor(product)}
        <button class="btn secondary" type="button" data-details="${esc(product.cartridge_id)}">Detalles</button>
      </footer>
    </article>
  `;
}

function renderProducts() {
  const q = view.query.trim().toLowerCase();
  const products = view.products.filter((p) => {
    if (!q) return true;
    return [p.name, p.cartridge_id, p.description, p.category, ...(p.commercial?.data_domains || [])]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(q));
  });
  const host = document.getElementById('market-products');
  if (!host) return;
  host.innerHTML = products.length
    ? products.map(productCard).join('')
    : '<div class="empty">No hay cartuchos para ese filtro.</div>';
}

function renderInstallations() {
  const host = document.getElementById('market-installations');
  if (!host) return;
  if (!view.installations.length) {
    const emptyMsg = view.mode === 'customer'
      ? 'Tu workspace todavía no tiene cartuchos activos ni solicitados. ' +
        'Abre el catálogo en Marketplace para pedir uno; un administrador deberá aprobarlo.'
      : 'Sin cartuchos activos o solicitados para este workspace.';
    host.innerHTML = `<div class="empty">${esc(emptyMsg)}</div>`;
    return;
  }
  host.innerHTML = view.installations.map((row) => {
    const [label, variant] = statusLabel(row.access_status || row.status);
    const canRetry = Boolean(row.can_retry);
    const updated = (row.updated_at || '').slice(0, 19) || 'sin actividad';
    const dataState = row.status === 'ready'
      ? 'Disponible'
      : row.status === 'requested'
        ? 'Pendiente de aprobación'
        : ['paused', 'revoked', 'expired', 'suspended'].includes(row.status)
          ? 'Sin permiso'
          : 'Pendiente de conexión';
    const copilotState = row.status === 'ready' ? 'Disponible' : 'Bloqueado hasta que el cartucho esté activo';
    return `
      <article class="install-row">
        <div>
          <h4>${esc(row.product_name || row.cartridge_id)}</h4>
          <p>${esc(row.cartridge_id)} · actualizado ${esc(updated)}</p>
          <p>Datos: ${esc(dataState)} · Copiloto: ${esc(copilotState)}</p>
        </div>
        <div class="install-actions">
          <span class="pill ${variant}">${esc(label)}</span>
          ${canRetry ? `<button class="btn secondary" type="button" data-retry="${esc(row.id)}">Reintentar</button>` : ''}
          <a class="btn secondary" href="mailto:support@omega.local?subject=Soporte%20Marketplace">Soporte</a>
        </div>
      </article>
    `;
  }).join('');
}

function renderAll() {
  renderMetrics();
  renderProducts();
  renderInstallations();
}

async function loadMarketplace() {
  const [productData, installData] = await Promise.all([
    fetchJson('/api/marketplace/products'),
    fetchJson('/api/customer/cartridges'),
  ]);
  view.products = productData.products || [];
  view.installations = installData.installations || [];
  renderAll();
}

async function requestActivation(cartridgeId) {
  view.busy.add(cartridgeId);
  renderProducts();
  try {
    await fetchJson(`/api/marketplace/products/${encodeURIComponent(cartridgeId)}/request`, { method: 'POST' });
    toast(`Solicitud enviada. Un admin debe aprobar ${cartridgeId}.`);
    await loadMarketplace();
  } catch (error) {
    toast(error.message || 'No se pudo solicitar el cartucho.', 'error');
  } finally {
    view.busy.delete(cartridgeId);
    renderProducts();
  }
}

async function retry(installationId) {
  try {
    await fetchJson(`/api/marketplace/installations/${encodeURIComponent(installationId)}/retry`, { method: 'POST' });
    toast('Instalación reintentada.');
    await loadMarketplace();
  } catch (error) {
    toast(error.message || 'No se pudo reintentar.', 'error');
  }
}

function panel(title, desc, body) {
  const section = el('section', 'panel');
  const head = el('div', 'panel-head');
  const copy = el('div');
  copy.append(el('h3', null, title), el('p', null, desc));
  head.appendChild(copy);
  section.append(head, body);
  return section;
}

function workspaceSelector() {
  const workspaces = state.user?.workspaces || [];
  if (workspaces.length < 2) return null;
  view.workspaceId = view.workspaceId || state.user?.active_workspace_id || workspaces[0]?.workspace_id || null;
  const wrap = el('label', 'workspace-switch');
  wrap.appendChild(el('span', null, 'Workspace'));
  const select = document.createElement('select');
  select.id = 'market-workspace';
  for (const workspace of workspaces) {
    const option = document.createElement('option');
    option.value = workspace.workspace_id;
    option.textContent = workspace.workspace_name || workspace.workspace_id;
    option.selected = workspace.workspace_id === view.workspaceId;
    select.appendChild(option);
  }
  wrap.appendChild(select);
  return wrap;
}

function renderShell(root) {
  root.replaceChildren();
  view.workspaceId = state.user?.active_workspace_id || view.workspaceId;
  const shell = el('div', 'home-shell marketplace-view');
  const container = el('div', 'home-container market-main');
  const top = el('header', 'market-top');
  const copy = el('div');
  copy.append(
    el('p', 'eyebrow', 'Marketplace'),
    el('h2', null, 'Cartuchos empresariales'),
    el('p', null, 'Elige capacidades para tu workspace. Al solicitar un cartucho se abre una instalación controlada; un admin aprueba el acceso y ΩMEGA habilita resultados sin exponer Studio técnico.')
  );
  const actions = el('div', 'top-actions');
  const selector = workspaceSelector();
  if (selector) actions.appendChild(selector);
  const mine = el('a', 'btn secondary', 'Mis cartuchos');
  mine.href = '/customer/cartridges';
  const workspace = el('a', 'btn secondary', 'Workspace');
  workspace.href = '/workspace';
  const refresh = el('button', 'btn primary', 'Actualizar');
  refresh.type = 'button';
  refresh.dataset.marketRefresh = 'true';
  actions.append(mine, workspace, refresh);
  top.append(copy, actions);

  const metrics = el('section', 'metrics');
  metrics.id = 'market-metrics';

  const catalogBody = el('div');
  const search = document.createElement('input');
  search.id = 'market-search';
  search.type = 'search';
  search.placeholder = 'Buscar por sistema, dato o resultado...';
  search.autocomplete = 'off';
  const products = el('div', 'products-grid products-grid-wide');
  products.id = 'market-products';
  products.appendChild(el('div', 'empty', 'Cargando marketplace...'));
  catalogBody.append(search, products);

  const installs = el('div', 'install-list');
  installs.id = 'market-installations';
  installs.appendChild(el('div', 'empty', 'Sin instalaciones.'));

  container.append(
    top,
    metrics,
    panel('Catálogo comercial', 'Lo que el cliente puede pedir: resultados, datos, dashboards y preguntas que habilita cada cartucho.', catalogBody),
    panel('Mis cartuchos', 'Estado real del workspace: pendiente, activo, pausado o sin permiso.', installs)
  );
  shell.append(renderTopbar(), container);
  root.appendChild(shell);
}

function adminStatusActions(row) {
  const id = esc(row.id);
  const approveStates = ['requested', 'pending_connection', 'waiting_credentials', 'failed', 'ready'];
  const pauseStates = ['ready', 'pending_connection', 'failed'];
  const reactivateStates = ['paused', 'revoked', 'expired', 'suspended'];
  const revokeStates = ['requested', 'pending_connection', 'waiting_credentials', 'ready', 'failed', 'paused', 'expired', 'suspended'];
  const approve = approveStates.includes(row.status) && row.status !== 'ready'
    ? `<button class="btn primary" type="button" data-admin-action="approve" data-installation="${id}">Aprobar workspace</button>` : '';
  const pause = pauseStates.includes(row.status) ? `<button class="btn secondary" type="button" data-admin-action="pause" data-installation="${id}">Pausar workspace</button>` : '';
  const reactivate = reactivateStates.includes(row.status)
    ? `<button class="btn primary" type="button" data-admin-action="reactivate" data-installation="${id}">Reactivar workspace</button>` : '';
  const revoke = revokeStates.includes(row.status) ? `<button class="btn danger" type="button" data-admin-action="revoke" data-installation="${id}">Revocar workspace</button>` : '';
  const users = `<button class="btn secondary" type="button" data-access-toggle="${id}">Usuarios</button>`;
  return `${users}${approve}${pause}${reactivate}${revoke}`;
}

function renderAccessDrawer(row) {
  if (view.accessOpen !== row.id) return '';
  const data = view.access[row.id];
  if (!data) {
    return '<div class="access-drawer"><div class="empty compact">Cargando usuarios del workspace...</div></div>';
  }
  const users = data.users || [];
  if (!users.length) {
    return '<div class="access-drawer"><div class="empty compact">Sin usuarios asignados a este workspace.</div></div>';
  }
  return `
    <div class="access-drawer">
      <header>
        <div>
          <strong>Acceso por usuario</strong>
          <p>Bloquear aquí quita el cartucho a ese correo en Workspace/Copilot/MCP sin revocar al cliente completo.</p>
        </div>
        <span class="pill ${data.installation?.usable ? 'ok' : 'warn'}">${data.installation?.usable ? 'Instalación activa' : 'Instalación no usable'}</span>
      </header>
      <div class="access-table">
        ${users.map((u) => {
          const mode = u.mode || 'inherit';
          const status = u.effective_access ? ['Con acceso', 'ok'] : ['Sin acceso', 'off'];
          return `
            <div class="access-row">
              <div>
                <strong>${esc(u.email)}</strong>
                <span>${esc(u.name || u.workspace_role || 'usuario')} · ${esc(u.workspace_role || u.global_role || '')}</span>
              </div>
              <span class="pill ${status[1]}">${status[0]}</span>
              <select data-user-access="${esc(row.id)}" data-user-id="${esc(u.id)}">
                <option value="inherit" ${mode === 'inherit' ? 'selected' : ''}>Heredar workspace</option>
                <option value="deny" ${mode === 'deny' ? 'selected' : ''}>Bloquear</option>
              </select>
            </div>
          `;
        }).join('')}
      </div>
    </div>
  `;
}

function renderAdminRows() {
  const host = document.getElementById('admin-installations');
  if (!host) return;
  const q = view.query.trim().toLowerCase();
  const rows = view.adminInstallations.filter((row) => {
    if (!q) return true;
    return [row.product_name, row.cartridge_id, row.tenant_name, row.workspace_name, row.status, row.created_by_email]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(q));
  });
  if (!rows.length) {
    host.innerHTML = '<div class="empty">Sin instalaciones para ese filtro.</div>';
    return;
  }
  host.innerHTML = rows.map((row) => {
    const [label, variant] = statusLabel(row.access_status || row.status);
    return `
      <article class="install-row admin-install-row">
        <div>
          <h4>${esc(row.product_name || row.cartridge_id)}</h4>
          <p>${esc(row.tenant_name || row.tenant_id)} · ${esc(row.workspace_name || row.workspace_id)} · ${esc(row.cartridge_id)}</p>
          <p>${esc(row.current_step || 'sin paso')} · ${esc((row.updated_at || '').slice(0, 19))} · ${esc(row.created_by_email || 'sin usuario')}</p>
        </div>
        <div class="install-actions">
          <span class="pill ${variant}">${esc(label)}</span>
          ${adminStatusActions(row)}
        </div>
        ${renderAccessDrawer(row)}
      </article>
    `;
  }).join('');
}

async function loadAdmin() {
  const data = await fetchJson('/api/admin/installations');
  view.adminInstallations = data.installations || [];
  const metrics = document.getElementById('admin-metrics');
  if (metrics) {
    const requested = view.adminInstallations.filter((i) => i.status === 'requested').length;
    const ready = view.adminInstallations.filter((i) => i.status === 'ready').length;
    const blocked = view.adminInstallations.filter((i) => ['paused', 'revoked', 'expired', 'suspended'].includes(i.status)).length;
    metrics.replaceChildren(
      metricCard('Instalaciones', view.adminInstallations.length),
      metricCard('Pendientes', requested),
      metricCard('Activas', ready),
      metricCard('Bloqueadas', blocked)
    );
  }
  renderAdminRows();
}

async function loadAccess(installationId) {
  view.accessOpen = installationId;
  renderAdminRows();
  const data = await fetchJson(`/api/admin/installations/${encodeURIComponent(installationId)}/access`);
  view.access[installationId] = data;
  renderAdminRows();
}

async function setUserAccess(installationId, userId, mode) {
  const key = `access:${installationId}:${userId}`;
  if (view.busy.has(key)) return;
  view.busy.add(key);
  try {
    const data = await fetchJson(
      `/api/admin/installations/${encodeURIComponent(installationId)}/access/${encodeURIComponent(userId)}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode }),
      }
    );
    view.access[installationId] = data;
    toast(mode === 'deny' ? 'Usuario bloqueado para este cartucho.' : 'Usuario vuelve a heredar el acceso del workspace.');
    renderAdminRows();
  } catch (error) {
    toast(error.message || 'No se pudo cambiar el acceso del usuario.', 'error');
    loadAccess(installationId).catch(() => {});
  } finally {
    view.busy.delete(key);
  }
}

async function adminAction(id, action) {
  const key = `admin:${id}:${action}`;
  if (view.busy.has(key)) return;
  view.busy.add(key);
  try {
    await fetchJson(`/api/admin/installations/${encodeURIComponent(id)}/${encodeURIComponent(action)}`, { method: 'POST' });
    toast(`Instalación ${action}: listo.`);
    await loadAdmin();
  } catch (error) {
    toast(error.message || 'No se pudo actualizar la instalación.', 'error');
  } finally {
    view.busy.delete(key);
  }
}

function renderAdminShell(root) {
  root.replaceChildren();
  const shell = el('div', 'home-shell marketplace-view');
  const container = el('div', 'home-container market-main');
  const top = el('header', 'market-top');
  const copy = el('div');
  copy.append(
    el('p', 'eyebrow', 'Admin interno'),
    el('h2', null, 'Gestión de cartuchos por cliente'),
    el('p', null, 'Aquí se aprueba, pausa, revoca o reactiva el derecho de uso. No es la tienda del cliente; es el control operativo para licencias e instalaciones por tenant y workspace.')
  );
  const actions = el('div', 'top-actions');
  const market = el('a', 'btn secondary', 'Marketplace');
  market.href = '/marketplace';
  const refresh = el('button', 'btn primary', 'Actualizar');
  refresh.type = 'button';
  refresh.dataset.adminRefresh = 'true';
  actions.append(market, refresh);
  top.append(copy, actions);

  const metrics = el('section', 'metrics');
  metrics.id = 'admin-metrics';
  const body = el('div');
  const search = document.createElement('input');
  search.id = 'admin-install-search';
  search.type = 'search';
  search.placeholder = 'Buscar tenant, workspace, cartucho o estado...';
  const rows = el('div', 'install-list');
  rows.id = 'admin-installations';
  rows.appendChild(el('div', 'empty', 'Cargando instalaciones...'));
  body.append(search, rows);
  container.append(top, metrics, panel('Instalaciones y permisos', 'Los cambios aquí alteran allowed_cartridges y bloquean Workspace/Copilot/MCP server-side.', body));
  shell.append(renderTopbar(), container);
  root.appendChild(shell);
}

export async function renderMarketplace(root) {
  view.mode = 'catalog';
  view.root = root;
  renderShell(root);
  await loadMarketplace().catch((error) => {
    const host = document.getElementById('market-products');
    if (host) host.innerHTML = `<div class="empty">Error cargando marketplace: ${esc(error.message)}</div>`;
    toast(error.message || 'Error cargando marketplace.', 'error');
  });
}

// Phase-4 — /customer/cartridges is the "Mis cartuchos" view.
// Same backend (`/api/customer/cartridges`) but the shell is focused on
// the workspace's actual installations: status, last activity, retry,
// support. The catalog and search are deliberately hidden so the user
// does not see commercial copy alongside their operational state.
function renderCustomerShell(root) {
  root.replaceChildren();
  view.workspaceId = state.user?.active_workspace_id || view.workspaceId;
  const shell = el('div', 'home-shell marketplace-view marketplace-customer');
  const container = el('div', 'home-container market-main');

  const top = el('header', 'market-top');
  const copy = el('div');
  copy.append(
    el('p', 'eyebrow', 'Mis cartuchos'),
    el('h2', null, 'Cartuchos del workspace'),
    el(
      'p',
      null,
      'Estos son los cartuchos disponibles para tu workspace. Aquí ves el estado real ' +
      '(activo, pendiente, pausado, revocado) y puedes reintentar instalaciones fallidas. ' +
      'Para solicitar un cartucho nuevo, abre Marketplace.'
    ),
  );
  const actions = el('div', 'top-actions');
  const selector = workspaceSelector();
  if (selector) actions.appendChild(selector);
  const catalog = el('a', 'btn secondary', 'Ver catálogo');
  catalog.href = '/marketplace';
  const workspace = el('a', 'btn secondary', 'Workspace');
  workspace.href = '/workspace';
  const refresh = el('button', 'btn primary', 'Actualizar');
  refresh.type = 'button';
  refresh.dataset.marketRefresh = 'true';
  actions.append(catalog, workspace, refresh);
  top.append(copy, actions);

  const metrics = el('section', 'metrics');
  metrics.id = 'market-metrics';

  const installs = el('div', 'install-list');
  installs.id = 'market-installations';
  installs.appendChild(el('div', 'empty', 'Cargando cartuchos del workspace...'));

  container.append(
    top,
    metrics,
    panel(
      'Mis cartuchos',
      'Estado actual de los cartuchos solicitados o activos para tu workspace. ' +
      'No es el catálogo comercial — usa “Ver catálogo” para descubrir más.',
      installs,
    ),
  );
  shell.append(renderTopbar(), container);
  root.appendChild(shell);
}

// Customer view — same load function so we share metrics + installations
// data; the renderer just skips the catalog panel.
function renderCustomerAll() {
  renderMetrics();
  renderInstallations();
}

async function loadCustomerCartridges() {
  // We still fetch products so the metrics block (which counts the
  // catalog size in the header) renders correctly; the catalog grid is
  // not painted in customer mode.
  const [productData, installData] = await Promise.all([
    fetchJson('/api/marketplace/products'),
    fetchJson('/api/customer/cartridges'),
  ]);
  view.products = productData.products || [];
  view.installations = installData.installations || [];
  renderCustomerAll();
}

export async function renderCustomerCartridges(root) {
  view.mode = 'customer';
  view.root = root;
  renderCustomerShell(root);
  await loadCustomerCartridges().catch((error) => {
    const host = document.getElementById('market-installations');
    if (host) host.innerHTML = `<div class="empty">Error cargando tus cartuchos: ${esc(error.message)}</div>`;
    toast(error.message || 'Error cargando tus cartuchos.', 'error');
  });
}

export async function renderMarketplaceAdmin(root) {
  view.mode = 'admin';
  view.root = root;
  renderAdminShell(root);
  await loadAdmin().catch((error) => {
    const host = document.getElementById('admin-installations');
    if (host) host.innerHTML = `<div class="empty">Error cargando instalaciones: ${esc(error.message)}</div>`;
    toast(error.message || 'Error cargando instalaciones.', 'error');
  });
}

document.addEventListener('click', (event) => {
  if (!view.root) return;
  const requestButton = event.target.closest('[data-request]');
  if (requestButton) {
    requestActivation(requestButton.dataset.request);
    return;
  }
  const detailButton = event.target.closest('[data-details]');
  if (detailButton) {
    const product = view.products.find((p) => p.cartridge_id === detailButton.dataset.details);
    if (product) toast(product.commercial?.headline || product.description || product.cartridge_id);
    return;
  }
  const retryButton = event.target.closest('[data-retry]');
  if (retryButton) {
    retry(retryButton.dataset.retry);
    return;
  }
  const refresh = event.target.closest('[data-market-refresh]');
  if (refresh) {
    loadMarketplace().then(() => toast('Marketplace actualizado.')).catch((error) => toast(error.message, 'error'));
    return;
  }
  const adminRefresh = event.target.closest('[data-admin-refresh]');
  if (adminRefresh) {
    loadAdmin().then(() => toast('Admin actualizado.')).catch((error) => toast(error.message, 'error'));
    return;
  }
  const adminButton = event.target.closest('[data-admin-action][data-installation]');
  if (adminButton) {
    adminAction(adminButton.dataset.installation, adminButton.dataset.adminAction);
    return;
  }
  const accessToggle = event.target.closest('[data-access-toggle]');
  if (accessToggle) {
    const id = accessToggle.dataset.accessToggle;
    if (view.accessOpen === id) {
      view.accessOpen = null;
      renderAdminRows();
    } else {
      loadAccess(id).catch((error) => toast(error.message || 'No se pudo cargar acceso de usuarios.', 'error'));
    }
  }
});

document.addEventListener('input', (event) => {
  if (event.target?.id === 'market-workspace') {
    view.workspaceId = event.target.value || null;
    loadMarketplace().catch((error) => toast(error.message, 'error'));
    return;
  }
  if (event.target?.id === 'market-search') {
    view.query = event.target.value || '';
    renderProducts();
    return;
  }
  if (event.target?.id === 'admin-install-search') {
    view.query = event.target.value || '';
    renderAdminRows();
  }
});

document.addEventListener('change', (event) => {
  if (!view.root) return;
  const selector = event.target.closest?.('[data-user-access][data-user-id]');
  if (!selector) return;
  setUserAccess(selector.dataset.userAccess, selector.dataset.userId, selector.value);
});
