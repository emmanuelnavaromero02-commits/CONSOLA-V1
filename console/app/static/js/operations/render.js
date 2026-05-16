export function renderServices(container, health) {
  container.replaceChildren();
  const h = document.createElement('h2');
  h.textContent = `Servicios (${health.summary.up}/${health.summary.total} UP)`;
  container.appendChild(h);
  const list = document.createElement('div');
  list.className = 'svc-grid';
  for (const s of health.services) {
    const card = document.createElement('div');
    card.className = `svc-card svc-${s.status}`;
    const name = document.createElement('div');
    name.className = 'svc-name';
    name.textContent = s.name;
    const status = document.createElement('div');
    status.className = 'svc-status';
    status.textContent = s.status.toUpperCase();
    card.appendChild(name);
    card.appendChild(status);
    list.appendChild(card);
  }
  container.appendChild(list);
}

export function renderMigrations(container, migrations) {
  container.replaceChildren();
  const h = document.createElement('h2');
  h.textContent = `Migraciones aplicadas (${migrations.length})`;
  container.appendChild(h);
  const ul = document.createElement('ul');
  ul.className = 'mig-list';
  for (const m of migrations) {
    const li = document.createElement('li');
    const t = new Date(m.applied_at);
    const fn = document.createElement('code');
    fn.textContent = m.filename;
    const dt = document.createElement('span');
    dt.className = 'mig-date';
    dt.textContent = t.toLocaleString();
    li.appendChild(fn);
    li.appendChild(dt);
    ul.appendChild(li);
  }
  container.appendChild(ul);
}

export function renderError(el, msg) {
  el.hidden = false;
  el.textContent = msg;
}

export function setVersion(el, v) {
  el.textContent = `v${v}`;
}

// ── Sprint v1.41.1 ────────────────────────────────────────────────────────
// Operational counters table. Plain semantic HTML on purpose — charts are
// scoped to v1.44 (design system refresh). All values go through
// textContent, never innerHTML, so user-controlled cartridge/entity names
// in slowest_entities_7d can't smuggle in markup.

function _fmtSeconds(s) {
  if (s == null) return '—';
  const n = Number(s);
  if (!Number.isFinite(n)) return '—';
  if (n < 60) return `${n.toFixed(1)} s`;
  if (n < 3600) return `${(n / 60).toFixed(1)} min`;
  return `${(n / 3600).toFixed(1)} h`;
}

function _kv(container, label, value) {
  const row = document.createElement('div');
  row.className = 'metric-row';
  const k = document.createElement('span');
  k.className = 'metric-key';
  k.textContent = label;
  const v = document.createElement('span');
  v.className = 'metric-val';
  v.textContent = String(value);
  row.appendChild(k);
  row.appendChild(v);
  container.appendChild(row);
}

export function renderMetrics(container, m) {
  container.replaceChildren();
  const h = document.createElement('h2');
  h.textContent = 'Métricas operacionales (últimas 24h)';
  container.appendChild(h);

  const grid = document.createElement('div');
  grid.className = 'metric-grid';
  _kv(grid, 'Extracciones 24h',     m.extractions_24h ?? 0);
  _kv(grid, 'Errores 24h',          m.errors_24h ?? 0);
  _kv(grid, 'Duración media 24h',   _fmtSeconds(m.avg_duration_seconds));
  _kv(grid, 'Eventos de auditoría 24h', m.audit_events_24h ?? 0);
  container.appendChild(grid);

  const sub = document.createElement('h3');
  sub.textContent = 'Top 5 entidades más lentas (7 días)';
  container.appendChild(sub);

  const slowest = Array.isArray(m.slowest_entities_7d) ? m.slowest_entities_7d : [];
  if (slowest.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'metric-empty';
    empty.textContent = 'Sin corridas exitosas en los últimos 7 días.';
    container.appendChild(empty);
    return;
  }
  const table = document.createElement('table');
  table.className = 'metric-table';
  const thead = document.createElement('thead');
  thead.innerHTML = '<tr><th>Cartridge</th><th>Entidad</th><th>Promedio</th></tr>';
  table.appendChild(thead);
  const tbody = document.createElement('tbody');
  for (const r of slowest) {
    const tr = document.createElement('tr');
    const tdC = document.createElement('td');
    tdC.textContent = r.cartridge_id ?? '—';
    const tdE = document.createElement('td');
    tdE.textContent = r.entity_name ?? '—';
    const tdS = document.createElement('td');
    tdS.textContent = _fmtSeconds(r.avg_sec);
    tr.appendChild(tdC); tr.appendChild(tdE); tr.appendChild(tdS);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  container.appendChild(table);
}
