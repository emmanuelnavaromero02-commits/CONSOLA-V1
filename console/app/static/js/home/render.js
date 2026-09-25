import { renderTopbar } from './navigation.js';
import { humanizeTerm } from '../i18n/labels.js';
import { hasPermission, state } from './state.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function linkButton(label, href, variant = 'secondary', enabled = true, reason = '') {
  if (!enabled) return null;
  const node = document.createElement(enabled ? 'a' : 'span');
  node.className = `home-btn ${variant} ${enabled ? '' : 'disabled'}`.trim();
  node.textContent = label;
  if (enabled) node.href = href;
  return node;
}

function appendIf(parent, ...nodes) {
  nodes.filter(Boolean).forEach((node) => parent.append(node));
}

function chip(text, variant = '') {
  return el('span', `home-mini-chip ${variant}`.trim(), text);
}

function statusDot(status) {
  return el('span', `home-dot ${status === 'online' ? 'ok' : status === 'offline' ? 'off' : ''}`.trim());
}

function isAdminUser() {
  const role = String(state.user?.role || state.role || '').toLowerCase();
  return ['admin', 'owner', 'super_admin'].includes(role);
}

function renderHero() {
  const hero = el('section', 'home-hero');
  const copy = el('div', 'home-hero-copy');
  copy.append(
    el('div', 'home-eyebrow', 'Panel operativo'),
    el('h1', null, 'Panel operativo'),
    el('p', null, 'Administra tus datos, fuentes, permisos y procesos desde un solo lugar.')
  );

  const actions = el('div', 'home-hero-actions');
  appendIf(
    actions,
    linkButton('Abrir Workspace', '/workspace', 'primary', hasPermission('workspace.access')),
    linkButton('Sala de Control', '/control-room', 'secondary', hasPermission('workspace.access')),
    linkButton('Abrir Copiloto', '/copilot', 'secondary', hasPermission('copilot.use')),
  );
  if (isAdminUser()) {
    appendIf(
      actions,
      linkButton('Abrir Monitor', '/monitor', 'secondary', hasPermission('monitor.read'))
    );
  }

  const badges = el('div', 'home-badges');
  badges.append(
    el('span', 'home-badge red', 'IAM protegido'),
    el('span', 'home-badge', state.statuses.airflow?.label || 'Airflow no verificado'),
    el('span', 'home-badge', 'Fuente de datos Replicon'),
    el('span', 'home-badge', 'Datos preparados')
  );
  copy.append(actions, badges);

  const panel = el('aside', 'home-panel home-hero-panel');
  panel.append(
    el('h2', 'home-panel-title', 'Lo importante ahora'),
    el('p', 'home-panel-copy', 'El panel se adapta a tu rol y muestra accesos, bloqueos y próximos pasos con lenguaje claro.'),
    renderStatusStrip()
  );
  hero.append(copy, panel);
  return hero;
}

function card({ title, icon, description, href, primary, permission, secondary = [], size = 'secondary-card', kind = '', meta = [], adminOnly = false }) {
  const allowed = (!adminOnly || isAdminUser()) && (!permission || hasPermission(permission));
  if (!allowed) return null;
  const node = el('article', `home-card ${size} ${kind}`.trim());
  const top = el('div', 'home-card-top');
  const copy = el('div');
  copy.append(el('div', 'home-icon', icon), el('h3', null, title));
  top.append(copy);
  node.append(top, el('p', null, description));

  const metaWrap = el('div', 'home-card-meta');
  if (permission) metaWrap.append(chip('Disponible', 'ok'));
  meta.forEach((item) => metaWrap.append(chip(item.text, item.variant || '')));
  node.appendChild(metaWrap);

  const actions = el('div', 'home-card-actions');
  appendIf(actions, linkButton(primary, href, 'primary', true));
  secondary.forEach((action) => {
    const actionAllowed = (!action.adminOnly || isAdminUser()) && (!action.permission || hasPermission(action.permission));
    appendIf(actions, linkButton(action.label, action.href, 'secondary', actionAllowed));
  });
  node.appendChild(actions);
  return node;
}

