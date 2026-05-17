import { exportCartridge, importCartridge } from './api.js';
import { selectCartridge } from './cartridges.js';
import { openNewCartridgeModal } from './modals.js';
import { state } from './state.js';
import { humanizeTerm } from '../i18n/labels.js';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function button(label, className, onClick) {
  const node = el('button', `studio-modern-btn ${className || ''}`.trim(), label);
  node.type = 'button';
  node.addEventListener('click', onClick);
  return node;
}

function link(label, href) {
  const node = el('a', 'studio-modern-link', label);
  node.href = href;
  return node;
}

function selected() {
  return state.selectedCartridge || state.cartridges[0] || null;
}

function markLegacySelection(id) {
  const host = document.getElementById('step-content');
  if (!host) return;
  let marker = document.getElementById('studio-selected-cartridge-marker');
  if (!marker) {
    marker = document.createElement('div');
    marker.id = 'studio-selected-cartridge-marker';
    marker.className = 'empty-state';
    marker.style.cssText = 'padding:6px 10px;font-size:10px;color:var(--text3)';
    host.prepend(marker);
  }
  marker.textContent = `Cartucho seleccionado: ${id || 'ninguno'}`;
}

function entityCount(cartridge) {
  return Array.isArray(cartridge?.entities) ? cartridge.entities.length : 0;
}

function cartridgeName(cartridge) {
  return cartridge?.name || cartridge?.id || 'Replicon PSA';
}

function cartridgeDescription(cartridge) {
  return cartridge?.description || 'Fuente de datos operativa para conectar tablas, tareas automáticas, reportes y conocimiento semántico.';
}

// Maps the status returned by /studio/cartridges/{id}/status into the chip
// label + variant rendered in Studio.
function statusBadge(cartridge) {
  if (cartridge?.healthy === false) return { label: 'Revisar', variant: 'warning' };
  switch (cartridge?.status) {
    case 'operational': return { label: 'Operativo',              variant: 'success' };
    case 'degraded':    return { label: 'Configuración pendiente', variant: 'warning' };
    case 'offline':     return { label: 'Offline',                 variant: 'error' };
    case 'unknown':     return { label: 'Sin diagnóstico',         variant: '' };
    default:            return { label: 'Operativo',               variant: 'success' };
  }
}

function renderHeader() {
  const top = el('div', 'studio-modern-top');
  const copy = el('div');
  copy.append(
    el('div', 'studio-modern-eyebrow', 'OMEGA Console'),
    el('h1', 'studio-modern-title', 'Studio'),
    el('p', 'studio-modern-desc', 'Configura fuentes de datos, tablas, transformaciones y conocimiento semántico desde una consola operativa clara.')
  );
  const nav = el('nav', 'studio-modern-nav');
  nav.setAttribute('aria-label', 'Studio navigation');
  nav.append(link('Monitor', '/monitor'), link('RAG', '/rag'), link('Inicio', '/'));
  top.append(copy, nav);
  return top;
}

export function renderToolbar(onRender) {
  const cartridge = selected();
  const toolbar = el('section', 'studio-modern-toolbar');

  const selectWrap = el('div');
  const label = el('label', 'studio-modern-label', humanizeTerm('cartridge'));
  label.htmlFor = 'studio-modern-cartridge';
  const select = el('select', 'studio-modern-select');
  select.id = 'studio-modern-cartridge';
  select.name = 'cartridge';
  select.dataset.testid = 'cartridge-picker';
  state.cartridges.forEach((item) => {
    const option = document.createElement('option');
    option.value = item.id;
    option.textContent = `${item.name || item.id} (${item.id})`;
    if (item.id === cartridge?.id) option.selected = true;
    select.appendChild(option);
  });
  select.addEventListener('change', async () => {
    await selectCartridge(select.value);
    markLegacySelection(select.value);
    onRender();
  });
  selectWrap.append(label, select);

  const badge = statusBadge(cartridge);
  const summary = el('div', 'studio-modern-summary');
  summary.append(
    chip(cartridgeName(cartridge)),
    chip(`v${cartridge?.version || '0.1.0'}`),
    chip(`${entityCount(cartridge)} tablas`),
    chip(cartridge?.pattern || cartridge?.category || 'tarea automática'),
    chip(badge.label, badge.variant)
  );

  const actions = el('div', 'studio-modern-toolbar-actions');
  actions.append(
    button('Nueva fuente', 'primary', () => openNewCartridgeModal(onRender)),
    button('Descargar ZIP', '', () => exportCartridge(cartridge?.id))
  );

  toolbar.append(selectWrap, summary, actions);
  return toolbar;
}

