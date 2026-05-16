import { fetchHealth, fetchMigrations, fetchMetrics } from './api.js';
import { renderServices, renderMigrations, renderMetrics, renderError, setVersion } from './render.js';

const svcEl  = document.getElementById('operations-services');
const migEl  = document.getElementById('operations-migrations');
const metEl  = document.getElementById('operations-metrics');
const errEl  = document.getElementById('operations-error');
const verEl  = document.getElementById('operations-version');
const btn    = document.getElementById('operations-refresh');

async function refresh() {
  errEl.hidden = true;
  try {
    // Fetch every panel in parallel. Metrics may fail (DB read latency,
    // empty tables on a fresh install) without sinking the rest of the
    // page, so settle individually instead of Promise.all.
    const [hRes, mRes, opRes] = await Promise.allSettled([
      fetchHealth(),
      fetchMigrations(),
      fetchMetrics(),
    ]);
    if (hRes.status === 'fulfilled') {
      setVersion(verEl, hRes.value.version);
      renderServices(svcEl, hRes.value);
    } else { throw hRes.reason; }

    if (mRes.status === 'fulfilled') {
      renderMigrations(migEl, mRes.value.migrations);
    } else { throw mRes.reason; }

    if (opRes.status === 'fulfilled') {
      renderMetrics(metEl, opRes.value);
    } else {
      // Soft fail — leave the metrics panel showing the previous render
      // (or empty on first load) rather than aborting the whole page.
      metEl.replaceChildren();
      const note = document.createElement('p');
      note.className = 'metric-empty';
      note.textContent = `Métricas no disponibles: ${opRes.reason?.message ?? 'error'}`;
      metEl.appendChild(note);
    }
  } catch (e) {
    const msg = e.message === 'UNAUTHENTICATED' ? 'Tu sesión expiró.'
              : e.message === 'FORBIDDEN'       ? 'Solo administradores.'
              :                                   `Error: ${e.message}`;
    renderError(errEl, msg);
  }
}

btn.addEventListener('click', refresh);
refresh();
