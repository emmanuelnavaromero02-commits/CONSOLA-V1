// ── MODecissions PaaS — Console UI ───────────────────────────────────────────

// Apply saved theme synchronously (script at end of body — DOM already parsed)
document.documentElement.dataset.theme = localStorage.getItem('mod-theme') || 'dark';

let _history = [];
let _busy = false;

// ── Boot sequence ─────────────────────────────────────────────────────────────

window.addEventListener('DOMContentLoaded', async () => {
  // Sync button label with saved theme
  const saved = localStorage.getItem('mod-theme') || 'dark';
  const btn = document.getElementById('theme-btn');
  if (btn) btn.textContent = saved === 'light' ? '☀ LIGHT' : '☾ DARK';
  await bootSequence();
});

async function bootSequence() {
  await delay(400);
  setText('boot-mcp-line', 'Connecting to MCP registry...');
  await loadServers(true);   // force health-check on boot
  await Promise.all([loadTokens(), loadJobs()]);
  await delay(200);
  setText('boot-ready', '▶ SYSTEM READY — Type your query or use QUICK OPS');
}

// ── API helper ────────────────────────────────────────────────────────────────

async function apiFetch(url, method = 'GET', body = null) {
  try {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    const r = await fetch(url, opts);
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  }
}

// ── Chat ──────────────────────────────────────────────────────────────────────

function handleKey(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
  // Auto-resize textarea
  const ta = e.target;
  ta.style.height = 'auto';
  ta.style.height = Math.min(ta.scrollHeight, 120) + 'px';
}

async function sendMessage() {
  if (_busy) return;
  const input = document.getElementById('chat-input');
  const msg = input.value.trim();
  if (!msg) return;

  input.value = '';
  input.style.height = 'auto';
  _busy = true;

  appendMessage('user', msg);
  log('info', `Query: ${msg.substring(0, 40)}...`);

  const typing = appendTyping();

  const data = await apiFetch('/assistant/chat', 'POST', {
    message: msg,
    history: _history,
  });

  typing.remove();
  _busy = false;

  if (!data) {
    appendMessage('system', '⚠ Connection error — check console service.', true);
    log('err', 'Assistant error');
    return;
  }

  _history = data.messages || _history;
  appendMessage('system', data.reply || '(no response)');

  // Auto-open viewer panel for any deeplinks returned by monitoring tools
  const viewerLinks = data.viewer_urls || [];
  viewerLinks.forEach(({ url, label }) => openViewer(url, label));

  log('ok', 'Response received');
  loadTokens();
  loadJobs();
}

function quickCmd(cmd) {
  document.getElementById('chat-input').value = cmd;
  sendMessage();
}

function appendMessage(role, text, isError = false) {
  const win = document.getElementById('chat-window');
  const ts = new Date().toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  const div = document.createElement('div');
  div.className = `msg ${role}`;
  div.innerHTML = `
    <div class="msg-header">
      <span class="msg-role-${role === 'user' ? 'user' : 'system'}">
        ${role === 'user' ? '◀ YOU' : '◈ MOD·AI'}
      </span>
      <span class="msg-ts">${ts}</span>
    </div>
    <div class="msg-body${isError ? ' err' : ''}">${renderText(text)}</div>
  `;
  win.appendChild(div);
  win.scrollTop = win.scrollHeight;
}

function appendTyping() {
  const win = document.getElementById('chat-window');
  const div = document.createElement('div');
  div.className = 'typing-indicator';
  div.innerHTML = `<div class="typing-dot"></div><div class="typing-dot"></div><div class="typing-dot"></div>`;
  win.appendChild(div);
  win.scrollTop = win.scrollHeight;
  return div;
}

