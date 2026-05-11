/* ──────────────────────────────────────────────────────────────────────────
   vault.js — controller for /viewer/vault

   Replaces the inline script that previously lived in vault.html. No
   business logic changed; the endpoints, payloads and revealed-token cache
   are identical to the legacy script. What is new:

   - No inline event handlers (`onclick=`, `onchange=`). All wired with
     addEventListener + a single delegated handler on the tbody for row
     buttons, so the markup stays static and works under a strict CSP.
   - All dynamic HTML goes through escHtml(). User-controlled values never
     reach innerHTML un-escaped.
   - Esc closes the open modal. Cancel/✕ also close. Backdrop click closes
     only when it lands on the backdrop, not on the box.
   - Save buttons toggle disabled + "Guardando…" while the request flies.
   - 403 from any endpoint surfaces a permission-denied banner inside the
     viewer instead of a blocking native dialog.
   ─────────────────────────────────────────────────────────────────────── */

const state = {
  cartridge: 'replicon',
  currentTab: 'conn',
  editingConnId: null,
  editingSecretKey: null,
  revealedTokens: {},
  revealedSecrets: {},
};

const $ = (id) => document.getElementById(id);

function escHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function showErr(el, msg) {
  if (!el) return;
  el.textContent = msg;
  el.style.display = '';
}

function clearErr(el) {
  if (!el) return;
  el.textContent = '';
  el.style.display = 'none';
}

async function readError(resp) {
  try {
    const data = await resp.json();
    if (data && (data.detail || data.error)) return String(data.detail || data.error);
  } catch (_) { /* fall through */ }
  try {
    const txt = await resp.text();
    return txt || `HTTP ${resp.status}`;
  } catch (_) {
    return `HTTP ${resp.status}`;
  }
}

function showPermissionDenied(msg) {
  const wrap = document.querySelector('.viewer-wrap');
  if (!wrap) return;
  wrap.innerHTML = `
    <div class="permission-denied">
      <h2>Acceso denegado</h2>
      <p>${escHtml(msg || 'No tienes permisos para administrar Vault. Pide a un administrador acceso al recurso vault.connections.read.')}</p>
      <p style="margin-top:14px"><a class="link-btn" href="/">← Volver a la consola</a></p>
    </div>`;
}

/* ── Toast (transient feedback) ─────────────────────────────────────────── */
let _toastTimer = null;
function toast(message, kind = 'success') {
  let el = $('vault-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'vault-toast';
    el.className = 'toast';
    document.body.appendChild(el);
  }
  el.classList.remove('error', 'success', 'warning');
  if (kind) el.classList.add(kind);
  el.textContent = message;
  el.style.display = 'block';
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.style.display = 'none'; }, 3000);
}

/* ── Tabs ───────────────────────────────────────────────────────────────── */
function switchTab(tab) {
  state.currentTab = tab;
  $('pane-conn').style.display    = tab === 'conn'    ? '' : 'none';
  $('pane-secrets').style.display = tab === 'secrets' ? '' : 'none';
  $('tab-conn').classList.toggle('active',    tab === 'conn');
  $('tab-secrets').classList.toggle('active', tab === 'secrets');
  $('tab-conn').setAttribute('aria-selected',    tab === 'conn'    ? 'true' : 'false');
  $('tab-secrets').setAttribute('aria-selected', tab === 'secrets' ? 'true' : 'false');
}

/* ── Cartridge selector ─────────────────────────────────────────────────── */
async function loadCartridgeSelector() {
  try {
    const r = await fetch('/studio/cartridges', { credentials: 'same-origin' });
    if (!r.ok) return;
    const d = await r.json();
    const list = d.cartridges || [];
    const sel = $('cart-sel');
    sel.innerHTML = list.map((c) =>
      `<option value="${escHtml(c.id)}" ${c.id === state.cartridge ? 'selected' : ''}>${escHtml(c.name)} (${escHtml(c.id)})</option>`
    ).join('');
    if (list.length) state.cartridge = sel.value;
  } catch (_) { /* selector is best-effort */ }
}