function appendCard(parent, config) {
  appendIf(parent, card(config));
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
  appendCard(
    grid,
    {
      title: 'Workspace',
      icon: 'W',
      description: 'Área diaria para apps, decisiones, memoria y aprobaciones visibles para el cliente.',
      href: '/workspace',
      primary: 'Abrir Workspace',
      permission: 'workspace.access',
      size: 'primary-card',
      meta: [{ text: 'Apps' }, { text: 'Decisiones' }, { text: 'Workspace' }],
    }
  );
  appendCard(
    grid,
    {
      title: 'Sala de Control',
      icon: 'Ω',
      description: 'Dashboard operativo para anomalías, investigación, decisiones y aprobaciones auditadas.',
      href: '/control-room',
      primary: 'Abrir Sala',
      permission: 'workspace.access',
      size: 'primary-card',
      meta: [{ text: 'Anomalías' }, { text: 'Decisiones' }, { text: 'Auditoría' }],
    }
  );
  appendCard(
    grid,
    {
      title: 'Copiloto',
      icon: 'AI',
      description: 'Asistente operativo para consultar información, preparar respuestas y solicitar acciones aprobadas.',
      href: '/copilot',
      primary: 'Abrir Copiloto',
      permission: 'copilot.use',
      size: 'primary-card',
      meta: [{ text: 'Chat' }, { text: 'Acciones' }, { text: 'Aprobaciones' }],
    }
  );
  appendCard(
    grid,
    {
      title: 'Marketplace',
      icon: 'MK',
      description: 'Catálogo comercial para conocer cartuchos, pedir activación y revisar estado del workspace.',
      href: '/marketplace',
      primary: 'Abrir Marketplace',
      permission: 'marketplace.read',
      size: 'primary-card',
      meta: [{ text: 'Catálogo' }, { text: 'Solicitud' }, { text: 'Estado' }],
    }
  );
  appendCard(
    grid,
    {
      title: 'Monitor',
      icon: 'M',
      description: 'Revisa flujos automáticos, trabajos recientes y salud operativa sin entrar a Studio.',
      href: '/monitor',
      primary: 'Abrir Monitor',
      permission: 'monitor.read',
      size: 'primary-card',
      meta: [{ text: state.statuses.mcp?.label || 'Servicios internos no verificados' }],
    }
  );
  if (isAdminUser()) {
    appendCard(
      grid,
      {
        title: 'Studio interno',
        icon: 'S',
        description: 'Herramienta técnica para raw, silver y gold: entidades, transformaciones y ejecuciones.',
        href: '/studio',
        primary: 'Abrir Studio',
        permission: 'studio.read',
        adminOnly: true,
        meta: [{ text: 'Interno' }, { text: 'Datos' }, { text: 'Pipelines' }],
      }
    );
  }
  return renderSection('Trabajo operativo', 'Lo que usas para trabajar con datos, fuentes y decisiones.', grid);
}

function usersMeta() {
  if (!state.usersSummary) return [{ text: 'Acceso limitado', variant: 'locked' }];
  return [
    { text: `${state.usersSummary.total} usuarios`, variant: 'ok' },
    { text: `${state.usersSummary.active} activos` },
    { text: `${state.usersSummary.admins} admins` },
  ];
}

