/* ──────────────────────────────────────────────────────────────────────────
   admin_users.js — controller for /admin/users

   Replaces the inline script previously embedded in admin_users.html.
   Behavior identical to the legacy script except for:

   - Zero inline handlers; everything wired with addEventListener and
     event delegation on the table.
   - JSON is NEVER serialised into an HTML attribute. Edit/Delete actions
     resolve the row's user record from a Map keyed by id.
   - Native alert()/confirm() replaced with non-blocking toast() and an
     in-page confirmation modal.
   - 401 and 403 surface distinct, actionable banners. Non-admins still
     see the list (if backend granted iam.users.read) but every mutating
     action is hidden, not just rejected after the click.
   - Every fetch shows loading on the trigger button and re-enables it
     after the request completes, success or failure.
   ─────────────────────────────────────────────────────────────────────── */

const $ = (id) => document.getElementById(id);

const state = {
  me: null,
  users: new Map(),   // id → user record (used by row action handlers)
  canWrite: false,    // ME has role 'admin'; drives action visibility
};

/* ── Utilities ──────────────────────────────────────────────────────── */

function escHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function fmtDate(s) {
  return s ? String(s).slice(0, 10) : '—';
}

function isValidEmail(s) {
  // Minimal RFC-ish gate. Backend is the source of truth.
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(s);
}

async function readError(resp) {
  try {
    const d = await resp.json();
    if (d && (d.detail || d.error)) return String(d.detail || d.error);
  } catch (_) { /* fall through */ }
  try { return (await resp.text()) || `HTTP ${resp.status}`; }
  catch { return `HTTP ${resp.status}`; }
}

/* ── Toast (non-blocking feedback) ───────────────────────────────── */
let _toastTimer = null;
function toast(message, kind = 'success') {
  let el = $('admin-toast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'admin-toast';
    el.className = 'toast';
    document.body.appendChild(el);
  }
  el.classList.remove('error', 'success', 'warning');
  if (kind) el.classList.add(kind);
  el.textContent = message;
  el.style.display = 'block';
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.style.display = 'none'; }, 3500);
}

/* ── Permission banner (replaces the page on 401/403) ────────────── */
function showPermissionDenied(kind /* '401' | '403' */) {
  const wrap = document.querySelector('.shell');
  if (!wrap) return;
  const title = kind === '401' ? 'Sesión requerida' : 'Acceso denegado';
  const msg = kind === '401'
    ? 'Tu sesión expiró o aún no inicias sesión. Vuelve a iniciar para administrar usuarios.'
    : 'Tu cuenta no tiene el permiso iam.users.read para administrar usuarios. Pide acceso a un administrador.';
  const cta = kind === '401'
    ? '<a class="link-btn" href="/login">Ir a login</a>'
    : '<a class="link-btn" href="/">← Volver a la consola</a>';
  wrap.innerHTML = `
    <div class="permission-denied">
      <h2>${escHtml(title)}</h2>
      <p>${escHtml(msg)}</p>
      <p style="margin-top:14px">${cta}</p>
    </div>`;
}

/* ── Auth / Me ───────────────────────────────────────────────────── */

async function loadMe() {
  try {
    const r = await fetch('/auth/me', { credentials: 'same-origin' });
    if (r.ok) {
      const d = await r.json();
      state.me = d.user || null;
    }
  } catch (_) { state.me = null; }
  state.canWrite = !!(state.me && state.me.role === 'admin');
  renderUserBar();
  // Mutating UI must reflect role at first paint, before any row renders.
  $('btn-invite').hidden = !state.canWrite;
  $('btn-invite').disabled = !state.canWrite;
  if (!state.canWrite) {
    $('btn-invite').title = 'Sólo administradores pueden invitar usuarios';
  }
}

