// Sprint v1.11 phase 3 — extracted from rag.html for strict CSP.

// ── State ─────────────────────────────────────────────────────────────────────
let sources = [];
let selectedSourceIds = [];  // empty = all
let lastResults = [];

// ── Sources ───────────────────────────────────────────────────────────────────
async function loadSources() {
  try {
    const r = await fetch('/api/rag/sources');
    const data = await r.json();
    sources = data.sources || [];
    renderSources();
  } catch(e) {
    document.getElementById('sourcesList').innerHTML =
      `<div style="padding:14px;font-family:var(--font-mono);font-size:10px;color:#e05c5c;">
        RAG service not available.<br>Check that the rag container is running.
       </div>`;
  }
}

function renderSources() {
  const el = document.getElementById('sourcesList');
  document.getElementById('srcCount').textContent = sources.length;
  if (!sources.length) {
    el.innerHTML = `<div style="padding:20px 14px;font-family:var(--font-mono);font-size:10px;color:var(--text3);">
      No documents ingested yet.
    </div>`;
    return;
  }
  // Sprint v1.11 phase 3: inline onclick → data-action on the dynamically-rendered
  // source item and delete button; a single delegated listener at the bottom
  // dispatches.
  el.innerHTML = sources.map(s => {
    const active = selectedSourceIds.includes(s.id) ? 'active' : '';
    const sourceId = Number(s.id);
    const chunks = s.chunk_count ? `${Number(s.chunk_count).toLocaleString()} chunks` : '';
    const date   = (s.created_at || '').slice(0, 10);
    return `<div class="source-item ${active}" data-action="toggle-source" data-id="${sourceId}">
      <div class="source-info">
        <div class="source-name" title="${esc(s.name)}">${esc(s.name)}</div>
        <div class="source-meta">${esc(chunks)}${chunks && date ? ' · ' : ''}${esc(date)}</div>
        ${s.description ? `<div class="source-meta" style="color:var(--text2)">${esc(s.description)}</div>` : ''}
      </div>
      <button class="source-del" data-action="del-source" data-id="${sourceId}" title="Delete">✕</button>
    </div>`;
  }).join('');
  updateFilterLabel();
}

function toggleSource(id) {
  const idx = selectedSourceIds.indexOf(id);
  if (idx === -1) selectedSourceIds.push(id);
  else selectedSourceIds.splice(idx, 1);
  renderSources();
}

function updateFilterLabel() {
  const el = document.getElementById('filterLabel');
  if (!selectedSourceIds.length) { el.textContent = ''; return; }
  const names = sources
    .filter(s => selectedSourceIds.includes(s.id))
    .map(s => s.name).join(', ');
  el.textContent = `Filter: ${names}`;
}

async function delSource(e, id) {
  e.stopPropagation();
  const src = sources.find(s => s.id === id);
  if (!confirm(`Delete "${src?.name}"? All chunks will be removed.`)) return;
  try {
    await fetch(`/api/rag/sources/${id}`, { method: 'DELETE' });
    selectedSourceIds = selectedSourceIds.filter(x => x !== id);
    await loadSources();
  } catch(err) {
    alert('Delete failed: ' + err.message);
  }
}

// ── File loading ──────────────────────────────────────────────────────────────
let pendingFileData = null;  // {content, mime_type}

async function onFileChange(e) {
  const file = e.target.files[0];
  if (!file) return;
  const label = document.getElementById('fileLabel');
  const labelText = document.getElementById('fileLabelText');

  labelText.textContent = file.name;
  label.classList.add('has-file');

  if (!document.getElementById('inName').value) {
    document.getElementById('inName').value = file.name.replace(/\.[^.]+$/, '');
  }

  const mime = file.type || 'text/plain';
  if (mime === 'application/pdf' || file.name.endsWith('.pdf')) {
    const buf = await file.arrayBuffer();
    const b64 = btoa(String.fromCharCode(...new Uint8Array(buf)));
    pendingFileData = { content: b64, mime_type: 'application/pdf' };
    document.getElementById('inContent').value = '[PDF — will be extracted server-side]';
    document.getElementById('inContent').disabled = true;
  } else {
    const text = await file.text();
    document.getElementById('inContent').value = text;
    document.getElementById('inContent').disabled = false;
    pendingFileData = null;
  }
}

// ── Ingest ────────────────────────────────────────────────────────────────────
async function doIngest() {
  const name    = document.getElementById('inName').value.trim();
  const desc    = document.getElementById('inDesc').value.trim();
  const content = document.getElementById('inContent').value.trim();
  const status  = document.getElementById('ingestStatus');
  const btn     = document.getElementById('ingestBtn');

  if (!name) { status.innerHTML = '<span class="err">Name is required.</span>'; return; }
  if (!content && !pendingFileData) { status.innerHTML = '<span class="err">Content is required.</span>'; return; }

  btn.disabled = true;
  status.innerHTML = '<span class="dim"><span class="spinner"></span> Embedding chunks…</span>';

  try {
    const body = pendingFileData
      ? { name, description: desc, ...pendingFileData }
      : { name, description: desc, content, mime_type: 'text/plain' };

    const r = await fetch('/api/rag/ingest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail || r.statusText);
    }
    const data = await r.json();
    status.innerHTML = `<span class="ok">✓ Ingested: ${Number(data.parents || 0).toLocaleString()} parents, ${Number(data.children || 0).toLocaleString()} child chunks.</span>`;
    document.getElementById('inName').value = '';
    document.getElementById('inDesc').value = '';
    document.getElementById('inContent').value = '';
    document.getElementById('inContent').disabled = false;
    document.getElementById('fileInput').value = '';
    document.getElementById('fileLabelText').textContent = 'Load file…';
    document.getElementById('fileLabel').classList.remove('has-file');
    pendingFileData = null;
    await loadSources();
  } catch(err) {
    status.innerHTML = `<span class="err">✗ ${esc(err.message)}</span>`;
  } finally {
    btn.disabled = false;
  }
}