function renderText(text) {
  // Viewer deeplinks → inline button that opens in the bottom panel
  // Matches both relative (/viewer/...) and absolute (http://host/viewer/...) URLs
  const viewerLinks = [];
  text = text.replace(/\[([^\]]+)\]\(((?:https?:\/\/[^/)\s]+)?\/viewer\/[^)]+)\)/g, (_, label, rawUrl) => {
    // Strip host so it always works regardless of CONSOLE_URL value
    const url = rawUrl.replace(/^https?:\/\/[^/]+/, '');
    const id = `vlnk_${viewerLinks.length}`;
    viewerLinks.push({ id, label, url });
    return `\x00VLNK:${id}\x00`;
  });

  // Regular external links (run after viewer links so /viewer/ URLs are already consumed)
  const extLinks = [];
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, (_, label, url) => {
    const id = `elnk_${extLinks.length}`;
    extLinks.push({ id, label, url });
    return `\x00ELNK:${id}\x00`;
  });

  let html = esc(text)
    .replace(/```([\s\S]*?)```/g, '<pre>$1</pre>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong style="color:var(--amber)">$1</strong>')
    .replace(/^#{1,3} (.+)$/gm, '<strong style="color:var(--cyan)">$1</strong>')
    .replace(/\n/g, '<br>');

  // Restore viewer buttons
  viewerLinks.forEach(({ id, label, url }) => {
    html = html.replace(
      `\x00VLNK:${id}\x00`,
      `<button class="viewer-link-btn" onclick="openViewer('${url.replace(/'/g,"\\'")}','${label.replace(/'/g,"\\'")}')">◈ ${esc(label)}</button>`
    );
  });

  // Restore external links
  extLinks.forEach(({ id, label, url }) => {
    html = html.replace(
      `\x00ELNK:${id}\x00`,
      `<a href="${url}" target="_blank" style="color:var(--cyan);text-decoration:underline">${esc(label)}</a>`
    );
  });

  return html;
}

// ── MCP Servers ───────────────────────────────────────────────────────────────

async function loadServers(forceCheck = false) {
  if (forceCheck) await apiFetch('/mcp/servers/health-check', 'POST');
  const data = await apiFetch('/mcp/servers');
  const list = document.getElementById('servers-list');
  const servers = data?.servers || [];

  if (!servers.length) {
    list.innerHTML = `<div class="dim" style="padding:10px;font-size:10px">No servers registered.<br>Services register on startup.</div>`;
    setText('tools-count', '0');
    return;
  }

  const cartridges = servers.filter(s => s.category === 'cartridge');
  const platform   = servers.filter(s => s.category !== 'cartridge');

  let totalTools = 0;
  const renderCard = s => {
    const tools = Array.isArray(s.tools) ? s.tools : [];
    totalTools += tools.length;
    return `<div class="server-card ${s.healthy ? 'healthy' : 'unhealthy'}"
      onclick="showServerTools('${s.id}','${esc(s.name)}')" title="${esc(s.description || '')}">
      <div class="server-name">${esc(s.name)}</div>
      <div class="server-meta">
        <span class="server-cat">${esc(s.category)}</span>
        <span class="server-tools">⚙ ${tools.length} tools</span>
      </div>
    </div>`;
  };

  let html = '';
  if (cartridges.length) {
    html += `<div class="server-group-label">CARTUCHOS</div>`;
    html += cartridges.map(renderCard).join('');
  }
  if (platform.length) {
    html += `<div class="server-group-label" style="margin-top:6px">PLATAFORMA</div>`;
    html += platform.map(renderCard).join('');
  }
  list.innerHTML = html;

  setText('tools-count', totalTools);
  log('ok', `${servers.length} MCP server(s) online`);
}

async function showServerTools(serverId, serverName) {
  const data = await apiFetch(`/mcp/servers/${serverId}/tools`);
  const tools = data?.tools || [];
  if (!tools.length) { log('info', `No tools for ${serverName}`); return; }

  const names = tools.map(t => `• ${t.name}: ${t.description || '—'}`).join('\n');
  appendMessage('system', `◈ ${serverName} — ${tools.length} tools:\n\n${names}`);
  document.getElementById('chat-window').scrollTop = 99999;
}

// ── Datasets modal ────────────────────────────────────────────────────────────

function openDatasets() {
  document.getElementById('datasets-modal').style.display = 'flex';
  loadDatasets();
}

async function loadDatasets() {
  const body = document.getElementById('datasets-body');
  body.innerHTML = '<div class="loading-dots">LOADING<span class="dots"></span></div>';
  const data = await apiFetch('/datasets');
  const datasets = data?.datasets || [];

  if (!datasets.length) {
    body.innerHTML = `<div class="dim" style="padding:20px;text-align:center">
      No datasets defined yet.<br><br>
      Ask the assistant to generate a transformation.
    </div>`;
    return;
  }

  body.innerHTML = `
    <div style="display:grid;grid-template-columns:180px 70px 1fr 120px;gap:10px;padding:6px 10px;font-size:9px;color:var(--text3);font-family:var(--font-pixel);letter-spacing:1px">
      <span>NAME</span><span>LAYER</span><span>DESCRIPTION</span><span style="text-align:right">LAST REFRESH</span>
    </div>
    ${datasets.map(d => {
      const layerClass = d.layer === 'gold' ? 'layer-gold' : 'layer-silver';
      const refreshed = d.last_refresh ? new Date(d.last_refresh).toLocaleString('es', {dateStyle:'short',timeStyle:'short'}) : '—';
      return `<div class="dataset-row">
        <div class="dataset-name">${esc(d.name)}</div>
        <div class="dataset-layer ${layerClass}">${d.layer.toUpperCase()}</div>
        <div class="dataset-desc">${esc(d.description || '—')}</div>
        <div class="dataset-meta">
          ${d.row_count != null ? `<div>${Number(d.row_count).toLocaleString()} rows</div>` : ''}
          <div>${refreshed}</div>
          <button class="btn-sm" style="margin-top:4px" onclick="refreshDataset('${esc(d.name)}')">↺ refresh</button>
        </div>
      </div>`;
    }).join('')}
  `;
}