function renderUserBar() {
  const bar = $('user-bar');
  bar.replaceChildren();
  if (!state.me) {
    const a = document.createElement('a');
    a.href = '/login';
    a.textContent = 'INICIAR SESIÓN';
    bar.appendChild(a);
    return;
  }
  const me = document.createElement('span');
  me.className = 'me';
  const strong = document.createElement('strong');
  strong.textContent = state.me.email;
  me.appendChild(strong);

  const role = document.createElement('span');
  role.className = 'role-pill' + (state.me.role === 'admin' ? ' admin' : '');
  role.textContent = String(state.me.role || '').toUpperCase();

  const profile = document.createElement('a');
  profile.href = '/me';
  profile.textContent = 'MI PERFIL';

  const logout = document.createElement('a');
  logout.href = '#';
  logout.id = 'link-logout';
  logout.textContent = 'SALIR';
  logout.addEventListener('click', async (e) => {
    e.preventDefault();
    try {
      await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin' });
    } catch (_) { /* ignore — we redirect regardless */ }
    location.href = '/login';
  });

  bar.append(me, role, profile, logout);
}

/* ── Users list ─────────────────────────────────────────────────── */

async function loadUsers() {
  const cont = $('users-container');
  cont.innerHTML = '<div class="empty-state"><span class="loading">Cargando…</span></div>';
  let r;
  try {
    r = await fetch('/api/admin/users', { credentials: 'same-origin' });
  } catch (e) {
    cont.innerHTML = `<div class="empty-state status-err">Error de red: ${escHtml(e.message)}</div>`;
    return;
  }
  if (r.status === 401) { showPermissionDenied('401'); return; }
  if (r.status === 403) { showPermissionDenied('403'); return; }
  if (!r.ok) {
    const msg = await readError(r);
    cont.innerHTML = `<div class="empty-state status-err">Error: ${escHtml(msg)}</div>`;
    return;
  }
  const d = await r.json();
  const users = d.users || [];
  state.users.clear();
  users.forEach((u) => state.users.set(u.id, u));
  if (!users.length) {
    cont.innerHTML = '<div class="empty-state">Sin usuarios registrados.</div>';
    return;
  }
  cont.replaceChildren(buildTable(users));
}

function buildTable(users) {
  const tpl = $('tpl-users-table');
  const table = tpl.content.firstElementChild.cloneNode(true);
  const tbody = table.querySelector('tbody');
  users.forEach((u) => tbody.appendChild(buildRow(u)));
  // One delegated listener on the tbody covers all row actions.
  tbody.addEventListener('click', onRowAction);
  return table;
}

function buildRow(u) {
  const tr = document.createElement('tr');
  tr.dataset.userId = String(u.id);
  const isMe   = state.me && state.me.id === u.id;
  const pending = !u.is_active && !u.last_login;

  // EMAIL
  const tdEmail = document.createElement('td');
  const strong = document.createElement('strong');
  strong.textContent = u.email;
  tdEmail.appendChild(strong);
  if (isMe) {
    const tag = document.createElement('span');
    tag.style.color = 'var(--text3)';
    tag.style.marginLeft = '6px';
    tag.textContent = '(tú)';
    tdEmail.appendChild(tag);
  }
  // NAME
  const tdName = document.createElement('td');
  tdName.style.color = 'var(--text2)';
  tdName.textContent = u.name || '—';
  // ROLE
  const tdRole = document.createElement('td');
  const roleBadge = document.createElement('span');
  roleBadge.className = `badge b-${u.role === 'admin' ? 'admin' : 'user'}`;
  roleBadge.textContent = String(u.role || '').toUpperCase();
  tdRole.appendChild(roleBadge);
  // STATE
  const tdState = document.createElement('td');
  const stateBadge = document.createElement('span');
  stateBadge.className = 'badge';
  if (pending) {
    stateBadge.classList.add('b-pending');
    stateBadge.textContent = 'PENDIENTE';
  } else if (u.is_active) {
    stateBadge.classList.add('b-active');
    stateBadge.textContent = 'ACTIVO';
  } else {
    stateBadge.classList.add('b-inactive');
    stateBadge.textContent = 'INACTIVO';
  }
  tdState.appendChild(stateBadge);
  // CREATED / LAST LOGIN
  const tdCreated = document.createElement('td');
  tdCreated.style.color = 'var(--text2)';
  tdCreated.textContent = fmtDate(u.created_at);
  const tdLast = document.createElement('td');
  tdLast.style.color = 'var(--text2)';
  tdLast.textContent = fmtDate(u.last_login);

  // ACTIONS
  const tdActions = document.createElement('td');
  tdActions.style.textAlign = 'right';
  if (state.canWrite) {
    tdActions.appendChild(actionBtn('edit',     'EDITAR',     'btn-ghost'));
    if (pending) {
      tdActions.appendChild(actionBtn('reinvite', '↻ REENVIAR', 'btn-ghost'));
    } else if (u.is_active) {
      tdActions.appendChild(actionBtn('reset',    '🔑 RESET',   'btn-ghost'));
    }
    if (!isMe) {
      tdActions.appendChild(actionBtn('delete',   '×',          'btn-danger', 'Eliminar usuario'));
    }
  } else {
    const ro = document.createElement('span');
    ro.style.color = 'var(--text3)';
    ro.style.fontSize = '10px';
    ro.textContent = 'solo lectura';
    tdActions.appendChild(ro);
  }

  tr.append(tdEmail, tdName, tdRole, tdState, tdCreated, tdLast, tdActions);
  return tr;
}