// ── Search ────────────────────────────────────────────────────────────────────
async function doSearch() {
  const query = document.getElementById('searchInput').value.trim();
  if (!query) return;
  const topK  = parseInt(document.getElementById('topK').value, 10);
  const area  = document.getElementById('resultsArea');
  const bar   = document.getElementById('copyBar');

  area.innerHTML = `<div class="results-empty"><span class="spinner"></span></div>`;
  bar.classList.remove('visible');

  try {
    const body = { query, top_k: topK };
    if (selectedSourceIds.length) body.source_ids = selectedSourceIds;

    const r = await fetch('/api/rag/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const data = await r.json();
    lastResults = data.results || [];
    renderResults(lastResults, query);
  } catch(err) {
    area.innerHTML = `<div class="results-empty"><span class="err">✗ ${esc(err.message)}</span></div>`;
  }
}

function renderResults(results, query) {
  const area = document.getElementById('resultsArea');
  const bar  = document.getElementById('copyBar');

  if (!results.length) {
    area.innerHTML = `<div class="results-empty">
      No results found.<br><span class="hint">Try a different query or ingest more documents.</span>
    </div>`;
    bar.classList.remove('visible');
    return;
  }

  // Sprint v1.11 phase 3: inline onclick → data-action.
  area.innerHTML = results.map((r, i) => {
    const pct = Math.round(r.similarity * 100);
    const scoreColor = pct >= 80 ? 'var(--green)' : pct >= 60 ? 'var(--amber)' : 'var(--text3)';
    return `<div class="result-card" id="rc${i}">
      <div class="result-card-hdr" data-action="toggle-card" data-idx="${i}">
        <span class="result-rank">${i + 1}</span>
        <span class="result-source">${esc(r.source_name)}</span>
        <span class="result-score" style="color:${scoreColor}">${pct}% match</span>
        <span class="result-toggle">▼</span>
      </div>
      <div class="result-body">
        <div class="result-match-lbl">Matched segment</div>
        <div class="result-match">${esc(r.child_content)}</div>
        <div class="result-ctx-lbl">Parent context (sent to LLM)</div>
        <div class="result-ctx">${esc(r.context)}</div>
        <button class="btn btn-green result-copy-btn" data-action="copy-ctx" data-idx="${i}">COPY CONTEXT</button>
      </div>
    </div>`;
  }).join('');

  // Auto-open top result
  toggleCard(0);

  document.getElementById('copyBarLabel').textContent =
    `${results.length} result${results.length !== 1 ? 's' : ''} · query: "${query}"`;
  bar.classList.add('visible');
}

function toggleCard(i) {
  const card = document.getElementById(`rc${i}`);
  if (card) card.classList.toggle('open');
}

function copyCtx(i) {
  const r = lastResults[i];
  if (!r) return;
  navigator.clipboard.writeText(r.context).then(() => {
    const btn = document.querySelectorAll('.result-copy-btn')[i];
    if (btn) { btn.textContent = 'COPIED!'; setTimeout(() => btn.textContent = 'COPY CONTEXT', 1500); }
  });
}

function copyAllContext() {
  if (!lastResults.length) return;
  const text = lastResults.map((r, i) =>
    `## Context ${i + 1} — ${r.source_name} (${Math.round(r.similarity * 100)}% match)\n\n${r.context}`
  ).join('\n\n---\n\n');
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.querySelector('.copy-bar .btn');
    btn.textContent = 'COPIED!';
    setTimeout(() => btn.textContent = 'COPY CONTEXT', 1500);
  });
}

// ── Utils ─────────────────────────────────────────────────────────────────────
function esc(s) {
  return String(s ?? '')
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

// ── Init ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
  document.getElementById('fileInput').addEventListener('change', onFileChange);
  document.getElementById('ingestBtn').addEventListener('click', doIngest);

  const searchInput = document.getElementById('searchInput');
  searchInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(); });
  document.getElementById('searchBtn').addEventListener('click', doSearch);
  document.getElementById('copyAllBtn').addEventListener('click', copyAllContext);

  // Delegated handlers for dynamic content (sources list + result cards).
  document.body.addEventListener('click', (ev) => {
    const target = ev.target.closest('[data-action]');
    if (!target) return;
    const action = target.dataset.action;
    if (action === 'del-source') {
      delSource(ev, Number(target.dataset.id));
    } else if (action === 'toggle-source') {
      // Don't fire toggle if the user actually clicked the delete button inside.
      if (ev.target.closest('[data-action="del-source"]')) return;
      toggleSource(Number(target.dataset.id));
    } else if (action === 'toggle-card') {
      toggleCard(Number(target.dataset.idx));
    } else if (action === 'copy-ctx') {
      copyCtx(Number(target.dataset.idx));
    }
  });

  await loadSources();
});