function onCartridgeChange() {
  state.cartridge = $('cart-sel').value;
  state.revealedTokens = {};
  load();
}

function load() {
  if (state.currentTab === 'conn') loadConnections();
  else loadSecrets();
}

/* ── Connections ────────────────────────────────────────────────────────── */
async function loadConnections() {
  const tbody  = $('conn-tbody');
  const status = $('conn-status');
  tbody.innerHTML = '<tr class="empty-row"><td colspan="5"><span class="loading">Cargando…</span></td></tr>';
  status.innerHTML = '';
  try {
    const r = await fetch(`/api/vault/connections/${encodeURIComponent(state.cartridge)}`, { credentials: 'same-origin' });
    if (r.status === 403) { showPermissionDenied(); return; }
    if (!r.ok) throw new Error(await readError(r));
    const d = await r.json();
    const conns = d.connections || [];
    if (!conns.length) {
      tbody.innerHTML = `<tr class="empty-row"><td colspan="5">
        Sin conexiones para el cartucho <b>${escHtml(state.cartridge)}</b>.
        Usa "+ Agregar" para crear la primera.
      </td></tr>`;
      status.innerHTML = `<span class="status-ok">✓ Vault OK</span> — 0 conexiones`;
      return;
    }
    tbody.innerHTML = conns.map(renderConnRow).join('');
    status.innerHTML = `<span class="status-ok">✓ Vault OK</span> — ${conns.length} conexión${conns.length > 1 ? 'es' : ''}`;
  } catch (e) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="5"><span class="status-err">Error: ${escHtml(e.message)}</span></td></tr>`;
    status.innerHTML = `<span class="status-err">✖ ${escHtml(e.message)}</span>`;
  }
}

function renderConnRow(c) {
  const cid = c.conn_id;
  const revealed = state.revealedTokens[cid];
  const tokenHtml = revealed != null
    ? `<span class="token-cell token-revealed">${escHtml(revealed)}</span>
       <button type="button" class="btn-sm" data-action="hide-token" data-conn-id="${escHtml(cid)}">ocultar</button>`
    : `<span class="token-cell">•••••••••••••</span>
       <button type="button" class="btn-sm" data-action="reveal-token" data-conn-id="${escHtml(cid)}">revelar</button>`;
  return `
    <tr data-conn-id="${escHtml(cid)}">
      <td><span class="conn-id">${escHtml(cid)}</span></td>
      <td><span class="base-url" title="${escHtml(c.base_url || '')}">${escHtml(c.base_url || '—')}</span></td>
      <td><span class="method-badge">${escHtml(c.auth_method || '—')}</span></td>
      <td class="token-cell">${tokenHtml}</td>
      <td class="actions-cell">
        <button type="button" class="btn-sm" data-action="edit-conn" data-conn-id="${escHtml(cid)}">✎ Editar</button>
        <button type="button" class="btn-sm btn-danger-text" data-action="del-conn" data-conn-id="${escHtml(cid)}">✕ Eliminar</button>
      </td>
    </tr>`;
}

async function revealToken(connId) {
  try {
    const r = await fetch(`/api/vault/connections/${encodeURIComponent(state.cartridge)}/${encodeURIComponent(connId)}/reveal`, { credentials: 'same-origin' });
    if (r.status === 403) { toast('Sin permisos para revelar', 'error'); return; }
    if (!r.ok) throw new Error(await readError(r));
    const d = await r.json();
    state.revealedTokens[connId] = d.token || '(vacío)';
    rerenderOneConnRow(connId);
  } catch (e) {
    toast('Error: ' + e.message, 'error');
  }
}

function hideToken(connId) {
  delete state.revealedTokens[connId];
  rerenderOneConnRow(connId);
}

async function rerenderOneConnRow(connId) {
  try {
    const r = await fetch(`/api/vault/connections/${encodeURIComponent(state.cartridge)}`, { credentials: 'same-origin' });
    if (!r.ok) return;
    const d = await r.json();
    const c = (d.connections || []).find((x) => x.conn_id === connId);
    if (!c) return;
    const row = document.querySelector(`#conn-tbody tr[data-conn-id="${CSS.escape(connId)}"]`);
    if (row) row.outerHTML = renderConnRow(c);
  } catch (_) { /* silent — next manual reload will fix */ }
}

