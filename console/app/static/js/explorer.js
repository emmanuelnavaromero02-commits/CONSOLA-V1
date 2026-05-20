const $ = (id) => document.getElementById(id);

const state = {
  buckets: [],
  quicklinks: [],
  currentBucket: null,
  currentPrefix: '',
  nextToken: '',
  canDelete: false,
};

function esc(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)csrf_token=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

function csrfHeaders(base = {}) {
  const token = csrfToken();
  return token ? { ...base, 'X-CSRF-Token': token } : base;
}

async function readError(response) {
  try {
    const data = await response.json();
    return data.detail || data.error || `HTTP ${response.status}`;
  } catch (_) {
    try {
      return (await response.text()).slice(0, 240) || `HTTP ${response.status}`;
    } catch {
      return `HTTP ${response.status}`;
    }
  }
}

async function fetchJson(url, options = {}) {
  const method = String(options.method || 'GET').toUpperCase();
  const init = {
    credentials: 'same-origin',
    ...options,
    method,
    headers: { Accept: 'application/json', ...(options.headers || {}) },
  };
  if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    Object.assign(init.headers, csrfHeaders());
  }
  const response = await fetch(url, init);
  if (response.redirected && new URL(response.url).pathname === '/login') {
    location.href = '/login';
    throw new Error('Sesion requerida');
  }
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

function toast(message, kind = '') {
  const wrap = $('toasts');
  const node = document.createElement('div');
  node.className = `toast ${kind}`.trim();
  node.textContent = message;
  wrap.appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

function fmtBytes(size) {
  if (size == null) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = Number(size);
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(value < 10 && index > 0 ? 1 : 0)} ${units[index]}`;
}

function fmtDate(iso) {
  return String(iso || '').replace('T', ' ').slice(0, 19);
}

function selectedBucket() {
  const id = $('bucket-sel').value;
  state.currentBucket = state.buckets.find((bucket) => bucket.id === id) || state.buckets[0] || null;
  return state.currentBucket;
}

function renderQuicklinks() {
  const host = $('quicklinks');
  host.replaceChildren();
  state.quicklinks.forEach((quick, index) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn';
    btn.textContent = quick.label || quick.prefix || quick.bucket || `Ruta ${index + 1}`;
    btn.dataset.quick = String(index);
    host.appendChild(btn);
  });
}

function renderStatus(data) {
  const status = $('status');
  status.replaceChildren();
  if (!state.currentBucket) {
    status.textContent = 'Sin buckets configurados.';
    return;
  }
  const root = document.createElement('button');
  root.type = 'button';
  root.className = 'crumb';
  root.textContent = state.currentBucket.name;
  root.dataset.prefix = '';
  status.appendChild(root);

  const parts = state.currentPrefix.split('/').filter(Boolean);
  let cumulative = '';
  parts.forEach((part) => {
    cumulative += `${part}/`;
    status.append(' / ');
    const crumb = document.createElement('button');
    crumb.type = 'button';
    crumb.className = 'crumb';
    crumb.textContent = part;
    crumb.dataset.prefix = cumulative;
    status.appendChild(crumb);
  });
  const count = ` · ${(data.folders || []).length} carpetas · ${(data.objects || []).length} objetos${data.is_truncated ? ' · truncado' : ''}`;
  status.append(count);
}

function renderResults(data) {
  const folders = data.folders || [];
  const objects = data.objects || [];
  state.nextToken = data.next_token || '';
  renderStatus(data);
  const host = $('results');
  if (!folders.length && !objects.length) {
    host.innerHTML = '<div class="empty">No hay objetos en este prefijo.</div>';
    return;
  }
  const folderRows = folders.map((folder) => {
    const display = folder.replace(state.currentPrefix, '');
    return `
      <tr>
        <td class="key folder" data-folder="${esc(folder)}">${esc(display)}</td>
        <td class="muted">carpeta</td>
        <td class="muted">—</td>
        <td class="right">—</td>
      </tr>`;
  }).join('');
  const objectRows = objects.map((obj) => {
    const display = obj.key.replace(state.currentPrefix, '') || obj.key;
    return `
      <tr>
        <td class="key">${esc(display)}</td>
        <td class="muted">${fmtBytes(obj.size)}</td>
        <td class="muted">${esc(fmtDate(obj.last_modified))}</td>
        <td class="right">
          <div class="row-actions">
            <button class="btn" type="button" data-download="${esc(obj.key)}">Descargar</button>
            ${state.canDelete ? `<button class="btn danger" type="button" data-delete="${esc(obj.key)}">Borrar</button>` : ''}
          </div>
        </td>
      </tr>`;
  }).join('');
  host.innerHTML = `
    <table>
      <thead>
        <tr><th>Key</th><th>Tamano</th><th>Ultima modificacion</th><th class="right">Acciones</th></tr>
      </thead>
      <tbody>${folderRows}${objectRows}</tbody>
    </table>
    ${state.nextToken ? '<div class="empty"><button class="btn" type="button" data-next-page="1">Siguiente pagina</button></div>' : ''}`;
}

async function listObjects(continuationToken = '') {
  const bucket = selectedBucket();
  if (!bucket) return;
  const prefix = $('prefix-input').value || '';
  state.currentPrefix = prefix;
  $('status').textContent = `Listando s3://${bucket.name}/${prefix}...`;
  $('results').innerHTML = '';
  try {
    const params = new URLSearchParams({
      bucket: bucket.name,
      prefix,
      max_keys: '200',
    });
    if (continuationToken) params.set('continuation_token', continuationToken);
    const data = await fetchJson(`/api/explorer/list?${params.toString()}`);
    renderResults(data);
  } catch (error) {
    $('status').textContent = `Error: ${error.message}`;
  }
}

