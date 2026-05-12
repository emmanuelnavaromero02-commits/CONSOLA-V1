async function getJson(url) {
  const response = await fetch(url, { credentials: 'same-origin' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(data.detail || `${response.status} ${response.statusText}`);
    error.status = response.status;
    throw error;
  }
  return data;
}

export async function fetchCurrentUser() {
  const data = await getJson('/auth/me');
  return data.user || null;
}

export async function fetchConfig() {
  return getJson('/api/config');
}

export async function fetchMcpServers() {
  return getJson('/api/mcp/servers');
}

export async function fetchPermissions() {
  return getJson('/security/permissions');
}

export async function fetchAdminUsers() {
  return getJson('/api/admin/users');
}

export async function fetchAudit() {
  return getJson('/security/audit');
}

export async function fetchPipeline() {
  return getJson('/api/pipeline');
}

/**
 * GET /api/system/info — returns {version, env, service}.
 * Requires authenticated session. Throws on non-2xx.
 */
export async function fetchSystemInfo() {
  const response = await fetch('/api/system/info', { credentials: 'same-origin' });
  if (!response.ok) throw new Error(`system info failed: ${response.status}`);
  return response.json();
}
