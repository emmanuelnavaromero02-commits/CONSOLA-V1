import { listSettings } from './api.js';
import { renderSettings, renderError } from './render.js';

const statusEl = document.getElementById('settings-status');
const contentEl = document.getElementById('settings-content');
const errorEl = document.getElementById('settings-error');

async function refresh() {
  statusEl.hidden = false;
  statusEl.textContent = 'Cargando configuración…';
  contentEl.hidden = true;
  errorEl.hidden = true;

  try {
    const { settings } = await listSettings();
    renderSettings(contentEl, settings, refresh);
    statusEl.hidden = true;
    contentEl.hidden = false;
  } catch (e) {
    statusEl.hidden = true;
    if (e.message === 'UNAUTHENTICATED') {
      renderError(errorEl, 'Tu sesión expiró. Vuelve a iniciar sesión.');
    } else if (e.message === 'FORBIDDEN') {
      renderError(errorEl, 'Solo los administradores pueden ver esta sección.');
    } else {
      renderError(errorEl, `No se pudo cargar: ${e.message}`);
    }
  }
}

refresh();
