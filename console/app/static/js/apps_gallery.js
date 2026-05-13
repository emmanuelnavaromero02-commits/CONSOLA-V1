// Sprint v1.11 phase 3 — extracted from apps_gallery.html for strict CSP.

document.documentElement.dataset.theme = localStorage.getItem('mod-theme') || 'dark';

async function loadApps() {
  const container = document.getElementById('apps-container');
  try {
    const r = await fetch('/api/apps');
    const d = await r.json();
    const apps = d.apps || [];

    if (!apps.length) {
      container.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">▦</div>
          <div>No hay aplicaciones publicadas aún.</div>
          <div>Pídele al asistente en Monitor que genere una app analítica.</div>
          <div style="margin-top:16px;font-family:var(--font-mono);font-size:9px;color:var(--text3)">
            Ejemplo: "Genera una app de P&amp;L por Revenue Manager con los datos de pnl_mensual"
          </div>
        </div>`;
      return;
    }

    // Sprint v1.11 phase 3: inline onclick="deleteApp(...)" → data-action on the
    // dynamically-rendered delete button; a single delegated listener at the
    // bottom dispatches.
    container.innerHTML = `<div class="apps-grid">${apps.map(app => `
      <div class="app-card" id="card-${escHtml(app.name)}">
        <div class="app-card-icon">▦</div>
        <div class="app-card-title">${escHtml(app.title)}</div>
        <div class="app-card-desc">${escHtml(app.description || '—')}</div>
        <div class="app-card-meta">Actualizado: ${escHtml(app.updated_at ? app.updated_at.slice(0,16).replace('T',' ') : '—')}</div>
        <div style="display:flex;gap:8px;align-items:center">
          <a class="app-card-btn" href="/apps/${encodeURIComponent(app.name || '')}" target="_blank" style="flex:1">→ ABRIR APP</a>
          <button class="app-card-btn"
                  data-action="delete-app"
                  data-app-name="${escHtml(app.name || '')}"
                  data-app-title="${escHtml(app.title || '')}"
                  title="Eliminar aplicación"
                  style="background:transparent;border-color:#c44;color:#c44;cursor:pointer;padding:6px 10px">🗑</button>
        </div>
      </div>
    `).join('')}</div>`;
  } catch (e) {
    container.innerHTML = `<div class="empty-state"><div>Error cargando apps: ${escHtml(e.message)}</div></div>`;
  }
}

async function deleteApp(name, title) {
  if (!confirm(`¿Eliminar la aplicación "${title}"?\n\nEsto no se puede deshacer.`)) return;
  try {
    const r = await fetch('/api/apps/' + encodeURIComponent(name), { method: 'DELETE' });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      alert('Error eliminando: ' + (d.detail || r.statusText));
      return;
    }
    await loadApps();
  } catch (e) {
    alert('Error: ' + e.message);
  }
}

function escHtml(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('apps-container').addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-action="delete-app"]');
    if (btn) deleteApp(btn.dataset.appName, btn.dataset.appTitle);
  });
  loadApps();
});
