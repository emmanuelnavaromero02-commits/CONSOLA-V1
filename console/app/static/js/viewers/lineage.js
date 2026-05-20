const $ = (id) => document.getElementById(id);

const state = {
  nodes: [],
  edges: [],
  selectedId: null,
  initialCartridge: new URLSearchParams(location.search).get('cartridge') || '',
};

function esc(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

async function readError(response) {
  try {
    const data = await response.json();
    return data.detail || data.error || `HTTP ${response.status}`;
  } catch (_) {
    return `HTTP ${response.status}`;
  }
}

async function fetchJson(url) {
  const response = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
  if (response.redirected && new URL(response.url).pathname === '/login') {
    location.href = '/login';
    throw new Error('Sesion requerida');
  }
  if (!response.ok) throw new Error(await readError(response));
  return response.json();
}

function toast(message) {
  const wrap = $('toasts');
  const node = document.createElement('div');
  node.className = 'toast';
  node.textContent = message;
  wrap.appendChild(node);
  setTimeout(() => node.remove(), 4200);
}

function formatDate(value) {
  if (!value) return '';
  return String(value).replace('T', ' ').slice(0, 16);
}

function byId() {
  return new Map(state.nodes.map((node) => [node.id, node]));
}

function incoming(id) {
  return state.edges.filter((edge) => edge.to === id);
}

function outgoing(id) {
  return state.edges.filter((edge) => edge.from === id);
}

function populateFilters() {
  const select = $('cartridge-filter');
  const current = select.value || state.initialCartridge;
  const carts = [...new Set(state.nodes.map((node) => node.cartridge).filter(Boolean))].sort();
  select.innerHTML = '<option value="">Todos</option>' + carts
    .map((cart) => `<option value="${esc(cart)}">${esc(cart)}</option>`)
    .join('');
  select.value = carts.includes(current) ? current : '';
  state.initialCartridge = '';
}

function filteredNodes() {
  const cart = $('cartridge-filter').value;
  const layer = $('layer-filter').value;
  return state.nodes.filter((node) =>
    (!cart || node.cartridge === cart) &&
    (!layer || node.type === layer)
  );
}

function nodeCard(node) {
  const stale = node.is_stale ? '<span class="pill warn">Stale</span>' : '';
  const rows = [
    node.cartridge ? esc(node.cartridge) : '',
    node.row_count != null ? `${Number(node.row_count).toLocaleString('es')} filas` : '',
    node.last_refresh ? formatDate(node.last_refresh) : '',
  ].filter(Boolean).join(' · ');
  return `
    <button type="button" class="node ${esc(node.type || '')} ${node.id === state.selectedId ? 'active' : ''}" data-node="${esc(node.id)}">
      <div class="node-title">${esc(node.label || node.id)}</div>
      <div class="node-meta">${esc(rows || node.type || 'dataset')}</div>
      ${stale}
    </button>`;
}

function renderLane(title, type, nodes) {
  const body = nodes.length
    ? nodes.map(nodeCard).join('')
    : '<div class="empty">Sin nodos</div>';
  return `
    <section class="lane">
      <div class="lane-title">${title}</div>
      <div class="lane-body">${body}</div>
    </section>`;
}

function renderGraph() {
  const nodes = filteredNodes();
  const lanes = {
    raw: nodes.filter((node) => node.type === 'raw'),
    silver: nodes.filter((node) => node.type === 'silver'),
    gold: nodes.filter((node) => node.type === 'gold'),
  };
  $('graph').innerHTML = [
    renderLane('RAW', 'raw', lanes.raw),
    renderLane('SILVER', 'silver', lanes.silver),
    renderLane('GOLD', 'gold', lanes.gold),
  ].join('');
  renderMetrics(nodes);
  if (state.selectedId && !nodes.some((node) => node.id === state.selectedId)) {
    state.selectedId = null;
    renderDetails(null);
  }
}

function renderMetrics(nodes = state.nodes) {
  $('m-nodes').textContent = String(nodes.length);
  const nodeIds = new Set(nodes.map((node) => node.id));
  $('m-edges').textContent = String(state.edges.filter((edge) => nodeIds.has(edge.from) && nodeIds.has(edge.to)).length);
  $('m-gold').textContent = String(nodes.filter((node) => node.type === 'gold').length);
  $('m-stale').textContent = String(nodes.filter((node) => node.is_stale).length);
}

function renderEdgeList(title, edges, direction, map) {
  if (!edges.length) return `<div class="detail-title">${title}</div><p class="muted">Sin dependencias.</p>`;
  const items = edges.map((edge) => {
    const other = map.get(direction === 'in' ? edge.from : edge.to);
    return `<li>${esc(other?.label || (direction === 'in' ? edge.from : edge.to))}</li>`;
  }).join('');
  return `<div class="detail-title">${title}</div><ul class="edge-list">${items}</ul>`;
}

function renderDetails(node) {
  if (!node) {
    $('node-detail').innerHTML = '<div class="detail-title">Selecciona un nodo</div><p class="muted">Veras dependencias entrantes, salientes y metadatos principales.</p>';
    $('edge-detail').innerHTML = '<div class="detail-title">Dependencias</div><p class="muted">El grafo se alimenta de metadata de datasets y fuentes raw.</p>';
    return;
  }
  const map = byId();
  const ins = incoming(node.id);
  const outs = outgoing(node.id);
  $('node-detail').innerHTML = `
    <div class="detail-title">${esc(node.label || node.id)}</div>
    <p class="muted">${esc(node.type || 'dataset')}${node.cartridge ? ` · ${esc(node.cartridge)}` : ''}</p>
    <ul class="edge-list">
      <li>ID: ${esc(node.id)}</li>
      ${node.row_count != null ? `<li>Filas: ${Number(node.row_count).toLocaleString('es')}</li>` : ''}
      ${node.last_refresh ? `<li>Ultimo refresh: ${esc(formatDate(node.last_refresh))}</li>` : ''}
      ${node.is_stale ? '<li>Estado: stale</li>' : ''}
    </ul>`;
  $('edge-detail').innerHTML = `
    ${renderEdgeList('Depende de', ins, 'in', map)}
    <div style="height:12px"></div>
    ${renderEdgeList('Usado por', outs, 'out', map)}`;
}

async function reload() {
  try {
    const currentCart = $('cartridge-filter').value || state.initialCartridge;
    const params = new URLSearchParams();
    if (currentCart) params.set('cartridge', currentCart);
    const data = await fetchJson(`/api/lineage${params.toString() ? `?${params.toString()}` : ''}`);
    state.nodes = data.nodes || [];
    state.edges = data.edges || [];
    populateFilters();
    renderGraph();
    renderDetails(state.nodes.find((node) => node.id === state.selectedId) || null);
  } catch (error) {
    toast(error.message);
    $('graph').innerHTML = `<div class="empty">Error cargando linaje: ${esc(error.message)}</div>`;
  }
}

$('refresh-btn').addEventListener('click', reload);
$('cartridge-filter').addEventListener('change', reload);
$('layer-filter').addEventListener('change', renderGraph);
$('graph').addEventListener('click', (event) => {
  const button = event.target.closest('[data-node]');
  if (!button) return;
  state.selectedId = button.dataset.node;
  const node = state.nodes.find((item) => item.id === state.selectedId);
  renderGraph();
  renderDetails(node || null);
});

reload();