/* ── Connection modal ───────────────────────────────────────────────────── */
function openAddConn() {
  state.editingConnId = null;
  $('conn-modal-title').textContent = 'Nueva conexión';
  $('f-cartridge').value  = state.cartridge;
  $('f-conn-id').value    = '';
  $('f-conn-id').readOnly = false;
  $('f-base-url').value   = '';
  $('f-auth-method').value = 'bearer_token';
  $('f-token').value      = '';
  $('f-extra').value      = '';
  clearErr($('conn-modal-err'));
  openModal('conn-modal');
  setTimeout(() => $('f-conn-id').focus(), 30);
}

async function openEditConn(connId) {
  state.editingConnId = connId;
  $('conn-modal-title').textContent = `Editar — ${connId}`;
  $('f-cartridge').value  = state.cartridge;
  $('f-conn-id').value    = connId;
  $('f-conn-id').readOnly = true;
  clearErr($('conn-modal-err'));
  try {
    const r = await fetch(`/api/vault/connections/${encodeURIComponent(state.cartridge)}/${encodeURIComponent(connId)}/reveal`, { credentials: 'same-origin' });
    if (r.status === 403) { toast('Sin permisos para editar (requiere reveal)', 'error'); return; }
    if (!r.ok) throw new Error(await readError(r));
    const d = await r.json();
    $('f-base-url').value    = d.base_url    || '';
    $('f-auth-method').value = d.auth_method || 'bearer_token';
    $('f-token').value       = d.token       || '';
    const extra = Object.fromEntries(
      Object.entries(d).filter(([k]) => !['conn_id', 'base_url', 'auth_method', 'token'].includes(k))
    );
    $('f-extra').value = Object.keys(extra).length ? JSON.stringify(extra, null, 2) : '';
  } catch (e) {
    showErr($('conn-modal-err'), e.message);
    return;
  }
  openModal('conn-modal');
}

