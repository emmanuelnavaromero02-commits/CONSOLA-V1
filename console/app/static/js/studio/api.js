async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    credentials: 'same-origin',
    ...options,
    headers: {
      ...(options.headers || {}),
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
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
    body: data,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || `${response.status} ${response.statusText}`);
  }
  return payload;
}
