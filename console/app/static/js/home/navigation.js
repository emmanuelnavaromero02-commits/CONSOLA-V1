import { humanizeTerm } from '../i18n/labels.js';
import { hasPermission, state } from './state.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function statusDot(status) {
  return el('span', `home-dot ${status === 'online' ? 'ok' : status === 'offline' ? 'off' : ''}`.trim());
}

function navLink(label, href) {
  const node = el('a', null, label);
  node.href = href;
  return node;
}

const ADMIN_ROLES = new Set(['admin', 'owner', 'super_admin']);

function isAdminUser() {
  const role = String(state.user?.role || state.role || '').toLowerCase();
  return ADMIN_ROLES.has(role);
}

const SERVICE_REGISTRY = [
  { label: 'Panel operativo', href: '/', category: 'Trabajo diario', aliases: 'inicio dashboard home consola resumen accesos' },
  { label: 'Workspace', href: '/workspace', category: 'Trabajo diario', permission: 'workspace.access', aliases: 'trabajo apps decisiones aplicaciones publicadas' },
  { label: 'Sala de Control', href: '/control-room', category: 'Trabajo diario', permission: 'workspace.access', aliases: 'omega control room sala control anomalías anomalias decisiones auditoria auditoría aprobaciones sap' },
  { label: 'Copiloto', href: '/copilot', category: 'Trabajo diario', permission: 'copilot.use', aliases: 'chat asistente ia acciones aprobaciones memoria redactar workflows' },
  { label: 'Apps analíticas', href: '/control-room#apps', category: 'Trabajo diario', permission: 'apps.read', aliases: 'aplicaciones dashboards control room workspace analiticas' },
  { label: 'Decisiones', href: '/decisions', category: 'Trabajo diario', permission: 'apps.read', adminOnly: true, aliases: 'decisions aprobaciones historial decisiones' },

  { label: 'Marketplace', href: '/marketplace', category: 'Cartuchos y clientes', permission: 'marketplace.read', aliases: 'catalogo comercial cartuchos solicitar comprar activar' },
  { label: 'Mis cartuchos', href: '/customer/cartridges', category: 'Cartuchos y clientes', permission: 'marketplace.read', aliases: 'cartuchos activos instalados estado workspace cliente' },
  { label: 'Gestión de cartuchos', href: '/admin/installations', category: 'Cartuchos y clientes', permission: 'marketplace.admin', adminOnly: true, aliases: 'instalaciones revocar pausar reactivar licencias clientes entitlements' },
  { label: 'Licencias', href: '/admin/licenses', category: 'Cartuchos y clientes', permission: 'marketplace.admin', adminOnly: true, aliases: 'licencias entitlements planes expiraciones' },

  { label: 'Datos preparados', href: '/viewer/datasets', category: 'Datos y analítica', permission: 'datasets.read', adminOnly: true, aliases: 'reportes tablas datasets gold silver datos' },
  { label: 'Reportes de datos', href: '/viewer/datasets', category: 'Datos y analítica', permission: 'datasets.read', adminOnly: true, aliases: 'reportes tablas consultas detalles' },
  { label: 'Lineage', href: '/viewer/lineage', category: 'Datos y analítica', permission: 'datasets.read', aliases: 'linaje grafo dependencias datasets lineage' },
  { label: 'Schema Explorer', href: '/viewer/schema', category: 'Datos y analítica', permission: 'datasets.read', adminOnly: true, aliases: 'schema columnas catalogo tablas' },
  { label: 'Semántica', href: '/viewer/semantic', category: 'Datos y analítica', permission: 'datasets.read', adminOnly: true, aliases: 'semantica catalogo relaciones rag data catalog' },
  { label: 'RAG', href: '/rag', category: 'Datos y analítica', permission: 'studio.read', adminOnly: true, aliases: 'rag documentos fuentes reindex semantic rebuild' },

  { label: 'Monitor', href: '/monitor', category: 'Operación y flujos', permission: 'monitor.read', aliases: 'salud airflow dags flujos jobs procesos mcp' },
  { label: 'Pipeline Monitor', href: '/viewer/pipeline', category: 'Operación y flujos', permission: 'monitor.read', adminOnly: true, aliases: 'pipeline dag bronze silver gold extraccion' },
  { label: 'Trabajos recientes', href: '/viewer/jobs', category: 'Operación y flujos', permission: 'monitor.read', adminOnly: true, aliases: 'jobs runs historial ejecuciones logs' },
  { label: 'Explorer', href: '/explorer', category: 'Operación y flujos', permission: 'pipelines.read', aliases: 'minio s3 objetos archivos buckets descargas' },
  { label: 'Studio interno', href: '/studio', category: 'Operación y flujos', permission: 'studio.read', adminOnly: true, aliases: 'raw silver gold datasets tecnico transformaciones entidades' },

  { label: 'Mis accesos', href: '/my-access', category: 'Seguridad e identidad', aliases: 'mis accesos identidad workspace rol permisos efectivos cartuchos bloqueados deny perfil' },
  { label: 'Usuarios y accesos', href: '/iam', category: 'Seguridad e identidad', permission: 'iam.users.read', adminOnly: true, aliases: 'iam usuarios roles permisos sesiones' },
  { label: 'Usuarios admin', href: '/admin/users', category: 'Seguridad e identidad', permission: 'iam.users.read', adminOnly: true, aliases: 'admin users usuarios invitar activar bloquear' },
  { label: 'Seguridad', href: '/security', category: 'Seguridad e identidad', permission: 'security.audit.read', aliases: 'auditoria sesiones intentos login seguridad' },
  { label: 'Vault y credenciales', href: '/viewer/vault', category: 'Seguridad e identidad', permission: 'vault.connections.read', aliases: 'vault secretos conexiones tokens credenciales conn id' },

  { label: 'Agentes', href: '/agents', category: 'IA y automatización', permission: 'studio.write', adminOnly: true, aliases: 'agents automatizacion mcp tools runs cron programacion' },
  { label: 'Herramientas MCP', href: '/monitor', category: 'IA y automatización', permission: 'monitor.read', adminOnly: true, aliases: 'mcp tools herramientas servidores catalogo' },
  { label: 'Operaciones Studio', href: '/studio', category: 'IA y automatización', permission: 'studio.read', adminOnly: true, aliases: 'studio ops tools operaciones internas' },

  { label: 'Operaciones', href: '/operations', category: 'Administración', permission: 'operations.read', adminOnly: true, aliases: 'version migraciones health soporte plataforma' },
  { label: 'Configuración', href: '/settings', category: 'Administración', permission: 'settings.read', adminOnly: true, aliases: 'settings parametros feature flags configuracion' },
];