async function saveConn() {
  const connId     = $('f-conn-id').value.trim();
  const baseUrl    = $('f-base-url').value.trim();
  const authMethod = $('f-auth-method').value;
  const token      = $('f-token').value.trim();
  const extraStr   = $('f-extra').value.trim();
  const errEl      = $('conn-modal-err');
  const btn        = $('btn-save-conn');

  if (!connId)  { showErr(errEl, 'Conn ID es requerido'); return; }
  if (!/^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(connId)) {
    showErr(errEl, 'Conn ID inválido: solo letras, números, guiones y guion bajo (máx 64).'); return;
  }
  if (!baseUrl) { showErr(errEl, 'Base URL es requerida'); return; }
  try { new URL(baseUrl); } catch { showErr(errEl, 'Base URL no es una URL válida'); return; }

  let extra = {};
  if (extraStr) {
    try { extra = JSON.parse(extraStr); }
    catch { showErr(errEl, 'JSON inválido en campos adicionales'); return; }
    if (extra === null || typeof extra !== 'object' || Array.isArray(extra)) {
      showErr(errEl, 'Los campos adicionales deben ser un objeto JSON');
      return;
    }
  }

  const body = { base_url: baseUrl, auth_method: authMethod, ...extra };
  if (token) body.token = token;

  btn.disabled = true;
  const originalLabel = btn.textContent;
  btn.textContent = 'Guardando…';
  try {
    const r = await fetch(
      `/api/vault/connections/${encodeURIComponent(state.cartridge)}/${encodeURIComponent(connId)}`,
      { method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }
    );
    if (r.status === 403) { showErr(errEl, 'Sin permisos para escribir en Vault.'); return; }
    if (!r.ok) throw new Error(await readError(r));
    closeAllModals();
    toast(state.editingConnId ? 'Conexión actualizada' : 'Conexión creada', 'success');
    loadConnections();
  } catch (e) {
    showErr(errEl, e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
}

function confirmDeleteConn(connId) {
  $('del-msg').innerHTML =
    `¿Eliminar la conexión <b style="color:var(--amber)">${escHtml(connId)}</b> del cartucho <b>${escHtml(state.cartridge)}</b>?<br>
     <span style="color:var(--text3);font-size:11px">Esta acción no se puede deshacer.</span>`;
  $('del-confirm-btn').dataset.target = 'conn';
  $('del-confirm-btn').dataset.id = connId;
  openModal('del-modal');
  setTimeout(() => $('del-confirm-btn').focus(), 30);
}

async function deleteConn(connId) {
  try {
    const r = await fetch(
      `/api/vault/connections/${encodeURIComponent(state.cartridge)}/${encodeURIComponent(connId)}`,
      { method: 'DELETE', credentials: 'same-origin' }
    );
    if (r.status === 403) { toast('Sin permisos para eliminar', 'error'); return; }
    if (!r.ok) throw new Error(await readError(r));
    closeAllModals();
    toast('Conexión eliminada', 'success');
    loadConnections();
  } catch (e) {
    toast('Error: ' + e.message, 'error');
  }
}

/* ── Secrets ────────────────────────────────────────────────────────────── */
async function loadSecrets() {
  const scope  = $('scope-input').value.trim() || 'platform';
  const tbody  = $('secrets-tbody');
  const status = $('secrets-status');
  $('secrets-title').textContent = `Secrets — scope: ${scope}`;
  tbody.innerHTML = '<tr class="empty-row"><td colspan="3"><span class="loading">Cargando…</span></td></tr>';
  status.innerHTML = '';
  try {
    const r = await fetch(`/api/vault/secrets/${encodeURIComponent(scope)}`, { credentials: 'same-origin' });
    if (r.status === 403) { showPermissionDenied(); return; }
    if (!r.ok) throw new Error(await readError(r));
    const d = await r.json();
    const keys = d.keys || [];
    if (!keys.length) {
      tbody.innerHTML = `<tr class="empty-row"><td colspan="3">Sin secrets en scope <b>${escHtml(scope)}</b></td></tr>`;
      status.innerHTML = `<span class="status-ok">✓ Vault OK</span> — 0 secrets`;
      return;
    }
    tbody.innerHTML = keys.map((k) => renderSecretRow(scope, k)).join('');
    status.innerHTML = `<span class="status-ok">✓ Vault OK</span> — ${keys.length} secret${keys.length > 1 ? 's' : ''}`;
  } catch (e) {
    tbody.innerHTML = `<tr class="empty-row"><td colspan="3"><span class="status-err">Error: ${escHtml(e.message)}</span></td></tr>`;
    status.innerHTML = `<span class="status-err">✖ ${escHtml(e.message)}</span>`;
  }
}

function renderSecretRow(scope, key) {
  const val = state.revealedSecrets[key];
  const valHtml = val !== undefined
    ? `<span class="token-cell token-revealed">${escHtml(val)}</span>
       <button type="button" class="btn-sm" data-action="hide-secret" data-key="${escHtml(key)}" data-scope="${escHtml(scope)}">ocultar</button>`
    : `<span class="token-cell">•••••••••••••</span>
       <button type="button" class="btn-sm" data-action="reveal-secret" data-key="${escHtml(key)}" data-scope="${escHtml(scope)}">revelar</button>`;
  return `
    <tr data-secret-key="${escHtml(key)}">
      <td><span class="conn-id">${escHtml(key)}</span></td>
      <td>${valHtml}</td>
      <td class="actions-cell">
        <button type="button" class="btn-sm" data-action="edit-secret" data-key="${escHtml(key)}" data-scope="${escHtml(scope)}">✎ Editar</button>
        <button type="button" class="btn-sm btn-danger-text" data-action="del-secret" data-key="${escHtml(key)}" data-scope="${escHtml(scope)}">✕ Eliminar</button>
      </td>
    </tr>`;
}

async function revealSecret(key, scope) {
  try {
    const r = await fetch(`/api/vault/secrets/${encodeURIComponent(scope)}/${encodeURIComponent(key)}/reveal`, { credentials: 'same-origin' });
    if (r.status === 403) { toast('Sin permisos para revelar', 'error'); return; }
    if (!r.ok) throw new Error(await readError(r));
    const d = await r.json();
    state.revealedSecrets[key] = String(d.value ?? '(vacío)');
    await loadSecrets();
  } catch (e) {
    toast('Error: ' + e.message, 'error');
  }
}

function hideSecret(key) {
  delete state.revealedSecrets[key];
  loadSecrets();
}

function openAddSecret() {
  state.editingSecretKey = null;
  const scope = $('scope-input').value.trim() || 'platform';
  $('secret-modal-title').textContent = 'Nuevo secret';
  $('fs-scope').value = scope;
  $('fs-key').value   = '';
  $('fs-key').readOnly = false;
  $('fs-value').value = '';
  clearErr($('secret-modal-err'));
  openModal('secret-modal');
  setTimeout(() => $('fs-key').focus(), 30);
}

async function openEditSecret(key, scope) {
  state.editingSecretKey = key;
  $('secret-modal-title').textContent = `Editar — ${key}`;
  $('fs-scope').value = scope;
  $('fs-key').value   = key;
  $('fs-key').readOnly = true;
  clearErr($('secret-modal-err'));
  try {
    const r = await fetch(`/api/vault/secrets/${encodeURIComponent(scope)}/${encodeURIComponent(key)}/reveal`, { credentials: 'same-origin' });
    if (r.status === 403) { toast('Sin permisos para editar (requiere reveal)', 'error'); return; }
    if (!r.ok) throw new Error(await readError(r));
    const d = await r.json();
    $('fs-value').value = String(d.value ?? '');
  } catch (e) {
    showErr($('secret-modal-err'), e.message);
    return;
  }
  openModal('secret-modal');
}

async function saveSecret() {
  const scope  = $('fs-scope').value.trim();
  const key    = $('fs-key').value.trim();
  const value  = $('fs-value').value;
  const errEl  = $('secret-modal-err');
  const btn    = $('btn-save-secret');

  if (!scope) { showErr(errEl, 'Scope es requerido'); return; }
  if (!/^[a-zA-Z][a-zA-Z0-9_-]{0,63}$/.test(scope)) {
    showErr(errEl, 'Scope inválido: solo letras, números, guiones y guion bajo (máx 64).'); return;
  }
  if (!key) { showErr(errEl, 'Key es requerida'); return; }
  if (!/^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$/.test(key)) {
    showErr(errEl, 'Key inválida: solo letras, números, puntos, guiones y guion bajo (máx 128).'); return;
  }

  btn.disabled = true;
  const originalLabel = btn.textContent;
  btn.textContent = 'Guardando…';
  try {
    const r = await fetch(
      `/api/vault/secrets/${encodeURIComponent(scope)}/${encodeURIComponent(key)}`,
      { method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value }) }
    );
    if (r.status === 403) { showErr(errEl, 'Sin permisos para escribir secrets.'); return; }
    if (!r.ok) throw new Error(await readError(r));
    closeAllModals();
    toast(state.editingSecretKey ? 'Secret actualizado' : 'Secret creado', 'success');
    loadSecrets();
  } catch (e) {
    showErr(errEl, e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = originalLabel;
  }
}

function confirmDeleteSecret(key, scope) {
  $('del-msg').innerHTML =
    `¿Eliminar el secret <b style="color:var(--amber)">${escHtml(key)}</b> del scope <b>${escHtml(scope)}</b>?<br>
     <span style="color:var(--text3);font-size:11px">Esta acción no se puede deshacer.</span>`;
  $('del-confirm-btn').dataset.target = 'secret';
  $('del-confirm-btn').dataset.id = key;
  $('del-confirm-btn').dataset.scope = scope;
  openModal('del-modal');
  setTimeout(() => $('del-confirm-btn').focus(), 30);
}

async function deleteSecret(key, scope) {
  try {
    const r = await fetch(
      `/api/vault/secrets/${encodeURIComponent(scope)}/${encodeURIComponent(key)}`,
      { method: 'DELETE', credentials: 'same-origin' }
    );
    if (r.status === 403) { toast('Sin permisos para eliminar', 'error'); return; }
    if (!r.ok) throw new Error(await readError(r));
    closeAllModals();
    toast('Secret eliminado', 'success');
    loadSecrets();
  } catch (e) {
    toast('Error: ' + e.message, 'error');
  }
}

/* ── Modal helpers ─────────────────────────────────────────────────────── */
function openModal(id) {
  document.querySelectorAll('.modal-overlay.open').forEach((el) => el.classList.remove('open'));
  $(id).classList.add('open');
  document.body.dataset.modalOpen = '1';
}
function closeAllModals() {
  document.querySelectorAll('.modal-overlay.open').forEach((el) => el.classList.remove('open'));
  delete document.body.dataset.modalOpen;
}

/* ── Wiring ────────────────────────────────────────────────────────────── */
function wire() {
  $('cart-sel').addEventListener('change', onCartridgeChange);
  $('btn-refresh').addEventListener('click', load);
  $('tab-conn').addEventListener('click', () => switchTab('conn'));
  $('tab-secrets').addEventListener('click', () => switchTab('secrets'));
  $('btn-add-conn').addEventListener('click', openAddConn);
  $('btn-add-secret').addEventListener('click', openAddSecret);
  $('btn-load-secrets').addEventListener('click', loadSecrets);
  $('scope-input').addEventListener('change', loadSecrets);
  $('scope-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); loadSecrets(); }
  });

  // Conn modal buttons
  $('btn-close-conn').addEventListener('click', closeAllModals);
  $('btn-cancel-conn').addEventListener('click', closeAllModals);
  $('btn-save-conn').addEventListener('click', saveConn);

  // Secret modal buttons
  $('btn-close-secret').addEventListener('click', closeAllModals);
  $('btn-cancel-secret').addEventListener('click', closeAllModals);
  $('btn-save-secret').addEventListener('click', saveSecret);

  // Delete modal buttons
  $('btn-close-del').addEventListener('click', closeAllModals);
  $('btn-cancel-del').addEventListener('click', closeAllModals);
  $('del-confirm-btn').addEventListener('click', () => {
    const target = $('del-confirm-btn').dataset.target;
    const id     = $('del-confirm-btn').dataset.id;
    const scope  = $('del-confirm-btn').dataset.scope;
    if (target === 'conn')   deleteConn(id);
    if (target === 'secret') deleteSecret(id, scope);
  });

  // Backdrop click → close only if it landed on the overlay itself.
  document.querySelectorAll('.modal-overlay').forEach((overlay) => {
    overlay.addEventListener('click', (e) => {
      if (e.target === overlay) closeAllModals();
    });
  });

  // Esc closes the open modal.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.body.dataset.modalOpen) closeAllModals();
  });

  // Delegated handlers for row buttons (rendered dynamically).
  $('conn-tbody').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const cid = btn.dataset.connId;
    switch (btn.dataset.action) {
      case 'reveal-token': revealToken(cid); break;
      case 'hide-token':   hideToken(cid); break;
      case 'edit-conn':    openEditConn(cid); break;
      case 'del-conn':     confirmDeleteConn(cid); break;
    }
  });
  $('secrets-tbody').addEventListener('click', (e) => {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const key   = btn.dataset.key;
    const scope = btn.dataset.scope;
    switch (btn.dataset.action) {
      case 'reveal-secret': revealSecret(key, scope); break;
      case 'hide-secret':   hideSecret(key); break;
      case 'edit-secret':   openEditSecret(key, scope); break;
      case 'del-secret':    confirmDeleteSecret(key, scope); break;
    }
  });
}

/* ── Init ──────────────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
  wire();
  loadCartridgeSelector().then(() => loadConnections());
});
