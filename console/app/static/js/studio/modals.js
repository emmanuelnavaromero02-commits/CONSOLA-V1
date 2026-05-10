import { createCartridge } from './cartridges.js';
import { setState } from './state.js';
import { humanizeError } from '../i18n/labels.js';

const IDS = {
  overlay: 'cc-overlay',
  id: 'cc-id',
  name: 'cc-name',
  desc: 'cc-desc',
  msg: 'cc-msg',
  submit: 'cc-submit',
};

let afterSubmit = null;

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function input(id, placeholder) {
  const node = document.createElement('input');
  node.id = id;
  node.type = 'text';
  node.placeholder = placeholder;
  node.autocomplete = 'off';
  return node;
}

function field(labelText, control) {
  const wrap = el('div', 'cc-field');
  const label = el('label', null, labelText);
  label.htmlFor = control.id;
  wrap.append(label, control);
  return wrap;
}

function setMessage(message, type = '') {
  const msg = document.getElementById(IDS.msg);
  if (!msg) return;
  msg.className = `cc-message ${type}`.trim();
  msg.textContent = message || '';
}

export function closeModal() {
  document.getElementById(IDS.overlay)?.remove();
  document.body.classList.remove('studio-modal-open');
  document.removeEventListener('keydown', handleEscape);
  setState({ modalOpen: false });
  afterSubmit = null;
}

export function handleEscape(event) {
  if (event.key === 'Escape') closeModal();
}

function buildModal() {
  const overlay = el('div', 'cc-modal-overlay');
  overlay.id = IDS.overlay;
  overlay.addEventListener('click', (event) => {
    if (event.target === overlay) closeModal();
  });

  const modal = el('div', 'cc-modal');
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');
  modal.setAttribute('aria-labelledby', 'cc-title');

  const header = el('div', 'cc-modal-header');
  const titleWrap = el('div');
  const title = el('h3', 'cc-modal-title', 'Nueva fuente de datos');
  title.id = 'cc-title';
  const subtitle = el('p', 'cc-modal-subtitle', 'Registra una fuente para conectar tablas, tareas automáticas y reportes.');
  titleWrap.append(title, subtitle);
  const closeBtn = el('button', 'cc-close', '×');
  closeBtn.type = 'button';
  closeBtn.setAttribute('aria-label', 'Cerrar');
  closeBtn.addEventListener('click', closeModal);
  header.append(titleWrap, closeBtn);

  const form = el('form', 'cc-form');
  const message = el('div', 'cc-message');
  message.id = IDS.msg;
  form.append(
    field('ID único', input(IDS.id, 'ej: sap_erp')),
    field('Nombre', input(IDS.name, 'ej: SAP ERP')),
    field('Descripción', input(IDS.desc, 'Opcional')),
    message
  );
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    submitNewCartridge().catch((err) => setMessage(humanizeError(err), 'error'));
  });

  const actions = el('div', 'cc-actions');
  const cancel = el('button', 'btn', 'Cancelar');
  cancel.type = 'button';
  cancel.addEventListener('click', closeModal);
  const save = el('button', 'btn btn-primary', 'Crear fuente');
  save.id = IDS.submit;
  save.type = 'submit';
  save.addEventListener('click', (event) => {
    event.preventDefault();
    submitNewCartridge().catch((err) => setMessage(humanizeError(err), 'error'));
  });
  actions.append(cancel, save);

  modal.append(header, form, actions);
  overlay.appendChild(modal);
  return overlay;
}

export function openNewCartridgeModal(onCreated) {
  closeModal();
  afterSubmit = onCreated;
  document.body.appendChild(buildModal());
  document.body.classList.add('studio-modal-open');
  document.addEventListener('keydown', handleEscape);
  setState({ modalOpen: true });
  document.getElementById(IDS.id)?.focus();
}

export async function submitNewCartridge() {
  const id = document.getElementById(IDS.id)?.value.trim() || '';
  const name = document.getElementById(IDS.name)?.value.trim() || '';
  const description = document.getElementById(IDS.desc)?.value.trim() || '';
  const submitBtn = document.getElementById(IDS.submit);

  if (!id || !name) {
    setMessage('ID y nombre son obligatorios.', 'error');
    return;
  }

  submitBtn.disabled = true;
  setMessage('Creando fuente...', '');
  try {
    await createCartridge({ id, name, description });
    setMessage('Fuente de datos creada.', 'success');
    const callback = afterSubmit;
    closeModal();
    if (typeof callback === 'function') await callback(true);
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}

export function installModalGlobals() {
  window.StudioCartridgeModal = { open: openNewCartridgeModal, close: closeModal, submit: submitNewCartridge };
}
