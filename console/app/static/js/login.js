// Sprint v1.11 — extracted from login.html so the page can ship under a
// strict CSP (no 'unsafe-inline' for scripts). Behaviour identical to the
// previous inline block: theme bootstrap, next-URL whitelist, CSRF token
// reader and the form submit handler.

document.documentElement.dataset.theme = localStorage.getItem('mod-theme') || 'dark';

function nextUrl() {
  const p = new URLSearchParams(location.search);
  const n = p.get('next');
  if (!n) return '/';
  // Same-path relative URLs always allowed.
  if (n.startsWith('/') && !n.startsWith('//')) return n;
  // Allow same-hostname absolute URLs (cross-port within the org / dev),
  // and any localhost target for development.
  try {
    const u = new URL(n);
    if (u.hostname === location.hostname) return u.toString();
    if (location.hostname === 'localhost' && u.hostname === 'localhost') return u.toString();
  } catch (_) {}
  return '/';
}

// Sprint v1.9 — CSRF double-submit cookie. The server seeds `csrf_token`
// on GET /login; we echo it back as a header on every sensitive POST.
// The cookie is NOT HttpOnly so document.cookie can read it.
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
