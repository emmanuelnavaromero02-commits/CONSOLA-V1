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
