const BASE = '/api/operations';

async function _json(r) {
  if (r.status === 401) throw new Error('UNAUTHENTICATED');
  if (r.status === 403) throw new Error('FORBIDDEN');
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export const fetchHealth     = () => fetch(`${BASE}/health`,     { credentials: 'same-origin' }).then(_json);
export const fetchMigrations = () => fetch(`${BASE}/migrations`, { credentials: 'same-origin' }).then(_json);
