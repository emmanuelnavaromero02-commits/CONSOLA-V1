import { hasPermission, state } from './state.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function linkButton(label, href, variant = 'secondary', enabled = true, reason = '') {
  const node = document.createElement(enabled ? 'a' : 'span');
  node.className = `home-btn ${variant} ${enabled ? '' : 'disabled'}`.trim();
  node.textContent = enabled ? label : `${label} · ${reason}`;
  if (enabled) node.href = href;
  return node;
}

function chip(text, variant = '') {
  return el('span', `home-mini-chip ${variant}`.trim(), text);
}

function statusDot(status) {
  const dot = el('span', `home-dot ${status === 'online' ? 'ok' : status === 'offline' ? 'off' : ''}`.trim());
  return dot;
}

function renderTopbar() {
  const top = el('header', 'home-topbar');
  const brand = el('div', 'home-brand');
  brand.append(el('span', 'home-mark', 'M'), el('span', null, 'MODecissions PaaS'));

  const nav = el('nav', 'home-nav');
  nav.setAttribute('aria-label', 'Main navigation');
  [
    ['Workspace', state.workspaceUrl],
    ['Monitor', '/monitor'],
    ['Studio', '/studio'],
    ['IAM', '/iam'],
    ['Security', '/security'],
  ].forEach(([label, href]) => {
    const a = el('a', null, label);
    a.href = href;
    nav.appendChild(a);
  });

  const user = el('div', 'home-user');
  const system = el('span', 'home-status-pill');
  system.append(statusDot(state.statuses.mcp?.status), el('span', null, state.statuses.mcp?.label || 'Status not checked'));
  user.append(system, el('span', 'home-role', state.role || 'anonymous'));
  if (state.user?.email) user.append(el('span', null, state.user.email));

  top.append(brand, nav, user);
  return top;
}

function renderHero() {
  const hero = el('section', 'home-hero');
  const copy = el('div', 'home-hero-copy');
  copy.append(
    el('div', 'home-eyebrow', 'Enterprise operational control plane'),
    el('h1', null, 'MODecissions PaaS'),
    el('p', null, 'Operational control plane for data, cartridges, pipelines and decisions.')
  );
  const actions = el('div', 'home-hero-actions');
  actions.append(
    linkButton('Open Workspace', state.workspaceUrl, 'primary', hasPermission('workspace.access'), 'Requires workspace.access'),
    linkButton('Open Monitor', '/monitor', 'secondary', hasPermission('monitor.read'), 'Requires monitor.read'),
    linkButton('Manage IAM', '/iam', 'secondary', hasPermission('iam.users.read'), 'Requires iam.users.read')
  );
  const badges = el('div', 'home-badges');
  badges.append(
    el('span', 'home-badge red', 'IAM protected'),
    el('span', 'home-badge', state.statuses.airflow?.label || 'Airflow not checked'),
    el('span', 'home-badge', 'Replicon cartridge'),
    el('span', 'home-badge', 'Bronze lakehouse')
  );
  copy.append(actions, badges);

  const panel = el('aside', 'home-panel home-hero-panel');
  panel.append(
    el('h2', 'home-panel-title', 'Control plane status'),
    el('p', 'home-panel-copy', 'Home adapts to the signed-in role and only promotes actions that the backend permits. Restricted cards explain the required permission.'),
    renderStatusStrip()
  );
  hero.append(copy, panel);
  return hero;
}

function card({ title, icon, description, href, primary, permission, secondary = [], size = 'secondary-card', kind = '', meta = [] }) {
  const allowed = !permission || hasPermission(permission);
  const node = el('article', `home-card ${size} ${kind} ${allowed ? '' : 'locked'}`.trim());
  const top = el('div', 'home-card-top');
  const copy = el('div');
  copy.append(el('div', 'home-icon', icon), el('h3', null, title));
  top.append(copy);
  node.append(top, el('p', null, description));

  const metaWrap = el('div', 'home-card-meta');
  if (permission) metaWrap.append(chip(allowed ? 'Allowed' : `Requires ${permission}`, allowed ? 'ok' : 'locked'));
  meta.forEach((item) => metaWrap.append(chip(item.text, item.variant || '')));
  node.appendChild(metaWrap);

  const actions = el('div', 'home-card-actions');
  actions.append(linkButton(primary, href, allowed ? 'primary' : 'secondary', allowed, permission ? `Requires ${permission}` : 'Limited access'));
  secondary.forEach((action) => {
    const actionAllowed = !action.permission || hasPermission(action.permission);
    actions.append(linkButton(action.label, action.href, 'secondary', actionAllowed, action.permission ? `Requires ${action.permission}` : 'Not available'));
  });
  node.appendChild(actions);
  return node;
}

function renderSection(title, desc, content) {
  const section = el('section', 'home-section');
  const head = el('div', 'home-section-head');
  const copy = el('div');
  copy.append(el('h2', 'home-section-title', title), el('p', 'home-section-desc', desc));
  head.appendChild(copy);
  section.append(head, content);
  return section;
}

