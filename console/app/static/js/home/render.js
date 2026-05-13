import { permissionText, quickActions } from './actions.js';
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
  return el('span', `home-dot ${status === 'online' ? 'ok' : status === 'offline' ? 'off' : ''}`.trim());
}

function isAdminUser() {
  // Sprint v1.5: the home is split into "Workspace-only" for non-admins
  // and "everything" for admins. We deliberately key off the literal role
  // here (binary admin gate) rather than a granular permission so analyst /
  // workspace_admin / security_admin etc. all collapse to the non-admin
  // experience the client requested in the demo.
  return state.user?.role === 'admin';
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
  actions.append(
    linkButton('Abrir Workspace', state.workspaceUrl, 'primary', hasPermission('workspace.access'), permissionText('workspace.access')),
  );
  if (isAdminUser()) {
    actions.append(
      linkButton('Abrir Monitor', '/monitor', 'secondary', hasPermission('monitor.read'), permissionText('monitor.read')),
      linkButton('Gestionar IAM', '/iam', 'secondary', hasPermission('iam.users.read'), permissionText('iam.users.read'))
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

function card({ title, icon, description, href, primary, permission, secondary = [], size = 'secondary-card', kind = '', meta = [] }) {
  const allowed = !permission || hasPermission(permission);
  const node = el('article', `home-card ${size} ${kind} ${allowed ? '' : 'locked'}`.trim());
  const top = el('div', 'home-card-top');
  const copy = el('div');
  copy.append(el('div', 'home-icon', icon), el('h3', null, title));
  top.append(copy);
  node.append(top, el('p', null, description));

  const metaWrap = el('div', 'home-card-meta');
  if (permission) metaWrap.append(chip(allowed ? 'Disponible' : permissionText(permission), allowed ? 'ok' : 'locked'));
  meta.forEach((item) => metaWrap.append(chip(item.text, item.variant || '')));
  node.appendChild(metaWrap);

  const actions = el('div', 'home-card-actions');
  actions.append(linkButton(primary, href, allowed ? 'primary' : 'secondary', allowed, permission ? permissionText(permission) : 'Acceso limitado'));
  secondary.forEach((action) => {
    const actionAllowed = !action.permission || hasPermission(action.permission);
    actions.append(linkButton(action.label, action.href, 'secondary', actionAllowed, action.permission ? permissionText(action.permission) : 'Disponible desde el módulo'));
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

function renderQuickAccess() {
  const panel = el('section', 'home-quick-panel');
  const copy = el('div');
  copy.append(el('h2', 'home-section-title', 'Acceso rápido'), el('p', 'home-section-desc', 'Tus accesos principales, ordenados por utilidad y permisos.'));
  const actions = el('div', 'home-quick-actions');
  quickActions().forEach((item) => {
    actions.append(linkButton(item.label, item.href, item.allowed ? 'primary' : 'secondary', item.allowed, item.hint));
  });
  panel.append(copy, actions);
  return panel;
}

function renderOperational() {
  const grid = el('div', 'home-operational-grid');
  // Workspace card is the only one a non-admin sees here. For admins we
  // keep the original full grid (Workspace + Studio + Monitor + Decisions).
  grid.append(
    card({
      title: 'Workspace',
      icon: 'W',
      description: 'Aplicaciones, asistente y datos listos para usuarios de negocio.',
      href: state.workspaceUrl,
      primary: 'Abrir Workspace',
      permission: 'workspace.access',
      size: 'primary-card',
      meta: [{ text: 'Apps' }, { text: 'Asistente' }],
      secondary: [{ label: 'Abrir Apps', href: state.workspaceUrl, permission: 'workspace.access' }],
    }),
  );
  if (isAdminUser()) {
    grid.append(
      card({
        title: 'Studio',
        icon: 'S',
        description: 'Configura fuentes de datos, tablas, transformaciones y conocimiento semántico.',
        href: '/studio',
        primary: 'Abrir Studio',
        permission: 'studio.read',
        size: 'primary-card',
        meta: [{ text: 'Fuentes de datos' }, { text: 'Reportes' }],
        secondary: [{ label: 'Nueva fuente', href: '/studio', permission: 'studio.write' }],
      }),
      card({
        title: 'Monitor',
        icon: 'M',
        description: 'Revisa flujos automáticos, trabajos recientes, reportes y servicios internos.',
        href: '/monitor',
        primary: 'Abrir Monitor',
        permission: 'monitor.read',
        meta: [{ text: state.statuses.mcp?.label || 'Servicios internos no verificados' }],
      }),
      card({
        title: 'Decisions',
        icon: 'D',
        description: 'Registra decisiones, KPIs, acciones y seguimiento operativo.',
        href: '/decisions',
        primary: 'Abrir Decisiones',
        permission: 'workspace.access',
        meta: [{ text: 'KPIs' }, { text: 'Acciones' }],
      }),
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
  grid.append(
    card({
      title: 'IAM / Accesos',
      icon: 'I',
      description: 'Administra accesos, roles, sesiones y reglas visibles.',
      href: '/iam',
      primary: 'Abrir IAM',
      permission: 'iam.users.read',
      kind: 'admin',
      meta: [{ text: 'Permisos por rol' }],
      secondary: [{ label: 'Gestionar usuarios', href: '/admin/users', permission: 'iam.users.read' }],
    }),
    card({
      title: 'Usuarios',
      icon: 'U',
      description: 'Gestiona cuentas, roles, estado e invitaciones.',
      href: '/admin/users',
      primary: 'Gestionar usuarios',
      permission: 'iam.users.read',
      kind: 'admin',
      meta: usersMeta(),
      secondary: [{ label: 'Invitar usuario', href: '/iam', permission: 'iam.users.write' }],
    }),
    card({
      title: 'Centro de seguridad',
      icon: 'A',
      description: 'Revisa auditoría, sesiones e intentos de acceso.',
      href: '/security',
      primary: 'Abrir Seguridad',
      permission: 'security.audit.read',
      kind: 'admin',
      meta: [{ text: state.activity.audit || 'Auditoría no verificada' }],
      secondary: [{ label: 'Ver auditoría', href: '/security', permission: 'security.audit.read' }],
    }),
    card({
      title: 'Caja fuerte',
      icon: 'V',
      description: 'Consulta conexiones protegidas y configura integraciones.',
      href: '/viewer/vault',
      primary: 'Abrir Caja fuerte',
      permission: 'vault.connections.read',
      kind: 'admin',
      meta: [{ text: 'Secretos ocultos' }],
      secondary: [{ label: 'Configurar conexiones', href: '/viewer/vault', permission: 'vault.connections.write' }],
    }),
    card({
      title: 'Configuración',
      icon: 'C',
      description: 'Credenciales, integraciones, feature flags y rotación de secretos.',
      href: '/settings',
      primary: 'Abrir Configuración',
      permission: 'settings.read',
      kind: 'admin',
      meta: [{ text: 'Editable desde UI' }],
      secondary: [{ label: 'Editar secretos', href: '/settings', permission: 'settings.write' }],
    }),
    card({
      title: 'Operaciones',
      icon: 'O',
      description: 'Versión, migraciones y salud de servicios.',
      href: '/operations',
      primary: 'Abrir Operaciones',
      permission: 'operations.read',
      kind: 'admin',
      meta: [{ text: 'Health en vivo' }],
    })
  );
  return renderSection('Administración', 'Identidad, seguridad y accesos viven separados del trabajo diario.', grid);
}

function renderDataProcesses() {
  const grid = el('div', 'home-admin-grid');
  grid.append(
    card({
      title: humanizeTerm('datasets'),
      icon: 'DT',
      description: 'Explora reportes y tablas preparadas para análisis.',
      href: '/viewer/datasets',
      primary: 'Ver reportes',
      permission: 'datasets.read',
      kind: 'system',
      meta: [{ text: 'Datos preparados' }],
    }),
    card({
      title: humanizeTerm('pipelines'),
      icon: 'F',
      description: 'Revisa ejecuciones, estados y próximos procesos.',
      href: '/monitor',
      primary: 'Ver flujos',
      permission: 'pipelines.read',
      kind: 'system',
      secondary: [{ label: 'Ejecutar Replicon', href: '/monitor', permission: 'pipelines.run' }],
    }),
    card({
      title: 'Replicon',
      icon: 'R',
      description: 'Fuente de datos operativa para extracción y gobierno de datos Replicon.',
      href: '/studio',
      primary: 'Abrir fuente',
      permission: 'studio.read',
      kind: 'system',
      meta: [{ text: state.statuses.replicon?.label || 'No verificado' }],
    }),
    card({
      title: 'Trabajos',
      icon: 'T',
      description: 'Consulta historial y detalle de trabajos recientes.',
      href: '/viewer/jobs',
      primary: 'Ver trabajos',
      permission: 'monitor.read',
      kind: 'system',
    })
  );
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

export function renderHome(root) {
  root.replaceChildren();
  const shell = el('div', 'home-shell');
  const container = el('div', 'home-container');
  // Non-admins only get the hero (with the Workspace CTA) and a single
  // Workspace card in the operational grid — the administration,
  // data-processes and system-ops blocks expose admin-only surface and
  // are hidden entirely instead of being rendered with "Acceso limitado"
  // chips. Admins keep the original layout.
  container.append(renderHero());
  if (isAdminUser()) container.append(renderQuickAccess());
  container.append(renderOperational());
  if (isAdminUser()) {
    container.append(renderAdministration(), renderDataProcesses(), renderSystemOps());
  }
  shell.append(renderTopbar(), container);
  root.appendChild(shell);
}

/**
 * Render the version badge in the home header.
 * @param {{version:string, env:string, service:string}|null} info
 */
export function renderVersionBadge(info, health = null) {
  const target = document.querySelector('header, .home-header, .topbar') || document.body;
  let badge = document.getElementById('version-badge');
  if (!badge) {
    badge = document.createElement('span');
    badge.id = 'version-badge';
    target.appendChild(badge);
  }
  // Reset class then add status modifier
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
