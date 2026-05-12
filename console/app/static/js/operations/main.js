import { fetchHealth, fetchMigrations } from './api.js';
import { renderServices, renderMigrations, renderError, setVersion } from './render.js';

const svcEl = document.getElementById('operations-services');
const migEl = document.getElementById('operations-migrations');
const errEl = document.getElementById('operations-error');
const verEl = document.getElementById('operations-version');
const btn   = document.getElementById('operations-refresh');

async function refresh() {
  errEl.hidden = true;
  try {
    const [h, m] = await Promise.all([fetchHealth(), fetchMigrations()]);
    setVersion(verEl, h.version);
    renderServices(svcEl, h);
    renderMigrations(migEl, m.migrations);
  } catch (e) {
    const msg = e.message === 'UNAUTHENTICATED' ? 'Tu sesión expiró.'
              : e.message === 'FORBIDDEN'       ? 'Solo administradores.'
              :                                   `Error: ${e.message}`;
    renderError(errEl, msg);
  }
}

btn.addEventListener('click', refresh);
refresh();
