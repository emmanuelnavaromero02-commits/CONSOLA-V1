import { humanizeTerm } from '../i18n/labels.js';
import { state } from './state.js';

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

function menuAction(item) {
  const node = document.createElement(item.allowed ? 'a' : 'span');
  node.className = `home-menu-item ${item.allowed ? '' : 'disabled'}`.trim();
  if (item.allowed) node.href = item.href;
  const label = el('strong', null, item.label);
  const hint = el('small', null, item.hint || item.href);
  node.append(label, hint);
  return node;
}

function actionMenu(label, items) {
  const details = el('details', 'home-action-menu');
  const summary = el('summary', null, label);
  const panel = el('div', 'home-menu-panel');
  items.forEach((item) => panel.appendChild(menuAction(item)));
  details.append(summary, panel);
  return details;
}

export function renderTopbar() {
  const top = el('header', 'home-topbar');
  const brand = el('a', 'home-brand');
  brand.href = '/';
  brand.append(el('span', 'home-mark', 'Ω'), el('span', null, 'OMEGA'), el('small', null, 'by EPI USE'));

  const nav = el('nav', 'home-nav');
  nav.setAttribute('aria-label', 'Navegación principal');
  nav.append(
    navLink('Inicio', '/'),
    navLink('Workspace', '/workspace'),
    navLink('Copiloto', '/copilot'),
    navLink('Vault', '/viewer/vault'),
    navLink('Studio', '/studio'),
    navLink('Explorer', '/explorer'),
    navLink('Lineage', '/viewer/lineage'),
    navLink('Monitor', '/monitor'),
    navLink('Datos', '/viewer/datasets'),
    navLink('Operaciones', '/operations'),
    navLink('Seguridad', '/security'),
    navLink('Administración', '/iam')
  );
  if (state.user?.role === 'admin') {
    nav.append(navLink('Agentes', '/agents'));
  }

  // Action menus block (Crear / Ejecutar / Revisar / Configurar) removed in
  // sprint v1.2-pr1: the four home cards already expose those flows and the
  // dropdown menus were covering the assistant + service chips at 100% zoom.

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

  top.append(brand, nav, user);
  return top;
}
