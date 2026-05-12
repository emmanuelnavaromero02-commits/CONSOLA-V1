/**
 * Settings API client.
 * All endpoints require admin role (RBAC enforced server-side).
 * Cookies/session are sent via credentials: 'same-origin'.
 */

const BASE = '/api/settings';

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
  });
  return jsonOrThrow(r);
}

export async function updateSetting(key, value) {
  const r = await fetch(`${BASE}/${encodeURIComponent(key)}`, {
    method: 'PUT',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value }),
  });
  return jsonOrThrow(r);
}

export async function rotateSecret(key) {
  const r = await fetch(`${BASE}/${encodeURIComponent(key)}/rotate`, {
    method: 'POST',
    credentials: 'same-origin',
  });
  return jsonOrThrow(r);
}