async function downloadObject(key) {
  const bucket = selectedBucket();
  if (!bucket) return;
  const params = new URLSearchParams({ bucket: bucket.name, key });
  const data = await fetchJson(`/api/explorer/download?${params.toString()}`);
  if (data.url) window.open(data.url, '_blank', 'noopener');
}

async function deleteObject(key) {
  const bucket = selectedBucket();
  if (!bucket) return;
  if (!confirm(`Borrar definitivamente?\n${key}`)) return;
  const params = new URLSearchParams({ bucket: bucket.name, key });
  await fetchJson(`/api/explorer/object?${params.toString()}`, { method: 'DELETE' });
  toast('Objeto eliminado.', 'success');
  await listObjects();
}

function goUp() {
  let prefix = $('prefix-input').value || '';
  if (!prefix) return;
  if (prefix.endsWith('/')) prefix = prefix.slice(0, -1);
  const index = prefix.lastIndexOf('/');
  $('prefix-input').value = index >= 0 ? prefix.slice(0, index + 1) : '';
  listObjects();
}

async function init() {
  try {
    const me = await fetchJson('/auth/me').catch(() => ({}));
    const role = me.user?.role || '';
    state.canDelete = ['owner', 'super_admin', 'admin', 'workspace_admin'].includes(role);
    const data = await fetchJson('/api/explorer/buckets');
    state.buckets = data.buckets || [];
    state.quicklinks = data.quicklinks || [];
    const select = $('bucket-sel');
    select.innerHTML = state.buckets
      .map((bucket) => `<option value="${esc(bucket.id)}">${esc(bucket.label)} (${esc(bucket.name)})</option>`)
      .join('');
    state.currentBucket = state.buckets[0] || null;
    renderQuicklinks();
    $('status').textContent = state.currentBucket
      ? 'Selecciona un prefijo o usa una ruta rapida.'
      : 'No hay buckets configurados.';
  } catch (error) {
    $('status').textContent = `Error cargando Explorer: ${error.message}`;
  }
}

$('bucket-sel').addEventListener('change', () => {
  $('prefix-input').value = '';
  listObjects();
});
$('prefix-input').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') listObjects();
});
$('list-btn').addEventListener('click', listObjects);
$('refresh-btn').addEventListener('click', listObjects);
$('up-btn').addEventListener('click', goUp);

$('quicklinks').addEventListener('click', (event) => {
  const btn = event.target.closest('[data-quick]');
  if (!btn) return;
  const quick = state.quicklinks[Number(btn.dataset.quick)];
  if (!quick) return;
  $('bucket-sel').value = quick.bucket || $('bucket-sel').value;
  $('prefix-input').value = quick.prefix || '';
  listObjects();
});

$('status').addEventListener('click', (event) => {
  const btn = event.target.closest('[data-prefix]');
  if (!btn) return;
  $('prefix-input').value = btn.dataset.prefix || '';
  listObjects();
});

$('results').addEventListener('click', async (event) => {
  const folder = event.target.closest('[data-folder]');
  const download = event.target.closest('[data-download]');
  const del = event.target.closest('[data-delete]');
  try {
    if (folder) {
      $('prefix-input').value = folder.dataset.folder || '';
      await listObjects();
    } else if (download) {
      await downloadObject(download.dataset.download || '');
    } else if (del) {
      await deleteObject(del.dataset.delete || '');
    } else if (event.target.closest('[data-next-page]')) {
      await listObjects(state.nextToken);
    }
  } catch (error) {
    toast(error.message, 'error');
  }
});

init();
