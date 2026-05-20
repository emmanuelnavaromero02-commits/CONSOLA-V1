function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

function csrfHeaders(base = {}) {
  const token = csrfToken();
  return token ? { ...base, 'X-CSRF-Token': token } : base;
}

async function requestJson(url, options = {}) {
  const method = String(options.method || 'GET').toUpperCase();
  const response = await fetch(url, {
    credentials: 'same-origin',
    ...options,
    method,
    headers: {
      ...(options.headers || {}),
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(!['GET', 'HEAD', 'OPTIONS'].includes(method) ? csrfHeaders() : {}),
    },
  });
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
  const response = await fetch('/studio/import', {
    method: 'POST',
    credentials: 'same-origin',
    headers: csrfHeaders(),
    body: data,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || `${response.status} ${response.statusText}`);
  }
  return payload;
}
