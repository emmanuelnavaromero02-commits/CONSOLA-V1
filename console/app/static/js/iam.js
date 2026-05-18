// Sprint v1.11 phase 3 — extracted from iam.html for strict CSP.
// The body executes after the script is parsed; the original <script> tag
// sat at the bottom of <body>, so the DOM is already available.

    document.documentElement.dataset.theme = localStorage.getItem('mod-theme') || 'light';

    const state = { users: [], sessions: [], audit: [], permissions: null, attempts: [], editing: null };
    const $ = (id) => document.getElementById(id);
    const fmt = (v) => {
      if (!v) return '-';
      const d = new Date(v);
      return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString();
    };
    const set = (id, value) => { $(id).textContent = value == null || value === '' ? '-' : String(value); };

    function showError(id, message) {
      const el = $(id);
      el.style.display = message ? 'block' : 'none';
      el.textContent = message || '';
    }

    function toast(message, type) {
      const item = document.createElement('div');
      item.className = `toast ${type || 'success'}`;
      item.textContent = message;
      $('toast-wrap').appendChild(item);
      window.setTimeout(() => item.remove(), 4200);
    }

    const humanizePermission = (key) => window.ModLabels?.humanizePermission?.(key) || key;
    const humanizePermissionKey = (key) => window.ModLabels?.humanizePermissionKey?.(key) || key;
    const humanizeError = (err) => window.ModLabels?.humanizeError?.(err) || (err?.message || String(err || 'Error'));

    function csrfToken() {
      const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
      return match ? decodeURIComponent(match[1]) : '';
    }

    async function fetchJson(url, options) {
      const init = { ...(options || {}) };
      const method = (init.method || 'GET').toUpperCase();
      const headers = new Headers(init.headers || {});
      if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && !headers.has('X-CSRF-Token')) {
        const token = csrfToken();
        if (token) headers.set('X-CSRF-Token', token);
      }
      init.headers = headers;

      const res = await fetch(url, { credentials: 'same-origin', ...init });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `${res.status} ${res.statusText}`);
      }
      return res.json();
    }

    function clearRows(tbody, cols, message) {
      tbody.replaceChildren();
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = cols;
      td.className = 'empty';
      td.textContent = message;
      tr.appendChild(td);
      tbody.appendChild(tr);
    }

    function td(row, text, className) {
      const cell = document.createElement('td');
      if (className) cell.className = className;
      cell.textContent = text == null || text === '' ? '-' : String(text);
      row.appendChild(cell);
      return cell;
    }

    function button(label, className, onClick) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = className || 'btn';
      btn.textContent = label;
      btn.addEventListener('click', onClick);
      return btn;
    }

    function safeMetadata(obj) {
      if (!obj) return '-';
      if (typeof obj === 'string') return obj;
      const clean = {};
      Object.keys(obj).slice(0, 10).forEach((key) => {
        if (!/token|secret|password|api[_-]?key|cookie|jwt|refresh/i.test(key)) clean[key] = obj[key];
      });
      return JSON.stringify(clean);
    }

    function assignableRoles() {
      const roles = state.permissions?.roles || [];
      const assignable = roles.filter((role) => role.assignable).map((role) => role.name);
      return assignable.length ? assignable : ['user', 'admin'];
    }

    function roleDefinition(name) {
      return (state.permissions?.roles || []).find((role) => role.name === name) || null;
    }

    function rolePermissions(name) {
      const matrix = state.permissions?.matrix || {};
      const row = matrix[name] || {};
      return Object.keys(row).filter((key) => row[key]).sort();
    }

    function populateRoleSelect(select, selected) {
      select.replaceChildren();
      const roles = assignableRoles();
      roles.forEach((role) => {
        const opt = document.createElement('option');
        opt.value = role;
        opt.textContent = role;
        select.appendChild(opt);
      });
      select.value = roles.includes(selected) ? selected : (roles.includes('user') ? 'user' : roles[0]);
    }

    function appendPermissionChips(parent, permissions) {
      const wrap = document.createElement('div');
      wrap.className = 'permission-list';
      if (!permissions.length) {
        const muted = document.createElement('span');
        muted.className = 'muted';
        muted.textContent = 'Sin permisos efectivos.';
        wrap.appendChild(muted);
      } else {
        permissions.slice(0, 22).forEach((key) => {
          const chip = document.createElement('span');
          chip.className = 'permission-chip';
          chip.title = humanizePermission(key);
          chip.textContent = humanizePermissionKey(key);
          wrap.appendChild(chip);
        });
        if (permissions.length > 22) {
          const more = document.createElement('span');
          more.className = 'permission-chip';
          more.textContent = `+${permissions.length - 22}`;
          wrap.appendChild(more);
        }
      }
      parent.appendChild(wrap);
    }

    function renderMetrics() {
      const admins = state.users.filter((u) => ['owner', 'super_admin', 'admin'].includes(u.role)).length;
      const active = state.users.filter((u) => u.is_active).length;
      set('m-users', state.users.length);
      set('m-users-note', `${active} activos / ${state.users.length - active} inactivos`);
      set('m-admins', admins);
      set('m-sessions', state.sessions.length);
      set('m-audit', state.audit.length);
    }

    function renderUsers() {
      const body = $('users-body');
      body.replaceChildren();
      if (!state.users.length) return clearRows(body, 7, 'No hay usuarios.');
      state.users.forEach((u) => {
        const tr = document.createElement('tr');
        td(tr, u.email);
        td(tr, u.name || '-');
        td(tr, u.role);
        td(tr, u.is_active ? 'activo' : 'inactivo');
        td(tr, fmt(u.created_at), 'muted');
        td(tr, fmt(u.last_login), 'muted');
        const actions = document.createElement('td');
        actions.appendChild(button('Editar', 'btn', () => openEditUser(u)));
        actions.appendChild(button('Permisos', 'btn', () => showUserPermissions(u)));
        if (!u.is_active && !u.last_login) actions.appendChild(button('Reinvite', 'btn', () => reinviteUser(u)));
        if (u.is_active) actions.appendChild(button('Restablecer', 'btn', () => sendReset(u)));
        tr.appendChild(actions);
        body.appendChild(tr);
      });
    }

    function renderRoles() {
      const body = $('roles-body');
      body.replaceChildren();
      const rows = state.permissions?.roles || [];
      if (!rows.length) return clearRows(body, 5, 'No hay matriz de permisos.');
      rows.forEach((r) => {
        const tr = document.createElement('tr');
        td(tr, r.name);
        td(tr, r.legacy ? 'compatible con rol existente' : r.builtin ? 'rol integrado' : 'personalizado');
        const assignable = document.createElement('td');
        const badge = document.createElement('span');
        badge.className = r.assignable ? 'pill' : 'pill neutral';
        badge.textContent = r.assignable ? 'Asignable ahora' : 'Solo backend';
        assignable.appendChild(badge);
        tr.appendChild(assignable);
        const perms = document.createElement('td');
        appendPermissionChips(perms, rolePermissions(r.name));
        tr.appendChild(perms);
        td(tr, r.description || '-');
        body.appendChild(tr);
      });
    }

    function showUserPermissions(user) {
      const box = $('user-permissions');
      box.style.display = 'block';
      box.replaceChildren();
      const title = document.createElement('strong');
      title.textContent = `${user.email} · ${user.role}`;
      const role = roleDefinition(user.role);
      const desc = document.createElement('p');
      desc.className = 'muted';
      desc.textContent = role ? role.description : 'Rol no registrado en el registro de permisos; los permisos nuevos se bloquean por defecto.';
      box.append(title, desc);
      appendPermissionChips(box, rolePermissions(user.role));
    }

    function renderSessions() {
      const body = $('sessions-body');
      body.replaceChildren();
      if (!state.sessions.length) return clearRows(body, 6, 'No hay sesiones activas.');
      state.sessions.forEach((s) => {
        const tr = document.createElement('tr');
        td(tr, s.user_email || s.user_id);
        td(tr, s.ip);
        td(tr, s.user_agent, 'muted');
        td(tr, fmt(s.created_at), 'muted');
        td(tr, fmt(s.last_seen), 'muted');
        const actions = document.createElement('td');
        if (s.session_id) actions.appendChild(button('Revocar', 'btn danger', () => revokeSession(s.session_id)));
        else actions.textContent = 'No revocable';
        tr.appendChild(actions);
        body.appendChild(tr);
      });
    }

    function renderAudit() {
      const q = $('audit-filter').value.trim().toLowerCase();
      const rows = q ? state.audit.filter((a) => JSON.stringify(a).toLowerCase().includes(q)) : state.audit;
      const body = $('audit-body');
      body.replaceChildren();
      if (!rows.length) return clearRows(body, 5, 'No hay eventos de auditoría.');
      rows.forEach((a) => {
        const tr = document.createElement('tr');
        td(tr, fmt(a.created_at || a.timestamp), 'muted');
        td(tr, a.user_email || a.email || a.user_id || 'System');
        td(tr, a.action);
        td(tr, a.resource_id || a.target || a.resource_type || '-');
        td(tr, safeMetadata(a.details || a.metadata), 'muted');
        body.appendChild(tr);
      });
    }

    function renderSchemaNotes() {
      const status = state.permissions?.schema_status || {};
      const audit = status.audit_events;
      const note = $('audit-schema-note');
      if (audit && audit.exists === false) {
        note.style.display = 'block';
        note.replaceChildren();
        const strong = document.createElement('strong');
        strong.textContent = 'Migración de auditoría pendiente';
        const text = document.createElement('p');
        text.className = 'muted';
        text.textContent = 'La tabla de auditoría no está aplicada en este volumen de base de datos. El backend es compatible, pero falta la migración infra/init/16_audit_events.sql.';
        note.append(strong, text);
      } else {
        note.style.display = 'none';
      }
    }

    function renderPolicies() {
      const list = $('policies-list');
      list.replaceChildren();
      const sections = {
        ...(state.permissions?.policies || {}),
        ...(state.permissions?.credential_lifetimes || {})
      };
      Object.keys(sections).forEach((key) => {
        const item = document.createElement('div');
        item.className = 'policy';
        const title = document.createElement('strong');
        title.textContent = key.replaceAll('_', ' ');
        const value = document.createElement('span');
        value.className = 'muted';
        value.textContent = sections[key] == null ? 'No configurado' : sections[key];
        item.append(title, value);
        list.appendChild(item);
      });
      const schema = state.permissions?.schema_status || {};
      Object.keys(schema).forEach((key) => {
        const item = document.createElement('div');
        item.className = 'policy';
        const title = document.createElement('strong');
        title.textContent = `${key.replaceAll('_', ' ')} · estructura`;
        const value = document.createElement('span');
        value.className = schema[key].exists ? 'pill' : 'pill warn';
        value.textContent = schema[key].exists ? 'Aplicada' : `Pendiente ${schema[key].migration || ''}`;
        item.append(title, value);
        if (schema[key].missing_columns && schema[key].missing_columns.length) {
          const miss = document.createElement('p');
          miss.className = 'muted';
          miss.textContent = `Columnas faltantes: ${schema[key].missing_columns.join(', ')}`;
          item.appendChild(miss);
        }
        list.appendChild(item);
      });
      const body = $('attempts-body');
      body.replaceChildren();
      if (!state.attempts.length) return clearRows(body, 4, 'No hay intentos de login registrados.');
      state.attempts.forEach((a) => {
        const tr = document.createElement('tr');
        td(tr, a.email);
        td(tr, a.ip);
        td(tr, a.success === true ? 'correcto' : 'fallido');
        td(tr, fmt(a.created_at), 'muted');
        body.appendChild(tr);
      });
    }

    function openCreateUser() {
      state.editing = null;
      $('modal-title').textContent = 'Crear usuario';
      $('user-mode').value = 'create';
      $('user-id').value = '';
      $('user-email').value = '';
      $('user-email').readOnly = false;
      $('user-name').value = '';
      $('user-password').value = '';
      populateRoleSelect($('user-role'), 'user');
      $('password-field').style.display = '';
      $('active-field').style.display = '';
      $('user-active').value = 'true';
      $('user-modal').style.display = 'flex';
    }

    function openInviteUser() {
      state.editing = null;
      $('modal-title').textContent = 'Invitar usuario';
      $('user-mode').value = 'invite';
      $('user-id').value = '';
      $('user-email').value = '';
      $('user-email').readOnly = false;
      $('user-name').value = '';
      $('user-password').value = '';
      populateRoleSelect($('user-role'), 'user');
      $('password-field').style.display = 'none';
      $('active-field').style.display = 'none';
      $('user-modal').style.display = 'flex';
    }

    function openEditUser(user) {
      state.editing = user;
      $('modal-title').textContent = 'Editar usuario';
      $('user-mode').value = 'edit';
      $('user-id').value = user.id;
      $('user-email').value = user.email || '';
      $('user-email').readOnly = true;
      $('user-name').value = user.name || '';
      populateRoleSelect($('user-role'), user.role || 'user');
      $('user-password').value = '';
      $('password-field').style.display = 'none';
      $('user-active').value = user.is_active ? 'true' : 'false';
      $('active-field').style.display = '';
      $('user-modal').style.display = 'flex';
    }

    async function saveUser() {
      const id = $('user-id').value;
      const mode = $('user-mode').value;
      const body = {
        name: $('user-name').value.trim() || null,
        role: $('user-role').value
      };
      if (mode === 'invite') {
        body.email = $('user-email').value.trim();
        const data = await fetchJson('/api/admin/users/invite', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        closeModal();
        await loadUsers();
        toast(data.email_sent ? 'Invitación enviada' : 'Usuario creado; correo no enviado', data.email_sent ? 'success' : 'error');
        return;
      }
      if (mode === 'create') {
        body.email = $('user-email').value.trim();
        body.password = $('user-password').value;
        if (!body.password || body.password.length < 8) throw new Error('La contraseña temporal debe tener al menos 8 caracteres');
        const created = await fetchJson('/api/admin/users', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body)
        });
        if ($('user-active').value === 'false') {
          await fetchJson(`/api/admin/users/${encodeURIComponent(created.id)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_active: false })
          });
        }
        closeModal();
        await loadUsers();
        toast('Usuario creado');
        return;
      }
      body.is_active = $('user-active').value === 'true';
      await fetchJson(`/api/admin/users/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
      });
      closeModal();
      await loadUsers();
      toast('Usuario actualizado');
    }

    async function sendReset(user) {
      if (!window.confirm(`Enviar reset a ${user.email}?`)) return;
      const data = await fetchJson(`/api/admin/users/${encodeURIComponent(user.id)}/send-reset`, { method: 'POST' });
      toast(data.sent ? 'Correo de recuperación enviado' : 'Llave temporal generada; correo no enviado', data.sent ? 'success' : 'error');
    }

    async function reinviteUser(user) {
      if (!window.confirm(`Reenviar invitación a ${user.email}?`)) return;
      const data = await fetchJson(`/api/admin/users/${encodeURIComponent(user.id)}/reinvite`, { method: 'POST' });
      toast(data.email_sent ? 'Invitación reenviada' : 'Llave temporal generada; correo no enviado', data.email_sent ? 'success' : 'error');
    }

    async function revokeSession(sessionId) {
      if (!window.confirm('¿Revocar esta sesión?')) return;
      await fetchJson(`/security/sessions/${encodeURIComponent(sessionId)}`, { method: 'DELETE' });
      await loadSessions();
      toast('Sesión revocada');
    }

    function closeModal() { $('user-modal').style.display = 'none'; }

    async function loadUsers() {
      showError('users-error', '');
      try {
        const data = await fetchJson('/api/admin/users');
        state.users = data.users || [];
        renderUsers();
        renderMetrics();
      } catch (err) {
        showError('users-error', `Usuarios: ${humanizeError(err)}`);
      }
    }

    async function loadSessions() {
      showError('sessions-error', '');
      try {
        state.sessions = await fetchJson('/security/sessions');
        renderSessions();
        renderMetrics();
      } catch (err) {
        showError('sessions-error', `Sesiones: ${humanizeError(err)}`);
      }
    }

    async function loadAudit() {
      showError('audit-error', '');
      try {
        state.audit = await fetchJson('/security/audit');
        renderAudit();
        renderMetrics();
      } catch (err) {
        showError('audit-error', `Auditoría: ${humanizeError(err)}`);
      }
    }

    async function loadPermissions() {
      showError('permissions-error', '');
      try {
        state.permissions = await fetchJson('/security/permissions');
        renderRoles();
        renderSchemaNotes();
        renderTesterOptions();
        renderPolicies();
      } catch (err) {
        showError('permissions-error', `Permisos: ${humanizeError(err)}`);
      }
    }

    function renderTesterOptions() {
      const roleSel = $('tester-role');
      const resSel = $('tester-resource');
      const actionSel = $('tester-action');
      roleSel.replaceChildren();
      resSel.replaceChildren();
      actionSel.replaceChildren();
      const roles = (state.permissions?.roles || []).map((role) => role.name);
      const resources = [
        '/security',
        '/security/audit',
        '/security/sessions',
        '/api/admin/users',
        '/viewer/vault',
        '/api/vault/connections/replicon',
        '/studio',
        '/monitor',
        '/api/pipeline',
        '/datasets'
      ];
      const actions = ['read', 'write', 'revoke', 'reveal', 'run', 'access'];
      roles.forEach((role) => {
        const opt = document.createElement('option');
        opt.value = role;
        opt.textContent = role;
        roleSel.appendChild(opt);
      });
      resources.forEach((resource) => {
        const opt = document.createElement('option');
        opt.value = resource;
        opt.textContent = resource;
        resSel.appendChild(opt);
      });
      actions.forEach((action) => {
        const opt = document.createElement('option');
        opt.value = action;
        const labels = { read: 'ver', write: 'modificar', revoke: 'revocar', reveal: 'revelar', run: 'ejecutar', access: 'acceder' };
        opt.textContent = labels[action] || action;
        actionSel.appendChild(opt);
      });
      if (roles.includes('viewer')) roleSel.value = 'viewer';
      resSel.value = '/security/audit';
      actionSel.value = 'read';
      renderTesterResult().catch((err) => toast(err.message, 'error'));
    }

    async function renderTesterResult() {
      const box = $('tester-result');
      box.replaceChildren();
      const title = document.createElement('strong');
      title.textContent = 'Evaluando...';
      box.appendChild(title);
      const params = new URLSearchParams({
        role: $('tester-role').value,
        resource: $('tester-resource').value,
        action: $('tester-action').value
      });
      const result = await fetchJson(`/security/access-check?${params.toString()}`);
      title.textContent = result.allowed ? 'Permitido' : 'Bloqueado';
      const badge = document.createElement('span');
      badge.className = result.allowed ? 'pill' : 'pill err';
      badge.textContent = result.permission ? humanizePermissionKey(result.permission) : 'sin permiso mapeado';
      const detail = document.createElement('p');
      detail.className = 'muted';
      detail.textContent = `Rol ${result.role}, acción ${$('tester-action').selectedOptions[0]?.textContent || result.action}, recurso ${result.resource}. Fuente: ${result.source}.`;
      box.append(badge, detail);
    }

    async function loadAttempts() {
      showError('attempts-error', '');
      try {
        state.attempts = await fetchJson('/security/login-attempts');
        renderPolicies();
      } catch (err) {
        showError('attempts-error', `Intentos de acceso: ${humanizeError(err)}`);
      }
    }

    async function refreshAll() {
      $('refresh-btn').disabled = true;
      try {
        await Promise.all([loadUsers(), loadSessions(), loadAudit(), loadPermissions(), loadAttempts()]);
      } finally {
        $('refresh-btn').disabled = false;
      }
    }

    function activateTab(tab) {
      const target = document.querySelector(`.nav button[data-tab="${tab}"]`);
      if (!target) return;
      document.querySelectorAll('.nav button[data-tab]').forEach((b) => b.classList.toggle('active', b === target));
      document.querySelectorAll('.panel').forEach((p) => p.classList.toggle('active', p.id === `tab-${tab}`));
    }

    document.querySelectorAll('.nav button[data-tab]').forEach((btn) => {
      btn.addEventListener('click', () => activateTab(btn.dataset.tab));
    });

    // /admin/users is a compatibility URL inside the IAM ecosystem, not a
    // separate page. It opens the Users tab directly without redirecting.
    const _qs = new URLSearchParams(location.search);
    const _preUserId = _qs.get('user_id');
    const _requestedTab = _qs.get('tab');
    if (location.pathname === '/admin/users' || _requestedTab === 'users' || _preUserId) {
      activateTab('users');
    } else if (_requestedTab) {
      activateTab(_requestedTab);
    }
    if (_preUserId) {
      window.__iamPreselectUserId = _preUserId;
    }
    $('refresh-btn').addEventListener('click', refreshAll);
    $('create-user').addEventListener('click', openCreateUser);
    $('invite-user').addEventListener('click', openInviteUser);
    $('modal-close').addEventListener('click', closeModal);
    $('modal-cancel').addEventListener('click', closeModal);
    $('modal-save').addEventListener('click', () => saveUser().catch((err) => toast(humanizeError(err), 'error')));
    $('audit-filter').addEventListener('input', renderAudit);
    $('tester-run').addEventListener('click', () => renderTesterResult().catch((err) => toast(err.message, 'error')));
    $('tester-role').addEventListener('change', () => renderTesterResult().catch((err) => toast(err.message, 'error')));
    $('tester-resource').addEventListener('change', () => renderTesterResult().catch((err) => toast(err.message, 'error')));
    $('tester-action').addEventListener('change', () => renderTesterResult().catch((err) => toast(err.message, 'error')));
    refreshAll();
