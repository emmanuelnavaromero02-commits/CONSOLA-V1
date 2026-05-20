// Sprint v1.11 — extracted from activate.html for strict CSP.
const TOKEN = new URLSearchParams(location.search).get('token') || '';

async function loadInfo() {
  try {
    const r = await fetch('/auth/activate/info?token=' + encodeURIComponent(TOKEN));
    const d = await r.json();
    if (!d.valid) { document.getElementById('invalid').style.display = ''; return; }
    document.getElementById('who').textContent = `Hola${d.name ? ' ' + d.name : ''} · ${d.email}`;
    document.getElementById('form').style.display = '';
  } catch { document.getElementById('invalid').style.display = ''; }
}

async function doActivate(ev) {
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
    const r = await fetch('/auth/activate', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
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
  const form = document.getElementById('activate-form');
  if (form) form.addEventListener('submit', doActivate);
  loadInfo();
});
