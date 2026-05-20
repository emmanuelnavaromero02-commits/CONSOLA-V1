// Sprint v1.11 — extracted from reset_password.html for strict CSP.
const TOKEN = new URLSearchParams(location.search).get('token') || '';

// Sprint v1.9 — CSRF cookie reader. Server seeds csrf_token on GET /reset-password.
function getCsrfToken() {
  const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : '';
}

async function loadInfo() {
  try {
    const r = await fetch('/auth/reset/info?token=' + encodeURIComponent(TOKEN));
    const d = await r.json();
    if (!d.valid) { document.getElementById('invalid').style.display = ''; return; }
    document.getElementById('who').textContent = `Cuenta: ${d.email}`;
    document.getElementById('form').style.display = '';
  } catch { document.getElementById('invalid').style.display = ''; }
}

async function doReset(ev) {
  ev.preventDefault();
  const pw = document.getElementById('pw').value;
  const p2 = document.getElementById('pw2').value;
  const err = document.getElementById('err');
  err.textContent = '';
  if (pw !== p2)     { err.textContent = 'Los passwords no coinciden'; return false; }
  if (pw.length < 8) { err.textContent = 'Mínimo 8 caracteres'; return false; }
  const btn = document.getElementById('btn');
  btn.disabled = true;
  try {
    const r = await fetch('/auth/reset-password', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': getCsrfToken(),
      },
      body: JSON.stringify({ token: TOKEN, new_password: pw }),
    });
    if (!r.ok) {
      const e = await r.json().catch(() => ({}));
      err.textContent = e.detail || ('Error: ' + r.statusText);
      btn.disabled = false;
      return false;
    }
    location.href = '/';
  } catch (e) {
    err.textContent = 'Error de red: ' + e.message;
    btn.disabled = false;
  }
  return false;
}

document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('reset-form');
  if (form) form.addEventListener('submit', doReset);
  loadInfo();
});
