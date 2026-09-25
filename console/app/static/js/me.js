function fmt(s) { return s ? String(s).slice(0, 16).replace('T', ' ') : '—'; }

function getCsrfToken() {
  const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : '';
}

async function loadMe() {
  const r = await fetch('/api/me');
  if (!r.ok) { location.href = '/login'; return; }
  const u = await r.json();
  document.getElementById('me-email').textContent = u.email;
  document.getElementById('me-name').textContent = u.name || '—';
  const role = document.getElementById('me-role');
  role.textContent = u.role.toUpperCase();
  role.classList.toggle('admin', u.role === 'admin');
  document.getElementById('me-created').textContent = fmt(u.created_at);
  document.getElementById('me-last').textContent = fmt(u.last_login);
  if (u.must_change_password) {
    document.getElementById('forced-banner').style.display = '';
    document.getElementById('back-link').classList.add('disabled');
    document.getElementById('back-link').textContent = '← INICIO (bloqueado hasta cambiar password)';
  }
}

async function changePassword(ev) {
  ev.preventDefault();
  const cur = document.getElementById('cur').value;
  const nw  = document.getElementById('new').value;
  const cf  = document.getElementById('confirm').value;
  const err = document.getElementById('err');
  const ok  = document.getElementById('ok');
  const btn = document.getElementById('btn');
  err.textContent = ''; ok.textContent = '';
  if (nw !== cf) { err.textContent = 'Los passwords nuevos no coinciden'; return false; }
  if (nw.length < 8) { err.textContent = 'El nuevo password debe tener al menos 8 caracteres'; return false; }
  btn.disabled = true;
  try {
    const r = await fetch('/api/me/change-password', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': getCsrfToken(),
      },
      body: JSON.stringify({ current_password: cur, new_password: nw }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      err.textContent = e.detail || ('Error: ' + r.statusText);
      btn.disabled = false;
      return false;
    }
    ok.textContent = '✓ Password actualizado. Redirigiendo…';
    setTimeout(() => location.href = '/', 800);
  } catch (e) {
    err.textContent = 'Error de red: ' + e.message;
    btn.disabled = false;
  }
  return false;
}

async function doLogout(ev) {
  if (ev) ev.preventDefault();
  await fetch('/auth/logout', {
    method: 'POST',
    headers: { 'X-CSRF-Token': getCsrfToken() },
  });
  location.href = '/login';
}

document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('pw-form');
  if (form) form.addEventListener('submit', changePassword);
  const logout = document.getElementById('logout-link');
  if (logout) logout.addEventListener('click', doLogout);
  loadMe();
});