async function refreshDataset(name) {
  log('info', `Refreshing ${name}...`);
  const data = await apiFetch(`/datasets/${name}/refresh`, 'POST');
  if (data?.row_count != null) {
    log('ok', `${name}: ${data.row_count} rows materialized`);
    loadDatasets();
  } else {
    log('err', `Failed to refresh ${name}`);
  }
}

function closeModal(e) {
  if (e.target === e.currentTarget) {
    e.currentTarget.style.display = 'none';
  }
}

// ── Activity log ──────────────────────────────────────────────────────────────

function log(type, msg) {
  const el = document.getElementById('activity-log');
  const ts = new Date().toLocaleTimeString('es', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  const prefix = type === 'ok' ? '✓' : type === 'err' ? '✗' : '·';
  const entry = document.createElement('div');
  entry.className = `log-entry ${type}`;
  entry.innerHTML = `<span class="log-ts">${ts}</span>${prefix} ${esc(msg)}`;
  // Remove placeholder
  el.querySelectorAll('.dim').forEach(d => d.remove());
  el.insertBefore(entry, el.firstChild);
  // Keep last 30
  while (el.children.length > 30) el.removeChild(el.lastChild);
}

// ── Utils ─────────────────────────────────────────────────────────────────────

function esc(str) {
  if (str == null) return '';
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

// ── Jobs ──────────────────────────────────────────────────────────────────────

let _jobPollTimer = null;

async function loadJobs() {
  const data = await apiFetch('/jobs?limit=8');
  const jobs = data?.jobs || [];
  const el = document.getElementById('jobs-list');
  if (!el) return;

  const running = jobs.filter(j => j.status === 'running').length;
  setText('jobs-running', running ? `${running} running` : `${jobs.length} total`);

  if (!jobs.length) {
    el.innerHTML = '<div class="dim" style="padding:8px;font-size:10px">No jobs yet.</div>';
    _stopJobPoll();
    return;
  }

  el.innerHTML = jobs.map(j => {
    const tool = esc(j.tool.replace(/^.*__/, ''));   // bare tool name
    const srv  = esc(j.tool.split('__')[0] || '');
    const msg  = esc(j.message || j.error || '—');
    const url  = `/viewer/jobs/${j.job_id}`;
    const lbl  = `Job ${j.job_id}`;
    return `<div class="job-entry" title="${esc(j.tool)}\n${esc(j.message||'')}"
      onclick="openViewer('${url}','${lbl}')" style="cursor:pointer">
      <div class="job-dot ${j.status}"></div>
      <div class="job-info">
        <div class="job-tool">${srv} › ${tool}</div>
        <div class="job-msg">${esc(msg)}</div>
      </div>
      <div class="job-id">${esc(j.job_id)}</div>
    </div>`;
  }).join('');

  // Auto-poll while any job is running
  if (running > 0) {
    _startJobPoll();
  } else {
    _stopJobPoll();
  }
}

function _startJobPoll() {
  if (_jobPollTimer) return;
  _jobPollTimer = setInterval(() => loadJobs(), 4000);
}

function _stopJobPoll() {
  if (_jobPollTimer) { clearInterval(_jobPollTimer); _jobPollTimer = null; }
}

// ── Token usage ───────────────────────────────────────────────────────────────

async function loadTokens() {
  const data = await apiFetch('/tokens/summary');
  if (!data) return;

  const fmt = n => Number(n).toLocaleString('es');
  const cost = data.cost_usd != null
    ? '$' + Number(data.cost_usd).toLocaleString('en', { minimumFractionDigits: 4, maximumFractionDigits: 4 })
    : '—';

  setText('tok-in',    fmt(data.input_tokens  || 0));
  setText('tok-out',   fmt(data.output_tokens || 0));
  setText('tok-cost',  cost);
  setText('token-calls', `${data.calls || 0} calls`);

  const modelsEl = document.getElementById('token-models');
  if (!modelsEl) return;
  modelsEl.innerHTML = (data.models || []).map(m => `
    <div class="token-model-row">
      <span class="token-model-name" title="${esc(m.model)}">${esc(m.model.replace('claude-', ''))}</span>
      <span>${Number(m.input_tokens + m.output_tokens).toLocaleString('es')} tok</span>
    </div>`).join('');
}

// ── Viewer Panel ──────────────────────────────────────────────────────────────

let _viewerTabs = [];   // [{url, label}]
let _activeViewerUrl = null;
let _viewerLoadTimer = null;
const VIEWER_DEFAULT_H = 360;

function openViewer(url, label) {
  // Add tab if not already open
  if (!_viewerTabs.find(t => t.url === url)) {
    _viewerTabs.push({ url, label: label || url });
  }
  _switchViewerTab(url);
  _showViewerPanel();
  log('info', `Viewer: ${label || url}`);
}

function _switchViewerTab(url) {
  _activeViewerUrl = url;
  const fallback = document.getElementById('viewer-fallback');
  const frame = document.getElementById('viewer-frame');
  if (fallback) fallback.style.display = 'none';
  if (_viewerLoadTimer) clearTimeout(_viewerLoadTimer);
  frame.onload = () => {
    if (fallback) fallback.style.display = 'none';
    if (_viewerLoadTimer) clearTimeout(_viewerLoadTimer);
  };
  frame.onerror = () => {
    if (fallback) fallback.style.display = 'flex';
  };
  frame.src = url;
  _viewerLoadTimer = setTimeout(() => {
    if (_activeViewerUrl === url && fallback) fallback.style.display = 'flex';
  }, 8000);
  _renderViewerTabs();
}

function _renderViewerTabs() {
  const tabs = document.getElementById('viewer-tabs');
  tabs.innerHTML = _viewerTabs.map(t => `
    <div class="viewer-tab ${t.url === _activeViewerUrl ? 'active' : ''}"
         onclick="openViewer('${t.url.replace(/'/g,"\\'")}','${t.label.replace(/'/g,"\\'")}')">
      ${esc(t.label)}
      <span class="viewer-tab-close"
            onclick="event.stopPropagation();closeViewerTab('${t.url.replace(/'/g,"\\'")}')">×</span>
    </div>`).join('');
}

function closeViewerTab(url) {
  _viewerTabs = _viewerTabs.filter(t => t.url !== url);
  if (!_viewerTabs.length) {
    closeViewerPanel();
    return;
  }
  if (_activeViewerUrl === url) {
    _switchViewerTab(_viewerTabs[_viewerTabs.length - 1].url);
  } else {
    _renderViewerTabs();
  }
}

function closeViewerPanel() {
  document.getElementById('viewer-panel').style.display = 'none';
  document.getElementById('resize-handle').style.display = 'none';
  document.getElementById('viewer-frame').src = 'about:blank';
  const fallback = document.getElementById('viewer-fallback');
  if (fallback) fallback.style.display = 'none';
  if (_viewerLoadTimer) clearTimeout(_viewerLoadTimer);
}

function popoutViewer() {
  if (_activeViewerUrl) window.open(_activeViewerUrl, '_blank');
}

function _showViewerPanel() {
  const panel = document.getElementById('viewer-panel');
  const handle = document.getElementById('resize-handle');
  panel.style.display = 'flex';
  handle.style.display = 'block';
  if (!panel.style.height || panel.style.height === '0px') {
    panel.style.height = VIEWER_DEFAULT_H + 'px';
  }
}

// Drag resize
(function () {
  const handle = document.getElementById('resize-handle');
  if (!handle) return;
  let dragging = false, startY = 0, startH = 0;

  handle.addEventListener('mousedown', e => {
    dragging = true;
    startY = e.clientY;
    startH = document.getElementById('viewer-panel').offsetHeight;
    document.body.style.cursor = 'ns-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });

  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const delta = startY - e.clientY;
    const newH = Math.max(120, Math.min(window.innerHeight * 0.85, startH + delta));
    document.getElementById('viewer-panel').style.height = newH + 'px';
  });

  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
  });
})();

// ── Theme ─────────────────────────────────────────────────────────────────────

function cycleTheme() {
  const current = document.documentElement.dataset.theme || 'dark';
  const next    = current === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = next;
  localStorage.setItem('mod-theme', next);
  const btn = document.getElementById('theme-btn');
  if (btn) btn.textContent = next === 'light' ? '☀ LIGHT' : '☾ DARK';
  log('info', `Theme: ${next}`);
}
