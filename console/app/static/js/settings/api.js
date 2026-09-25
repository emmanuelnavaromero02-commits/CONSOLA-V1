const BASE = '/api/settings';

function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

function csrfHeaders(base = {}) {
  const token = csrfToken();
  return token ? { ...base, 'X-CSRF-Token': token } : base;
}

async function jsonOrThrow(response) {
  if (response.status === 401) throw new Error('UNAUTHENTICATED');
  if (response.status === 403) throw new Error('FORBIDDEN');
  if (!response.ok) {
    let detail = '';
    try { detail = (await response.json()).detail || ''; } catch {}
    throw new Error(`HTTP ${response.status}${detail ? ': ' + detail : ''}`);
  }
  return response.json();
}

export async function listSettings() {
  const r = await fetch(BASE, { credentials: 'same-origin' });
  return jsonOrThrow(r);
}

export async function getSetting(key) {
  const r = await fetch(`${BASE}/${encodeURIComponent(key)}`, { credentials: 'same-origin' });
  return jsonOrThrow(r);
}

export async function revealSetting(key) {
  const r = await fetch(`${BASE}/${encodeURIComponent(key)}/reveal`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: csrfHeaders(),
  });
  return jsonOrThrow(r);
}

export async function updateSetting(key, value) {
  const r = await fetch(`${BASE}/${encodeURIComponent(key)}`, {
    method: 'PUT',
    credentials: 'same-origin',
    headers: csrfHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ value }),
  });
  return jsonOrThrow(r);
}

export async function rotateSecret(key) {
  const r = await fetch(`${BASE}/${encodeURIComponent(key)}/rotate`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: csrfHeaders(),
  });
  return jsonOrThrow(r);
}