function chip(text, variant) {
  return el('span', `studio-chip ${variant || ''}`.trim(), text);
}

export function renderCartridgeCard(onRender) {
  const cartridge = selected();
  const card = el('section', 'studio-modern-card');

  const head = el('div', 'studio-card-head');
  const copy = el('div');
  copy.append(
    el('div', 'studio-card-kicker', cartridge?.id || 'replicon'),
    el('h2', 'studio-card-title', cartridgeName(cartridge)),
    el('p', 'studio-card-desc', cartridgeDescription(cartridge))
  );
  head.append(copy, el('span', 'studio-status', 'Protected'));

  const badge = statusBadge(cartridge);
  const stats = el('div', 'studio-modern-stats');
  stats.append(
    stat('Tipo', cartridge?.pattern || 'tarea automática'),
    stat('Tablas', String(entityCount(cartridge))),
    stat('Estado', badge.label)
  );

  const actions = el('div', 'studio-modern-actions');
  actions.append(
    button('Ver tablas', 'primary', () => { if (typeof window.goStep === 'function') window.goStep(3); }),
    button('ZIP', '', () => exportCartridge(cartridge?.id)),
    button('Configurar', '', () => { if (typeof window.goStep === 'function') window.goStep(2); }),
    button('Nuevo', 'success', () => openNewCartridgeModal(onRender))
  );

  card.append(head, stats, actions);
  return card;
}

function stat(label, value) {
  const node = el('div', 'studio-stat');
  node.append(el('span', null, label), el('strong', null, value));
  return node;
}

export function renderImportCard(onRender) {
  const card = el('section', 'studio-modern-import');
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = '.zip,application/zip';
  input.hidden = true;

  const body = el('div');
  body.append(
    el('div', 'studio-import-icon', 'ZIP'),
    el('h2', 'studio-import-title', 'Importar fuente desde ZIP'),
    el('p', 'studio-import-copy', 'Sube un paquete exportado previamente para registrarlo en esta instalación sin tocar servicios internos ni rutas.'),
    el('div', 'studio-import-drop', 'Arrastra un ZIP aquí o usa el botón de importación.')
  );

  const status = el('div', 'studio-import-status', 'Listo para importar.');
  const importBtn = button('Seleccionar ZIP', 'primary', () => input.click());

  async function handleFile(file) {
    if (!file) return;
    status.className = 'studio-import-status';
    status.textContent = `Importando ${file.name}...`;
    try {
      await importCartridge(file);
      status.className = 'studio-import-status success';
      status.textContent = 'Fuente de datos importada.';
      if (typeof window.loadCartridges === 'function') await window.loadCartridges();
      await onRender(true);
    } catch (error) {
      status.className = 'studio-import-status error';
      status.textContent = error.message || 'No se pudo importar el ZIP.';
    }
  }

  input.addEventListener('change', () => handleFile(input.files?.[0]));
  card.addEventListener('dragover', (event) => { event.preventDefault(); card.classList.add('dragging'); });
  card.addEventListener('dragleave', () => card.classList.remove('dragging'));
  card.addEventListener('drop', (event) => {
    event.preventDefault();
    card.classList.remove('dragging');
    handleFile(event.dataTransfer?.files?.[0]);
  });

  const actions = el('div', 'studio-modern-actions');
  actions.append(importBtn);
  card.append(body, status, actions, input);
  return card;
}

export function renderStudioIsland(root, onRender) {
  root.replaceChildren();
  const modern = el('div', 'studio-modern');
  const shell = el('div', 'studio-modern-shell');
  const grid = el('div', 'studio-modern-grid');

  grid.append(renderCartridgeCard(onRender), renderImportCard(onRender));
  shell.append(renderHeader(), renderToolbar(onRender), grid);

  if (state.error) {
    const error = el('div', 'studio-import-status error', `Fallback activo: ${state.error}`);
    shell.appendChild(error);
  }

  modern.appendChild(shell);
  root.appendChild(modern);
  document.body.classList.add('studio-modern-ready');
}