function renderOperational() {
  const grid = el('div', 'home-operational-grid');
  grid.append(
    card({
      title: 'Workspace',
      icon: 'W',
      description: 'Business apps, assistant and Gold data for day-to-day users.',
      href: state.workspaceUrl,
      primary: 'Open Workspace',
      permission: 'workspace.access',
      size: 'primary-card',
      meta: [{ text: 'Apps' }, { text: 'Assistant' }],
      secondary: [{ label: 'Open Apps', href: state.workspaceUrl, permission: 'workspace.access' }],
    }),
    card({
      title: 'Studio',
      icon: 'S',
      description: 'Configure cartridges, entities, transformations and semantic knowledge.',
      href: '/studio',
      primary: 'Open Studio',
      permission: 'studio.read',
      size: 'primary-card',
      meta: [{ text: 'Cartridges' }, { text: 'Datasets' }],
      secondary: [{ label: 'New Cartridge', href: '/studio', permission: 'studio.write' }],
    }),
    card({
      title: 'Monitor',
      icon: 'M',
      description: 'Track pipelines, jobs, datasets and MCP services.',
      href: '/monitor',
      primary: 'Open Monitor',
      permission: 'monitor.read',
      meta: [{ text: state.statuses.mcp?.label || 'MCP not checked' }],
    }),
    card({
      title: 'Decisions',
      icon: 'D',
      description: 'Register decisions, KPIs, actions and operational follow-up.',
      href: '/decisions',
      primary: 'Open Decisions',
      permission: 'workspace.access',
      meta: [{ text: 'KPIs' }, { text: 'Actions' }],
    })
  );
  return renderSection('Operational Workspace', 'Primary modules for business users and operators.', grid);
}

function usersMeta() {
  if (!state.usersSummary) return [{ text: 'Limited access', variant: 'locked' }];
  return [
    { text: `${state.usersSummary.total} users`, variant: 'ok' },
    { text: `${state.usersSummary.active} active` },
    { text: `${state.usersSummary.admins} admins` },
  ];
}

function renderAdministration() {
  const grid = el('div', 'home-admin-grid');
  grid.append(
    card({
      title: 'IAM / Access',
      icon: 'I',
      description: 'Operate users, permissions, sessions and access policies.',
      href: '/iam',
      primary: 'Open IAM',
      permission: 'iam.users.read',
      kind: 'admin',
      meta: [{ text: 'RBAC' }],
      secondary: [{ label: 'Manage users', href: '/admin/users', permission: 'iam.users.read' }],
    }),
    card({
      title: 'Users',
      icon: 'U',
      description: 'Manage accounts, roles, active status and invitations.',
      href: '/admin/users',
      primary: 'Manage Users',
      permission: 'iam.users.read',
      kind: 'admin',
      meta: usersMeta(),
      secondary: [{ label: 'Invite User', href: '/iam', permission: 'iam.users.write' }],
    }),
    card({
      title: 'Security Center',
      icon: 'A',
      description: 'Review audit, sessions, login attempts and security posture.',
      href: '/security',
      primary: 'Open Security',
      permission: 'security.audit.read',
      kind: 'admin',
      meta: [{ text: state.activity.audit || 'Audit not checked' }],
      secondary: [{ label: 'View Audit', href: '/security', permission: 'security.audit.read' }],
    }),
    card({
      title: 'Vault',
      icon: 'V',
      description: 'View masked connection metadata and configure protected integrations.',
      href: '/viewer/vault',
      primary: 'Open Vault',
      permission: 'vault.connections.read',
      kind: 'admin',
      meta: [{ text: 'Secrets masked' }],
      secondary: [{ label: 'Configure Connections', href: '/viewer/vault', permission: 'vault.connections.write' }],
    })
  );
  return renderSection('Administration Center', 'Security and identity modules are separated from operational work.', grid);
}

function renderSystemOps() {
  const panel = el('section', 'home-status-panel');
  const head = el('div', 'home-section-head');
  const copy = el('div');
  copy.append(el('h2', 'home-section-title', 'System Operations'), el('p', 'home-section-desc', 'Technical status is checked only where APIs are available. Unknown means not checked, not online.'));
  head.appendChild(copy);
  panel.appendChild(head);

  const grid = el('div', 'home-status-grid');
  ['console', 'workspace', 'airflow', 'mcp', 'replicon', 'minio'].forEach((key) => {
    const item = state.statuses[key] || { label: 'Not checked', status: 'unknown' };
    const box = el('div', 'home-service');
    const title = el('strong');
    title.append(statusDot(item.status), document.createTextNode(` ${item.name || key}`));
    box.append(title, el('span', null, item.label));
    grid.appendChild(box);
  });
  panel.appendChild(grid);

  const activity = el('div', 'home-activity');
  const pipe = el('div', 'home-activity-item');
  pipe.append(el('strong', null, 'Pipeline / Replicon'), el('p', null, state.activity.pipeline || 'Pipeline status not checked.'));
  const next = el('div', 'home-activity-item');
  const text = hasPermission('pipelines.run') ? 'View pipelines or run a safe Replicon extraction from Monitor/Studio.' : 'Run extraction disabled. Requires pipelines.run.';
  next.append(el('strong', null, 'Next step'), el('p', null, text));
  activity.append(pipe, next);
  panel.appendChild(activity);
  return panel;
}

function renderStatusStrip() {
  const strip = el('div', 'home-status-strip');
  Object.values(state.statuses).slice(0, 4).forEach((item) => {
    const pill = el('span', 'home-status-pill');
    pill.append(statusDot(item.status), el('span', null, `${item.name}: ${item.label}`));
    strip.appendChild(pill);
  });
  return strip;
}

export function renderHome(root) {
  root.replaceChildren();
  const shell = el('div', 'home-shell');
  const container = el('div', 'home-container');
  container.append(renderHero(), renderOperational(), renderAdministration(), renderSystemOps());
  shell.append(renderTopbar(), container);
  root.appendChild(shell);
}