function renderAdministration() {
  const grid = el('div', 'home-admin-grid');
  [
    {
      title: 'IAM / Accesos',
      icon: 'I',
      description: 'Administra accesos, roles, sesiones y reglas visibles.',
      href: '/iam',
      primary: 'Abrir IAM',
      permission: 'iam.users.read',
      kind: 'admin',
      meta: [{ text: 'Permisos por rol' }, ...usersMeta()],
      secondary: [{ label: 'Gestionar usuarios', href: '/iam?tab=users', permission: 'iam.users.read' }],
    },
    {
      title: 'Centro de seguridad',
      icon: 'A',
      description: 'Revisa auditoría, sesiones e intentos de acceso.',
      href: '/security',
      primary: 'Abrir Seguridad',
      permission: 'security.audit.read',
      kind: 'admin',
      meta: [{ text: state.activity.audit || 'Auditoría no verificada' }],
    },
    {
      title: 'Vault / Credenciales',
      icon: 'V',
      description: 'Administra credenciales de cartuchos con conn_id, base URL, método auth, token y JSON adicional.',
      href: '/viewer/vault',
      primary: 'Configurar credenciales',
      permission: 'vault.connections.read',
      kind: 'admin',
      meta: [{ text: 'Replicon' }, { text: 'SAP' }, { text: 'Secretos ocultos' }],
      secondary: [{ label: 'Agregar conexión', href: '/viewer/vault', permission: 'vault.connections.write' }],
    },
    {
      title: 'Gestión de cartuchos',
      icon: 'GC',
      description: 'Aprueba, pausa, revoca y reactiva instalaciones por cliente, workspace y cartucho.',
      href: '/admin/installations',
      primary: 'Abrir gestión',
      permission: 'marketplace.admin',
      adminOnly: true,
      kind: 'admin',
      meta: [{ text: 'Licencias' }, { text: 'Instalaciones' }, { text: 'SaaS' }],
    },
    {
      title: 'Agentes',
      icon: 'G',
      description: 'Configura agentes especializados por cartucho con prompt, tools permitidas, modelo, personalidad, RAG y agenda.',
      href: '/agents',
      primary: 'Administrar agentes',
      permission: 'studio.write',
      kind: 'admin',
      meta: [{ text: 'Prompt' }, { text: 'Tools' }, { text: 'Runs' }],
    },
    {
      title: 'Configuración',
      icon: 'C',
      description: 'Credenciales, integraciones, feature flags y rotación de secretos.',
      href: '/settings',
      primary: 'Abrir Configuración',
      permission: 'settings.read',
      kind: 'admin',
      meta: [{ text: 'Editable desde UI' }],
    },
    {
      title: 'Operaciones',
      icon: 'O',
      description: 'Versión, migraciones, soporte técnico y salud de servicios de la plataforma.',
      href: '/operations',
      primary: 'Abrir Operaciones',
      permission: 'operations.read',
      kind: 'admin',
      meta: [{ text: 'Health en vivo' }],
    },
  ].forEach((config) => appendCard(grid, config));
  return renderSection('Administración', 'Identidad, seguridad y accesos viven separados del trabajo diario.', grid);
}

function renderDataProcesses() {
  const grid = el('div', 'home-admin-grid');
  [
    {
      title: humanizeTerm('datasets'),
      icon: 'DT',
      description: 'Explora reportes y tablas preparadas para análisis.',
      href: '/viewer/datasets',
      primary: 'Ver reportes',
      permission: 'datasets.read',
      kind: 'system',
      meta: [{ text: 'Datos preparados' }],
      adminOnly: true,
    },
    {
      title: humanizeTerm('pipelines'),
      icon: 'F',
      description: 'Revisa ejecuciones, estados y próximos procesos.',
      href: '/monitor',
      primary: 'Ver flujos',
      permission: 'pipelines.read',
      kind: 'system',
    },
    {
      title: 'Fuente Replicon',
      icon: 'R',
      description: 'Fuente de datos operativa para extracción y gobierno de datos Replicon.',
      href: '/studio',
      primary: 'Abrir en Studio',
      permission: 'studio.read',
      kind: 'system',
      meta: [{ text: state.statuses.replicon?.label || 'No verificado' }],
      adminOnly: true,
    },
    {
      title: 'Trabajos',
      icon: 'T',
      description: 'Consulta historial y detalle de trabajos recientes.',
      href: '/viewer/jobs',
      primary: 'Ver trabajos',
      permission: 'monitor.read',
      kind: 'system',
      adminOnly: true,
    },
  ].forEach((config) => appendCard(grid, config));
  return renderSection('Datos y procesos', 'Flujos automáticos, fuentes y reportes en una zona operativa compacta.', grid);
}

