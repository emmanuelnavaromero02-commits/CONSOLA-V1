// Sprint v1.11 — extracted from forgot_password.html for strict CSP.

function getCsrfToken() {
  const m = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : '';
}

async function doForgot(ev) {
  ev.preventDefault();
  const email = document.getElementById('email').value.trim();
  const btn = document.getElementById('btn');
  btn.disabled = true;
  try {
    await fetch('/auth/forgot-password', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': getCsrfToken(),
      },
      body: JSON.stringify({ email }),
    });
  } catch (e) { /* generic message regardless */ }
  document.getElementById('f-form').style.display = 'none';
  const msg = document.getElementById('msg');
  msg.style.display = '';
  msg.textContent = '✓ Si la cuenta existe, recibirás un correo con instrucciones en breve.';
}

document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('f-form');
  if (form) form.addEventListener('submit', doForgot);
});
