    // ── SQL Runner ──────────────────────────────────────────────────────────
    import { state } from './legacy-state.js';
    import { esc, _renderQueryTable, _currentEditorEntity } from './legacy.js';

    function readCookie(name) {
      const prefix = `${name}=`;
      for (const raw of document.cookie.split(';')) {
        const c = raw.trim();
        if (c.startsWith(prefix)) return decodeURIComponent(c.slice(prefix.length));
      }
      return null;
    }

    function jsonHeaders() {
      const headers = {'Content-Type': 'application/json'};
      const csrf = readCookie('csrf_token');
      if (csrf) headers['X-CSRF-Token'] = csrf;
      return headers;
    }

    function currentCartridgeId() {
      return state._currentCartridge?.id
        || document.getElementById('ds-ed-cart')?.value.trim()
        || document.getElementById('cartridge-sel')?.value
        || '';
    }

    function currentEditorSources() {
      const entity = _currentEditorEntity();
      const cart = currentCartridgeId();
      return entity && cart ? [`raw/${cart}/${entity}`] : [];
    }

    export function openSqlRunner(sql, label, sources) {
      const ta = document.getElementById('sql-runner-ta');
      const ov = document.getElementById('sql-runner-overlay');
      const src = document.getElementById('sql-runner-source');
      if (!ta || !ov) return;
      ta.value = sql || '';
      if (src) src.textContent = label || '';
      state._sqlRunnerSources = sources || [];
      document.getElementById('sql-runner-results').innerHTML = '';
      document.getElementById('sql-runner-status').textContent = '';
      ov.style.display = 'flex';
      ta.focus();
    }

    export function closeSqlRunner() {
      document.getElementById('sql-runner-overlay').style.display = 'none';
    }

    export async function execSqlRunner() {
      const ta      = document.getElementById('sql-runner-ta');
      const status  = document.getElementById('sql-runner-status');
      const results = document.getElementById('sql-runner-results');
      if (!ta) return;

      // Selected text or full content
      const sel = ta.selectionStart !== ta.selectionEnd
        ? ta.value.slice(ta.selectionStart, ta.selectionEnd).trim()
        : ta.value.trim();

      if (!sel) return;
      if (status) status.textContent = '⟳ ejecutando...';
      results.innerHTML = '';
      const t0 = Date.now();
      try {
        const r = await fetch('/api/bronze/query', {
          method: 'POST',
          credentials: 'include',
          headers: jsonHeaders(),
          body: JSON.stringify({ sql: sel, limit: 200, sources: state._sqlRunnerSources }),
        });
        const d      = await r.json();
        const elapsed = ((Date.now()-t0)/1000).toFixed(2);
        const result = d.result || d;
        if (result.error) {
          status.textContent = '✗ error';
          results.innerHTML = `<pre style="color:var(--red);font-size:11px;
            font-family:var(--font-mono);white-space:pre-wrap">${esc(result.error)}</pre>`;
          return;
        }
        const rows   = result.data   || [];
        const schema = result.schema || [];
        status.textContent = `✓ ${rows.length} filas · ${elapsed}s`;
        results.innerHTML = rows.length
          ? _renderQueryTable(schema, rows, 99999)
          : `<div style="color:var(--text3);font-style:italic;padding:8px">Sin resultados</div>`;
      } catch(e) {
        status.textContent = '✗ error de red';
        results.innerHTML = `<div style="color:var(--red);font-size:11px">${esc(e.message)}</div>`;
      }
    }

    // Hook Ctrl+F5 or Ctrl+Shift+Enter globally to open runner with active textarea
    document.addEventListener('keydown', e => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'Enter') {
        e.preventDefault();
        _openRunnerFromActiveTextarea();
      }
    });

    export function openDsEditorRunner() {
      const ta     = document.getElementById('ds-ed-sql');
      if (!ta) return;
      const sel    = ta.selectionStart !== ta.selectionEnd
        ? ta.value.slice(ta.selectionStart, ta.selectionEnd).trim()
        : ta.value.trim();
      const label  = document.getElementById('ds-ed-name')?.value || '';
      const sources = currentEditorSources();
      openSqlRunner(sel, label, sources);
    }

    export function _openRunnerFromActiveTextarea() {
      const focused = document.activeElement;
      if (!focused || focused.tagName !== 'TEXTAREA') return;
      const id = focused.id;
      let sql = '', label = '', sources = [];

      if (id === 'ds-ed-sql') {
        // Silver / Gold / Master dataset editor
        const sel = focused.selectionStart !== focused.selectionEnd
          ? focused.value.slice(focused.selectionStart, focused.selectionEnd).trim()
          : focused.value.trim();
        sql     = sel;
        label   = document.getElementById('ds-ed-name')?.value || '';
        sources = currentEditorSources();
      } else if (id === 'bronze-sql') {
        sql   = focused.value.trim();
        label = 'Bronze query';
      } else {
        sql = focused.value.trim();
      }
      if (sql) openSqlRunner(sql, label, sources);
    }
