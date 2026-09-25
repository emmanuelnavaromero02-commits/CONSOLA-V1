function nextUrl() {
  const p = new URLSearchParams(location.search);
  const n = p.get('next');
  if (!n) return '/';
  if (n.startsWith('/') && !n.startsWith('//')) return n;
  try {
    const u = new URL(n);
    if (u.hostname === location.hostname) return u.toString();
    if (location.hostname === 'localhost' && u.hostname === 'localhost') return u.toString();
  } catch (_) {}
  return '/';
}

function getCsrfToken() {
  const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : '';
}

async function doLogin(ev) {
  ev.preventDefault();
  const btn = document.getElementById('btn-login');
  const err = document.getElementById('err');
  err.textContent = '';
  btn.disabled = true;
  try {
    const r = await fetch('/auth/login', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': getCsrfToken(),
      },
      body: JSON.stringify({
        email: document.getElementById('email').value,
        password: document.getElementById('password').value,
      })
    });
    if (!r.ok) {
      if (r.status === 401) err.textContent = 'Credenciales inválidas';
      else                  err.textContent = 'Error: ' + r.statusText;
      btn.disabled = false;
      return;
    }
    location.href = nextUrl();
  } catch (e) {
    err.textContent = 'Error de red: ' + e.message;
    btn.disabled = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('login-form');
  if (form) form.addEventListener('submit', doLogin);
});