function actionBtn(action, label, cls, title) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = cls;
  b.dataset.action = action;
  b.textContent = label;
  if (title) b.title = title;
  b.style.marginLeft = '4px';
  return b;
}

function onRowAction(e) {
  const btn = e.target.closest('button[data-action]');
  if (!btn) return;
  const tr = btn.closest('tr[data-user-id]');
  if (!tr) return;
  const u = state.users.get(Number(tr.dataset.userId));
  if (!u) return;
  switch (btn.dataset.action) {
    case 'edit':     openEditModal(u); break;
    case 'reinvite': askReinvite(u, btn); break;
    case 'reset':    askSendReset(u, btn); break;
    case 'delete':   askDeleteUser(u, btn); break;
  }
}

/* ── Modal helpers ─────────────────────────────────────────────── */

function openModal(id) {
  document.querySelectorAll('.modal-overlay.open').forEach((m) => m.classList.remove('open'));
  $(id).classList.add('open');
  document.body.dataset.modalOpen = '1';
}
function closeAllModals() {
  document.querySelectorAll('.modal-overlay.open').forEach((m) => m.classList.remove('open'));
  delete document.body.dataset.modalOpen;
}

function setModalError(el, msg) { if (!el) return; el.textContent = msg; el.style.display = ''; }
function clearModalError(el)    { if (!el) return; el.textContent = ''; el.style.display = 'none'; }

async function withLoading(btn, label, fn) {
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = label;
  try { return await fn(); }
  finally { btn.disabled = false; btn.textContent = original; }
}

/* ── Invite flow ───────────────────────────────────────────────── */

function openInviteModal() {
  if (!state.canWrite) { toast('Sólo administradores pueden invitar', 'error'); return; }
  $('i-email').value = '';
  $('i-name').value  = '';
  $('i-role').value  = 'user';
  clearModalError($('invite-err'));
  openModal('invite-modal');
  setTimeout(() => $('i-email').focus(), 30);
}

