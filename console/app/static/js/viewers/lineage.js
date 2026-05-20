const $ = (id) => document.getElementById(id);

const state = {
  nodes: [],
  edges: [],
  selectedId: null,
  initialCartridge: new URLSearchParams(location.search).get('cartridge') || '',
};

let cy = null;
let dagreRegistered = false;

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

function colorFor(node) {
  if (node.is_stale) return '#ff8e3c';
  switch (node.type) {
    case 'raw': return '#6e7681';
    case 'silver': return '#7c9fff';
    case 'gold': return '#d29922';
    default: return '#8b949e';
  }
}

function registerDagre() {
  if (dagreRegistered || !window.cytoscape || !window.cytoscapeDagre) return;
  window.cytoscape.use(window.cytoscapeDagre);
  dagreRegistered = true;
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
  if (window.cytoscape && nodes.length) {
    renderCyGraph(nodes);
    renderMetrics(nodes);
    return;
  }
  if (nodes.length) {
    renderSvgGraph(nodes);
    renderMetrics(nodes);
    return;
  }
  cy = null;
  $('graph').classList.remove('cy-graph', 'svg-graph');
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

function svgEl(name, attrs = {}) {
  const el = document.createElementNS('http://www.w3.org/2000/svg', name);
  Object.entries(attrs).forEach(([key, value]) => el.setAttribute(key, String(value)));
  return el;
}

function shortLabel(value, max = 24) {
  const text = String(value || '');
  return text.length > max ? `${text.slice(0, max - 1)}...` : text;
}

function renderSvgGraph(nodes) {
  cy = null;
  const host = $('graph');
  host.classList.remove('cy-graph');
  host.classList.add('svg-graph');
  host.replaceChildren();

  const laneOrder = ['raw', 'silver', 'gold'];
  const grouped = Object.fromEntries(laneOrder.map((lane) => [lane, []]));
  nodes.forEach((node) => {
    const lane = laneOrder.includes(node.type) ? node.type : 'silver';
    grouped[lane].push(node);
  });

  const nodeW = 190;
  const nodeH = 58;
  const laneW = 300;
  const top = 72;
  const left = 54;
  const gap = 26;
  const maxRows = Math.max(1, ...laneOrder.map((lane) => grouped[lane].length));
  const width = left * 2 + laneW * (laneOrder.length - 1) + nodeW + 40;
  const height = Math.max(520, top * 2 + maxRows * (nodeH + gap));
  const positions = new Map();
  laneOrder.forEach((lane, laneIndex) => {
    grouped[lane].forEach((node, rowIndex) => {
      positions.set(node.id, {
        x: left + laneIndex * laneW,
        y: top + rowIndex * (nodeH + gap),
      });
    });
  });

  const svg = svgEl('svg', {
    class: 'lineage-svg',
    viewBox: `0 0 ${width} ${height}`,
    role: 'img',
    'aria-label': 'Grafo de linaje de datasets',
  });
  svg.dataset.defaultViewBox = `0 0 ${width} ${height}`;
  const defs = svgEl('defs');
  const marker = svgEl('marker', {
    id: 'lineage-arrow',
    viewBox: '0 0 10 10',
    refX: 9,
    refY: 5,
    markerWidth: 6,
    markerHeight: 6,
    orient: 'auto-start-reverse',
  });
  marker.appendChild(svgEl('path', { d: 'M 0 0 L 10 5 L 0 10 z', fill: 'currentColor' }));
  defs.appendChild(marker);
  svg.appendChild(defs);

  laneOrder.forEach((lane, laneIndex) => {
    const x = left + laneIndex * laneW;
    const label = svgEl('text', {
      x,
      y: 30,
      fill: 'currentColor',
      'font-family': 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
      'font-size': 12,
      'font-weight': 800,
    });
    label.textContent = lane.toUpperCase();
    svg.appendChild(label);
  });

  const selectedEdges = new Set([
    ...incoming(state.selectedId || '').map((edge) => `${edge.from}->${edge.to}`),
    ...outgoing(state.selectedId || '').map((edge) => `${edge.from}->${edge.to}`),
  ]);
  state.edges
    .filter((edge) => positions.has(edge.from) && positions.has(edge.to))
    .forEach((edge) => {
      const from = positions.get(edge.from);
      const to = positions.get(edge.to);
      const startX = from.x + nodeW;
      const startY = from.y + nodeH / 2;
      const endX = to.x;
      const endY = to.y + nodeH / 2;
      const mid = Math.max(40, (endX - startX) / 2);
      const path = svgEl('path', {
        d: `M ${startX} ${startY} C ${startX + mid} ${startY}, ${endX - mid} ${endY}, ${endX} ${endY}`,
        class: `lineage-edge ${selectedEdges.has(`${edge.from}->${edge.to}`) ? 'related' : ''}`.trim(),
        'marker-end': 'url(#lineage-arrow)',
      });
      svg.appendChild(path);
    });

  nodes.forEach((node) => {
    const pos = positions.get(node.id);
    if (!pos) return;
    const group = svgEl('g', {
      class: `lineage-node ${node.id === state.selectedId ? 'active' : ''} ${node.is_stale ? 'stale' : ''}`.trim(),
      'data-node': node.id,
      tabindex: 0,
    });
    group.appendChild(svgEl('rect', {
      x: pos.x,
      y: pos.y,
      width: nodeW,
      height: nodeH,
      fill: colorFor(node),
      opacity: 0.92,
    }));
    const title = svgEl('text', { x: pos.x + 12, y: pos.y + 24 });
    title.textContent = shortLabel(node.label || node.id);
    const meta = svgEl('text', { x: pos.x + 12, y: pos.y + 43, class: 'meta' });
    meta.textContent = shortLabel(node.cartridge || node.type || 'dataset', 28);
    group.append(title, meta);
    svg.appendChild(group);
  });

  host.appendChild(svg);
}

function renderCyGraph(nodes) {
  registerDagre();
  const host = $('graph');
  host.classList.add('cy-graph');
  host.replaceChildren();
  const nodeIds = new Set(nodes.map((node) => node.id));
  const elements = [
    ...nodes.map((node) => ({
      classes: node.is_stale ? 'stale' : '',
      data: {
        id: node.id,
        label: node.label || node.id,
        type: node.type || 'dataset',
        color: colorFor(node),
      },
    })),
    ...state.edges
      .filter((edge) => nodeIds.has(edge.from) && nodeIds.has(edge.to))
      .map((edge, index) => ({
        data: {
          id: `${edge.from}->${edge.to}-${index}`,
          source: edge.from,
          target: edge.to,
        },
      })),
  ];
  cy = window.cytoscape({
    container: host,
    elements,
    layout: {
      name: window.cytoscapeDagre ? 'dagre' : 'breadthfirst',
      rankDir: 'LR',
      nodeSep: 34,
      edgeSep: 10,
      rankSep: 92,
      fit: true,
      padding: 34,
    },
    style: [
      {
        selector: 'node',
        style: {
          'background-color': 'data(color)',
          'border-width': 1,
          'border-color': '#d0d7de',
          color: '#f0f6fc',
          label: 'data(label)',
          'font-size': 11,
          'font-family': 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
          'text-valign': 'center',
          'text-halign': 'center',
          'text-wrap': 'wrap',
          'text-max-width': 130,
          width: 150,
          height: 46,
          shape: 'round-rectangle',
        },
      },
      {
        selector: 'node.stale',
        style: {
          'border-width': 3,
          'border-color': '#ff8e3c',
        },
      },
      {
        selector: 'edge',
        style: {
          width: 2,
          'line-color': '#4b5563',
          'target-arrow-color': '#4b5563',
          'target-arrow-shape': 'triangle',
          'curve-style': 'bezier',
        },
      },
      {
        selector: 'node:selected',
        style: {
          'border-width': 4,
          'border-color': '#58a6ff',
        },
      },
      {
        selector: '.related',
        style: {
          'line-color': '#58a6ff',
          'target-arrow-color': '#58a6ff',
          width: 3,
        },
      },
    ],
  });
  cy.on('tap', 'node', (event) => {
    state.selectedId = event.target.id();
    const node = state.nodes.find((item) => item.id === state.selectedId);
    renderDetails(node || null);
    highlightRelated();
  });
  highlightRelated();
}

function highlightRelated() {
  if (!cy) return;
  cy.elements().removeClass('related');
  if (!state.selectedId) return;
  const node = cy.getElementById(state.selectedId);
  if (!node || node.empty()) return;
  node.select();
  node.connectedEdges().addClass('related');
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
$('fit-btn').addEventListener('click', () => {
  if (cy) {
    cy.fit(undefined, 32);
    return;
  }
  const svg = $('graph').querySelector('svg.lineage-svg');
  if (svg?.dataset.defaultViewBox) svg.setAttribute('viewBox', svg.dataset.defaultViewBox);
});
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
