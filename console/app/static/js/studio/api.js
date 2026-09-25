const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);

export function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

export function apiFetch(url, options = {}) {
  const method = String(options.method || 'GET').toUpperCase();
  const headers = new Headers(options.headers || {});
  if (typeof options.body === 'string' && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (!SAFE_METHODS.has(method)) {
    const token = csrfToken();
    if (token) headers.set('X-CSRF-Token', token);
  }
  return fetch(url, { ...options, method, headers, credentials: 'same-origin' });
}

export async function requestJson(url, options = {}) {
  const response = await apiFetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.detail || data.error || `${response.status} ${response.statusText}`);
  }
  return data;
}

export async function listCartridges() {
  const data = await requestJson('/studio/cartridges');
  return data.cartridges || [];
}

export async function getCartridge(id) {
  return requestJson(`/studio/cartridges/${encodeURIComponent(id)}`);
}

export async function getCartridgeStatus(id) {
  return requestJson(`/studio/cartridges/${encodeURIComponent(id)}/status`);
}

export async function createCartridge(payload) {
  if (typeof window.__studioCreateCartridge === 'function') {
    return window.__studioCreateCartridge(payload);
  }
  return requestJson('/studio/cartridges', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export function exportCartridge(id) {
  if (!id) return;
  window.open(`/studio/cartridges/${encodeURIComponent(id)}/export`, '_blank');
}

export async function importCartridge(file) {
  const data = new FormData();
  data.append('file', file);
  return requestJson('/studio/import', { method: 'POST', body: data });
}