async function submitInvite() {
  const email = $('i-email').value.trim().toLowerCase();
  const name  = $('i-name').value.trim();
  const role  = $('i-role').value;
  const err   = $('invite-err');
  const btn   = $('btn-invite-submit');

  if (!email)            { setModalError(err, 'Email es obligatorio'); return; }
  if (!isValidEmail(email)) { setModalError(err, 'Email no parece válido'); return; }
  if (!role)             { setModalError(err, 'Rol es obligatorio'); return; }

  await withLoading(btn, 'Enviando…', async () => {
    let r;
    try {
      r = await fetch('/api/admin/users/invite', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, name: name || null, role }),
      });
    } catch (e) {
      setModalError(err, 'Error de red: ' + e.message);
      return;
    }
    if (r.status === 401) { setModalError(err, 'Sesión expirada. Inicia sesión de nuevo.'); return; }
    if (r.status === 403) { setModalError(err, 'Sin permiso iam.users.write.'); return; }
    if (r.status === 409) { setModalError(err, 'Ya existe un usuario con ese email.'); return; }
    if (!r.ok) { setModalError(err, await readError(r)); return; }
    const d = await r.json();
    closeAllModals();
    toast(d.email_sent
      ? 'Invitación enviada por correo'
      : 'Usuario creado, pero falló el envío del correo. Revisa la configuración SMTP.',
      d.email_sent ? 'success' : 'warning');
    loadUsers();
  });
}

/* ── Edit flow ─────────────────────────────────────────────────── */

function openEditModal(u) {
  if (!state.canWrite) { toast('Sólo administradores pueden editar', 'error'); return; }
  $('edit-user-id').value = String(u.id);
  $('f-email').value      = u.email;
  $('f-name').value       = u.name || '';
  $('f-role').value       = u.role || 'user';
  $('f-active').value     = u.is_active ? 'true' : 'false';
  // Prevent self-demotion / self-disable in the UI as well as the backend.
  const isMe = state.me && state.me.id === u.id;
  $('f-role').disabled   = isMe;
  $('f-active').disabled = isMe;
  $('edit-self-hint').hidden = !isMe;
  clearModalError($('edit-err'));
  openModal('edit-modal');
  setTimeout(() => $('f-name').focus(), 30);
}