function renderSystemOps() {
  const panel = el('section', 'home-status-panel');
  const head = el('div', 'home-section-head');
  const copy = el('div');
  copy.append(el('h2', 'home-section-title', 'Estado del sistema'), el('p', 'home-section-desc', 'Mostramos estado real cuando existe API. “No verificado” no significa en línea.'));
  head.appendChild(copy);
  panel.appendChild(head);

  const grid = el('div', 'home-status-grid');
  ['console', 'workspace', 'airflow', 'mcp', 'replicon', 'minio'].forEach((key) => {
    const item = state.statuses[key] || { label: 'No verificado', status: 'unknown' };
    const box = el('div', 'home-service');
    const title = el('strong');
    title.append(statusDot(item.status), document.createTextNode(` ${item.name || key}`));
    box.append(title, el('span', null, item.label));
    grid.appendChild(box);
  });
  panel.appendChild(grid);

  const activity = el('div', 'home-activity');
  const pipe = el('div', 'home-activity-item');
  pipe.append(el('strong', null, 'Flujo Replicon'), el('p', null, state.activity.pipeline || 'Flujo automático no verificado.'));
  const next = el('div', 'home-activity-item');
  const text = hasPermission('pipelines.run') ? 'Puedes revisar flujos automáticos o ejecutar una extracción segura desde Monitor o Studio.' : 'Ejecución deshabilitada. Pide permiso para ejecutar flujos automáticos.';
  next.append(el('strong', null, 'Siguiente paso'), el('p', null, text));
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

function renderIdentityBar() {
  if (!window.OmegaSecurityContext || typeof window.OmegaSecurityContext.renderHomeIdentityBar !== 'function') {
    console.warn('[home] OmegaSecurityContext widget not loaded — identity bar skipped');
    return null;
  }
  const snap = state.meAccess
    || (window.__omegaSecurityContext || window.OmegaSecurityContext.emptySnapshot('hydrating'));
  return window.OmegaSecurityContext.renderHomeIdentityBar(snap);
}

export function renderHome(root) {
  root.replaceChildren();
  const shell = el('div', 'home-shell');
  const container = el('div', 'home-container');
  const identityBar = renderIdentityBar();
  if (identityBar) container.append(identityBar);
  container.append(renderHero());
  container.append(renderOperational());
  if (isAdminUser()) {
    container.append(renderAdministration(), renderDataProcesses(), renderSystemOps());
  }
  shell.append(renderTopbar(), container);
  root.appendChild(shell);
}

export function renderVersionBadge(info, health = null) {
  const target = document.querySelector('header, .home-header, .topbar') || document.body;
  let badge = document.getElementById('version-badge');
  if (!badge) {
    badge = document.createElement('span');
    badge.id = 'version-badge';
    target.appendChild(badge);
  }
  badge.className = 'version-badge';
  if (info && info.version) {
    badge.textContent = `v${info.version}`;
    if (health && health.summary) {
      const down = health.summary.down;
      if (down === 0)         badge.classList.add('version-badge--ok');
      else if (down <= 3)     badge.classList.add('version-badge--warn');
      else                    badge.classList.add('version-badge--crit');
      badge.title = `${info.service} · ${info.env} · ${health.summary.up}/${health.summary.total} UP`;
    } else {
      badge.title = `${info.service} · ${info.env}`;
    }
  } else {
    badge.textContent = 'v? — offline';
    badge.classList.add('version-badge--crit');
    badge.title = 'system info unavailable';
  }
}
