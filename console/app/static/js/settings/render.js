import { revealSetting, updateSetting, rotateSecret } from './api.js';

const CATEGORY_LABELS = {
  integrations: 'Integraciones',
  security: 'Seguridad',
  features: 'Funcionalidades',
  general: 'General',
};

const VAULT_MANAGED_PREFIXES = [
  'replicon_',
  'sap_hcm_',
  'sap_s4hana_',
  'sap_successfactors_',
];

const VAULT_MANAGED_SUFFIXES = [
  '_api_key',
  '_base_url',
  '_client_id',
  '_client_secret',
  '_password',
  '_secret',
  '_tenant',
  '_token',
  '_username',
];

function isVaultManagedSetting(item) {
  if (!item || typeof item.key !== 'string') return false;
  const key = item.key;
  return VAULT_MANAGED_PREFIXES.some((prefix) => key.startsWith(prefix))
    && VAULT_MANAGED_SUFFIXES.some((suffix) => key.endsWith(suffix));
}

function formatValue(item) {
  if (item.is_secret && item.value === '***') return '***';
  if (typeof item.value === 'object') return JSON.stringify(item.value);
  if (typeof item.value === 'string') return item.value;
  return String(item.value);
}

function buildVaultCallout(hiddenCount) {
  const callout = document.createElement('section');
  callout.className = 'settings-vault-callout';

  const copy = document.createElement('div');
  const title = document.createElement('h2');
  title.textContent = 'Credenciales de cartuchos';
  const body = document.createElement('p');
  body.textContent = hiddenCount > 0
    ? `${hiddenCount} configuración(es) de credenciales se gestionan ahora desde Vault para evitar duplicidad.`
    : 'Las credenciales de Replicon y SAP se gestionan desde Vault.';
  copy.append(title, body);

  const link = document.createElement('a');
  link.className = 'btn-primary settings-vault-link';
  link.href = '/viewer/vault';
  link.textContent = 'Abrir Vault';

  callout.append(copy, link);
  return callout;
}

function buildItem(item, onChange) {
  const row = document.createElement('div');
  row.className = 'settings-item';
  row.dataset.key = item.key;

  const label = document.createElement('div');
  label.className = 'settings-item-label';
  const labelKey = document.createElement('strong');
  labelKey.textContent = item.key;
  const labelDesc = document.createElement('span');
  labelDesc.className = 'settings-item-desc';
  labelDesc.textContent = item.description || '';
  label.append(labelKey, labelDesc);
  row.appendChild(label);

  const valueWrap = document.createElement('div');
  valueWrap.className = 'settings-item-value';
  const valueText = document.createElement('code');
  valueText.className = 'settings-item-text';
  valueText.textContent = formatValue(item);
  valueWrap.appendChild(valueText);
  row.appendChild(valueWrap);

  const actions = document.createElement('div');
  actions.className = 'settings-item-actions';

  const editBtn = document.createElement('button');
  editBtn.type = 'button';
  editBtn.className = 'btn-secondary';
  editBtn.textContent = 'Editar';
  editBtn.addEventListener('click', () => startEdit(row, item, onChange));
  actions.appendChild(editBtn);

  if (item.is_secret) {
    const revealBtn = document.createElement('button');
    revealBtn.type = 'button';
    revealBtn.className = 'btn-secondary';
    revealBtn.textContent = 'Mostrar';
    revealBtn.addEventListener('click', async () => {
      try {
        const revealed = await revealSetting(item.key);
        valueText.textContent = formatValue({ ...revealed, is_secret: false });
        revealBtn.textContent = 'Mostrado';
        revealBtn.disabled = true;
      } catch (e) {
        alert(`No se pudo mostrar: ${e.message}`);
      }
    });
    actions.appendChild(revealBtn);

    const rotateBtn = document.createElement('button');
    rotateBtn.type = 'button';
    rotateBtn.className = 'btn-danger';
    rotateBtn.textContent = 'Rotar';
    rotateBtn.addEventListener('click', async () => {
      if (!confirm(`¿Rotar el secreto "${item.key}"? Esta acción es destructiva y queda registrada.`)) return;
      try {
        await rotateSecret(item.key);
        await onChange();
      } catch (e) {
        alert(`No se pudo rotar: ${e.message}`);
      }
    });
    actions.appendChild(rotateBtn);
  }

  row.appendChild(actions);
  return row;
}

function startEdit(row, item, onChange) {
  const valueWrap = row.querySelector('.settings-item-value');
  const actions = row.querySelector('.settings-item-actions');
  valueWrap.replaceChildren();
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'settings-item-input';
  input.value = item.is_secret ? '' : (typeof item.value === 'object' ? JSON.stringify(item.value) : String(item.value));
  input.placeholder = item.is_secret ? 'Nuevo valor (no se muestra el actual)' : '';
  valueWrap.appendChild(input);

  actions.replaceChildren();
  const saveBtn = document.createElement('button');
  saveBtn.type = 'button';
  saveBtn.className = 'btn-primary';
  saveBtn.textContent = 'Guardar';
  saveBtn.addEventListener('click', async () => {
    let value = input.value;
    if (typeof item.value === 'object') {
      try { value = JSON.parse(input.value); }
      catch { alert('El valor debe ser JSON válido (objeto o array).'); return; }
    }
    try {
      await updateSetting(item.key, value);
      await onChange();
    } catch (e) {
      alert(`No se pudo guardar: ${e.message}`);
    }
  });
  actions.appendChild(saveBtn);

  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'btn-secondary';
  cancelBtn.textContent = 'Cancelar';
  cancelBtn.addEventListener('click', () => onChange());
  actions.appendChild(cancelBtn);

  input.focus();
}

export function renderSettings(container, settings, onChange) {
  container.replaceChildren();

  const visibleSettings = (settings || []).filter((item) => !isVaultManagedSetting(item));
  const hiddenVaultCount = (settings || []).length - visibleSettings.length;

  if (hiddenVaultCount > 0) {
    container.appendChild(buildVaultCallout(hiddenVaultCount));
  }

  if (!visibleSettings || visibleSettings.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'settings-empty';
    empty.textContent = hiddenVaultCount > 0
      ? 'No hay configuraciones globales para mostrar aquí.'
      : 'No hay configuraciones registradas.';
    container.appendChild(empty);
    return;
  }

  const grouped = {};
  for (const s of visibleSettings) (grouped[s.category] ||= []).push(s);

  for (const [cat, items] of Object.entries(grouped)) {
    const section = document.createElement('section');
    section.className = 'settings-section';
    section.dataset.category = cat;
    const h = document.createElement('h2');
    h.textContent = CATEGORY_LABELS[cat] || cat;
    section.appendChild(h);
    for (const item of items) section.appendChild(buildItem(item, onChange));
    container.appendChild(section);
  }
}

export function renderError(container, message) {
  container.hidden = false;
  container.textContent = message;
}