async function submitEdit() {
  const id   = Number($('edit-user-id').value);
  if (!id) return;
  const body = {
    name:      $('f-name').value.trim() || null,
    role:      $('f-role').value,
    is_active: $('f-active').value === 'true',
  };
  const err = $('edit-err');
  const btn = $('btn-edit-submit');

  await withLoading(btn, 'Guardando…', async () => {
    let r;
    try {
      r = await fetch(`/api/admin/users/${id}`, {
        method: 'PATCH',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    } catch (e) { setModalError(err, 'Error de red: ' + e.message); return; }
    if (r.status === 401) { setModalError(err, 'Sesión expirada.'); return; }
    if (r.status === 403) { setModalError(err, 'Sin permiso iam.users.write.'); return; }
    if (r.status === 404) { setModalError(err, 'Usuario no encontrado (¿fue eliminado?).'); return; }
    if (!r.ok) { setModalError(err, await readError(r)); return; }
    closeAllModals();
    toast('Usuario actualizado', 'success');
    loadUsers();
  });
}

/* ── Confirm dialog (reinvite / reset / delete) ───────────────── */

function askConfirm({ title, message, confirmLabel, kind, onConfirm }) {
  $('confirm-title').textContent   = title;
  $('confirm-message').textContent = message;
  const btn = $('confirm-ok');
  btn.textContent = confirmLabel || 'Confirmar';
  btn.classList.remove('btn-primary', 'btn-danger');
  btn.classList.add(kind === 'danger' ? 'btn-danger' : 'btn-primary');
  btn.onclick = async () => {
    btn.disabled = true;
    try { await onConfirm(btn); }
    finally { btn.disabled = false; }
  };
  openModal('confirm-modal');
  setTimeout(() => btn.focus(), 30);
}

function askReinvite(u) {
  askConfirm({
    title: 'Reenviar invitación',
    message: `¿Reenviar invitación a "${u.email}"?`,
    confirmLabel: 'Reenviar',
    onConfirm: async () => {
      let r;
      try {
        r = await fetch(`/api/admin/users/${u.id}/reinvite`, { method: 'POST', credentials: 'same-origin' });
      } catch (e) { toast('Error de red: ' + e.message, 'error'); return; }
      if (r.status === 400) { toast('El usuario ya está activo; usa "RESET" en su lugar.', 'warning'); closeAllModals(); return; }
      if (r.status === 401 || r.status === 403) { toast('Sin permiso para reenviar', 'error'); closeAllModals(); return; }
      if (!r.ok) { toast('Error: ' + await readError(r), 'error'); return; }
      const d = await r.json();
      closeAllModals();
      toast(d.email_sent ? 'Invitación reenviada' : 'Token generado, pero el envío falló',
            d.email_sent ? 'success' : 'warning');
    },
  });
}

function askSendReset(u) {
  askConfirm({
    title: 'Enviar reset de password',
    message: `¿Enviar link de reset a "${u.email}"?`,
    confirmLabel: 'Enviar',
    onConfirm: async () => {
      let r;
      try {
        r = await fetch(`/api/admin/users/${u.id}/send-reset`, { method: 'POST', credentials: 'same-origin' });
      } catch (e) { toast('Error de red: ' + e.message, 'error'); return; }
      if (r.status === 404) { toast('Usuario no encontrado o inactivo', 'warning'); closeAllModals(); return; }
      if (r.status === 401 || r.status === 403) { toast('Sin permiso para enviar reset', 'error'); closeAllModals(); return; }
      if (!r.ok) { toast('Error: ' + await readError(r), 'error'); return; }
      const d = await r.json();
      closeAllModals();
      toast(d.sent ? 'Email de reset enviado' : 'Token generado, pero el envío falló',
            d.sent ? 'success' : 'warning');
    },
  });
}

function askDeleteUser(u) {
  askConfirm({
    title: 'Eliminar usuario',
    message: `¿Eliminar al usuario "${u.email}"? Esta acción no se puede deshacer.`,
    confirmLabel: 'Eliminar',
    kind: 'danger',
    onConfirm: async () => {
      let r;
      try {
        r = await fetch(`/api/admin/users/${u.id}`, { method: 'DELETE', credentials: 'same-origin' });
      } catch (e) { toast('Error de red: ' + e.message, 'error'); return; }
      if (r.status === 400) { toast('No puedes eliminar tu propia cuenta.', 'warning'); closeAllModals(); return; }
      if (r.status === 401 || r.status === 403) { toast('Sin permiso para eliminar', 'error'); closeAllModals(); return; }
      if (r.status === 404) { toast('Usuario no encontrado', 'warning'); closeAllModals(); return; }
      if (!r.ok) { toast('Error: ' + await readError(r), 'error'); return; }
      closeAllModals();
      toast('Usuario eliminado', 'success');
      loadUsers();
    },
  });
}

/* ── Wiring ───────────────────────────────────────────────────── */

function wire() {
  $('btn-invite').addEventListener('click', openInviteModal);

  // Invite modal
  $('btn-invite-close').addEventListener('click', closeAllModals);
  $('btn-invite-cancel').addEventListener('click', closeAllModals);
  $('btn-invite-submit').addEventListener('click', submitInvite);
  $('i-email').addEventListener('keydown', (e) => { if (e.key === 'Enter') submitInvite(); });

  // Edit modal
  $('btn-edit-close').addEventListener('click', closeAllModals);
  $('btn-edit-cancel').addEventListener('click', closeAllModals);
  $('btn-edit-submit').addEventListener('click', submitEdit);

  // Confirm modal
  $('btn-confirm-close').addEventListener('click', closeAllModals);
  $('btn-confirm-cancel').addEventListener('click', closeAllModals);

  // Backdrops + Esc.
  document.querySelectorAll('.modal-overlay').forEach((overlay) => {
    overlay.addEventListener('click', (e) => { if (e.target === overlay) closeAllModals(); });
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && document.body.dataset.modalOpen) closeAllModals();
  });
}

document.addEventListener('DOMContentLoaded', async () => {
  wire();
  await loadMe();
  await loadUsers();
});