function serviceAllowed(item) {
  if (item.adminOnly && !isAdminUser()) return false;
  return !item.permission || hasPermission(item.permission);
}

function visibleServices() {
  return SERVICE_REGISTRY.filter(serviceAllowed);
}

function renderServiceSearch(services) {
  const wrap = el('div', 'home-service-search');
  const input = el('input');
  input.type = 'search';
  input.placeholder = 'Buscar servicios, datos, cartuchos...';
  input.setAttribute('aria-label', 'Buscar servicios de la consola');
  const results = el('div', 'home-search-results');
  results.hidden = true;

  function matches(item, query) {
    const haystack = `${item.label} ${item.category || ''} ${item.aliases || ''}`.toLowerCase();
    return !query || haystack.includes(query);
  }

  function paint() {
    const query = input.value.trim().toLowerCase();
    const filtered = services.filter((item) => matches(item, query)).slice(0, 40);
    const groups = filtered.reduce((acc, item) => {
      const key = item.category || 'Servicios';
      if (!acc.has(key)) acc.set(key, []);
      acc.get(key).push(item);
      return acc;
    }, new Map());
    results.replaceChildren();
    groups.forEach((items, category) => {
      results.appendChild(el('div', 'home-search-category', category));
      items.forEach((item) => {
        const link = el('a');
        link.href = item.href;
        link.append(el('strong', null, item.label), el('small', null, item.href));
        results.appendChild(link);
      });
    });
    results.hidden = filtered.length === 0 || (!query && document.activeElement !== input);
  }

  input.addEventListener('input', paint);
  input.addEventListener('focus', paint);
  input.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter') return;
    const first = services.find((item) => matches(item, input.value.trim().toLowerCase()));
    if (first) window.location.assign(first.href);
  });
  document.addEventListener('click', (event) => {
    if (!wrap.contains(event.target)) results.hidden = true;
  });

  wrap.append(input, results);
  return wrap;
}

export function renderTopbar() {
  const top = el('header', 'home-topbar');
  const brand = el('a', 'home-brand');
  brand.href = '/';
  brand.append(el('span', 'home-mark', 'Ω'), el('span', null, 'OMEGA'), el('small', null, 'by EPI USE'));

  const services = visibleServices();
  const nav = el('nav', 'home-nav');
  nav.setAttribute('aria-label', 'Navegación principal');
  nav.append(navLink('Inicio', '/'));
  services
    .filter((item) => ['Workspace', 'Sala de Control', 'Copiloto', 'Marketplace', 'Monitor', 'Gestión de cartuchos'].includes(item.label))
    .forEach((item) => nav.append(navLink(item.label, item.href)));

  const user = el('div', 'home-user');
  const system = el('span', 'home-status-pill');
  system.append(statusDot(state.statuses.mcp?.status), el('span', null, state.statuses.mcp?.label || `${humanizeTerm('mcp')} no verificados`));
  const pref = document.documentElement.dataset.themePreference || 'light';
  const themeLabel = pref === 'dark' ? 'Tema: oscuro' : pref === 'system' ? 'Tema: sistema' : 'Tema: claro';
  const theme = el('button', 'home-theme-btn', themeLabel);
  theme.type = 'button';
  theme.dataset.themeToggle = 'true';
  user.append(system, theme, el('span', 'home-role', state.role || 'sin sesión'));
  if (state.user?.email) user.append(el('span', 'home-email', state.user.email));
  const profile = navLink('Perfil', '/me');
  const logout = el('button', 'home-link-button', 'Salir');
  logout.type = 'button';
  logout.dataset.logout = 'true';
  user.append(profile, logout);

  top.append(brand, renderServiceSearch(services), nav, user);
  return top;
}
