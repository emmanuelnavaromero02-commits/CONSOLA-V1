import { state } from './legacy-state.js';

    // Apply saved theme
    document.documentElement.dataset.theme = localStorage.getItem('mod-theme') || 'dark';

    // S3/MinIO bucket — viene de /api/config; default cubre dev local.
    fetch('/api/config').then(r => r.json())
      .then(d => { if (d.s3_bucket) state.S3_BUCKET = d.s3_bucket; })
      .catch(() => {});

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

    function csrfHeaders() {
      const csrf = readCookie('csrf_token');
      return csrf ? {'X-CSRF-Token': csrf} : {};
    }




    // ── Cartridge management ───────────────────────────────────────────────────

    export async function loadCartridges() {
      try {
        const r = await fetch('/studio/cartridges');
        const d = await r.json();
        state._cartridges = d.cartridges || [];
      } catch(e) { state._cartridges = []; }

      const sel = document.getElementById('cartridge-sel');
      const cur = state._currentCartridge?.id || '';
      sel.innerHTML = '<option value="">— seleccionar o crear —</option>'
        + state._cartridges.map(c =>
            `<option value="${esc(c.id)}" ${c.id === cur ? 'selected' : ''}>${esc(c.name)} (${esc(c.id)})</option>`
          ).join('');
    }

    export async function selectCartridge(id) {
      if (!id) { state._currentCartridge = null; _updateCartridgeInfo(); _refreshStudioMiniHeader(); return; }
      try {
        const r = await fetch(`/studio/cartridges/${encodeURIComponent(id)}`);
        state._currentCartridge = await r.json();
        _updateCartridgeInfo();
        _refreshStudioMiniHeader();
        const sel = document.getElementById('cartridge-sel');
        if (sel) sel.value = id;
        // Reset per-cartridge state
        state._selectedDag = null;
        state._selectedDS  = null;
        // Refresh current step with new context
        if (state.currentStep > 0) goStep(state.currentStep);
      } catch(e) {
        state._currentCartridge = null;
        _refreshStudioMiniHeader();
      }
    }

    export function _updateCartridgeInfo() {
      const info = document.getElementById('cartridge-info');
      const btn  = document.getElementById('btn-export-cart');
      if (!state._currentCartridge) {
        info.innerHTML = '';
        if (btn) btn.style.display = 'none';
        return;
      }
      const c = state._currentCartridge;
      const ents = (c.entities || []).length;
      const conn = c.connector?.type || '—';
      info.innerHTML = `
        <span class="ci-name">${esc(c.name)}</span>
        <span class="ci-badge">v${esc(c.version || '0.1.0')}</span>
        <span class="ci-badge">${esc(conn)}</span>
        <span class="ci-badge">${esc(ents)} entidades</span>`;
      if (btn) btn.style.display = '';
    }

    export function showCreateCartridge() {
      window.StudioCartridgeModal?.open();
    }

    export async function doCreateCartridge() {
      await window.StudioCartridgeModal?.submit();
    }

    window.__studioCreateCartridge = async function(payload) {
      const r = await fetch('/studio/cartridges', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.detail || JSON.stringify(d) || 'No se pudo crear el cartucho');
      return d;
    };

    window.__studioCartridgeCreated = async function(cartridge, id) {
      state._currentCartridge = cartridge;
      await loadCartridges();
      document.getElementById('cartridge-sel').value = id;
      _updateCartridgeInfo();
      if (state.currentStep === 1) renderCartridges();
    };

    export async function exportCartridge() {
      if (!state._currentCartridge) return;
      const id = state._currentCartridge.id;
      window.open(`/studio/cartridges/${encodeURIComponent(id)}/export`, '_blank');
    }

    export async function patchCartridge(updates) {
      if (!state._currentCartridge) return;
      const id = state._currentCartridge.id;
      const r  = await fetch(`/studio/cartridges/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(updates),
      });
      if (r.ok) {
        state._currentCartridge = await r.json();
        _updateCartridgeInfo();
      }
    }

    // ── File upload (specs) ────────────────────────────────────────────────────

    export function makeUploadZone(containerId, label, sublabel) {
      return `
        <div class="upload-zone" id="${containerId}"
             onclick="document.getElementById('fi-${containerId}').click()"
             ondragover="event.preventDefault();this.classList.add('drag-over')"
             ondragleave="this.classList.remove('drag-over')"
             ondrop="handleSpecDrop(event,'${containerId}')">
          <input type="file" id="fi-${containerId}" accept=".yaml,.yml,.json,.xml,.wsdl"
                 onchange="handleSpecFile(this,'${containerId}')">
          <div class="uz-icon">↑</div>
          <div class="uz-label">${esc(label)}</div>
          <div class="uz-sub">${esc(sublabel)}</div>
          <div id="uz-status-${containerId}" style="margin-top:8px;font-size:10px"></div>
        </div>`;
    }

    export async function handleSpecFile(input, zoneId) {
      const file = input.files[0];
      if (!file) return;
      await doUploadSpec(file, zoneId);
    }

    export async function handleSpecDrop(event, zoneId) {
      event.preventDefault();
      document.getElementById(zoneId).classList.remove('drag-over');
      const file = event.dataTransfer.files[0];
      if (!file) return;
      await doUploadSpec(file, zoneId);
    }

    export async function doUploadSpec(file, zoneId) {
      if (!state._currentCartridge) {
        alert('Primero selecciona o crea un cartucho.');
        return;
      }
      const statusEl = document.getElementById(`uz-status-${zoneId}`);
      statusEl.innerHTML = `<span style="color:var(--cyan)">⟳ Subiendo ${esc(file.name)}...</span>`;
      const form = new FormData();
      form.append('file', file);
      try {
        const r = await fetch(`/api/studio/entities/upload?cartridge=${encodeURIComponent(state._currentCartridge.id)}`, {
          method: 'POST',
          headers: csrfHeaders(),
          body: form,
        });
        const d = await r.json();
        if (!r.ok || d.accepted === false) {
          throw new Error(d.error || d.detail || `HTTP ${r.status}`);
        }
        const count = Number(d.accepted_count || 0);
        statusEl.innerHTML = `<span style="color:var(--green)">✓ ${esc(file.name)} procesado · ${count} entidades</span>`;
        await selectCartridge(state._currentCartridge.id);
        // Auto-notify assistant
        document.getElementById('ai-input').value =
          `Acabo de subir el spec "${file.name}". Léelo con infra__minio_read_spec y dime qué ${
            state.currentStep === 1 ? 'tipo de conexión describe y cómo conectarse' : 'entidades hay disponibles'
          }.`;
        aiSend();
      } catch(e) {
        statusEl.innerHTML = `<span style="color:#ff2d55">Error: ${esc(e.message)}</span>`;
      }
    }

    // ── Navigation ─────────────────────────────────────────────────────────────

    // Maps cartridge.status to the same Spanish label used by the modern summary
    // chip, so the mini-header stays consistent with the big block.
    function _miniStatusLabel(c) {
      if (!c) return '';
      if (c.healthy === false) return 'Revisar';
      switch (c.status) {
        case 'operational': return 'Operativo';
        case 'degraded':    return 'Configuración pendiente';
        case 'offline':     return 'Offline';
        case 'unknown':     return 'Sin diagnóstico';
        default:            return c.connector?.type || '';
      }
    }

    export function _refreshStudioMiniHeader() {
      const mini = document.getElementById('studio-mini-header');
      if (!mini) return;
      const nameEl = mini.querySelector('.studio-mini-name');
      const metaEl = mini.querySelector('.studio-mini-meta');
      const c = state._currentCartridge;
      if (!c) {
        if (nameEl) nameEl.textContent = '—';
        if (metaEl) metaEl.textContent = 'sin cartucho';
        return;
      }
      const name   = c.name || c.id || 'Cartucho';
      const tablas = `${(c.entities || []).length} tablas`;
      const estado = _miniStatusLabel(c);
      if (nameEl) nameEl.textContent = name;
      if (metaEl) metaEl.textContent = [tablas, estado].filter(Boolean).join(' · ');
    }

    export async function goStep(n) {
      state.currentStep = n;
      state.aiHistory = [];
      document.body.classList.toggle('studio-modern-summary-active', n === 1);
      document.body.dataset.studioStep = String(n);
      _refreshStudioMiniHeader();

      // Update step nav UI
      for (let i = 1; i <= 6; i++) {
        const el = document.getElementById(`si-${i}`);
        if (!el) continue;
        el.classList.toggle('active', i === n);
        el.classList.toggle('done',   i < n);
      }

      // Update AI context badge + hint
      document.getElementById('ai-ctx-badge').textContent = `Paso ${n}: ${state.STEP_LABELS[n]}`;
      document.getElementById('ai-chat').innerHTML =
        `<div class="ai-hint">${esc(state.STEP_HINTS[n])}</div>`;
      // Restore saved chat history for this step (if any, within TTL)
      _restoreChatHistory();

      // Render step
      const el = document.getElementById('step-content');
      el.style.padding  = '';   // restore padding (steps 2 & 4 remove it for edge-to-edge layout)
      el.style.overflow = '';
      el.innerHTML = '<div class="loading">Cargando...</div>';
      switch (n) {
        case 1: await renderResumen();  break;
        case 2: renderDagEditor();      break;
        case 3: await renderEntities(); break;
        case 4: await renderRefine();   break;
        case 5: renderAnalytics();      break;
        case 6: await renderSemantic(); break;
        case 7: await renderRAG();      break;
      }
    }

    // ── Step 1: Resumen ───────────────────────────────────────────────────────

    export async function renderResumen() {
      const cartridges = state._cartridges;
      document.getElementById('step-content').innerHTML = `
        <div class="step-title">
          <div>
            <h2>Resumen</h2>
            <p class="step-desc">Cartuchos registrados. Selecciona uno en el combo para ver sus detalles, o crea uno nuevo con el asistente.</p>
          </div>
        </div>
        ${makeUploadZone('uz-conn', 'Importar cartucho desde ZIP', 'Sube un ZIP exportado previamente para registrarlo en esta instalación')}
        <div class="item-grid" id="cart-grid">
          ${cartridges.length
            ? cartridges.map(c => cartCard(c)).join('')
            : '<div class="empty-card" style="grid-column:1/-1">Sin cartuchos. Usa el asistente para crear uno → describe la conexión al sistema origen.</div>'}
        </div>`;
    }


    export function prefillConnConfig(cartridgeId, connId) {
      document.getElementById('ai-input').value =
        `Configura la conexión "${connId}" del cartucho ${cartridgeId}. La URL base es: `;
      document.getElementById('ai-input').focus();
    }

    export function cartCard(c) {
      const pattern  = c.pattern || 'dag-based';
      const isDag    = pattern === 'dag-based';
      const entities = c.entities || 0;
      const version  = c.version  || '—';
      const selected = state._currentCartridge && state._currentCartridge.id === c.id;
      const cls      = selected ? 'ok' : '';
      const patternLabel = isDag ? 'DAG-BASED' : 'FASTAPI-MCP';
      const patternColor = isDag ? 'var(--cyan)' : 'var(--amber)';
      return `
        <div class="item-card ${cls}" onclick="selectCartridge(${escJsArg(c.id)})" style="cursor:pointer">
          <div class="ic-hdr">
            <div class="ic-icon" style="color:${patternColor}">◈</div>
            <div>
              <div class="ic-name">${esc(c.name || c.id)}</div>
              <div class="ic-id">${esc(c.id)} · v${esc(version)}</div>
            </div>
            <div class="ic-status-ok" style="color:${patternColor};font-size:9px">${patternLabel}</div>
          </div>
          <div class="ic-desc">${esc(c.description || '—')}</div>
          <div class="ic-meta">
            <span>${entities} entidades</span>
            ${c.connector ? `<span>conector: ${esc(c.connector)}</span>` : ''}
            <span style="color:var(--text3)">${esc(c.source || '')}</span>
          </div>
          <div style="display:flex;gap:6px;margin-top:10px">
            <button class="btn btn-sm" onclick="event.stopPropagation();selectCartridge(${escJsArg(c.id)});goStep(3)" title="Ver entidades">Entidades →</button>
            <button class="btn btn-sm" onclick="event.stopPropagation();exportCartridge()" title="Exportar ZIP">↓ ZIP</button>
          </div>
        </div>`;
    }

    // ── Step 2: Entidades ──────────────────────────────────────────────────────

    export async function renderEntities() {
      const cartridges = state._cartridges;

      document.getElementById('step-content').innerHTML = `
        <div class="step-title">
          <div>
            <h2>Entidades &amp; Extractores</h2>
            <p class="step-desc">Entidades definidas en el manifiesto del cartucho. Dispara extracciones individuales o usa el asistente para configurar watermarks y modos.</p>
          </div>
          <div style="display:flex;gap:8px;align-items:center">
            <button class="btn" onclick="loadEntityList()">↺</button>
            <button class="btn" style="color:var(--green);border-color:var(--green)" onclick="showAddEntityRow()">+ Entidad</button>
          </div>
        </div>
        ${makeUploadZone('uz-entity', 'Subir spec de entidades', 'OpenAPI · OData $metadata · WSDL — el asistente extrae las entidades automáticamente')}

        <div id="entity-list-area" class="empty-state">
          ${cartridges.length ? `
            <div class="et-table empty-state">
              <div class="et-hdr">
                <span>ENTIDAD</span>
                <span>DISPLAY NAME</span>
                <span>MODO</span>
                <span>DAG</span>
                <span>ACCIÓN</span>
              </div>
              <div class="et-row">
                <span><span class="et-name">preview_entity</span></span>
                <span><input type="text" value="Preview" aria-label="display name"></span>
                <span>
                  <select name="mode">
                    <option value="full">full</option>
                    <option value="incremental">incremental</option>
                  </select>
                </span>
                <span><select><option>preview_dag</option></select></span>
                <span><button class="btn btn-sm" type="button">► Extraer</button></span>
              </div>
            </div>` : '<div class="empty-card empty-state">No hay cartuchos. Ve al Paso 1 para crear uno.</div>'}
        </div>
      `;

      if (cartridges.length) loadEntityList();
    }

    // entity → last pipeline_run record
    // entity → 'extracting' | null  (for polling)

    export async function loadEntityList() {
      const cartridge = state._currentCartridge?.id;
      if (!cartridge) return;

      const area = document.getElementById('entity-list-area');
      const restoreNewEntityRow = state._newEntityRequested || !!document.getElementById('new-entity-row');
      area.innerHTML = '<div class="loading">Cargando entidades...</div>';

      try {
        // Fetch entities + pipeline_runs + DAG list in parallel
        const [semRes, runsRes, dagRes] = await Promise.all([
          fetch(`/api/studio/entities?cartridge=${encodeURIComponent(cartridge)}`),
          fetch(`/api/pipeline_runs?cartridge=${encodeURIComponent(cartridge)}&limit=200`).catch(() => null),
          fetch('/api/mcp/invoke', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ server: 'infra', tool: 'airflow_list_dags', args: {} }),
          }).catch(() => null),
        ]);

        const d        = await semRes.json();
        const raw      = d.entities || {};
        const entities = Array.isArray(raw) ? raw : (raw.entities || []);

        if (!entities.length) {
          area.innerHTML = '<div class="empty-card">Sin entidades registradas en este cartucho.</div>';
          if (restoreNewEntityRow) showAddEntityRow();
          return;
        }

        // Build last-run map (most recent per entity — API returns DESC so first = latest)
        state._runsByEntity = {};
        if (runsRes?.ok) {
          const runsData = await runsRes.json();
          for (const run of (runsData.runs || [])) {
            if (!state._runsByEntity[run.entity]) state._runsByEntity[run.entity] = run;
          }
        }

        // DAG list
        let dagOptions = [];
        if (dagRes?.ok) {
          const dagData = await dagRes.json();
          dagOptions = (dagData.result?.dags || [])
            .map(d => d.dag_id)
            .filter(id => id.startsWith(cartridge + '_'))
            .sort();
        }

        area.innerHTML = `
          <div class="et-table empty-state">
            <div class="et-hdr">
              <span>ENTIDAD</span>
              <span>DISPLAY NAME</span>
              <span>MODO</span>
              <span>DAG</span>
              <span>ACCIÓN</span>
            </div>
            ${entities.map(e => renderEntityRow(e, cartridge, dagOptions)).join('')}
          </div>`;
        if (restoreNewEntityRow) showAddEntityRow();
      } catch(e) {
        area.innerHTML = `<div class="empty-card" style="color:#ff2d55">Error: ${esc(e.message)}</div>`;
        if (restoreNewEntityRow) showAddEntityRow();
      }
    }

    export function renderEntityRow(e, cartridge, dagOptions) {
      const name        = esc(e.entity || e.id || e.name || '?');
      const rawName     = e.entity || e.id || e.name || '';
      const modeRaw     = e.mode || 'full';
      const dagRaw      = e.dag_id || '';
      const displayRaw  = e.display_name || '';
      const triggerType = e.trigger_type || 'manual';
      const cronRaw     = e.cron_expression || '';
      const isScheduled = triggerType === 'scheduled';

      // Run status badge
      const run = state._runsByEntity[rawName];
      const isExtracting = !!state._extractingEntities[rawName];
      let runBadgeHtml = '';
      if (isExtracting) {
        runBadgeHtml = `<div class="run-badge"><span class="run-dot-run">●</span><span style="color:var(--cyan)">extrayendo...</span></div>`;
      } else if (run) {
        const ok    = run.status === 'success';
        const dot   = ok ? 'run-dot-ok' : 'run-dot-fail';
        const sym   = ok ? '✓' : '✗';
        const date  = (run.finished_at || run.started_at || '').substring(0, 10);
        const recs  = run.record_count != null ? ` · ${Number(run.record_count).toLocaleString('es')} rows` : '';
        const errTip = !ok && run.error_message ? ` title="${esc(run.error_message)}"` : '';
        runBadgeHtml = `<div class="run-badge"${errTip}><span class="${dot}">${sym}</span><span style="color:var(--text3)">${esc(date)}${recs}</span></div>`;
      } else {
        runBadgeHtml = `<div class="run-badge"><span class="run-dot-never">○</span><span style="color:var(--text3)">sin ejecución</span></div>`;
      }

      // Display name input
      const displayInput = `
        <input type="text" value="${esc(displayRaw)}" placeholder="Nombre legible"
               style="width:100%;height:20px;font-size:9px;font-family:var(--font-mono);
                      background:var(--bg2);color:var(--text1);border:1px solid var(--border);
                      padding:0 4px;box-sizing:border-box"
               onchange="patchEntityField(${escJsArg(cartridge)},${escJsArg(rawName)},'display_name',this.value,this)">`;

      // Mode select
      const modeSel = `
        <select style="width:100%;height:22px;font-size:10px;padding:0 4px"
                onchange="patchEntityField(${escJsArg(cartridge)},${escJsArg(rawName)},'mode',this.value,this)">
          <option value="full"        ${modeRaw==='full'        ?'selected':''}>full</option>
          <option value="incremental" ${modeRaw==='incremental' ?'selected':''}>incremental</option>
        </select>`;

      // DAG select
      const dagOpts = dagOptions.length
        ? dagOptions.map(d => `<option value="${esc(d)}" ${d===dagRaw?'selected':''}>${esc(d)}</option>`).join('')
        : `<option value="${esc(dagRaw)}">${esc(dagRaw)||'—'}</option>`;
      const dagInput = `
        <select style="width:100%;height:22px;font-size:10px;padding:0 4px;font-family:var(--font-mono);color:var(--green)"
                onchange="patchEntityField(${escJsArg(cartridge)},${escJsArg(rawName)},'dag_id',this.value,this)">
          ${dagOpts}
        </select>`;

      // Schedule button
      const schedBtn = `
        <button class="btn btn-sm ${isScheduled?'btn-active':''}"
                style="${isScheduled?'color:var(--green);border-color:var(--green)':''}"
                title="${isScheduled?'Programado: '+cronRaw:'Manual'}"
                onclick="toggleEntitySchedule(${escJsArg(cartridge)},${escJsArg(rawName)},${escJsArg(triggerType)},${escJsArg(cronRaw)})">
          ${isScheduled ? '⏱ '+esc(cronRaw) : '⏱ manual'}
        </button>`;

      const extractBtnId = `ebtn-${rawName.replace(/\W/g,'_')}`;
      const extractBtn = isExtracting
        ? `<button class="btn btn-sm" id="${extractBtnId}" disabled style="color:var(--cyan)">⟳ ...</button>`
        : `<button class="btn btn-sm" id="${extractBtnId}"
                   onclick="extractNow(${escJsArg(cartridge)},${escJsArg(rawName)},${escJsArg(modeRaw)},${escJsArg(dagRaw)})">► Extraer</button>`;

      return `
        <div class="et-row" id="erow-${rawName.replace(/\W/g,'_')}">
          <span>
            <span class="et-name">${esc(name)}
              <button class="btn-sm" title="Renombrar entidad"
                      style="font-size:8px;padding:0 4px;margin-left:3px;opacity:.5"
                      onclick="renameEntity(${escJsArg(cartridge)},${escJsArg(rawName)})">✎</button>
            </span>
            ${runBadgeHtml}
          </span>
          <span>${displayInput}</span>
          <span>${modeSel}</span>
          <span>${dagInput}</span>
          <span style="display:flex;gap:5px;flex-wrap:wrap;align-items:center">
            ${extractBtn}
            ${schedBtn}
            <button class="btn btn-sm" title="Vista Previa Bronze"
                    onclick="toggleEntityPreview(${escJsArg(cartridge)},${escJsArg(rawName)},this)">◉</button>
            ${dagRaw ? `<button class="btn btn-sm" title="Editar DAG: ${esc(dagRaw)}"
                    style="color:var(--amber);border-color:var(--amber)"
                    onclick="openDagEditor(${escJsArg(dagRaw)})">✏ DAG</button>` : ''}
            ${run ? `<button class="btn btn-sm" title="Ver logs"
                    style="${run.status!=='success'?'color:#ff2d55;border-color:#ff2d55':''}"
                    onclick="toggleEntityLogs(${escJsArg(cartridge)},${escJsArg(rawName)},this)">
                      ⬡ Logs
                    </button>` : ''}
          </span>
        </div>
        <div class="et-preview" id="eprev-${rawName.replace(/\W/g,'_')}" style="display:none"></div>
        <div class="et-preview" id="elogs-${rawName.replace(/\W/g,'_')}" style="display:none"></div>`;
    }

    export async function patchEntityField(cartridge, entity, field, value, el) {
      const prev = el.dataset.prev ?? (el.tagName === 'SELECT' ? el.value : el.defaultValue);
      el.dataset.prev = value;
      el.style.borderColor = 'var(--cyan)';
      try {
        const r = await fetch(
          `/studio/cartridges/${encodeURIComponent(cartridge)}/entities/${encodeURIComponent(entity)}`,
          {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({[field]: value}),
          }
        );
        if (r.ok) {
          el.style.borderColor = 'var(--green)';
          setTimeout(() => { el.style.borderColor = ''; }, 1400);
          if (state._currentCartridge?.entities) {
            const ent = state._currentCartridge.entities.find(e => (e.id || e.entity) === entity);
            if (ent) ent[field] = value;
          }
        } else {
          if (el.tagName === 'SELECT') el.value = prev; else el.value = prev;
          el.style.borderColor = '#ff2d55';
        }
      } catch(e) {
        if (el.tagName === 'SELECT') el.value = prev; else el.value = prev;
        el.style.borderColor = '#ff2d55';
      }
    }

    export async function renameEntity(cartridge, entity) {
      const newName = prompt(`Nuevo nombre para la entidad "${entity}":`, entity);
      if (!newName || newName.trim() === entity) return;
      try {
        const r = await fetch(
          `/studio/cartridges/${encodeURIComponent(cartridge)}/entities/${encodeURIComponent(entity)}/rename`,
          {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ new_name: newName.trim() }),
          }
        );
        const d = await r.json();
        if (!r.ok) { alert(`Error: ${d.detail || JSON.stringify(d)}`); return; }
        loadEntityList();   // refresh — new name appears, old is gone
      } catch(e) { alert(`Error: ${e.message}`); }
    }

    export function updateEntityConn(cartridge, entity, val, el) {
      patchEntityField(cartridge, entity, 'connection_id', val, el);
    }

    export async function toggleEntitySchedule(cartridge, entity, currentType, currentCron) {
      if (currentType === 'scheduled') {
        // Switch to manual — clear schedule in Airflow
        if (!confirm(`¿Desactivar el schedule de ${entity} y dejarlo en manual?`)) return;
        await _setEntitySchedule(cartridge, entity, 'manual', null);
      } else {
        // Ask for cron expression
        const cron = prompt(
          `Cron expression para ${entity}:\n` +
          `Ejemplos:\n  0 6 * * 1   → cada lunes 6am\n  0 2 * * *   → diario 2am\n  0 0 1 * *   → mensual`,
          currentCron || '0 6 * * 1'
        );
        if (!cron) return;
        await _setEntitySchedule(cartridge, entity, 'scheduled', cron.trim());
      }
      loadEntityList(); // refresh
    }

    export async function _setEntitySchedule(cartridge, entity, triggerType, cronExpression) {
      // 1. Persist in entity_config
      await fetch(
        `/studio/cartridges/${encodeURIComponent(cartridge)}/entities/${encodeURIComponent(entity)}`,
        {
          method: 'PATCH',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ trigger_type: triggerType, cron_expression: cronExpression }),
        }
      );
      // v1.43.2 (Frontend R3): airflow_set_variable is dev-only in
      // mcp-infra. Skip the Airflow side-call in production — the
      // entity_config is the source of truth and a separate prod
      // pipeline syncs Variables out-of-band.
      if (!(await _isDevMode())) return;
      // 2. Update Airflow DAG schedule via MCP infra
      const dagId = ((state._currentCartridge?.entities || [])
        .find(e => (e.entity || e.id) === entity) || {}).dag_id || '';
      if (dagId) {
        await fetch('/api/mcp/invoke', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            server: 'infra',
            tool: triggerType === 'scheduled' ? 'airflow_unpause_dag' : 'airflow_pause_dag',
            args: { dag_id: dagId },
          }),
        }).catch(() => {});
        // If scheduled, also set the cron via variable so the DAG can read it on next parse
        if (triggerType === 'scheduled' && cronExpression) {
          await fetch('/api/mcp/invoke', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              server: 'infra',
              tool: 'airflow_set_variable',
              args: { key: `${entity.toLowerCase()}_schedule`, value: cronExpression },
            }),
          }).catch(() => {});
        }
      }
    }

    export function showAddEntityRow() {
      const area = document.getElementById('entity-list-area');
      if (!area) return;
      state._newEntityRequested = true;
      // Don't add duplicate rows
      if (document.getElementById('new-entity-row')) return;

      const cartridge = state._currentCartridge?.id || '';
      const conns = (state._currentCartridge?.connections || []).map(c => c.conn_id || c.id || c);
      const connOpts = conns.length
        ? conns.map(cid => `<option value="${esc(cid)}">${esc(cid)}</option>`).join('')
        : '<option value="services">services</option><option value="analytics">analytics</option>';

      const row = document.createElement('div');
      row.id = 'new-entity-row';
      row.setAttribute('role', 'dialog');
      row.style.cssText = 'display:grid;grid-template-columns:130px 150px 90px 120px 1fr;gap:8px;padding:7px 10px;align-items:center;border-top:1px solid var(--green);margin-top:4px';
      row.innerHTML = `
        <input id="ne-name" type="text" placeholder="NombreEntidad"
               style="height:22px;font-size:10px;font-family:var(--font-mono);background:var(--bg2);color:var(--amber);border:1px solid var(--border);padding:0 4px;width:100%;box-sizing:border-box">
        <input id="ne-display" type="text" placeholder="Nombre legible"
               style="height:20px;font-size:9px;font-family:var(--font-mono);background:var(--bg2);color:var(--text1);border:1px solid var(--border);padding:0 4px;width:100%;box-sizing:border-box">
        <select id="ne-mode" style="width:100%;height:22px;font-size:10px;padding:0 4px">
          <option value="full">full</option>
          <option value="incremental">incremental</option>
        </select>
        <select id="ne-dag" style="width:100%;height:22px;font-size:10px;padding:0 4px;font-family:var(--font-mono);color:var(--green)">
          <option value="">cargando DAGs…</option>
        </select>
        <span style="display:flex;gap:5px">
          <button class="btn btn-sm" style="color:var(--green);border-color:var(--green)"
                  onclick="saveNewEntity(${escJsArg(cartridge)})">✓ Guardar</button>
          <button class="btn btn-sm" style="color:#ff2d55;border-color:#ff2d55"
                  onclick="cancelNewEntity()">✕</button>
        </span>`;

      area.appendChild(row);
      document.getElementById('ne-name').focus();

      // Populate DAG selector from Airflow
      _loadDagSelector('ne-dag', cartridge);
    }

    export function cancelNewEntity() {
      state._newEntityRequested = false;
      document.getElementById('new-entity-row')?.remove();
    }

    export async function _loadDagSelector(selectId, cartridge) {
      const sel = document.getElementById(selectId);
      if (!sel) return;
      try {
        const r = await fetch('/api/mcp/invoke', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ server: 'infra', tool: 'airflow_list_dags', args: {} }),
        });
        if (!r.ok) throw new Error();
        const data = await r.json();
        const dags = (data.result?.dags || [])
          .map(d => d.dag_id)
          .filter(id => id.startsWith(cartridge + '_'))
          .sort();
        sel.innerHTML = dags.length
          ? dags.map(id => `<option value="${esc(id)}">${esc(id)}</option>`).join('')
          : `<option value="">— sin DAGs con prefijo ${esc(cartridge)}_ —</option>`;
      } catch (_) {
        sel.innerHTML = `<option value="">— Airflow no disponible —</option>`;
      }
    }

    export async function saveNewEntity(cartridge) {
      const name = document.getElementById('ne-name')?.value?.trim();
      if (!name) {
        document.getElementById('ne-name').style.borderColor = '#ff2d55';
        return;
      }
      const display = document.getElementById('ne-display')?.value?.trim() || null;
      const mode    = document.getElementById('ne-mode')?.value || 'full';
      const dag     = document.getElementById('ne-dag')?.value?.trim() || null;

      const btn = document.querySelector('#new-entity-row .btn');
      if (btn) btn.textContent = '...';

      try {
        const r = await fetch('/api/studio/entity', {
          method: 'POST',
          headers: jsonHeaders(),
          body: JSON.stringify({
            cartridge,
            entity: name,
            display_name: display || undefined,
            mode,
            dag_id: dag || undefined,
            enabled: true,
          }),
        });
        if (r.ok) {
          state._newEntityRequested = false;
          document.getElementById('new-entity-row')?.remove();
          await selectCartridge(cartridge);
          loadEntityList();   // refresh the full list
        } else {
          const msg = await r.text();
          if (btn) { btn.textContent = '✓ Guardar'; btn.style.borderColor = '#ff2d55'; }
          alert(`Error al guardar: ${msg}`);
        }
      } catch(e) {
        if (btn) { btn.textContent = '✓ Guardar'; btn.style.borderColor = '#ff2d55'; }
        alert(`Error: ${e.message}`);
      }
    }

    export function openDagEditor(dagId) {
      state._selectedDag = dagId;
      goStep(2);
    }

    export async function extractNow(cartridge, entity, mode, dagId) {
      dagId = dagId || `${cartridge}_extract`;  // fallback if none assigned
      const safeId = entity.replace(/\W/g,'_');
      const btn    = document.getElementById(`ebtn-${safeId}`);
      const row    = document.getElementById(`erow-${safeId}`);

      state._extractingEntities[entity] = true;
      if (btn) { btn.textContent = '⟳ ...'; btn.disabled = true; btn.style.color = 'var(--cyan)'; }
      _updateRunBadge(row, entity, 'extracting');

      try {
        const r = await fetch('/api/mcp/invoke', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({
            server: 'infra', tool: 'airflow_trigger_dag',
            args: { dag_id: dagId, conf: { entity, mode: mode || 'incremental' } },
          }),
        });
        const d    = await r.json();
        const runId = (d.result || d).dag_run_id || (d.result || d).run_id;
        if (!runId) throw new Error(JSON.stringify(d));

        // Poll pipeline_runs + Airflow run state in parallel
        _pollExtraction(cartridge, entity, safeId, dagId, runId);
      } catch(e) {
        delete state._extractingEntities[entity];
        if (btn) { btn.textContent = '► Extraer'; btn.disabled = false; btn.style.color = ''; }
        _updateRunBadge(row, entity, 'error', e.message);
      }
    }

    export async function _pollExtraction(cartridge, entity, safeId, dagId, runId) {
      const since = new Date().toISOString();
      const btn   = document.getElementById(`ebtn-${safeId}`);
      const row   = document.getElementById(`erow-${safeId}`);
      let tries   = 0;
      const MAX   = 60;  // ~5 min

      const _resetBtn = () => {
        delete state._extractingEntities[entity];
        if (btn) { btn.textContent = '► Extraer'; btn.disabled = false; btn.style.color = ''; }
      };

      while (tries++ < MAX) {
        await new Promise(res => setTimeout(res, 5000));

        // ── Check Airflow run state (fast path for failures) ──────────────────
        if (dagId && runId) {
          try {
            const ar = await fetch('/api/mcp/invoke', {
              method: 'POST', headers: {'Content-Type':'application/json'},
              body: JSON.stringify({ server: 'infra', tool: 'airflow_get_run_status',
                                     args: { dag_id: dagId, dag_run_id: runId } }),
            });
            if (ar.ok) {
              const ad = await ar.json();
              const state = (ad.result || ad).state;
              if (state === 'failed') {
                _resetBtn();
                _updateRunBadge(row, entity, 'fail', { error_message: 'DAG failed — ver logs ↓' });
                await _showAirflowErrorLogs(safeId, dagId, runId, entity, cartridge);
                return;
              }
              if (state === 'success') {
                // Pipeline_run record may not yet be written — wait one more cycle
              }
            }
          } catch(_) {}
        }

        // ── Check pipeline_runs table ────────────────────────────────────────
        try {
          const r = await fetch(`/api/pipeline_runs?cartridge=${encodeURIComponent(cartridge)}&entity=${encodeURIComponent(entity)}&limit=1`);
          if (!r.ok) continue;
          const d   = await r.json();
          const run = (d.runs || [])[0];
          const isNew = run && run.status !== 'running' && (
            !run.started_at ||
            run.started_at >= since ||
            run.run_id !== (state._runsByEntity[entity]?.run_id)
          );
          if (isNew) {
            state._runsByEntity[entity] = run;
            _resetBtn();
            _updateRunBadge(row, entity, run.status === 'success' ? 'ok' : 'fail', run);
            return;
          }
        } catch(_) {}
      }
      // Timeout — stop polling
      _resetBtn();
      _updateRunBadge(row, entity, 'fail', { error_message: 'timeout — revisar Airflow' });
    }

    export async function _showAirflowErrorLogs(safeId, dagId, runId, entity, cartridge) {
      const panel = document.getElementById(`elogs-${safeId}`);
      if (!panel) return;
      panel.style.display = '';
      panel.innerHTML = `<div class="et-preview-inner" style="color:var(--text3);font-size:11px">Cargando logs de Airflow...</div>`;

      try {
        // List task instances to find the failed one
        const ti = await fetch('/api/mcp/invoke', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ server: 'infra', tool: 'airflow_list_task_instances',
                                 args: { dag_id: dagId, dag_run_id: runId } }),
        });
        const tid = await ti.json();
        const tasks = (tid.result || tid).task_instances || [];
        const failed = tasks.find(t => t.state === 'failed') || tasks[0];
        const taskId = failed?.task_id || 'ingest';

        // Get logs for the failed task
        const lr = await fetch('/api/mcp/invoke', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ server: 'infra', tool: 'airflow_get_task_logs',
                                 args: { dag_id: dagId, dag_run_id: runId, task_id: taskId } }),
        });
        const ld = await lr.json();
        const logs = (ld.result || ld).content || ld.logs || JSON.stringify(ld);

        const airflowUrl = `http://localhost:8082/dags/${encodeURIComponent(dagId)}/grid`;
        const prompt = [
          `Analiza el fallo del DAG **${dagId}** para la entidad **${entity}** del cartucho **${cartridge}**.`,
          ``,
          `**Tarea fallida:** ${taskId}`,
          `**Run ID:** ${runId}`,
          logs ? `**Logs Airflow:**\n\`\`\`\n${logs.slice(-3000)}\n\`\`\`` : '',
          ``,
          `Identifica la causa raíz y propón la corrección.`,
        ].filter(Boolean).join('\n');

        panel.innerHTML = `<div class="et-preview-inner">
          <div class="preview-stats">
            <div class="preview-stat">Estado: <span style="color:#ff2d55">failed</span></div>
            <div class="preview-stat">DAG: <span>${esc(dagId)}</span></div>
            <div class="preview-stat">Tarea: <span>${esc(taskId)}</span></div>
            <div class="preview-stat">Run ID: <span style="font-size:9px">${esc(runId)}</span></div>
          </div>
          <div style="margin-bottom:10px">
            <div style="font-size:9px;color:var(--text3);font-family:var(--font-ui);font-weight:600;margin-bottom:4px">
              AIRFLOW LOGS <span style="font-weight:normal">(últimas líneas)</span>
            </div>
            <pre style="background:var(--bg);border:1px solid var(--border);border-radius:3px;padding:8px;
                        font-family:var(--font-mono);font-size:9px;color:var(--text2);
                        max-height:220px;overflow-y:auto;white-space:pre-wrap;word-break:break-word;margin:0">${esc(logs.slice(-3000))}</pre>
          </div>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn btn-sm" style="color:var(--cyan);border-color:var(--cyan)"
                    onclick="sendLogsToAssistant(${JSON.stringify(prompt).replace(/</g,'\\u003c').replace(/"/g,'&quot;')})">
              ✎ Analizar con Asistente
            </button>
            <a href="${esc(airflowUrl)}" target="_blank" class="btn btn-sm"
               style="color:var(--amber);border-color:var(--amber);text-decoration:none">
              ◈ Ver en Airflow
            </a>
          </div>
        </div>`;
      } catch(e) {
        panel.innerHTML = `<div class="et-preview-inner">
          <div class="preview-err">No se pudieron cargar los logs: ${esc(e.message)}</div>
          <a href="http://localhost:8082/dags/${encodeURIComponent(dagId)}/grid" target="_blank"
             class="btn btn-sm" style="color:var(--amber);border-color:var(--amber);text-decoration:none;margin-top:8px">
            ◈ Ver en Airflow
          </a>
        </div>`;
      }
    }

    export function _updateRunBadge(row, entity, state, data) {
      if (!row) return;
      const badgeEl = row.querySelector('.run-badge');
      if (!badgeEl) return;
      if (state === 'extracting') {
        badgeEl.outerHTML = `<div class="run-badge"><span class="run-dot-run">●</span><span style="color:var(--cyan)">extrayendo...</span></div>`;
      } else if (state === 'ok' && data) {
        const date  = (data.finished_at || data.started_at || '').substring(0,10);
        const recs  = data.record_count != null ? ` · ${Number(data.record_count).toLocaleString('es')} rows` : '';
        badgeEl.outerHTML = `<div class="run-badge"><span class="run-dot-ok">✓</span><span style="color:var(--text3)">${esc(date)}${recs}</span></div>`;
      } else if (state === 'fail' && data) {
        const err = typeof data === 'string' ? data : (data.error_message || 'failed');
        const safeId = entity.replace(/\W/g,'_');
        badgeEl.outerHTML = `<div class="run-badge" title="${esc(err)}" style="cursor:pointer"
          onclick="document.getElementById('elogs-${safeId}').style.display === 'none' ? document.getElementById('elogs-${safeId}').style.display='' : document.getElementById('elogs-${safeId}').style.display='none'">
          <span class="run-dot-fail">✗</span><span style="color:#ff2d55">error ↕</span></div>`;
      } else if (state === 'error') {
        const msg = typeof data === 'string' ? data : 'error al disparar';
        badgeEl.outerHTML = `<div class="run-badge" title="${esc(msg)}"><span class="run-dot-fail">✗</span><span style="color:#ff2d55">no iniciado</span></div>`;
      }
    }

    // ── Bronze preview ─────────────────────────────────────────────────────────

    export async function toggleEntityPreview(cartridge, entity, btn) {
      const safeId  = entity.replace(/\W/g,'_');
      const panel   = document.getElementById(`eprev-${safeId}`);
      if (!panel) return;

      if (panel.style.display !== 'none') {
        panel.style.display = 'none';
        btn.style.color = '';
        return;
      }

      btn.style.color = 'var(--cyan)';
      panel.style.display = '';
      panel.innerHTML = `<div class="et-preview-inner" style="color:var(--text3);font-size:11px">Cargando preview...</div>`;

      const source = `raw/${cartridge}/${entity}`;
      try {
        const r = await fetch(`/api/schema?source=${encodeURIComponent(source)}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const d = await r.json();

        // Last run stats
        const run      = state._runsByEntity[entity];
        const statsHtml = run ? `
          <div class="preview-stats">
            <div class="preview-stat">Estado: <span>${run.status === 'success' ? '✓ success' : '✗ '+run.status}</span></div>
            <div class="preview-stat">Modo: <span>${run.mode || '—'}</span></div>
            <div class="preview-stat">Filas: <span>${run.record_count != null ? Number(run.record_count).toLocaleString('es') : '—'}</span></div>
            <div class="preview-stat">Duración: <span>${run.duration_seconds ? run.duration_seconds.toFixed(1)+'s' : '—'}</span></div>
            <div class="preview-stat">Fecha: <span>${(run.finished_at||'').substring(0,16).replace('T',' ')}</span></div>
            ${run.storage_uri ? `<div class="preview-stat">Path: <span style="font-size:9px">${esc(run.storage_uri.replace(/^s3:\/\/[^/]+\//,''))}</span></div>` : ''}
          </div>` : '';

        // Schema
        const prev    = d.preview || {};
        const schema  = prev.schema || [];
        const rows    = prev.data  || [];
        const errMsg  = prev.error;

        if (errMsg) {
          panel.innerHTML = `<div class="et-preview-inner">
            ${statsHtml}
            <div class="preview-err">Sin datos Bronze disponibles: ${esc(errMsg)}</div>
          </div>`;
          return;
        }

        const colsHtml = schema.map(c =>
          `<span class="schema-col">${esc(c.name)} <em>${esc(c.type)}</em></span>`
        ).join('');

        let tableHtml = '';
        if (rows.length) {
          const cols = schema.map(c => c.name);
          tableHtml = `
            <div style="overflow-x:auto;margin-top:10px">
              <table class="preview-data-tbl">
                <thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead>
                <tbody>${rows.map(row =>
                  `<tr>${cols.map(c => `<td title="${esc(String(row[c]??''))}">${esc(String(row[c]??''))}</td>`).join('')}</tr>`
                ).join('')}</tbody>
              </table>
            </div>`;
        }

        panel.innerHTML = `<div class="et-preview-inner">
          ${statsHtml}
          <div class="preview-schema">
            <div style="font-size:9px;color:var(--text3);margin-bottom:5px;font-family:var(--font-ui);font-weight:600;letter-spacing:.05em">
              ${schema.length} COLUMNAS
            </div>
            <div class="schema-cols">${colsHtml}</div>
          </div>
          ${tableHtml || '<div style="color:var(--text3);font-size:10px;font-style:italic">Sin filas disponibles en Bronze</div>'}
        </div>`;
      } catch(e) {
        panel.innerHTML = `<div class="et-preview-inner"><div class="preview-err">Error: ${esc(e.message)}</div></div>`;
      }
    }

    // ── Entity logs panel ─────────────────────────────────────────────────────

    export async function toggleEntityLogs(cartridge, entity, btn) {
      const safeId = entity.replace(/\W/g,'_');
      const panel  = document.getElementById(`elogs-${safeId}`);
      if (!panel) return;

      if (panel.style.display !== 'none') {
        panel.style.display = 'none';
        btn.style.color = '';
        btn.style.borderColor = '';
        return;
      }

      btn.style.color = 'var(--cyan)';
      panel.style.display = '';
      panel.innerHTML = `<div class="et-preview-inner" style="color:var(--text3);font-size:11px">Cargando logs...</div>`;

      // Show error from pipeline_runs immediately (no extra fetch needed)
      const run = state._runsByEntity[entity];
      const errMsg = run?.error_message || '';

      // Then fetch full Airflow logs
      try {
        const r = await fetch('/studio_ops/mcp/invoke', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ tool: 'get_entity_logs', args: { cartridge_id: cartridge, entity } }),
        });
        const d = await r.json();

        const status    = d.status || run?.status || '?';
        const startedAt = d.started_at || '';
        const dagId     = d.dag_id || '';
        const runId     = d.dag_run_id || '';
        const afLogs    = d.airflow_logs || d.error || '(sin logs)';
        const errText   = d.error_message || errMsg || '';
        const isOk      = status === 'success';

        // Build prompt for assistant
        const prompt = [
          `Analiza el fallo del DAG para la entidad **${entity}** del cartucho **${cartridge}**.`,
          ``,
          `**DAG:** ${dagId}`,
          `**Estado:** ${status}`,
          `**Fecha:** ${startedAt}`,
          errText ? `**Error:**\n\`\`\`\n${errText}\n\`\`\`` : '',
          afLogs && afLogs !== '(sin logs)' ? `**Logs Airflow:**\n\`\`\`\n${afLogs.slice(-3000)}\n\`\`\`` : '',
          ``,
          `Identifica la causa raíz y propón la corrección.`,
        ].filter(Boolean).join('\n');

        const airflowUrl = `http://localhost:8082/dags/${encodeURIComponent(dagId)}/grid`;

        panel.innerHTML = `<div class="et-preview-inner">
          <div class="preview-stats">
            <div class="preview-stat">Estado: <span style="color:${isOk?'var(--green)':'#ff2d55'}">${esc(status)}</span></div>
            <div class="preview-stat">DAG: <span>${esc(dagId)}</span></div>
            <div class="preview-stat">Fecha: <span>${esc(startedAt)}</span></div>
            ${runId ? `<div class="preview-stat">Run ID: <span style="font-size:9px">${esc(runId)}</span></div>` : ''}
          </div>
          ${errText ? `<div style="background:rgba(255,45,85,.08);border:1px solid #ff2d55;border-radius:3px;padding:8px 10px;margin-bottom:10px">
            <div style="font-size:9px;color:#ff2d55;font-family:var(--font-ui);font-weight:600;margin-bottom:4px">ERROR</div>
            <pre style="margin:0;font-family:var(--font-mono);font-size:10px;color:#ff9999;white-space:pre-wrap;word-break:break-word">${esc(errText)}</pre>
          </div>` : ''}
          ${afLogs && afLogs !== '(sin logs)' ? `<div style="margin-bottom:10px">
            <div style="font-size:9px;color:var(--text3);font-family:var(--font-ui);font-weight:600;margin-bottom:4px">
              AIRFLOW LOGS <span style="font-weight:normal;color:var(--text3)">(últimas líneas)</span>
            </div>
            <pre style="background:var(--bg);border:1px solid var(--border);border-radius:3px;padding:8px;
                        font-family:var(--font-mono);font-size:9px;color:var(--text2);
                        max-height:200px;overflow-y:auto;white-space:pre-wrap;word-break:break-word;margin:0">${esc(afLogs.slice(-2000))}</pre>
          </div>` : ''}
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn btn-sm" style="color:var(--cyan);border-color:var(--cyan)"
                    onclick="sendLogsToAssistant(${JSON.stringify(prompt).replace(/</g,'\\u003c').replace(/"/g,'&quot;')})">
              ✎ Analizar con Asistente
            </button>
            <a href="${esc(airflowUrl)}" target="_blank" class="btn btn-sm"
               style="color:var(--amber);border-color:var(--amber);text-decoration:none">
              ◈ Ver en Airflow
            </a>
          </div>
        </div>`;
      } catch(e) {
        panel.innerHTML = `<div class="et-preview-inner">
          ${errMsg ? `<pre style="color:#ff9999;font-size:10px;font-family:var(--font-mono);white-space:pre-wrap">${esc(errMsg)}</pre>` : ''}
          <div class="preview-err">Error al cargar logs: ${esc(e.message)}</div>
        </div>`;
      }
    }

    export function sendLogsToAssistant(prompt) {
      const input = document.getElementById('ai-input');
      if (!input) return;
      input.value = prompt;
      input.style.borderColor = 'var(--cyan)';
      input.focus();
      input.scrollIntoView({ behavior: 'smooth', block: 'center' });
      setTimeout(() => { input.style.borderColor = ''; }, 2000);
    }

    // ── Step 3: Refinar ────────────────────────────────────────────────────────

    export async function renderRefine() {
      {
        const scFast = document.getElementById('step-content');
        scFast.style.padding  = '0';
        scFast.style.overflow = 'hidden';
        scFast.innerHTML = `
          <div style="display:flex;flex-direction:column;height:100%">
            <div class="step-title" style="flex-shrink:0;padding:14px 20px 10px;margin:0">
              <div>
                <h2 style="margin:0 0 2px">Refinamiento de Datos</h2>
                <p class="step-desc" style="margin:0">Capas de refinamiento listas para revisar.</p>
              </div>
            </div>
            <div class="tab-bar" style="flex-shrink:0;margin:0;display:flex;align-items:center">
              <div class="tab active" onclick="filterDS('bronze')">Bronze</div>
              <div class="tab" onclick="filterDS('silver')">Silver</div>
              <div class="tab" onclick="filterDS('master')">Master</div>
              <div class="tab" onclick="filterDS('gold')">Gold</div>
            </div>
            <div id="ds-list-area" class="${state._activeLayer}-content empty-state"
                 style="flex:1;min-height:0;overflow:auto;background:var(--bg2);border-top:1px solid var(--border);padding:14px">
              <div style="color:var(--text3);padding:16px">Cargando datasets reales...</div>
            </div>
          </div>`;
      }
      try {
        const [dsRes, srcRes] = await Promise.all([
          fetch('/datasets'),
          fetch('/api/sources'),
        ]);
        state._allDatasets = (await dsRes.json()).datasets || [];
        const sd = await srcRes.json();
        state._bronzeSources = (sd.sources || []).map(s => {
          const parts = s.split('/');
          return { source: s, cartridge: parts[1] || '', entity: parts[2] || '' };
        });
      } catch(e) { state._allDatasets = []; }

      const _cart = state._currentCartridge?.id || '';
      const _visDS = _cart ? state._allDatasets.filter(d => d.cartridge === _cart) : state._allDatasets;
      const counts = {
        silver: _visDS.filter(d => d.layer === 'silver').length,
        master: _visDS.filter(d => d.layer === 'master').length,
        gold:   _visDS.filter(d => d.layer === 'gold').length,
      };

      // Edge-to-edge layout: same technique as the DAG editor
      const sc = document.getElementById('step-content');
      sc.style.padding  = '0';
      sc.style.overflow = 'hidden';

      sc.innerHTML = `
        <div style="display:flex;flex-direction:column;height:100%">

          <!-- Header (shrinks to content) -->
          <div class="step-title" style="flex-shrink:0;padding:14px 20px 10px;margin:0">
            <div>
              <h2 style="margin:0 0 2px">Refinamiento de Datos</h2>
              <p class="step-desc" style="margin:0">Capas de refinamiento con snapshots, maestros y agregaciones listas para revisar.</p>
            </div>
          </div>

          <!-- Tab bar (shrinks to content) -->
          <div class="tab-bar" style="flex-shrink:0;margin:0;display:flex;align-items:center">
            <div class="tab ${state._activeLayer==='bronze'?'active':''}" onclick="filterDS('bronze')">
              BRONZE <span style="opacity:.6" id="bronze-count"></span>
            </div>
            <div class="tab ${state._activeLayer==='silver'?'active':''}" onclick="filterDS('silver')">
              SILVER <span style="opacity:.6">(${counts.silver})</span>
            </div>
            <div class="tab ${state._activeLayer==='master'?'active':''}" onclick="filterDS('master')">
              MASTER <span style="opacity:.6">(${counts.master})</span>
            </div>
            <div class="tab ${state._activeLayer==='gold'?'active':''}" onclick="filterDS('gold')">
              GOLD <span style="opacity:.6">(${counts.gold})</span>
            </div>
            <button class="btn btn-amber btn-sm" id="btn-new-ds" onclick="toggleNewDS()"
                    style="margin-left:auto;margin-right:8px;${state._activeLayer==='bronze'?'display:none':''}">+ Nuevo</button>
          </div>

          <!-- Workspace (fills remaining height) -->
          <div id="ds-list-area"
               class="${state._activeLayer}-content empty-state"
               style="flex:1;min-height:0;overflow:hidden;
                      background:var(--bg2);border-top:1px solid var(--border)">
            <div style="color:var(--text3);padding:16px">Cargando...</div>
          </div>

        </div>
      `;
      if (state._activeLayer === 'bronze') loadBronzeTab();
      else renderDSWorkspace(state._activeLayer);
    }


    export function filterDS(layer) {
      state._activeLayer = layer;
      document.querySelectorAll('#step-content .tab').forEach((t, i) => {
        t.classList.toggle('active', ['bronze','silver','master','gold'][i] === layer);
      });
      const btnNew = document.getElementById('btn-new-ds');
      if (btnNew) btnNew.style.display = layer === 'bronze' ? 'none' : '';
      const area = document.getElementById('ds-list-area');
      if (area) area.className = `${layer}-content empty-state`;
      if (layer === 'bronze') {
        document.getElementById('ds-list-area').innerHTML = '<div style="color:var(--text3);padding:16px">Cargando fuentes Bronze...</div>';
        loadBronzeTab();
      } else {
        renderDSWorkspace(layer);
      }
    }

    export function toggleNewDS() {
      selectDS(null);
      // Pre-select the active layer in the form dropdown
      const sel = document.getElementById('ds-layer');
      if (sel && state._activeLayer && state._activeLayer !== 'bronze') sel.value = state._activeLayer;
    }

    // ── Silver/Gold two-panel workspace ────────────────────────────────────────

    export function renderDSWorkspace(layer) {
      const area = document.getElementById('ds-list-area');
      if (!area) return;
      const _cart = state._currentCartridge?.id || '';
      const ds = state._allDatasets.filter(d => d.layer === layer && (!_cart || d.cartridge === _cart));
      const listHtml = ds.length ? ds.map(d => `
        <div class="ds-row ds-list-item ${state._selectedDS?.name===d.name?'ds-row-active':''}"
             style="cursor:pointer;padding:8px 10px"
             onclick="selectDS(${escJsArg(d.name)})">
          <div style="min-width:0">
            <div class="ds-row-name" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(d.name)}</div>
            <div class="ds-row-meta" style="font-size:9px">
              ${d.row_count != null ? Number(d.row_count).toLocaleString('es')+' rows' : 'sin datos'}
              ${d.last_refresh ? ' · '+fmt(d.last_refresh) : ''}
            </div>
          </div>
        </div>`).join('')
        : `<div class="empty-state" style="color:var(--text3);font-size:11px;padding:12px 10px;font-style:italic">Sin datasets</div>`;

      area.innerHTML = `
        <div style="display:flex;height:100%">
          <!-- LEFT: dataset list -->
          <div style="width:200px;flex-shrink:0;border-right:1px solid var(--border);display:flex;flex-direction:column">
        <div style="padding:10px 10px 6px;font-size:9px;letter-spacing:1px;color:var(--text3)">DATASETS ${esc(String(layer).toUpperCase())}</div>
            <div style="flex:1;overflow-y:auto">${listHtml}</div>
            <div style="padding:8px 10px;border-top:1px solid var(--border)">
              <button class="btn btn-sm" style="width:100%" onclick="selectDS(null)">+ Nuevo</button>
            </div>
          </div>
          <!-- RIGHT: editor -->
          <div id="ds-editor-panel" style="flex:1;display:flex;flex-direction:column;overflow:hidden;padding:14px 16px;gap:10px;min-height:0">
            <div style="color:var(--text3);font-size:11px;font-style:italic;margin-top:60px;text-align:center">
              Selecciona un dataset o crea uno nuevo →
            </div>
          </div>
        </div>`;

      // Auto-select first or restore selection
      if (state._selectedDS && ds.find(d => d.name === state._selectedDS.name)) {
        selectDS(state._selectedDS.name);
      } else if (ds.length) {
        selectDS(ds[0].name);
      }
    }

    export async function selectDS(name) {
      state._selectedDS = name ? (state._allDatasets.find(d => d.name === name) || { name }) : null;
      state._dsEditorDirty = false;

      // Highlight in list
      document.querySelectorAll('.ds-list-item').forEach(el => {
        el.classList.toggle('ds-row-active', el.querySelector('.ds-row-name')?.textContent === name);
      });

      const panel = document.getElementById('ds-editor-panel');
      if (!panel) return;

      const cartridge = state._currentCartridge?.id || '';
      const entities  = (state._currentCartridge?.entities || []).map(e => e.entity || e.id || '');

      if (!name) {
        // ── New dataset form ──────────────────────────────────────────────────
        panel.innerHTML = _dsEditorHtml({
          name: '', layer: state._activeLayer, cartridge, entity: '', description: '', sql: '', isNew: true
        });
        return;
      }

      panel.innerHTML = `<div style="color:var(--text3);padding:20px">Cargando...</div>`;

      let sqlDef = '', description = '', sources = [], layer = state._activeLayer;
      try {
        const r = await fetch(`/api/datasets/${encodeURIComponent(name)}/detail`);
        const d = await r.json();
        const detail = d.result || d;
        sqlDef      = detail.sql_def || '';
        description = detail.description || '';
        sources     = detail.sources || [];
        layer       = detail.layer || state._activeLayer;
      } catch(e) {}

      const meta = state._allDatasets.find(d => d.name === name) || {};
      const entity = (sources[0] || '').split('/')[2] || meta.source_entity || '';

      panel.innerHTML = _dsEditorHtml({
        name, layer, cartridge, entity, description, sql: sqlDef, isNew: false,
        rowCount: meta.row_count, lastRefresh: meta.last_refresh,
      });
    }

    export function _dsEditorHtml({ name, layer, cartridge, entity, description, sql, isNew,
                              rowCount, lastRefresh }) {
      const bronzeSources = state._bronzeSources.filter(s => !cartridge || s.cartridge === cartridge);
      const entityOpts = bronzeSources.map(s =>
        `<option value="${esc(s.entity)}" ${s.entity===entity?'selected':''}>${esc(s.entity)}</option>`
      ).join('');

      const statsHtml = !isNew && rowCount != null ? `
        <div style="display:flex;gap:16px;font-size:10px;color:var(--text3);margin-bottom:4px">
          <span>Filas: <span style="color:var(--cyan)">${Number(rowCount).toLocaleString('es')}</span></span>
          ${lastRefresh ? `<span>Actualizado: <span style="color:var(--cyan)">${esc(fmt(lastRefresh))}</span></span>` : ''}
        </div>` : '';

      return `
        <div style="display:flex;flex-direction:column;height:100%">
          <!-- TOP: editor panel (resizable) -->
          <div id="ds-editor-top" style="display:flex;flex-direction:column;gap:10px;
                                         height:320px;min-height:100px;flex-shrink:0;overflow:hidden">
          <!-- Header -->
          <div style="display:flex;gap:8px;align-items:flex-start">
            <div style="flex:1">
              <div style="font-size:9px;letter-spacing:1px;color:var(--text3);margin-bottom:4px">
                ${isNew ? 'NUEVO DATASET' : 'DATASET'}
              </div>
              <input id="ds-ed-name" type="text" value="${esc(name)}"
                placeholder="ej: silver_proyectos"
                style="font-size:13px;font-family:var(--font-mono);background:transparent;
                       border:none;border-bottom:1px solid var(--border);color:var(--green);
                       width:100%;outline:none;padding-bottom:4px"
                oninput="state._dsEditorDirty=true">
            </div>
            <span style="font-size:9px;letter-spacing:1px;color:var(--amber);border:1px solid var(--amber);
                         padding:2px 8px;border-radius:2px;font-family:var(--font-mono)">
              ${esc(String(layer).toUpperCase())}
            </span>
            <input type="hidden" id="ds-ed-layer" value="${esc(layer)}">
          </div>

          <!-- Meta row -->
          <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">
            <div style="display:flex;gap:6px;align-items:center;font-size:10px;color:var(--text3)">
              Entidad:
              <select id="ds-ed-entity" onchange="state._dsEditorDirty=true;_onEntityChange()"
                style="background:var(--bg2);border:1px solid var(--border);color:var(--cyan);
                       font-size:10px;padding:3px 6px;border-radius:2px;max-width:160px">
                <option value="">— selecciona —</option>
                ${entityOpts}
                <option value="__custom__">Personalizada...</option>
              </select>
            </div>
            <input id="ds-ed-entity-custom" type="text" value="${entity && !bronzeSources.find(s=>s.entity===entity) ? esc(entity) : ''}"
              placeholder="entidad personalizada"
              style="display:${entity && !bronzeSources.find(s=>s.entity===entity)?'block':'none'};
                     font-size:10px;background:var(--bg2);border:1px solid var(--border);
                     color:var(--cyan);padding:3px 6px;border-radius:2px;outline:none;width:120px"
              oninput="state._dsEditorDirty=true">
            <input id="ds-ed-desc" type="text" value="${esc(description)}"
              placeholder="descripción (opcional)"
              style="flex:1;font-size:10px;background:transparent;border:none;border-bottom:1px solid var(--border);
                     color:var(--text2);outline:none;padding-bottom:2px;min-width:0"
              oninput="state._dsEditorDirty=true">
          </div>

          ${statsHtml}

          <!-- Template buttons -->
          <div id="ds-tpl-bar" style="display:flex;gap:6px;flex-wrap:wrap">
            ${_templateButtons(layer)}
          </div>

          <!-- SQL editor -->
          <div style="font-size:9px;letter-spacing:1px;color:var(--text3)">SQL DUCKDB</div>
          <textarea id="ds-ed-sql" spellcheck="false"
            style="width:100%;box-sizing:border-box;flex:1;min-height:120px;
                   background:var(--bg2);border:1px solid var(--border);
                   color:var(--cyan);font-family:var(--font-mono);font-size:11px;
                   padding:10px;resize:vertical;border-radius:2px;outline:none"
            onkeydown="if(event.ctrlKey&&event.shiftKey&&event.key==='Enter'){event.preventDefault();_openRunnerFromActiveTextarea();}else if(event.ctrlKey&&event.key==='Enter'){previewDS();}"
            oninput="state._dsEditorDirty=true"
            placeholder="SELECT ... FROM read_parquet('s3://lakehouse/raw/...') ..."
          >${esc(sql)}</textarea>

          <!-- Action bar -->
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <button class="btn btn-amber" onclick="previewDS()">▶ Vista Previa</button>
            <button class="btn btn-amber" onclick="openDsEditorRunner()">⬡ Ejecutar SQL</button>
            <span style="font-size:10px;color:var(--text3)">Ctrl+Enter · Ctrl+⇧+Enter (modal)</span>
            <button class="btn" onclick="saveDS()">✓ Guardar</button>
            <button class="btn btn-amber" onclick="saveThenMaterialize()">✓⟳ Guardar y Materializar</button>
            ${!isNew ? `<button class="btn btn-sm" onclick="materializeDS(${escJsArg(name)})">⟳ Re-materializar</button>` : ''}
            ${!isNew ? `<button class="btn btn-sm" style="color:#ff2d55;border-color:#ff2d55" onclick="deleteDS(${escJsArg(name)})">✕ Eliminar</button>` : ''}
            <button class="btn btn-sm" style="margin-left:auto" onclick="sendDSToAI()">◈ Pedir ayuda a IA</button>
            <span id="ds-ed-status" style="font-size:10px;color:var(--text3)"></span>
          </div>

          </div><!-- /ds-editor-top -->

          <!-- Resize handle -->
          <div class="v-resize-handle" onmousedown="vResizeStart(event,'ds-editor-top')"
               title="Arrastra para redimensionar"></div>

          <!-- BOTTOM: Vista Previa -->
          <div style="flex:1;overflow:auto;min-height:60px;display:flex;flex-direction:column">
            <div style="font-size:9px;letter-spacing:1px;color:var(--text3);padding:6px 0 4px">VISTA PREVIA</div>
            <div id="ds-preview-area" style="flex:1;overflow:auto"></div>
          </div>
        </div>`;
    }

    export function _templateButtons(layer) {
      if (layer === 'gold') {
        return `<span style="font-size:9px;color:var(--text3)">Plantillas:</span>
          <button class="btn btn-sm" onclick="applyTemplate('gold_agg')">∑ Agregación</button>
          <button class="btn btn-sm" onclick="applyTemplate('gold_kpi')">◎ KPI único</button>`;
      }
      return `<span style="font-size:9px;color:var(--text3)">Plantillas:</span>
        <button class="btn btn-sm" onclick="applyTemplate('full_latest')">↓ Full — última partición</button>
        <button class="btn btn-sm" onclick="applyTemplate('full_all')">↓↓ Full — histórico</button>
        <button class="btn btn-sm" onclick="applyTemplate('incremental')">⊕ Incremental — consolidado</button>`;
    }

    export function _refreshTemplateButtons() {
      const layer = document.getElementById('ds-ed-layer')?.value || 'silver';
      const bar   = document.getElementById('ds-tpl-bar');
      if (bar) bar.innerHTML = _templateButtons(layer);
    }

    export function _onEntityChange() {
      const sel    = document.getElementById('ds-ed-entity');
      const custom = document.getElementById('ds-ed-entity-custom');
      if (!sel || !custom) return;
      custom.style.display = sel.value === '__custom__' ? 'block' : 'none';
    }

    export function _currentEditorEntity() {
      const sel = document.getElementById('ds-ed-entity');
      if (!sel) return '';
      if (sel.value === '__custom__')
        return document.getElementById('ds-ed-entity-custom')?.value.trim() || '';
      return sel.value;
    }

    export function applyTemplate(type) {
      const entity    = _currentEditorEntity();
      const cartridge = state._currentCartridge?.id || 'replicon';
      const base      = `s3://{bucket}/raw/${cartridge}/${entity || '{ENTITY}'}`;
      let sql = '';
      if (type === 'full_latest') {
        sql = `-- Última partición disponible (full extraction)
SELECT *
FROM read_parquet('${base}/load_date=*/data.parquet',
  hive_partitioning=true, union_by_name=true)
WHERE load_date = (
  SELECT MAX(load_date)
  FROM read_parquet('${base}/load_date=*/data.parquet', hive_partitioning=true)
)`;
      } else if (type === 'full_all') {
        sql = `-- Todas las particiones históricas (full extraction)
SELECT *, load_date
FROM read_parquet('${base}/load_date=*/data.parquet',
  hive_partitioning=true, union_by_name=true)
ORDER BY load_date DESC`;
      } else if (type === 'incremental') {
        sql = `-- Consolidado incremental: deduplicado por clave, más reciente primero
WITH ranked AS (
  SELECT *,
         load_date,
         ROW_NUMBER() OVER (PARTITION BY Id ORDER BY load_date DESC) AS rn
  FROM read_parquet('${base}/load_date=*/data.parquet',
    hive_partitioning=true, union_by_name=true)
)
SELECT * EXCLUDE (rn, load_date)
FROM ranked
WHERE rn = 1`;
      } else if (type === 'gold_agg') {
        sql = `-- Agregación Gold: edita los GROUP BY y métricas según tu caso
SELECT
  -- dimension1,
  -- dimension2,
  COUNT(*) AS total,
  SUM(Amount) AS total_amount
FROM silver_${entity || 'entity'}
-- GROUP BY dimension1, dimension2
ORDER BY total DESC`;
      } else if (type === 'gold_kpi') {
        sql = `-- KPI único (un solo valor escalar)
SELECT
  COUNT(DISTINCT Id) AS total_registros,
  SUM(Amount)        AS monto_total,
  AVG(Amount)        AS monto_promedio
FROM silver_${entity || 'entity'}`;
      }
      const ta = document.getElementById('ds-ed-sql');
      if (ta && sql) { ta.value = sql; state._dsEditorDirty = true; }
    }

    export async function previewDS() {
      const sql    = document.getElementById('ds-ed-sql')?.value.trim();
      const status = document.getElementById('ds-ed-status');
      const area   = document.getElementById('ds-preview-area');
      if (!sql || !area) return;
      if (status) status.textContent = '⟳ ejecutando...';
      area.innerHTML = '';
      const t0 = Date.now();
      // Resolve sources from the entity selector so {latest_date} can be injected
      const entity  = _currentEditorEntity();
      const cart    = document.getElementById('ds-ed-cart')?.value.trim();
      const sources = entity && cart ? [`raw/${cart}/${entity}`] : [];
      try {
        const r = await fetch('/api/bronze/query', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ sql, limit: 50, sources }),
        });
        const d = await r.json();
        const elapsed = ((Date.now()-t0)/1000).toFixed(2);
        const result  = d.result || d;
        if (result.error) {
          if (status) status.textContent = '✗ error';
          area.innerHTML = `<div style="color:#ff2d55;white-space:pre-wrap;font-size:11px;padding:8px 0">${esc(result.error)}</div>`;
          return;
        }
        const rows   = result.data   || [];
        const schema = result.schema || [];
        if (status) status.textContent = `✓ ${rows.length} filas · ${elapsed}s`;
        area.innerHTML = _renderQueryTable(schema, rows, 300);
      } catch(e) {
        if (status) status.textContent = '✗ error de red';
        area.innerHTML = `<div style="color:#ff2d55;font-size:11px;padding:8px 0">${esc(e.message)}</div>`;
      }
    }

    export function _renderQueryTable(schema, rows, maxHeight = 300) {
      if (!rows.length) return `<div style="color:var(--text3);font-style:italic;padding:8px 0">Sin resultados</div>`;
      const cols = schema.length ? schema.map(s => s.name || s[0]) : Object.keys(rows[0]);
      const thead = `<tr>${cols.map(c => `<th style="padding:4px 8px;border-bottom:1px solid var(--border);white-space:nowrap;color:var(--text3);font-weight:normal;text-align:left">${esc(c)}</th>`).join('')}</tr>`;
      const tbody = rows.map(row =>
        `<tr>${cols.map(c => `<td style="padding:3px 8px;border-bottom:1px solid var(--bg2);white-space:nowrap;max-width:260px;overflow:hidden;text-overflow:ellipsis" title="${esc(String(row[c]??''))}">${esc(String(row[c]??''))}</td>`).join('')}</tr>`
      ).join('');
      return `<div style="overflow:auto;max-height:${maxHeight}px;border:1px solid var(--border);border-radius:2px">
        <table style="border-collapse:collapse;width:100%;font-family:var(--font-mono);font-size:11px">
          <thead style="position:sticky;top:0;background:var(--bg)">${thead}</thead>
          <tbody>${tbody}</tbody>
        </table></div>`;
    }

    export async function saveDS() {
      const name    = document.getElementById('ds-ed-name')?.value.trim();
      const layer   = document.getElementById('ds-ed-layer')?.value || 'silver';
      const entity  = _currentEditorEntity();
      const desc    = document.getElementById('ds-ed-desc')?.value.trim() || '';
      const sql     = document.getElementById('ds-ed-sql')?.value.trim() || '';
      const status  = document.getElementById('ds-ed-status');
      const cart    = state._currentCartridge?.id || '';

      if (!name) { if (status) status.innerHTML = '<span style="color:#ff2d55">Nombre requerido</span>'; return; }
      if (!sql)  { if (status) status.innerHTML = '<span style="color:#ff2d55">SQL requerido</span>';    return; }

      if (status) status.textContent = '⟳ guardando...';
      try {
        const sources = entity && cart ? [`raw/${cart}/${entity}`] : [];
        const r = await fetch('/api/datasets/save', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ name, layer, sql, description: desc, cartridge: cart, sources }),
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        if (status) status.innerHTML = '<span style="color:var(--green)">✓ guardado</span>';
        state._dsEditorDirty = false;
        state._selectedDS = { name };
        // Refresh dataset list
        const rd = await fetch('/datasets');
        state._allDatasets = (await rd.json()).datasets || [];
        renderDSWorkspace(layer);
        selectDS(name);
        return name;   // for saveThenMaterialize
      } catch(e) {
        if (status) status.innerHTML = `<span style="color:#ff2d55">✗ ${esc(e.message)}</span>`;
        return null;
      }
    }

    export async function saveThenMaterialize() {
      const name = await saveDS();   // saveDS now returns the saved name
      if (name) await materializeDS(name);
    }

    export async function materializeDS(name) {
      const status = document.getElementById('ds-ed-status');
      if (status) status.textContent = '⟳ materializando...';
      try {
        const r = await fetch(`/datasets/${encodeURIComponent(name)}/refresh`, { method: 'POST' });
        const d = await r.json();
        const rows = d.row_count ?? d.result?.row_count;
        if (status) status.innerHTML = `<span style="color:var(--green)">✓ ${rows != null ? Number(rows).toLocaleString('es')+' filas' : 'ok'}</span>`;
        // Refresh list stats
        const rd = await fetch('/datasets');
        state._allDatasets = (await rd.json()).datasets || [];
        renderDSWorkspace(state._activeLayer);
        selectDS(name);
        // Reload dataset data preview
        await loadDSDataPreview(name);
      } catch(e) {
        if (status) status.innerHTML = `<span style="color:#ff2d55">✗ ${esc(e.message)}</span>`;
      }
    }

    export async function deleteDS(name) {
      if (!confirm(`¿Eliminar el dataset "${name}"?\n\nEsto borra el registro en Postgres, el Parquet en MinIO (silver/master) y la tabla en Postgres (master/gold). Esta acción no se puede deshacer.`)) return;
      const status = document.getElementById('ds-ed-status');
      if (status) status.textContent = '⟳ eliminando...';
      try {
        const r = await fetch(`/api/datasets?name=${encodeURIComponent(name)}`, { method: 'DELETE' });
        if (!r.ok) {
          const txt = await r.text().catch(() => '');
          const err = (() => { try { return JSON.parse(txt); } catch { return {}; } })();
          const msg = err.detail || err.error || txt || `HTTP ${r.status}`;
          if (status) status.innerHTML = `<span style="color:#ff2d55">✗ ${esc(msg)}</span>`;
          console.error('deleteDS', r.status, txt);
          return;
        }
        // Remove from local cache and re-render
        state._allDatasets = state._allDatasets.filter(d => d.name !== name);
        renderDSWorkspace(state._activeLayer);
        const panel = document.getElementById('ds-workspace-panel');
        if (panel) panel.innerHTML = `<div style="color:var(--text3);padding:20px;font-size:12px">Dataset <strong>${esc(name)}</strong> eliminado.</div>`;
      } catch(e) {
        if (status) status.innerHTML = `<span style="color:#ff2d55">✗ ${esc(e.message)}</span>`;
      }
    }

    export async function loadDSDataPreview(name) {
      const area = document.getElementById('ds-preview-area');
      if (!area) return;
      try {
        const r = await fetch(`/datasets/${encodeURIComponent(name)}/data?limit=50`);
        const d = await r.json();
        const rows   = d.rows   || d.data   || [];
        const schema = d.schema || d.columns || [];
        if (rows.length) {
          area.innerHTML = `<div style="font-size:9px;letter-spacing:1px;color:var(--text3);margin-bottom:6px">DATOS MATERIALIZADOS</div>`
            + _renderQueryTable(schema, rows, 280);
        }
      } catch(e) {}
    }

    export function sendDSToAI() {
      const name   = document.getElementById('ds-ed-name')?.value.trim() || '';
      const entity = _currentEditorEntity();
      const layer  = document.getElementById('ds-ed-layer')?.value || 'silver';
      const sql    = document.getElementById('ds-ed-sql')?.value.trim() || '';
      const cart   = state._currentCartridge?.id || '';
      const msg    = `Estoy diseñando el dataset "${name||'nuevo'}" (${layer.toUpperCase()}) `
        + `del cartucho "${cart}", entidad fuente: "${entity}".\n`
        + (sql ? `SQL actual:\n\`\`\`sql\n${sql}\n\`\`\`\n` : '')
        + `¿Puedes revisar/mejorar el SQL? Path Bronze: s3://${state.S3_BUCKET}/raw/${cart}/${entity}/load_date=*/data.parquet`;
      document.getElementById('ai-input').value = msg;
      aiSend();
    }

    // ── Bronze tab ─────────────────────────────────────────────────────────────
    export async function loadBronzeTab() {
      const area = document.getElementById('ds-list-area');
      if (!area) return;

      let sources = state._bronzeSources;
      if (!sources.length) {
        try {
          const r = await fetch('/api/sources');
          const d = await r.json();
          state._bronzeSources = (d.sources || []).map(s => {
            const parts = s.split('/');           // raw / cartridge / entity
            return { source: s, cartridge: parts[1] || '', entity: parts[2] || '' };
          });
          sources = state._bronzeSources;
        } catch(e) { sources = []; }
      }

      const cartridge = state._currentCartridge?.id || '';
      const filtered  = cartridge ? sources.filter(s => s.cartridge === cartridge) : sources;

      const count = document.getElementById('bronze-count');
      if (count) count.textContent = `(${filtered.length})`;

      const entityRows = filtered.map(s => `
        <div class="ds-row" style="cursor:pointer;padding-left:12px" onclick="bronzeSelectSource(${escJsArg(s.source)})">
          <div>
            <div class="ds-row-name">${esc(s.entity)}</div>
            <div class="ds-row-meta">${esc(s.source)}</div>
          </div>
          <button class="btn btn-sm" onclick="event.stopPropagation();bronzeSelectSource(${escJsArg(s.source)})">Query →</button>
        </div>`).join('') || '<div class="empty-card">Sin datos Bronze para este cartucho.</div>';

      area.innerHTML = `
        <div style="display:flex;gap:12px;height:100%;min-height:460px">

          <!-- Entity list -->
          <div style="width:220px;flex-shrink:0;border-right:1px solid var(--border);padding-right:12px;overflow-y:auto">
            <div style="font-size:9px;letter-spacing:1px;color:var(--text3);margin-bottom:10px">FUENTES BRONZE</div>
            ${entityRows}
          </div>

          <!-- Query editor + results -->
          <div style="flex:1;display:flex;flex-direction:column;overflow:hidden">

            <!-- TOP: query area (resizable) -->
            <div id="bronze-top-panel" style="display:flex;flex-direction:column;gap:8px;
                                               height:200px;min-height:80px;flex-shrink:0">
              <div style="font-size:9px;letter-spacing:1px;color:var(--text3)">QUERY DUCKDB</div>
              <textarea id="bronze-sql" spellcheck="false"
                style="width:100%;box-sizing:border-box;flex:1;min-height:60px;
                       background:var(--bg2);border:1px solid var(--border);
                       color:var(--cyan);font-family:var(--font-mono);font-size:11px;padding:10px;
                       resize:none;border-radius:2px;outline:none"
                placeholder="-- Haz click en una entidad de la izquierda para auto-llenar el query&#10;-- o escribe directamente"
                onkeydown="if(event.ctrlKey&&event.key==='Enter'){runBronzeQuery();}"
              >${esc(state._bronzeQuery)}</textarea>
              <div style="display:flex;gap:8px;align-items:center;flex-shrink:0">
                <button class="btn btn-amber" onclick="runBronzeQuery()">▶ Ejecutar</button>
                <span style="font-size:10px;color:var(--text3)">Ctrl+Enter</span>
                <span id="bronze-status" style="font-size:10px;color:var(--text3);margin-left:auto"></span>
              </div>
            </div>

            <!-- Resize handle -->
            <div class="v-resize-handle" onmousedown="vResizeStart(event,'bronze-top-panel')"
                 title="Arrastra para redimensionar"></div>

            <!-- BOTTOM: Vista Previa -->
            <div style="flex:1;overflow:auto;display:flex;flex-direction:column;min-height:60px">
              <div style="font-size:9px;letter-spacing:1px;color:var(--text3);padding:6px 0 4px">VISTA PREVIA</div>
              <div id="bronze-results" style="flex:1;overflow:auto;font-size:11px"></div>
            </div>
          </div>
        </div>
      `;
    }

    export function bronzeSelectSource(source) {
      const pattern = `s3://{bucket}/${source}/load_date=*/data.parquet`;
      const sql = `SELECT *\nFROM read_parquet('${pattern}',\n  hive_partitioning=true, union_by_name=true)\nLIMIT 100`;
      const ta = document.getElementById('bronze-sql');
      if (ta) { ta.value = sql; state._bronzeQuery = sql; }
    }

    export async function runBronzeQuery() {
      const ta  = document.getElementById('bronze-sql');
      const sql = ta?.value.trim();
      if (!sql) return;
      state._bronzeQuery = sql;
      const statusEl  = document.getElementById('bronze-status');
      const resultsEl = document.getElementById('bronze-results');
      if (statusEl)  statusEl.textContent = '⟳ ejecutando...';
      if (resultsEl) resultsEl.innerHTML  = '';
      const t0 = Date.now();
      try {
        const r = await fetch('/api/bronze/query', {
          method: 'POST', headers: {'Content-Type':'application/json'},
          body: JSON.stringify({ sql, limit: 500 }),
        });
        const d = await r.json();
        const elapsed = ((Date.now()-t0)/1000).toFixed(2);
        const result  = d.result || d;
        if (result.error) {
          if (statusEl) statusEl.textContent = `✗ error`;
          if (resultsEl) resultsEl.innerHTML = `<div style="color:#ff2d55;white-space:pre-wrap;font-size:11px">${esc(result.error)}</div>`;
          return;
        }
        const schema = result.schema || [];
        const rows   = result.data   || [];
        if (statusEl) statusEl.textContent = `✓ ${rows.length} filas · ${elapsed}s`;
        if (!rows.length) {
          resultsEl.innerHTML = '<div style="color:var(--text3);font-style:italic">Sin resultados</div>';
          return;
        }
        resultsEl.innerHTML = _renderQueryTable(schema, rows, 320);
      } catch(e) {
        if (statusEl)  statusEl.textContent = '✗ error de red';
        if (resultsEl) resultsEl.innerHTML  = `<div style="color:#ff2d55">${esc(e.message)}</div>`;
      }
    }

    export function askSqlHelp() {
      const cartridge = document.getElementById('ds-cart')?.value.trim() || '';
      const entity    = document.getElementById('ds-entity')?.value.trim() || '';
      const layer     = document.getElementById('ds-layer')?.value || 'silver';
      const msg = `Genera el SQL DuckDB para un dataset ${layer.toUpperCase()} del cartucho "${cartridge}", entidad "${entity}". `
        + `El path Bronze es s3://${state.S3_BUCKET}/raw/${cartridge}/${entity}/load_date={latest_date}/*.parquet. `
        + `Incluye las columnas más relevantes y limpieza básica de datos.`;
      document.getElementById('ai-input').value = msg;
      aiSend();
    }

    export function createDatasetViaAI() {
      const name   = document.getElementById('ds-name')?.value.trim() || '';
      const layer  = document.getElementById('ds-layer')?.value || 'silver';
      const cart   = document.getElementById('ds-cart')?.value.trim() || '';
      const entity = document.getElementById('ds-entity')?.value.trim() || '';
      const desc   = document.getElementById('ds-desc')?.value.trim() || '';
      const sql    = document.getElementById('ds-sql')?.value.trim() || '';
      if (!name) {
        document.getElementById('new-ds-msg').innerHTML = '<span style="color:#ff2d55">El nombre es obligatorio</span>';
        return;
      }
      const msg = `Crea un dataset ${layer.toUpperCase()} con los siguientes parámetros:\n`
        + `- Nombre: ${name}\n`
        + `- Cartucho: ${cart}\n`
        + `- Entidad fuente: ${entity}\n`
        + `- Descripción: ${desc}\n`
        + (sql ? `- SQL:\n\`\`\`sql\n${sql}\n\`\`\`` : '- Genera el SQL apropiado.');
      document.getElementById('ai-input').value = msg;
      aiSend();
      document.getElementById('new-ds-msg').innerHTML = '<span style="color:var(--cyan)">⟳ Enviado al asistente →</span>';
    }

    export async function refreshDS(name) {
      try {
        await fetch(`/datasets/${name}/refresh`, {method: 'POST'});
        renderRefine();
      } catch(e) { /* ignore */ }
    }

    // ── Step 4: Analytics ──────────────────────────────────────────────────────

    export function renderAnalytics() {
      document.getElementById('step-content').innerHTML = `
        <div class="step-title">
          <div>
            <h2>Analytics &amp; Superset</h2>
            <p class="step-desc">Crea datasets, gráficos y dashboards en Apache Superset directamente desde el asistente.
              Describe los KPIs que necesitas y el asistente los configura por ti.</p>
          </div>
          <a class="btn btn-sm btn-amber" href="http://localhost:8088" target="_blank">Abrir Superset ↗</a>
        </div>

        <div class="card">
          <div class="card-title">DATASETS GOLD DISPONIBLES</div>
          <button class="btn btn-sm" type="button">Ver SQL</button>
          <div id="gold-list"><div class="loading">Cargando...</div></div>
        </div>

        <div class="card" style="border-color:#7c9fff">
          <div class="card-title" style="color:#7c9fff">▦ APPS ANALÍTICAS</div>
          <p style="font-size:11px;color:var(--text2);margin-bottom:12px;line-height:1.7">
            Aplicaciones interactivas generadas por IA sobre los datos Gold. Pídele al asistente que cree una nueva.
          </p>
          <div id="apps-step5-list"><div class="loading">Cargando...</div></div>
        </div>

        <div class="card" style="border-color:var(--cyan)">
          <div class="card-title" style="color:var(--cyan)">◈ CREAR EN SUPERSET CON IA</div>
          <p style="font-size:11px;color:var(--text2);margin-bottom:12px;line-height:1.7">
            Describe al asistente qué dashboards, gráficos o métricas quieres y los creará automáticamente en Superset.
          </p>
          <div style="display:flex;flex-wrap:wrap;gap:8px">
            <button class="btn btn-sm" onclick="askSuperset('Crea un dataset y un dashboard con KPIs de utilización de recursos por proyecto en Superset')">
              ◈ KPIs de utilización
            </button>
            <button class="btn btn-sm" onclick="askSuperset('Lista las bases de datos registradas en Superset y dime qué database_id tiene modecissions_gold')">
              ◈ Ver conexiones Superset
            </button>
            <button class="btn btn-sm" onclick="askSuperset('Crea un gráfico de barras con las horas facturables por departamento usando los datos Gold disponibles')">
              ◈ Horas por departamento
            </button>
            <button class="btn btn-sm" onclick="askSuperset('Exporta el dashboard actual del cartucho para incluirlo en el ZIP portable')">
              ◈ Exportar dashboard
            </button>
          </div>
        </div>
      `;

      fetch('/datasets').then(r => r.json()).then(d => {
        const gold = (d.datasets || []).filter(ds => ds.layer === 'gold');
        const el   = document.getElementById('gold-list');
        if (!gold.length) {
          el.innerHTML = '<div class="empty-card">No hay datasets Gold definidos. Ve al Paso 3 (Refinar) para crearlos.</div>';
          return;
        }
        el.innerHTML = gold.map(ds => `
          <div class="ds-row">
            <div>
              <div class="ds-row-name">${esc(ds.name)}</div>
              <div class="ds-row-meta">${ds.row_count != null ? Number(ds.row_count).toLocaleString('es') + ' rows · ' : ''}${ds.updated_at ? esc(fmt(ds.updated_at)) : '—'}</div>
            </div>
            <div style="display:flex;gap:6px;flex-shrink:0">
              <button class="btn btn-sm btn-amber" onclick="createSupersetDataset(${escJsArg(ds.name)})">
                + Crear en Superset
              </button>
              <a class="btn btn-sm" href="/viewer/datasets/${encodeURIComponent(ds.name || '')}" target="_blank">Ver SQL →</a>
            </div>
          </div>`).join('');
      }).catch(() => {
        document.getElementById('gold-list').innerHTML = '<div class="empty-card">Error cargando datasets</div>';
      });

      // Cargar apps analíticas publicadas
      loadAppsInStep5();
    }

    export async function loadAppsInStep5() {
      const el = document.getElementById('apps-step5-list');
      if (!el) return;
      try {
        const r = await fetch('/api/apps');
        const d = await r.json();
        const apps = d.apps || [];
        if (!apps.length) {
          el.innerHTML = '<div class="empty-card">No hay apps publicadas aún. Pídele al asistente que genere una app analítica con los datasets Gold.</div>';
          return;
        }
        el.innerHTML = `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px">
          ${apps.map(app => `
          <div class="studio-app-card" style="background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:16px;display:flex;flex-direction:column;gap:10px;transition:border-color .2s"
               onmouseover="this.style.borderColor='#7c9fff'" onmouseout="this.style.borderColor='var(--border)'">
            <div style="font-size:18px;color:#7c9fff">▦</div>
            <div style="font-family:var(--font-mono);font-size:11px;color:var(--text1)">${esc(app.title)}</div>
            <div style="font-size:10px;color:var(--text2);line-height:1.6;flex:1">${esc(app.description || '—')}</div>
            <div style="font-size:9px;color:var(--text3);font-family:var(--font-mono)">
              ${app.updated_at ? esc(app.updated_at.slice(0,16).replace('T',' ')) : '—'}
            </div>
            <div style="display:flex;gap:6px">
              <a href="/apps/${encodeURIComponent(app.name || '')}" target="_blank"
                 style="flex:1;display:inline-block;padding:5px 12px;border:1px solid #7c9fff;border-radius:3px;
                        color:#7c9fff;font-size:10px;font-family:var(--font-mono);text-decoration:none;
                        text-align:center;transition:background .15s"
                 onmouseover="this.style.background='rgba(124,159,255,.12)'"
                 onmouseout="this.style.background='transparent'">→ ABRIR APP</a>
              <button onclick="deleteAnalyticApp(${escJsArg(app.name)}, ${escJsArg(app.title)})" title="Eliminar"
                      style="padding:5px 10px;border:1px solid #c44;border-radius:3px;background:transparent;
                             color:#c44;font-size:11px;font-family:var(--font-mono);cursor:pointer;transition:background .15s"
                      onmouseover="this.style.background='rgba(204,68,68,.12)'"
                      onmouseout="this.style.background='transparent'">🗑</button>
            </div>
          </div>`).join('')}
        </div>`;
      } catch(e) {
        el.innerHTML = `<div class="empty-card">Error cargando apps: ${esc(e.message)}</div>`;
      }
    }

    export async function deleteAnalyticApp(name, title) {
      if (!confirm(`¿Eliminar la aplicación "${title}"?\n\nEsto no se puede deshacer.`)) return;
      try {
        const r = await fetch('/api/apps/' + encodeURIComponent(name), {method: 'DELETE'});
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          alert('Error eliminando: ' + (d.detail || r.statusText));
          return;
        }
        await loadAppsInStep5();
      } catch(e) {
        alert('Error: ' + e.message);
      }
    }

    export function askSuperset(msg) {
      document.getElementById('ai-input').value = msg;
      aiSend();
    }

    export async function createSupersetDataset(datasetName) {
      const el = document.getElementById('gold-list');
      const tableName = `gold_${String(datasetName || '').replace(/^gold_/, '')}`;
      if (el) {
        el.querySelectorAll('.superset-status').forEach(n => n.remove());
        el.insertAdjacentHTML('afterbegin', '<div class="superset-status empty-card">⟳ Creando dataset en Superset...</div>');
      }
      try {
        const r = await fetch('/api/studio/superset/dataset', {
          method: 'POST',
          headers: jsonHeaders(),
          body: JSON.stringify({ table_name: tableName, schema: 'public' }),
        });
        const d = await r.json();
        const status = document.querySelector('.superset-status');
        if (!r.ok || d.error) {
          if (status) status.innerHTML = `<span style="color:#ff2d55">No se pudo crear: ${esc(d.error || d.detail || `HTTP ${r.status}`)}</span>`;
          return;
        }
        if (status) {
          status.innerHTML = d.existing
            ? `✓ Ya existía en Superset: ${esc(d.table || tableName)}`
            : `✓ Dataset creado en Superset: ${esc(d.table || tableName)}`;
        }
      } catch(e) {
        const status = document.querySelector('.superset-status');
        if (status) status.innerHTML = `<span style="color:#ff2d55">Error: ${esc(e.message)}</span>`;
      }
    }

    // ── Step 6: IA Semántica / Data Catalog ───────────────────────────────────


    // ── Step 7: RAG ───────────────────────────────────────────────────────────

    export async function renderRAG() {
      const el = document.getElementById('step-content');
      el.innerHTML = `
        <div class="step-title">
          <div>
            <h2 style="color:var(--cyan)">RAG · Knowledge Base</h2>
            <p class="step-desc">Ingiere documentos (texto o PDF) y consúltalos por similitud semántica. El asistente puede citarlos.</p>
          </div>
        </div>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">

          <div class="card" style="border-color:var(--cyan)">
            <div class="card-title" style="color:var(--cyan)">▣ FUENTES INGERIDAS</div>
            <div id="rag-sources-list" style="margin-top:10px;font-size:11px">Cargando…</div>
            <hr style="border:none;border-top:1px solid var(--border);margin:14px 0">
            <div class="card-title" style="color:var(--cyan)">+ INGRESAR DOCUMENTO</div>
            <div style="display:grid;gap:8px;margin-top:10px">
              <input id="rag-name" placeholder="Nombre único (ej. politica_vacaciones)"
                     style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:6px 10px;font-family:var(--font-mono);font-size:11px">
              <input id="rag-desc" placeholder="Descripción corta (opcional)"
                     style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:6px 10px;font-family:var(--font-mono);font-size:11px">
              <textarea id="rag-text" rows="6" placeholder="Pega texto, o usa el botón para subir un PDF"
                        style="background:var(--bg);border:1px solid var(--border);color:var(--text);padding:6px 10px;font-family:var(--font-mono);font-size:11px;resize:vertical"></textarea>
              <div style="display:flex;gap:6px">
                <input type="file" id="rag-pdf" accept=".pdf,application/pdf" style="font-size:11px;flex:1">
                <button class="btn" onclick="ragIngest()" style="color:var(--cyan)">▶ Ingerir</button>
              </div>
              <div id="rag-ingest-msg" style="font-size:11px;color:var(--text2);min-height:14px"></div>
            </div>
          </div>

          <div class="card">
            <div class="card-title" style="color:var(--green)">⌕ BÚSQUEDA SEMÁNTICA</div>
            <div style="display:flex;gap:6px;margin-top:10px">
              <input id="rag-query" placeholder="Pregunta o frase a buscar..." onkeydown="if(event.key==='Enter')ragAsk()"
                     style="flex:1;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:6px 10px;font-family:var(--font-mono);font-size:11px">
              <button class="btn" onclick="ragAsk()" title="Pregunta + síntesis con LLM">▶ Preguntar</button>
              <button class="btn" onclick="ragSearch()" title="Solo extractos, sin síntesis">⌕</button>
            </div>
            <div id="rag-results" style="margin-top:14px;font-size:11px;max-height:520px;overflow-y:auto"></div>
          </div>
        </div>
      `;
      await ragLoadSources();
    }

    export async function ragLoadSources() {
      const list = document.getElementById('rag-sources-list');
      try {
        const r = await fetch('/api/rag/sources');
        const d = await r.json();
        const sources = d.sources || [];
        if (!sources.length) {
          list.innerHTML = '<div style="color:var(--text2)">Sin fuentes aún.</div>';
          return;
        }
        list.innerHTML = sources.map(s => `
          <div style="display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--border)">
            <div>
              <div style="color:var(--text)">${esc(s.name)}</div>
              <div style="color:var(--text2);font-size:10px">${esc(s.description || '')} · ${Number(s.size_chars || 0).toLocaleString('es')} chars · ${Number(s.chunk_count || 0).toLocaleString('es')} chunks</div>
            </div>
            <button class="btn-sm" onclick="ragDeleteSource(${Number(s.id)})" title="Borrar">🗑</button>
          </div>`).join('');
      } catch(e) {
        list.innerHTML = `<div style="color:var(--red)">Error: ${esc(e.message)}</div>`;
      }
    }

    export async function ragDeleteSource(id) {
      if (!confirm('¿Borrar esta fuente del RAG?')) return;
      await fetch(`/api/rag/sources/${id}`, {method: 'DELETE'});
      await ragLoadSources();
    }

    export async function ragIngest() {
      const name = document.getElementById('rag-name').value.trim();
      const desc = document.getElementById('rag-desc').value.trim();
      const text = document.getElementById('rag-text').value;
      const pdf  = document.getElementById('rag-pdf').files[0];
      const msg  = document.getElementById('rag-ingest-msg');

      if (!name) { msg.innerHTML = '<span style="color:var(--red)">Falta nombre</span>'; return; }
      if (!text.trim() && !pdf) { msg.innerHTML = '<span style="color:var(--red)">Pega texto o sube un PDF</span>'; return; }

      msg.innerHTML = '<span style="color:var(--cyan)">⟳ Procesando…</span>';

      try {
        let body = {name, description: desc};
        if (pdf) {
          body.content = await fileToBase64(pdf);
          body.mime_type = 'application/pdf';
        } else {
          body.content = text;
        }

        const r = await fetch('/api/rag/ingest', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
        const d = await r.json();
        if (d.error) {
          msg.innerHTML = `<span style="color:var(--red)">${esc(d.error)}</span>`;
        } else {
          msg.innerHTML = `<span style="color:var(--green)">✓ Ingerido — ${Number(d.chunk_count || 0).toLocaleString('es')} chunks</span>`;
          document.getElementById('rag-name').value = '';
          document.getElementById('rag-desc').value = '';
          document.getElementById('rag-text').value = '';
          document.getElementById('rag-pdf').value = '';
          await ragLoadSources();
        }
      } catch(e) {
        msg.innerHTML = `<span style="color:var(--red)">${esc(e.message || e)}</span>`;
      }
    }

    export function fileToBase64(file) {
      return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
          const result = reader.result || '';
          const comma = result.indexOf(',');
          resolve(comma >= 0 ? result.slice(comma + 1) : '');
        };
        reader.onerror = () => reject(reader.error || new Error('FileReader error'));
        reader.readAsDataURL(file);
      });
    }

    export async function ragAsk() {
      const q  = document.getElementById('rag-query').value.trim();
      const ul = document.getElementById('rag-results');
      if (!q) return;
      ul.innerHTML = '<div style="color:var(--text2)">⟳ Recuperando contexto y sintetizando respuesta…</div>';
      try {
        const r = await fetch('/api/rag/ask', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({query: q, top_k: 5})});
        const d = await r.json();
        if (d.detail || d.error) {
          ul.innerHTML = `<div style="color:var(--red)">${esc(d.detail || d.error)}</div>`;
          return;
        }
        const answer = d.answer || '(sin respuesta)';
        const res = d.results || [];
        const answerHtml = `
          <div style="border:1px solid var(--green);padding:12px;margin-bottom:14px;border-radius:2px;background:rgba(0,255,128,0.04)">
            <div style="color:var(--green);font-size:10px;margin-bottom:8px">◆ RESPUESTA</div>
            <div style="white-space:pre-wrap;color:var(--text);line-height:1.5">${esc(answer)}</div>
          </div>`;
        const evidence = res.map((x, i) => {
          const score = (x.similarity ?? x.score ?? 0);
          const body  = x.context || x.parent_content || x.child_content || x.content || '';
          return `
          <div style="border:1px solid var(--border);padding:10px;margin-bottom:8px;border-radius:2px">
            <div style="color:var(--cyan);font-size:10px;margin-bottom:6px">
              [${i+1}] · ${esc(x.source_name || '?')} · score=${score.toFixed(3)}
            </div>
            <div style="white-space:pre-wrap;color:var(--text2)">${esc(body.slice(0, 500))}${body.length > 500 ? '…' : ''}</div>
          </div>`;
        }).join('');
        const evidenceWrap = res.length
          ? `<div style="color:var(--text2);font-size:10px;margin-bottom:6px">▸ FUENTES CITADAS</div>${evidence}`
          : '';
        ul.innerHTML = answerHtml + evidenceWrap;
      } catch(e) {
        ul.innerHTML = `<div style="color:var(--red)">${esc(e.message || e)}</div>`;
      }
    }

    export async function ragSearch() {
      const q  = document.getElementById('rag-query').value.trim();
      const ul = document.getElementById('rag-results');
      if (!q) return;
      ul.innerHTML = '<div style="color:var(--text2)">⟳ Buscando…</div>';
      try {
        const r = await fetch('/api/rag/search', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({query: q, top_k: 5})});
        const d = await r.json();
        const res = d.results || [];
        if (!res.length) { ul.innerHTML = '<div style="color:var(--text2)">Sin resultados.</div>'; return; }
        ul.innerHTML = res.map((r, i) => {
          const score = (r.similarity ?? r.score ?? 0);
          const body  = r.context || r.parent_content || r.child_content || r.content || '';
          return `
          <div style="border:1px solid var(--border);padding:10px;margin-bottom:8px;border-radius:2px">
            <div style="color:var(--cyan);font-size:10px;margin-bottom:6px">
              #${i+1} · ${esc(r.source_name || '?')} · score=${score.toFixed(3)}
            </div>
            <div style="white-space:pre-wrap;color:var(--text)">${esc(body.slice(0, 800))}${body.length > 800 ? '…' : ''}</div>
          </div>`;
        }).join('');
      } catch(e) {
        ul.innerHTML = `<div style="color:var(--red)">${esc(e.message)}</div>`;
      }
    }

    export async function renderSemantic() {
      document.getElementById('step-content').innerHTML = `
        <div class="step-title">
          <div>
            <h2>Data Catalog &amp; Semántica</h2>
            <p class="step-desc">Vocabulario de negocio del modelo de datos. Edita descripciones, tags y relaciones para que EPI IA genere SQL preciso.</p>
          </div>
          <button class="btn btn-amber" onclick="askSemanticHelp()" title="Pedir al asistente que enriquezca el catálogo">◈ Enriquecer con IA</button>
        </div>

        <div class="card" style="padding:10px 14px;gap:8px;display:flex;flex-direction:column">
          <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
            <div style="display:flex;gap:0;border:1px solid var(--border);border-radius:3px;overflow:hidden">
              <button id="cat-tab-cols" class="btn-tab active" onclick="catSwitchTab('cols')">COLUMNAS</button>
              <button id="cat-tab-rels" class="btn-tab"        onclick="catSwitchTab('rels')">RELACIONES</button>
            </div>
            <input id="cat-search" type="text" placeholder="Buscar dataset o columna…"
              style="flex:1;min-width:160px;background:var(--bg2);border:1px solid var(--border);
                     color:var(--text1);padding:4px 8px;font-size:11px;border-radius:3px"
              oninput="state._catFilter.search=this.value;catRender()">
            <select id="cat-layer" onchange="state._catFilter.layer=this.value;catReload()"
              style="background:var(--bg2);border:1px solid var(--border);color:var(--text2);
                     padding:4px 6px;font-size:10px;border-radius:3px">
              <option value="">Todas las capas</option>
              <option value="silver">silver</option>
              <option value="master">master</option>
              <option value="gold">gold</option>
            </select>
            <button class="btn btn-sm" onclick="catReload()" title="Recargar">↺</button>
          </div>
          <div id="cat-status" style="font-size:9px;color:var(--text3)"></div>
        </div>

        <div id="cat-cols-panel">
          <ul class="metrics-list" aria-label="Métricas semánticas"
              style="margin:0 0 10px;padding-left:18px;color:var(--text2);font-size:11px">
            <li>Catálogo semántico preparado</li>
            <li>Columnas y relaciones disponibles para revisión</li>
          </ul>
          <div id="cat-table-wrap" style="overflow-x:auto">
            <div class="loading">Cargando catálogo…</div>
          </div>
        </div>

        <div id="cat-rels-panel" style="display:none">
          <div class="card" style="padding:0;overflow:hidden">
            <div style="display:flex;justify-content:space-between;align-items:center;
                        padding:10px 14px;border-bottom:1px solid var(--border)">
              <span style="font-size:9px;letter-spacing:1px;color:var(--text3)">RELACIONES ENTRE DATASETS</span>
              <button class="btn btn-sm btn-amber" onclick="catAddRelModal()">+ Nueva relación</button>
            </div>
            <div id="cat-rels-wrap"><div class="loading" style="padding:16px">Cargando…</div></div>
          </div>
        </div>

        <!-- Modal nueva relación -->
        <div id="cat-rel-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);
             z-index:9000;align-items:center;justify-content:center">
          <div style="background:var(--bg1);border:1px solid var(--amber);border-radius:4px;
                      width:520px;max-width:95vw;padding:20px;display:flex;flex-direction:column;gap:12px">
            <div style="font-size:11px;letter-spacing:1px;color:var(--amber)">REGISTRAR RELACIÓN</div>
            <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">
              <div>
                <div style="font-size:9px;color:var(--text3);margin-bottom:3px">Dataset origen</div>
                <input id="rel-from-ds" placeholder="replicon_timeentry_latest" class="cat-inp">
              </div>
              <div>
                <div style="font-size:9px;color:var(--text3);margin-bottom:3px">Columna origen</div>
                <input id="rel-from-col" placeholder="projectcode" class="cat-inp">
              </div>
              <div>
                <div style="font-size:9px;color:var(--text3);margin-bottom:3px">Dataset destino</div>
                <input id="rel-to-ds" placeholder="replicon_project_latest" class="cat-inp">
              </div>
              <div>
                <div style="font-size:9px;color:var(--text3);margin-bottom:3px">Columna destino</div>
                <input id="rel-to-col" placeholder="code" class="cat-inp">
              </div>
            </div>
            <div>
              <div style="font-size:9px;color:var(--text3);margin-bottom:3px">Tipo JOIN</div>
              <select id="rel-join" class="cat-inp" style="width:140px">
                <option>LEFT</option><option>INNER</option><option>COALESCE</option>
              </select>
            </div>
            <div>
              <div style="font-size:9px;color:var(--text3);margin-bottom:3px">Descripción</div>
              <input id="rel-desc" placeholder="Proyecto del tiempo reportado" class="cat-inp">
            </div>
            <div>
              <div style="font-size:9px;color:var(--text3);margin-bottom:3px">Transform (opcional — si el JOIN no es directo)</div>
              <input id="rel-transform" placeholder="LOWER(TRIM(em.usuario)) = LOWER(TRIM(te.username))" class="cat-inp">
            </div>
            <div style="display:flex;gap:8px;justify-content:flex-end">
              <button class="btn btn-sm" onclick="document.getElementById('cat-rel-modal').style.display='none'">Cancelar</button>
              <button class="btn btn-amber" onclick="catSaveRel()">Guardar relación</button>
            </div>
          </div>
        </div>`;

      // inline styles for catalog inputs
      if (!document.getElementById('cat-styles')) {
        const s = document.createElement('style');
        s.id = 'cat-styles';
        s.textContent = `
          .btn-tab { background:none;border:none;color:var(--text3);padding:5px 12px;
                     font-size:9px;letter-spacing:1px;cursor:pointer;transition:.15s }
          .btn-tab.active { background:var(--amber);color:#000;font-weight:700 }
          .btn-tab:hover:not(.active) { color:var(--text1) }
          .cat-inp { width:100%;background:var(--bg2);border:1px solid var(--border);
                     color:var(--text1);padding:5px 8px;font-size:11px;border-radius:3px;box-sizing:border-box }
          .cat-inp:focus { outline:none;border-color:var(--amber) }
          .cat-table { width:100%;border-collapse:collapse;font-size:10px }
          .cat-table th { background:var(--bg2);color:var(--text3);font-size:8px;letter-spacing:1px;
                          padding:6px 10px;text-align:left;position:sticky;top:0;z-index:1;
                          border-bottom:1px solid var(--border) }
          .cat-table td { padding:5px 10px;border-bottom:1px solid var(--border);vertical-align:middle;
                          color:var(--text2);white-space:nowrap;max-width:220px;overflow:hidden;text-overflow:ellipsis }
          .cat-table tr:hover td { background:var(--bg2) }
          .cat-ds-row td { background:var(--bg2)!important;color:var(--amber);font-family:var(--font-mono);
                           font-size:10px;letter-spacing:.5px }
          .cat-tag { display:inline-block;padding:1px 6px;border-radius:2px;font-size:8px;margin:1px;
                     background:rgba(255,170,0,.15);color:var(--amber);border:1px solid rgba(255,170,0,.3) }
          .cat-flag { width:14px;height:14px;border-radius:2px;border:1px solid var(--border);
                      cursor:pointer;display:inline-block;text-align:center;line-height:13px;font-size:9px }
          .cat-flag.on { background:var(--amber);border-color:var(--amber);color:#000 }
          .cat-edit-desc { background:var(--bg2);border:1px solid var(--amber);color:var(--text1);
                           padding:3px 6px;font-size:10px;width:100%;box-sizing:border-box;border-radius:2px }
          .cat-rel-row td { color:var(--text2);font-size:10px }
          .cat-rel-from { color:var(--cyan);font-family:var(--font-mono) }
          .cat-rel-to   { color:var(--green,#39ff14);font-family:var(--font-mono) }
        `;
        document.head.appendChild(s);
      }

      catReload();
    }

    export async function catReload() {
      const layer = state._catFilter.layer;
      const qs    = layer ? `?layer=${layer}` : '';
      document.getElementById('cat-status').textContent = 'Cargando…';
      try {
        const r   = await fetch(`/api/catalog${qs}`);
        state._catData  = await r.json();
        const nDs = Object.keys(state._catData.datasets || {}).length;
        const nCo = Object.values(state._catData.datasets || {}).reduce((a,d)=>a+d.columns.length,0);
        const nRe = (state._catData.relationships || []).length;
        document.getElementById('cat-status').textContent =
          `${nDs} datasets · ${nCo} columnas · ${nRe} relaciones`;
        catRender();
      } catch(e) {
        document.getElementById('cat-status').textContent = 'Error: ' + e.message;
      }
    }

    export function catSwitchTab(tab) {
      state._catTab = tab;
      document.getElementById('cat-tab-cols').classList.toggle('active', tab==='cols');
      document.getElementById('cat-tab-rels').classList.toggle('active', tab==='rels');
      document.getElementById('cat-cols-panel').style.display = tab==='cols' ? '' : 'none';
      document.getElementById('cat-rels-panel').style.display = tab==='rels' ? '' : 'none';
      catRender();
    }

    export function catRender() {
      if (!state._catData) return;
      if (state._catTab === 'cols') catRenderCols();
      else catRenderRels();
    }

    export function catRenderCols() {
      const search = (state._catFilter.search || '').toLowerCase();
      const wrap   = document.getElementById('cat-table-wrap');
      if (!wrap) return;

      const datasets = state._catData.datasets || {};
      let rows = '';
      let totalVisible = 0;

      for (const [dsName, ds] of Object.entries(datasets)) {
        const cols = ds.columns.filter(c =>
          !search ||
          dsName.toLowerCase().includes(search) ||
          c.name.toLowerCase().includes(search) ||
          (c.description||'').toLowerCase().includes(search)
        );
        if (!cols.length) continue;

        rows += `<tr class="cat-ds-row">
          <td colspan="7" style="padding:8px 10px">
            <span style="opacity:.5;font-size:9px;margin-right:6px">${esc(ds.layer)}</span>
            ${esc(dsName)}
            <span style="font-size:9px;color:var(--text3);margin-left:6px">${ds.description?esc(ds.description.slice(0,80)):''}</span>
          </td>
        </tr>`;

        totalVisible += cols.length;
        for (const col of cols) {
          const editId = `${dsName}||${col.name}`;
          const isEditing = state._catEditing && state._catEditing.dataset===dsName && state._catEditing.col===col.name;
          const tagsHtml  = (col.tags||[]).map(t=>`<span class="cat-tag">${esc(t)}</span>`).join('');

          rows += `<tr id="cat-row-${CSS.escape(editId)}">
            <td style="color:var(--text3);font-family:var(--font-mono);font-size:9px;padding-left:18px">${esc(col.name)}</td>
            <td style="color:var(--text3);font-size:9px">${esc(col.type||'')}</td>
            <td style="max-width:300px;white-space:normal" id="cat-desc-cell-${CSS.escape(editId)}">
              ${isEditing
                ? `<input class="cat-edit-desc" id="cat-desc-inp-${CSS.escape(editId)}"
                     value="${esc(col.description||'')}"
                     onblur="catSaveDesc(${escJsArg(dsName)},${escJsArg(col.name)})"
                     onkeydown="if(event.key==='Enter')catSaveDesc(${escJsArg(dsName)},${escJsArg(col.name)});if(event.key==='Escape')catCancelEdit()">`
                : `<span style="cursor:pointer;color:${col.description?'var(--text1)':'var(--text3)'}"
                       onclick="catStartEdit(${escJsArg(dsName)},${escJsArg(col.name)})"
                       title="Click para editar">${esc(col.description||'— agregar descripción')}</span>`}
            </td>
            <td>${tagsHtml}</td>
            <td>
              <span class="cat-flag ${col.is_key?'on':''}"
                    onclick="catToggleFlag(${escJsArg(dsName)},${escJsArg(col.name)},'is_key',${!col.is_key})"
                    title="Clave de join">K</span>
            </td>
            <td>
              <span class="cat-flag ${col.is_metric?'on':''}"
                    onclick="catToggleFlag(${escJsArg(dsName)},${escJsArg(col.name)},'is_metric',${!col.is_metric})"
                    title="Métrica de negocio">M</span>
            </td>
            <td>
              <button class="btn btn-sm" style="padding:1px 6px;font-size:9px"
                onclick="catAddTagPrompt(${escJsArg(dsName)},${escJsArg(col.name)})">+tag</button>
            </td>
          </tr>`;
        }
      }

      if (!rows) {
        wrap.innerHTML = `<div class="empty-card">Sin columnas${search?' para la búsqueda "'+esc(search)+'"':''}</div>`;
        return;
      }

      wrap.innerHTML = `
        <div style="font-size:9px;color:var(--text3);padding:6px 10px;border-bottom:1px solid var(--border)">
          ${totalVisible} columnas · click en descripción para editar · K=join key · M=métrica
        </div>
        <div style="max-height:60vh;overflow-y:auto">
          <table class="cat-table">
            <thead><tr>
              <th>COLUMNA</th><th>TIPO</th><th style="min-width:200px">DESCRIPCIÓN</th>
              <th>TAGS</th><th>KEY</th><th>MTR</th><th></th>
            </tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>`;
    }

    export function catRenderRels() {
      const wrap = document.getElementById('cat-rels-wrap');
      if (!wrap) return;
      const rels = state._catData?.relationships || [];
      if (!rels.length) {
        wrap.innerHTML = `<div class="empty-card" style="padding:20px">Sin relaciones registradas</div>`;
        return;
      }
      wrap.innerHTML = `
        <div style="max-height:60vh;overflow-y:auto">
          <table class="cat-table">
            <thead><tr>
              <th>ORIGEN</th><th></th><th>DESTINO</th><th>JOIN</th>
              <th style="min-width:180px">DESCRIPCIÓN</th><th>TRANSFORM</th>
            </tr></thead>
            <tbody>
              ${rels.map(r=>`<tr class="cat-rel-row">
                <td class="cat-rel-from">${esc(r.from_dataset)}<br><span style="color:var(--text3)">.${esc(r.from_column)}</span></td>
                <td style="color:var(--text3);font-size:14px">→</td>
                <td class="cat-rel-to">${esc(r.to_dataset)}<br><span style="color:var(--text3)">.${esc(r.to_column)}</span></td>
                <td><span style="font-size:9px;padding:1px 6px;background:rgba(0,255,200,.1);
                    color:var(--cyan);border-radius:2px">${esc(r.join_hint||'LEFT')}</span></td>
                <td style="white-space:normal;max-width:220px">${esc(r.description||'')}</td>
                <td style="font-family:var(--font-mono);font-size:9px;color:var(--text3);
                    white-space:normal;max-width:200px">${esc(r.transform||'')}</td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>`;
    }

    export function catStartEdit(dataset, col) {
      state._catEditing = { dataset, col };
      catRenderCols();
      const id  = `${dataset}||${col}`;
      const inp = document.getElementById(`cat-desc-inp-${CSS.escape(id)}`);
      if (inp) { inp.focus(); inp.select(); }
    }

    export function catCancelEdit() {
      state._catEditing = null;
      catRenderCols();
    }

    export async function catSaveDesc(dataset, col) {
      const id  = `${dataset}||${col}`;
      const inp = document.getElementById(`cat-desc-inp-${CSS.escape(id)}`);
      if (!inp) return;
      const desc = inp.value.trim();
      state._catEditing = null;

      // Optimistic update
      const ds = state._catData?.datasets?.[dataset];
      const c  = ds?.columns?.find(x => x.name === col);
      if (c) c.description = desc;
      catRenderCols();

      await fetch('/api/catalog/entries', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ entries:[{ dataset, column_name:col, description:desc }] })
      });
    }

    export async function catToggleFlag(dataset, col, flag, value) {
      const ds = state._catData?.datasets?.[dataset];
      const c  = ds?.columns?.find(x => x.name === col);
      if (c) c[flag] = value;
      catRenderCols();

      await fetch('/api/catalog/entries', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ entries:[{ dataset, column_name:col, [flag]:value }] })
      });
    }

    export async function catAddTagPrompt(dataset, col) {
      const tag = prompt('Agregar tag (ej: pnl, metrica, join_key, tiempo):');
      if (!tag) return;
      const ds = state._catData?.datasets?.[dataset];
      const c  = ds?.columns?.find(x => x.name === col);
      if (c) { c.tags = [...new Set([...(c.tags||[]), tag.trim()])]; }
      catRenderCols();

      await fetch('/api/catalog/entries', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ entries:[{ dataset, column_name:col, tags: c?.tags||[tag.trim()] }] })
      });
    }

    export function catAddRelModal() {
      ['rel-from-ds','rel-from-col','rel-to-ds','rel-to-col','rel-desc','rel-transform']
        .forEach(id => { const el=document.getElementById(id); if(el) el.value=''; });
      document.getElementById('cat-rel-modal').style.display = 'flex';
      document.getElementById('rel-from-ds').focus();
    }

    export async function catSaveRel() {
      const get = id => (document.getElementById(id)?.value||'').trim();
      const body = {
        from_dataset: get('rel-from-ds'), from_column: get('rel-from-col'),
        to_dataset:   get('rel-to-ds'),   to_column:   get('rel-to-col'),
        join_hint:    get('rel-join'),     description: get('rel-desc'),
        transform:    get('rel-transform') || undefined,
      };
      if (!body.from_dataset || !body.from_column || !body.to_dataset || !body.to_column) {
        alert('Completa los 4 campos de dataset y columna'); return;
      }
      await fetch('/api/catalog/relationships', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(body)
      });
      document.getElementById('cat-rel-modal').style.display = 'none';
      await catReload();
      catSwitchTab('rels');
    }

    export function askSemanticHelp() {
      document.getElementById('ai-input').value =
        'Revisa el data catalog con get_data_catalog y enriquece las descripciones de las columnas más importantes usando upsert_catalog_entries. Enfócate en columnas sin descripción de los datasets gold y silver más usados en análisis.';
      aiSend();
    }

    // ── AI Chat Persistence ────────────────────────────────────────────────────


    export function _chatKey(cartridgeId, step) {
      return `studio_chat_${cartridgeId}_step${step}`;
    }

    export function _saveChatHistory() {
      const id   = state._currentCartridge?.id;
      const step = state.currentStep;
      if (!id || !step) return;
      // Collect rendered messages from DOM (skip hints/typing)
      const msgs = [];
      document.querySelectorAll('#ai-chat .ai-msg').forEach(el => {
        const role = el.classList.contains('ai-user') ? 'user' : 'assistant';
        const body = el.querySelector('.ai-body');
        msgs.push({ role, html: body ? body.innerHTML : '' });
      });
      try {
        localStorage.setItem(_chatKey(id, step), JSON.stringify({
          messages: state.aiHistory,
          rendered: msgs,
          ts: Date.now(),
        }));
      } catch(e) { /* storage full — silently skip */ }
    }

    export function _restoreChatHistory() {
      const id   = state._currentCartridge?.id;
      const step = state.currentStep;
      if (!id || !step) return false;
      try {
        const raw = localStorage.getItem(_chatKey(id, step));
        if (!raw) return false;
        const saved = JSON.parse(raw);
        if (!saved || (Date.now() - saved.ts) > state._CHAT_TTL_MS) {
          localStorage.removeItem(_chatKey(id, step));
          return false;
        }
        state.aiHistory = saved.messages || [];
        const chat = document.getElementById('ai-chat');
        (saved.rendered || []).forEach(({ role, html }) => {
          const div  = document.createElement('div');
          div.className = `ai-msg ai-${role}`;
          const label = role === 'user' ? 'TÚ' : '◈ MOD·AI';
          div.innerHTML = `<span class="ai-role">${esc(label)}</span><div class="ai-body">${sanitizeAiHtml(html)}</div>`;
          chat.appendChild(div);
        });
        chat.scrollTop = chat.scrollHeight;
        // Show restore notice
        const notice = document.createElement('div');
        notice.className = 'ai-hint';
        notice.style.cssText = 'color:var(--text3);font-size:9px;text-align:center;padding:4px 0';
        const age = Math.round((Date.now() - saved.ts) / 60000);
        notice.textContent = `↑ historial restaurado (hace ${age} min) — 🗑 para limpiar`;
        chat.appendChild(notice);
        chat.scrollTop = chat.scrollHeight;
        return true;
      } catch(e) { return false; }
    }

    export function aiClearHistory() {
      const id   = state._currentCartridge?.id;
      const step = state.currentStep;
      state.aiHistory = [];
      if (id && step) localStorage.removeItem(_chatKey(id, step));
      const chat = document.getElementById('ai-chat');
      const hint = state.STEP_HINTS[step] || '';
      chat.innerHTML = hint ? `<div class="ai-hint">${esc(hint)}</div>` : '';
    }

    // ── AI Assistant ───────────────────────────────────────────────────────────

    export function aiMakeTrail() {
      const chat = document.getElementById('ai-chat');
      const div  = document.createElement('div');
      div.className = 'ai-trail';
      const tk = document.createElement('div');
      tk.className = 'trail-row thinking';
      tk.textContent = 'Pensando…';
      div.appendChild(tk);
      chat.appendChild(div);
      chat.scrollTop = chat.scrollHeight;
      return { container: div, thinking: tk };
    }

    export function aiTrailUse(trail, evt) {
      if (trail.thinking) { trail.thinking.remove(); trail.thinking = null; }
      const row = document.createElement('div');
      row.className = 'trail-row use';
      const argStr = aiFormatArgs(evt.args);
      row.innerHTML = `<span class="trail-arrow">▸</span><span class="tool">${esc(evt.tool)}</span>${argStr ? `<span class="args">${esc(argStr)}</span>` : ''}`;
      trail.container.appendChild(row);
      const chat = document.getElementById('ai-chat');
      chat.scrollTop = chat.scrollHeight;
    }

    export function aiTrailResult(trail, evt) {
      const row = document.createElement('div');
      row.className = 'trail-row result';
      row.innerHTML = `<span class="trail-arrow">↳</span><span class="summary">${esc(evt.summary || 'ok')}</span>`;
      trail.container.appendChild(row);
      const chat = document.getElementById('ai-chat');
      chat.scrollTop = chat.scrollHeight;
    }

    export function aiTrailDone(trail, totalSteps) {
      if (totalSteps === 0) {
        trail.container.remove();
        return;
      }
      const tog = document.createElement('button');
      tog.className = 'trail-toggle';
      tog.textContent = `▾ ocultar razonamiento (${totalSteps} pasos)`;
      let collapsed = false;
      tog.onclick = () => {
        collapsed = !collapsed;
        trail.container.classList.toggle('collapsed', collapsed);
        tog.textContent = collapsed
          ? `▸ ver razonamiento (${totalSteps} pasos)`
          : `▾ ocultar razonamiento (${totalSteps} pasos)`;
      };
      trail.container.parentNode.insertBefore(tog, trail.container.nextSibling);
    }

    export function aiFormatArgs(args) {
      if (!args || typeof args !== 'object') return '';
      const keys = Object.keys(args);
      if (!keys.length) return '';
      const parts = keys.slice(0, 3).map(k => {
        let v = args[k];
        if (typeof v === 'object') v = JSON.stringify(v);
        v = String(v);
        if (v.length > 40) v = v.slice(0, 37) + '…';
        return `${k}=${v}`;
      });
      const more = keys.length > 3 ? ` (+${keys.length - 3})` : '';
      return parts.join(', ') + more;
    }

    export async function aiSend() {
      if (state.aiBusy) return;
      const input = document.getElementById('ai-input');
      const msg   = input.value.trim();
      if (!msg) return;
      input.value = '';
      aiAutogrow(input);
      state.aiBusy = true;

      aiAppend('user', msg);
      const trail = aiMakeTrail();
      let toolSteps = 0;

      try {
        const r = await fetch('/studio/chat/stream', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            message:      msg,
            history:      state.aiHistory,
            step:         state.currentStep,
            cartridge_id: state._currentCartridge?.id || null,
          }),
        });

        if (!r.ok) {
          trail.container.remove();
          let errMsg = `Error del servidor (${r.status})`;
          try {
            const errBody = await r.json();
            const detail  = errBody?.detail || errBody?.error || errBody?.message;
            if (detail) errMsg = detail;
          } catch(_) {
            const text = await r.text().catch(() => '');
            if (text.toLowerCase().includes('credit')) errMsg = 'Sin créditos en la cuenta Anthropic — recarga en console.anthropic.com';
            else if (text.toLowerCase().includes('quota')) errMsg = 'Cuota de API agotada — revisa tu cuenta Anthropic';
            else if (text) errMsg = text.slice(0, 200);
          }
          aiAppend('assistant', `⚠ ${errMsg}`);
          state.aiBusy = false;
          return;
        }

        const reader  = r.body.getReader();
        const decoder = new TextDecoder();
        let buffer        = '';
        let finalReply    = '';
        let finalMessages = null;
        let finalUrls     = [];
        let errored       = null;

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const blocks = buffer.split('\n\n');
          buffer = blocks.pop() || '';
          for (const block of blocks) {
            if (!block.trim()) continue;
            const lines = block.split('\n');
            let event = '', data = '';
            for (const line of lines) {
              if      (line.startsWith('event: ')) event = line.slice(7).trim();
              else if (line.startsWith('data: '))  data += line.slice(6);
            }
            if (!data) continue;
            let payload;
            try { payload = JSON.parse(data); } catch { continue; }
            if      (event === 'tool_use')    { aiTrailUse(trail, payload); toolSteps += 1; }
            else if (event === 'tool_result') { aiTrailResult(trail, payload); }
            else if (event === 'done')        {
              finalReply    = payload.reply || '';
              finalMessages = payload.messages;
              finalUrls     = payload.viewer_urls || [];
            }
            else if (event === 'error')       { errored = payload.message || 'unknown error'; }
          }
        }

        aiTrailDone(trail, toolSteps);

        if (errored) {
          aiAppend('assistant', `⚠ ${errored}`);
          state.aiBusy = false;
          return;
        }

        state.aiHistory = finalMessages || state.aiHistory;
        aiAppend('assistant', finalReply || '(sin respuesta)');
        _saveChatHistory();
        if (state._currentCartridge?.id) {
          const cr = await fetch(`/studio/cartridges/${encodeURIComponent(state._currentCartridge.id)}`);
          if (cr.ok) { state._currentCartridge = await cr.json(); _updateCartridgeInfo(); }
        }
        finalUrls.forEach(({ url }) => window.open(url, '_blank'));
      } catch(e) {
        trail.container.remove();
        aiAppend('assistant', `⚠ Sin conexión con el servidor — verifica que los servicios estén corriendo.`);
      }

      state.aiBusy = false;
    }

    export function aiKey(e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); aiSend(); }
    }

    export function aiAutogrow(el) {
      el.style.height = 'auto';
      el.style.height = Math.min(el.scrollHeight, 200) + 'px';
    }

    export function aiAppend(role, text) {
      const chat = document.getElementById('ai-chat');
      const div  = document.createElement('div');
      div.className = `ai-msg ai-${role}`;
      if (role === 'user') {
        div.innerHTML = `<span class="ai-role">TÚ</span><div class="ai-body">${esc(text)}</div>`;
      } else {
        div.innerHTML = `<span class="ai-role">◈ MOD·AI</span><div class="ai-body">${renderAiText(text)}</div>`;
      }
      chat.appendChild(div);
      chat.scrollTop = chat.scrollHeight;
    }

    export function aiAppendTyping() {
      const chat = document.getElementById('ai-chat');
      const div  = document.createElement('div');
      div.className = 'ai-typing';
      div.textContent = '●●●';
      chat.appendChild(div);
      chat.scrollTop = chat.scrollHeight;
      return div;
    }

    export function renderAiText(text) {
      return esc(text)
        .replace(/```[\w]*\n?([\s\S]*?)```/g, '<pre>$1</pre>')
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong style="color:var(--amber)">$1</strong>')
        .replace(/\n/g, '<br>');
    }

    export function sanitizeAiHtml(html) {
      const tpl = document.createElement('template');
      tpl.innerHTML = String(html || '');
      const allowed = new Set(['BR', 'CODE', 'PRE', 'STRONG']);
      tpl.content.querySelectorAll('*').forEach(node => {
        if (!allowed.has(node.tagName)) {
          node.replaceWith(document.createTextNode(node.textContent || ''));
          return;
        }
        [...node.attributes].forEach(attr => {
          if (node.tagName !== 'STRONG' || attr.name !== 'style' || attr.value !== 'color:var(--amber)') {
            node.removeAttribute(attr.name);
          }
        });
      });
      return tpl.innerHTML;
    }

    // ── Utils ──────────────────────────────────────────────────────────────────

    export function esc(s) {
      return String(s || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
    }

    export function escJsArg(s) {
      return JSON.stringify(String(s || '')).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026');
    }

    export function fmt(iso) {
      return iso ? iso.replace('T',' ').substring(0, 16) : '—';
    }

    // ── Step 2: DAG Editor ────────────────────────────────────────────────────


    export function _dagCartridge() {
      return state._currentCartridge?.id || '';
    }

    export function renderDagEditor() {
      const el = document.getElementById('step-content');
      el.style.padding = '0';   // remove default padding so editor fills edge-to-edge
      el.innerHTML = `
        <div class="dag-editor-step">
          <div class="dag-editor-layout">

            <!-- Sidebar -->
            <div class="dag-sidebar">
              <div class="dag-sidebar-hdr">
                ⚙ DAGS
                <button class="btn-sm" style="margin-left:auto" onclick="loadDags()" title="Recargar">↺</button>
                <button class="btn-sm" style="color:var(--green);border-color:var(--green)" onclick="newDag()" title="Nuevo DAG">+</button>
              </div>
              <div id="dag-list" class="dag-list empty-state">
                <div style="padding:20px;text-align:center;color:var(--text3);font-size:11px">Cargando…</div>
              </div>
              <div class="dag-sidebar-hdr" style="cursor:pointer;border-top:1px solid var(--border)"
                   onclick="toggleDagTemplates()" title="Plantillas de DAG">
                ◈ PLANTILLAS
                <span id="tpl-toggle-s" style="margin-left:auto;color:var(--text3);font-size:9px">▶</span>
              </div>
              <div id="tpl-list-s" class="templates-list plantillas" style="display:block">
                <div style="padding:20px;text-align:center;color:var(--text3);font-size:11px">Plantillas listas…</div>
              </div>
            </div>

            <!-- Code panel -->
            <div class="dag-code-panel" id="dag-code-panel">
              <div class="dag-empty-state empty-state" id="dag-empty-state">
                Selecciona un DAG o usa + para crear uno nuevo
              </div>
              <div id="dag-editor-body" style="display:none;flex-direction:column;flex:1;min-height:0">
                <div class="dag-code-toolbar">
                  <span class="dag-code-name" id="dag-editor-name">—</span>
                  <span id="dag-editor-badge"></span>
                  <span id="dag-dirty-badge" style="display:none;font-size:9px;color:var(--amber);font-family:var(--font-ui);margin-left:4px" title="Cambios sin desplegar">● modificado</span>
                  <span style="margin-left:auto;font-size:9px;color:var(--text3);font-family:var(--font-ui)">
                    Tab=indent &nbsp;·&nbsp; Ctrl+S=deploy
                  </span>
                  <a id="dag-airflow-link" href="#" target="_blank" class="btn btn-sm"
                     style="color:var(--amber);border-color:var(--amber);text-decoration:none"
                     title="Ver en Airflow UI">◈ Airflow</a>
                  <button class="btn btn-sm" id="btn-dag-graph" onclick="toggleDagGraph()"
                          style="color:var(--cyan);border-color:var(--cyan);background:rgba(0,230,230,.1)" title="Ocultar/mostrar grafo">⬡ Grafo</button>
                </div>
                <div id="dag-split-container" style="display:flex;flex:1;min-height:0;overflow:hidden">
                  <div id="dag-graph-panel" class="dag-graph-panel" style="width:45%;overflow:auto;flex-shrink:0">
                    <div style="padding:20px;color:var(--text3);font-size:11px;text-align:center">Cargando grafo…</div>
                  </div>
                  <div id="dag-resize-handle"
                       style="width:5px;flex-shrink:0;cursor:col-resize;background:var(--border);
                              transition:background .15s;position:relative;z-index:10"
                       onmouseenter="this.style.background='var(--amber)'"
                       onmouseleave="this.style.background=state._dagResizing?'var(--amber)':'var(--border)'"
                       onmousedown="dagResizeStart(event)">
                    <div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);
                                color:var(--text3);font-size:9px;letter-spacing:0;user-select:none">⋮</div>
                  </div>
                  <div class="dag-editor-wrap" id="dag-editor-wrap" style="flex:1;min-width:0">
                    <div class="dag-line-numbers" id="dag-line-numbers"></div>
                    <textarea id="dag-code-textarea" name="code" class="dag-code-textarea code-editor" spellcheck="false"
                              placeholder="# Código del DAG Python"
                              onkeydown="dagEditorKeydown(event)"
                              oninput="dagEditorOnInput()"
                              onscroll="dagSyncLineScroll()"></textarea>
                  </div>
                </div>
                <div class="dag-deploy-bar">
                  <button class="btn" style="color:var(--green);border-color:var(--green)"
                          onclick="deployDag()" id="btn-deploy">▶ Deploy a Airflow</button>
                  <button class="btn btn-sm" onclick="copyDagCode()" title="Copiar código">⎘ Copiar</button>
                  <button class="btn btn-sm" style="color:var(--cyan);border-color:var(--cyan)"
                          onclick="sendDagToAssistantStudio()" title="Enviar al asistente">✎ Asistente</button>
                  <button class="btn btn-sm" style="color:var(--amber);border-color:var(--amber)"
                          onclick="renameDag()" title="Renombrar DAG">✎ Renombrar</button>
                  <button class="btn btn-sm" style="color:#ff2d55;border-color:#ff2d55"
                          onclick="deleteDag()" title="Eliminar DAG">✕ Eliminar</button>
                  <span class="deploy-msg" id="deploy-msg"></span>
                </div>
              </div>
            </div>

          </div>
        </div>`;

      _showDagEditor();
      state._selectedDag = state._selectedDag || `${_dagCartridge() || 'replicon'}_extract`;
      const nameEl = document.getElementById('dag-editor-name');
      const badgeEl = document.getElementById('dag-editor-badge');
      const afLink = document.getElementById('dag-airflow-link');
      if (nameEl) nameEl.textContent = state._selectedDag;
      if (badgeEl) badgeEl.innerHTML = '<span class="dag-badge dag-paused">preview</span>';
      if (afLink) afLink.href = `http://localhost:8082/dags/${encodeURIComponent(state._selectedDag)}/grid`;
      dagSetEditorCode(
        `from airflow import DAG\n` +
        `from airflow.operators.empty import EmptyOperator\n\n` +
        `with DAG(dag_id='${state._selectedDag}', schedule=None, catchup=False) as dag:\n` +
        `    start = EmptyOperator(task_id='start')\n`
      );

      loadDags();
    }

    // Restore step-content padding when leaving step 2
    export function _showDagEditor() {
      document.getElementById('dag-empty-state').style.display = 'none';
      document.getElementById('dag-editor-body').style.display = 'flex';
    }

    export async function loadDags() {
      const list = document.getElementById('dag-list');
      if (!list) return;
      list.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text3);font-size:11px">Cargando…</div>';
      const cartridge = _dagCartridge();
      try {
        const r = await fetch('/api/mcp/invoke', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ server: 'infra', tool: 'airflow_list_dags', args: {} }),
        });
        const d = await r.json();
        const allDags = (d.result?.dags || []).sort((a, b) => a.dag_id.localeCompare(b.dag_id));
        state._dagsCache = cartridge
          ? allDags.filter(dag => dag.dag_id.startsWith(cartridge + '_'))
          : allDags;

        if (!state._dagsCache.length) {
          list.innerHTML = `<div style="padding:20px;text-align:center;color:var(--text3);font-size:11px">
            Sin DAGs${cartridge ? ' para <b>'+esc(cartridge)+'</b>' : ' en Airflow'}<br><br>
            <span style="font-size:9px">Usa + para crear uno o el asistente para generarlo.</span></div>`;
          return;
        }
        list.innerHTML = state._dagsCache.map(dag => {
          const statusDot = dag.is_paused
            ? `<span style="color:#555">● pausado</span>`
            : `<span style="color:var(--green)">● activo</span>`;
          const prefix = dag.dag_id.split('_')[0];
          const badge = prefix !== dag.dag_id
            ? `<span style="font-size:8px;color:var(--text3);margin-left:4px">[${esc(prefix)}]</span>`
            : '';
          return `<div class="dag-sidebar-item ${state._selectedDag === dag.dag_id ? 'selected' : ''}"
                       id="dagitem-${esc(dag.dag_id)}"
                       onclick="selectDag(${escJsArg(dag.dag_id)})">
            <div class="dag-item-id">${esc(dag.dag_id)}${badge}</div>
            <div class="dag-item-meta">${statusDot}</div>
          </div>`;
        }).join('');

        // Auto-select: state._selectedDag if exists, else first
        const toSelect = (state._selectedDag && state._dagsCache.find(d => d.dag_id === state._selectedDag))
          ? state._selectedDag : state._dagsCache[0]?.dag_id;
        if (toSelect) await selectDag(toSelect);
      } catch(e) {
        list.innerHTML = `<div style="padding:16px;color:#ff2d55;font-size:11px">Error: ${esc(e.message)}</div>`;
      }
    }

    export async function selectDag(dagId) {
      state._selectedDag = dagId;
      document.querySelectorAll('.dag-sidebar-item').forEach(el => {
        el.classList.toggle('selected', el.id === `dagitem-${dagId}`);
      });

      _showDagEditor();

      const textarea = document.getElementById('dag-code-textarea');
      const nameEl   = document.getElementById('dag-editor-name');
      const badgeEl  = document.getElementById('dag-editor-badge');
      const afLink   = document.getElementById('dag-airflow-link');
      if (!textarea || !nameEl) return;

      nameEl.textContent = dagId;
      if (afLink) afLink.href = `http://localhost:8082/dags/${encodeURIComponent(dagId)}/grid`;

      const dag = state._dagsCache.find(d => d.dag_id === dagId);
      if (dag && badgeEl) {
        badgeEl.innerHTML = dag.is_paused
          ? `<span class="dag-badge dag-paused">pausado</span>`
          : `<span class="dag-badge dag-active">activo</span>`;
      }

      dagSetEditorCode('# Cargando fuente…');
      setDeployMsg('', '');

      try {
        const r = await fetch('/api/mcp/invoke', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            server: 'infra', tool: 'dag_get_source',
            args: { cartridge_id: _dagCartridge(), dag_id: dagId },
          }),
        });
        const d = await r.json();
        if (d.result?.found && d.result?.source_code) {
          dagSetEditorCode(d.result.source_code);
          return;
        }
      } catch(_) {}

      dagSetEditorCode(
        `# Fuente no encontrada en BD para "${dagId}".\n` +
        `# El asistente puede regenerarla con infra__airflow_create_dag.\n`
      );
    }

    export function newDag() {
      state._selectedDag = '__new__';
      document.querySelectorAll('.dag-sidebar-item').forEach(el => el.classList.remove('selected'));
      _showDagEditor();
      const cartridge = _dagCartridge();
      const nameEl = document.getElementById('dag-editor-name');
      const badgeEl = document.getElementById('dag-editor-badge');
      const afLink  = document.getElementById('dag-airflow-link');
      if (nameEl) nameEl.textContent = 'nuevo_dag';
      if (badgeEl) badgeEl.innerHTML = '';
      if (afLink) afLink.href = '#';
      dagSetEditorCode(
        `# Nuevo DAG — ${cartridge}\n` +
        `# dag_id recomendado: ${cartridge}_<entidad>_<modo>\n\n` +
        `from airflow import DAG\n` +
        `from airflow.operators.python import PythonOperator\n` +
        `from datetime import datetime\n\n` +
        `with DAG(\n` +
        `    dag_id='${cartridge}_nueva_entidad',\n` +
        `    start_date=datetime(2024, 1, 1),\n` +
        `    schedule=None,\n` +
        `    catchup=False,\n` +
        `    tags=['${cartridge}'],\n` +
        `) as dag:\n` +
        `    pass\n`
      );
      setDeployMsg('', '');
    }

    // v1.43.2 (Frontend R2 hardening): cache /api/system/info once so
    // every deploy attempt + every render of the deploy bar share the
    // same dev-mode answer. Pre-R2 the button was unconditionally
    // active and clicking it in production surfaced the raw
    // PermissionError from infra__airflow_create_dag.
    //
    // v1.43.2 (Frontend R3 hardening): cache the failure path too.
    // Pre-R3, an HTTP-level error (401, 5xx) returned False without
    // setting _devModeCache, so every subsequent click would refetch.
    // Functionally fail-closed but defeated the cache contract.
    let _devModeCache = null;
    async function _isDevMode() {
      if (_devModeCache !== null) return _devModeCache;
      try {
        const r = await fetch('/api/system/info', {credentials: 'same-origin'});
        if (!r.ok) { _devModeCache = false; return false; }
        const info = await r.json();
        _devModeCache = !!info.dev_mode;
      } catch { _devModeCache = false; }
      return _devModeCache;
    }

    // v1.43.2 (Frontend R3 hardening): factored gate. Every action that
    // ultimately calls a mcp-infra dev-only tool (airflow_create_dag,
    // airflow_delete_dag, etc) must short-circuit with this in
    // production. Returns true when the call should proceed.
    async function _gateDevOnlyAction(messageEs) {
      if (await _isDevMode()) return true;
      setDeployMsg(messageEs, 'err');
      return false;
    }

    export async function deployDag() {
      // R2 gate: refuse early with a clear, actionable message in
      // production so the user never sees a raw mcp-infra error.
      if (!(await _isDevMode())) {
        const btn = document.getElementById('btn-deploy');
        if (btn) {
          btn.disabled = true;
          btn.title = 'Deploy disabled outside development';
          btn.style.opacity = '0.5';
          btn.style.cursor = 'not-allowed';
        }
        setDeployMsg(
          'Deploy a Airflow está deshabilitado fuera de desarrollo. ' +
          'Usa el pipeline de despliegue o la UI de Airflow.',
          'err',
        );
        return;
      }

      const code = document.getElementById('dag-code-textarea')?.value?.trim();
      if (!code) { setDeployMsg('Sin código', 'err'); return; }

      let dagId = state._selectedDag === '__new__' ? '' : state._selectedDag;
      const m = code.match(/dag_id\s*=\s*['"]([^'"]+)['"]/);
      if (m) dagId = m[1];
      if (!dagId) { setDeployMsg('No se encontró dag_id en el código', 'err'); return; }

      const btn = document.getElementById('btn-deploy');
      if (btn) btn.disabled = true;
      setDeployMsg('Desplegando…', '');

      try {
        const r = await fetch('/api/studio/dag-deploy', {
          method: 'POST',
          headers: jsonHeaders(),
          body: JSON.stringify({
            dag_id: dagId,
            code,
            cartridge_id: _dagCartridge(),
            description: `DAG del cartucho ${_dagCartridge()}`,
          }),
        });
        const d = await r.json();
        if (d.status === 'deployed' || d.result?.created || d.result?.dag_id) {
          setDeployMsg(`✓ Desplegado: ${esc(d.result?.created || d.dag_id || dagId)}`, 'ok');
          state._deployedCode = code;
          dagMarkDirty();
          const nameEl = document.getElementById('dag-editor-name');
          if (nameEl) nameEl.textContent = dagId;
          state._selectedDag = dagId;
          // Optimistic: show the DAG in the list immediately without waiting for Airflow
          if (!state._dagsCache.find(d => d.dag_id === dagId)) {
            state._dagsCache.push({ dag_id: dagId, is_paused: false });
            state._dagsCache.sort((a, b) => a.dag_id.localeCompare(b.dag_id));
            const list = document.getElementById('dag-list');
            if (list) list.innerHTML = state._dagsCache.map(dag => {
              const dot = dag.is_paused ? `<span style="color:#555">● pausado</span>` : `<span style="color:var(--green)">● activo</span>`;
              return `<div class="dag-sidebar-item ${state._selectedDag===dag.dag_id?'selected':''}" id="dagitem-${esc(dag.dag_id)}" onclick="selectDag(${escJsArg(dag.dag_id)})">
                <div class="dag-item-id">${esc(dag.dag_id)}</div>
                <div class="dag-item-meta">${dot}</div></div>`;
            }).join('');
          }
          // Sync with Airflow after scheduler picks up the file (typically 5-30s)
          setTimeout(loadDags, 8000);
        } else {
          setDeployMsg(`Error: ${esc(JSON.stringify(d.result || d))}`, 'err');
        }
      } catch(e) {
        setDeployMsg(`Error: ${esc(e.message)}`, 'err');
      }
      if (btn) btn.disabled = false;
    }

    export async function renameDag() {
      // v1.43.2 (Frontend R3): rename writes a new DAG file via
      // airflow_create_dag — same dev-only restriction as deploy.
      if (!(await _gateDevOnlyAction(
        'Renombrar DAG está deshabilitado fuera de desarrollo.',
      ))) return;
      if (!state._selectedDag || state._selectedDag === '__new__') {
        setDeployMsg('Selecciona un DAG primero', 'err'); return;
      }
      const oldId = state._selectedDag;
      const newId = prompt(`Nuevo nombre para "${oldId}":`, oldId);
      if (!newId || newId === oldId) return;
      if (!/^[a-zA-Z0-9_]+$/.test(newId)) {
        setDeployMsg('Nombre inválido (solo letras, números y _)', 'err'); return;
      }

      const textarea = document.getElementById('dag-code-textarea');
      const code = textarea?.value;
      if (!code) { setDeployMsg('Sin código en editor', 'err'); return; }

      // Replace dag_id in code
      const newCode = code
        .replace(/dag_id\s*=\s*['"][^'"]+['"]/g, `dag_id='${newId}'`);

      setDeployMsg('Renombrando…', '');
      try {
        // Deploy with new dag_id
        const r1 = await fetch('/api/mcp/invoke', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            server: 'infra', tool: 'airflow_create_dag',
            args: { dag_id: newId, code: newCode, cartridge_id: _dagCartridge(),
                    description: `DAG del cartucho ${_dagCartridge()}` },
          }),
        });
        const d1 = await r1.json();
        if (!d1.result?.created && !d1.result?.dag_id) {
          setDeployMsg(`Error al crear: ${esc(JSON.stringify(d1.result || d1))}`, 'err'); return;
        }
        // Delete old dag_id
        await fetch('/api/mcp/invoke', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            server: 'infra', tool: 'airflow_delete_dag',
            args: { dag_id: oldId },
          }),
        });
        setDeployMsg(`✓ Renombrado a ${esc(newId)}`, 'ok');
        state._selectedDag = newId;
        textarea.value = newCode;
        state._deployedCode = newCode;
        dagMarkDirty();

        // Actualiza cache localmente sin esperar a Airflow
        state._dagsCache = state._dagsCache.filter(d => d.dag_id !== oldId);
        if (!state._dagsCache.find(d => d.dag_id === newId)) {
          state._dagsCache.push({ dag_id: newId, is_paused: false, is_active: true, tags: [] });
          state._dagsCache.sort((a, b) => a.dag_id.localeCompare(b.dag_id));
        }
        // Re-render sidebar con el nuevo nombre seleccionado
        const list = document.getElementById('dag-list');
        if (list) {
          list.innerHTML = state._dagsCache.map(dag => {
            const statusDot = dag.is_paused
              ? `<span style="color:#555">● pausado</span>`
              : `<span style="color:var(--green)">● activo</span>`;
            const prefix = dag.dag_id.split('_')[0];
            const badge = prefix !== dag.dag_id
              ? `<span style="font-size:8px;color:var(--text3);margin-left:4px">[${esc(prefix)}]</span>` : '';
            return `<div class="dag-sidebar-item ${dag.dag_id === newId ? 'selected' : ''}"
                         id="dagitem-${esc(dag.dag_id)}"
                         onclick="selectDag(${escJsArg(dag.dag_id)})">
              <div class="dag-item-id">${esc(dag.dag_id)}${badge}</div>
              <div class="dag-item-meta">${statusDot}</div>
            </div>`;
          }).join('');
        }
        document.getElementById('dag-editor-name').textContent = newId;

        // Recarga real desde Airflow después de que el scheduler parsee el archivo
        setTimeout(loadDags, 5000);
      } catch(e) {
        setDeployMsg(`Error: ${esc(e.message)}`, 'err');
      }
    }

    export async function deleteDag() {
      // v1.43.2 (Frontend R3): airflow_delete_dag is gated by
      // _is_development() in mcp-infra — same dev-only contract.
      if (!(await _gateDevOnlyAction(
        'Eliminar DAG está deshabilitado fuera de desarrollo.',
      ))) return;
      if (!state._selectedDag || state._selectedDag === '__new__') {
        setDeployMsg('Selecciona un DAG primero', 'err'); return;
      }
      const dagId = state._selectedDag;
      if (!confirm(`¿Eliminar el DAG "${dagId}"?\nEsto borra el archivo y lo elimina de Airflow.`)) return;

      setDeployMsg('Eliminando…', '');
      try {
        const r = await fetch('/api/mcp/invoke', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            server: 'infra', tool: 'airflow_delete_dag',
            args: { dag_id: dagId },
          }),
        });
        const d = await r.json();
        if (d.result?.deleted_file || d.result?.deleted_db) {
          setDeployMsg(`✓ Eliminado: ${esc(dagId)}`, 'ok');
          state._selectedDag = null;
          document.getElementById('dag-editor-body')?.style && (_hideDagEditor());
          setTimeout(loadDags, 1200);
        } else {
          setDeployMsg(`No se pudo eliminar: ${esc(JSON.stringify(d.result || d))}`, 'err');
        }
      } catch(e) {
        setDeployMsg(`Error: ${esc(e.message)}`, 'err');
      }
    }

    export function _hideDagEditor() {
      const body  = document.getElementById('dag-editor-body');
      const empty = document.getElementById('dag-empty-state');
      if (body)  body.style.display  = 'none';
      if (empty) empty.style.display = 'flex';
    }

    export function dagEditorKeydown(e) {
      const ta    = e.target;
      const start = ta.selectionStart;
      const end   = ta.selectionEnd;
      const val   = ta.value;

      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault();
        deployDag();
        return;
      }
      if (e.key === 'Tab') {
        e.preventDefault();
        if (start === end) {
          const col    = start - val.lastIndexOf('\n', start - 1) - 1;
          const spaces = '    '.slice(col % 4) || '    ';
          _dagInsertAt(ta, start, end, spaces, spaces.length);
        } else {
          const lineStart = val.lastIndexOf('\n', start - 1) + 1;
          const lineEnd   = val.indexOf('\n', end - 1);
          const block     = val.substring(lineStart, lineEnd < 0 ? val.length : lineEnd);
          const replaced  = e.shiftKey
            ? block.replace(/^    /mg, '')
            : block.replace(/^/mg, '    ');
          _dagReplaceBlock(ta, lineStart, lineEnd < 0 ? val.length : lineEnd, replaced);
        }
        dagUpdateLineNumbers();
        dagMarkDirty();
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        const lineStart   = val.lastIndexOf('\n', start - 1) + 1;
        const currentLine = val.substring(lineStart, start);
        const indent      = currentLine.match(/^(\s*)/)[1];
        const extra       = currentLine.trimEnd().endsWith(':') ? '    ' : '';
        const insert      = '\n' + indent + extra;
        _dagInsertAt(ta, start, end, insert, insert.length);
        dagUpdateLineNumbers();
        dagMarkDirty();
      }
    }

    export function _dagInsertAt(ta, start, end, text, cursorOffset) {
      const v = ta.value;
      ta.value = v.substring(0, start) + text + v.substring(end);
      ta.selectionStart = ta.selectionEnd = start + cursorOffset;
    }

    export function _dagReplaceBlock(ta, from, to, text) {
      const v = ta.value;
      ta.value = v.substring(0, from) + text + (to < 0 ? '' : v.substring(to));
      ta.selectionStart = from;
      ta.selectionEnd   = from + text.length;
    }

    export function dagEditorOnInput() { dagUpdateLineNumbers(); dagMarkDirty(); }

    export function dagSyncLineScroll() {
      const ta = document.getElementById('dag-code-textarea');
      const ln = document.getElementById('dag-line-numbers');
      if (ln && ta) ln.scrollTop = ta.scrollTop;
    }

    export function dagUpdateLineNumbers() {
      const ta = document.getElementById('dag-code-textarea');
      const ln = document.getElementById('dag-line-numbers');
      if (!ta || !ln) return;
      const count   = (ta.value.match(/\n/g) || []).length + 1;
      const current = ln.querySelectorAll('span').length;
      if (current === count) return;
      ln.innerHTML = Array.from({length: count}, (_, i) => `<span>${i + 1}</span>`).join('');
    }

    export function dagMarkDirty() {
      const badge = document.getElementById('dag-dirty-badge');
      const ta    = document.getElementById('dag-code-textarea');
      if (badge && ta) badge.style.display = ta.value !== state._deployedCode ? '' : 'none';
    }

    // ── DAG Graph viewer ──────────────────────────────────────────────────────


    export function toggleDagGraph() {
      state._dagGraphVisible = !state._dagGraphVisible;
      const panel = document.getElementById('dag-graph-panel');
      const btn   = document.getElementById('btn-dag-graph');
      if (!panel) return;
      if (state._dagGraphVisible) {
        panel.classList.remove('collapsed');
        if (btn) btn.style.background = 'rgba(0,230,230,.1)';
        _renderDagGraph().catch(()=>{});
      } else {
        panel.classList.add('collapsed');
        if (btn) btn.style.background = '';
      }
    }

    export function _parseDagGraph(code) {
      const tasks = [];
      const edges = [];
      const lines = code.split('\n');
      const varToId = {};

      // ── Pass 1: collect ALL function definitions (name → line, indent)
      const allFns = {};  // name → { line (1-based), indent }
      lines.forEach((line, i) => {
        const m = line.match(/^(\s*)(?:async\s+)?def\s+(\w+)\s*\(/);
        if (m) allFns[m[2]] = { line: i + 1, indent: m[1].length };
      });

      // ── Pass 2: identify task nodes
      let pendingDecorator = false;
      const taskFnNames = new Set();

      lines.forEach((line, i) => {
        const s = line.replace(/#.*$/, '');
        if (/^\s*@task(\s*\(|$|\s)/.test(s)) { pendingDecorator = true; return; }
        if (pendingDecorator) {
          const dm = s.match(/^\s*(?:async\s+)?def\s+(\w+)\s*\(/);
          if (dm) {
            const fn = dm[1];
            taskFnNames.add(fn);
            varToId[fn] = fn;
            tasks.push({ id: fn, varName: fn, op: 'TaskFlow', line: i + 1, calls: [] });
          }
          pendingDecorator = false;
          return;
        }
        // Classic Operator
        const opM = s.match(/^\s*(\w+)\s*=\s*(\w+(?:Operator|Sensor|Branch\w*|ShortCircuit\w*|Trigger\w*))\s*\(/);
        if (opM) {
          const varName = opM[1], opName = opM[2];
          let taskId = varName;
          for (let j = i; j < Math.min(i + 15, lines.length); j++) {
            const tm = lines[j].match(/task_id\s*=\s*['"]([^'"]+)['"]/);
            if (tm) { taskId = tm[1]; break; }
            if (j > i && /^\s*\)/.test(lines[j])) break;
          }
          varToId[varName] = taskId;
          tasks.push({ id: taskId, varName, op: opName, line: i + 1, calls: [] });
        }
      });

      // ── Pass 3: for each task extract its body and find helper calls
      const helperFns = Object.fromEntries(
        Object.entries(allFns).filter(([name]) => !taskFnNames.has(name))
      );
      const helperNames = Object.keys(helperFns);
      const helperRe = new RegExp(`\\b(${helperNames.map(n => n.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('|')})\\s*\\(`, 'g');

      const extractBody = (startLine0) => {
        // startLine0 is 0-based index of the `def` line
        const baseIndent = (lines[startLine0].match(/^(\s*)/) || ['',''])[1].length;
        const body = [];
        for (let i = startLine0 + 1; i < lines.length; i++) {
          const l = lines[i];
          if (l.trim() === '') { body.push(l); continue; }
          const ind = (l.match(/^(\s*)/) || ['',''])[1].length;
          if (ind <= baseIndent) break;
          body.push(l);
        }
        return body;
      };

      if (helperNames.length) {
        tasks.forEach(t => {
          const body = extractBody(t.line - 1);
          const seen = new Set();
          body.forEach(l => {
            let m;
            helperRe.lastIndex = 0;
            while ((m = helperRe.exec(l)) !== null) {
              if (!seen.has(m[1])) {
                seen.add(m[1]);
                t.calls.push({ name: m[1], line: helperFns[m[1]].line });
              }
            }
          });
        });
      }

      // ── Pass 4a: TaskFlow implicit data dependencies
      // Detect: output_var = task_fn()  →  other_task(output_var)
      const outputVarToTask = {};
      lines.forEach(line => {
        const s = line.replace(/#.*$/, '').trim();
        const m = s.match(/^(\w+)\s*=\s*(\w+)\s*\(/);
        if (m && varToId[m[2]]) outputVarToTask[m[1]] = varToId[m[2]];
      });
      lines.forEach(line => {
        const s = line.replace(/#.*$/, '');
        // Find all fn(args) calls on this line
        const re = /(\w+)\s*\(([^)]*)\)/g;
        let m;
        while ((m = re.exec(s)) !== null) {
          const callerFn = m[1], argsStr = m[2];
          const callerId = varToId[callerFn];
          if (!callerId) continue;
          argsStr.split(',').map(a => a.trim()).forEach(arg => {
            const fromTask = outputVarToTask[arg];
            if (fromTask && fromTask !== callerId)
              edges.push([fromTask, callerId]);
          });
        }
      });

      // ── Pass 4b: explicit >> chains
      const resolveToken = raw => {
        const name = raw.replace(/\s*\(.*/, '').trim();
        return varToId[name] || null;
      };
      lines.forEach(line => {
        const s = line.replace(/#.*$/, '');
        if (!s.includes('>>')) return;
        const parts = s.split('>>').map(p => p.trim());
        const resolved = parts.map(p => {
          const lm = p.match(/\[([^\]]+)\]/);
          if (lm) return lm[1].split(',').map(v => resolveToken(v)).filter(Boolean);
          const t = resolveToken(p);
          return t ? [t] : [];
        });
        for (let i = 0; i < resolved.length - 1; i++)
          for (const f of resolved[i])
            for (const t of resolved[i + 1])
              if (f !== t) edges.push([f, t]);
      });

      // Deduplicate edges
      const edgeKey = ([f,t]) => `${f}→${t}`;
      const seen = new Set();
      const dedupEdges = edges.filter(e => { const k = edgeKey(e); return seen.has(k) ? false : seen.add(k); });

      return { tasks, edges: dedupEdges };
    }

    export function _layoutDagGraph(tasks, edges) {
      const taskMap = Object.fromEntries(tasks.map(t => [t.id, t]));
      const ids = tasks.map(t => t.id);
      const inDeg = Object.fromEntries(ids.map(id => [id, 0]));
      const out   = Object.fromEntries(ids.map(id => [id, []]));
      edges.forEach(([f, t]) => { if (inDeg[t] !== undefined) inDeg[t]++; if (out[f]) out[f].push(t); });

      // Topological layering
      const layer = {};
      const queue = ids.filter(id => inDeg[id] === 0);
      queue.forEach(id => layer[id] = 0);
      while (queue.length) {
        const cur = queue.shift();
        (out[cur] || []).forEach(nxt => {
          layer[nxt] = Math.max(layer[nxt] ?? 0, (layer[cur] ?? 0) + 1);
          if (--inDeg[nxt] === 0) queue.push(nxt);
        });
      }
      ids.forEach(id => { if (layer[id] === undefined) layer[id] = 0; });

      const groups = {};
      ids.forEach(id => { const l = layer[id]; (groups[l] = groups[l] || []).push(id); });

      const W = 180, PX = 24, PY = 40;
      const H_BASE = 52, H_CALL = 16;

      // Node heights
      const nodeH = id => H_BASE + ((taskMap[id]?.calls?.length || 0) * H_CALL);

      // Max layer width (for centering)
      const layerWidth = l => groups[l].length * W + (groups[l].length - 1) * PX;
      const maxW = Math.max(...Object.keys(groups).map(l => layerWidth(l)));
      const svgInnerW = maxW + 2 * PX;

      // Assign positions: vertical flow, centered horizontally
      const pos = {};
      let yOff = PY;
      Object.keys(groups).sort((a,b) => a-b).forEach(l => {
        const nodes = groups[l];
        const totalW = layerWidth(l);
        const xStart = (svgInnerW - totalW) / 2;
        const layerH = Math.max(...nodes.map(id => nodeH(id)));
        nodes.forEach((id, i) => {
          const h = nodeH(id);
          pos[id] = { x: xStart + i * (W + PX), y: yOff + (layerH - h) / 2, w: W, h };
        });
        yOff += layerH + PY;
      });

      return { pos, svgInnerW, svgH: yOff };
    }

    export async function _renderDagGraph() {
      const panel = document.getElementById('dag-graph-panel');
      if (!panel) return;
      const code = document.getElementById('dag-code-textarea')?.value || '';
      panel.innerHTML = '<div style="padding:20px;color:var(--text3);font-size:11px;text-align:center">Analizando…</div>';
      let tasks = [], edges = [];
      try {
        const r = await fetch('/api/dags/parse', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ source: code }),
        });
        const d = await r.json();
        if (d.error) { panel.innerHTML = `<div style="padding:16px;color:#ff2d55;font-size:11px">Syntax error: ${esc(d.error)}</div>`; return; }
        tasks = d.tasks || [];
        edges = d.edges || [];
      } catch(e) {
        panel.innerHTML = `<div style="padding:16px;color:#ff2d55;font-size:11px">Error: ${esc(e.message)}</div>`; return;
      }

      if (!tasks.length) {
        panel.innerHTML = '<div style="padding:16px;color:var(--text3);font-size:11px;text-align:center">No se detectaron tareas (Operator/Sensor) en este DAG</div>';
        return;
      }

      const { pos, svgInnerW, svgH } = _layoutDagGraph(tasks, edges);
      const svgW = svgInnerW;

      // Operator color palette
      const opColor = op => ({
        PythonOperator:   '#1e3a20',
        BashOperator:     '#1a2a4a',
        PostgresOperator: '#0d2a22',
        HttpOperator:     '#2a1a3a',
        BranchPythonOperator: '#3a2a10',
        ShortCircuitOperator: '#2a1010',
      }[op] || '#1a1a22');
      const opLabel = op => op.replace('Operator','').replace('Sensor','⚡').replace('Branch','⑂ ');

      // Edges — vertical flow: exit bottom-center, enter top-center
      let edgeSvg = '';
      edges.forEach(([f, t]) => {
        const fp = pos[f], tp = pos[t];
        if (!fp || !tp) return;
        const x1 = fp.x + fp.w / 2, y1 = fp.y + fp.h;
        const x2 = tp.x + tp.w / 2, y2 = tp.y;
        const cy = (y1 + y2) / 2;
        edgeSvg += `<path d="M${x1},${y1} C${x1},${cy} ${x2},${cy} ${x2},${y2}" stroke="#555" stroke-width="1.5" fill="none" marker-end="url(#arr)"/>`;
      });

      // Nodes
      let nodeSvg = '';
      tasks.forEach(t => {
        const p = pos[t.id];
        if (!p) return;
        const label = t.id.length > 22 ? t.id.slice(0, 20) + '…' : t.id;
        const bg    = opColor(t.op);

        // Header: operator type + task name
        let inner = `
          <rect x="${p.x}" y="${p.y}" width="${p.w}" height="${p.h}" rx="4"
                fill="${bg}" stroke="#444" stroke-width="1"/>
          <text x="${p.x + p.w/2}" y="${p.y + 14}" text-anchor="middle"
                fill="var(--text3)" font-family="var(--font-mono)" font-size="8" letter-spacing=".05em">
            ${esc(opLabel(t.op))}
          </text>
          <text x="${p.x + p.w/2}" y="${p.y + 30}" text-anchor="middle"
                fill="#d4d4d4" font-family="var(--font-mono)" font-size="10" font-weight="600">
            ${esc(label)}
          </text>`;

        // Divider + called functions list
        if (t.calls && t.calls.length) {
          inner += `<line x1="${p.x + 8}" y1="${p.y + 38}" x2="${p.x + p.w - 8}" y2="${p.y + 38}" stroke="#333" stroke-width="1"/>`;
          t.calls.forEach((c, ci) => {
            const cy = p.y + 52 + ci * 16;
            const fnLabel = c.name.length > 24 ? c.name.slice(0, 22) + '…' : c.name;
            inner += `
              <g onclick="event.stopPropagation();jumpToTaskLine(${c.line},${escJsArg(t.id)})" style="cursor:pointer">
                <rect x="${p.x + 4}" y="${cy - 11}" width="${p.w - 8}" height="14" rx="2"
                      fill="rgba(255,255,255,.04)" stroke="none"/>
                <text x="${p.x + 10}" y="${cy}" fill="var(--cyan)"
                      font-family="var(--font-mono)" font-size="9">
                  ƒ ${esc(fnLabel)}
                </text>
              </g>`;
          });
        }

        nodeSvg += `<g class="dag-graph-node" id="gnode-${esc(t.id)}"
            onclick="jumpToTaskLine(${t.line},${escJsArg(t.id)})" style="cursor:pointer"
            title="${esc(t.id)} — línea ${t.line}">
          ${inner}
        </g>`;
      });

      panel.innerHTML = `<svg width="${svgW}" height="${svgH}" style="display:block;min-width:${svgW}px">
        <defs>
          <marker id="arr" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
            <path d="M0,0 L7,3.5 L0,7 z" fill="#555"/>
          </marker>
        </defs>
        ${edgeSvg}${nodeSvg}
      </svg>`;
    }

    export function jumpToTaskLine(line, taskId) {
      const ta = document.getElementById('dag-code-textarea');
      if (!ta) return;
      const lines = ta.value.split('\n');
      let pos = 0;
      for (let i = 0; i < line - 1 && i < lines.length; i++) pos += lines[i].length + 1;
      ta.focus();
      ta.setSelectionRange(pos, pos + (lines[line - 1] || '').length);
      const lh = parseFloat(getComputedStyle(ta).lineHeight) || 17.6;
      ta.scrollTop = Math.max(0, (line - 4)) * lh;
      dagSyncLineScroll();
      document.querySelectorAll('.dag-graph-node').forEach(el => el.classList.remove('active'));
      const node = document.getElementById(`gnode-${taskId}`);
      if (node) node.classList.add('active');
    }

    // ── Split pane resize ─────────────────────────────────────────────────────

    // ── Generic vertical (top/bottom) panel resize ───────────────────────────

    export function vResizeStart(e, topPanelId) {
      state._vResizeEl  = document.getElementById(topPanelId);
      if (!state._vResizeEl) return;
      state._vResizing       = true;
      state._vResizeY0       = e.clientY;
      state._vResizeH0       = state._vResizeEl.getBoundingClientRect().height;
      state._vResizeHandleEl = e.currentTarget;
      state._vResizeHandleEl.classList.add('dragging');
      const overlay = document.createElement('div');
      overlay.id = 'v-resize-overlay';
      overlay.style.cssText = 'position:fixed;inset:0;cursor:row-resize;z-index:9999';
      overlay.addEventListener('mousemove', vResizeMove);
      overlay.addEventListener('mouseup',   vResizeEnd);
      document.body.appendChild(overlay);
      e.preventDefault();
    }

    export function vResizeMove(e) {
      if (!state._vResizing || !state._vResizeEl) return;
      const delta = e.clientY - state._vResizeY0;
      const newH  = Math.max(60, state._vResizeH0 + delta);
      state._vResizeEl.style.flex   = 'none';
      state._vResizeEl.style.height = newH + 'px';
    }

    export function vResizeEnd() {
      state._vResizing = false;
      if (state._vResizeHandleEl) state._vResizeHandleEl.classList.remove('dragging');
      const overlay = document.getElementById('v-resize-overlay');
      if (overlay) overlay.remove();
      state._vResizeEl = state._vResizeHandleEl = null;
    }

    // ── AI panel horizontal resize ────────────────────────────────────────────

    export function aiResizeStart(e) {
      const panel = document.getElementById('ai-panel');
      if (!panel) return;
      state._aiResizing = true;
      state._aiResizeX0 = e.clientX;
      state._aiResizeW0 = panel.getBoundingClientRect().width;
      document.getElementById('ai-resize-handle').classList.add('dragging');
      const overlay = document.createElement('div');
      overlay.id = 'ai-resize-overlay';
      overlay.style.cssText = 'position:fixed;inset:0;cursor:col-resize;z-index:9999';
      overlay.addEventListener('mousemove', aiResizeMove);
      overlay.addEventListener('mouseup',   aiResizeEnd);
      document.body.appendChild(overlay);
      e.preventDefault();
    }

    export function aiResizeMove(e) {
      if (!state._aiResizing) return;
      const panel = document.getElementById('ai-panel');
      if (!panel) return;
      // dragging left increases panel width (handle is on the left of the panel)
      const delta = state._aiResizeX0 - e.clientX;
      const newW  = Math.max(180, Math.min(state._aiResizeW0 + delta, window.innerWidth * 0.6));
      panel.style.width = newW + 'px';
    }

    export function aiResizeEnd() {
      state._aiResizing = false;
      const handle = document.getElementById('ai-resize-handle');
      if (handle) handle.classList.remove('dragging');
      const overlay = document.getElementById('ai-resize-overlay');
      if (overlay) overlay.remove();
    }

    export function dagResizeStart(e) {
      const panel = document.getElementById('dag-graph-panel');
      if (!panel) return;
      state._dagResizing = true;
      state._dagResizeX0 = e.clientX;
      state._dagResizeW0 = panel.getBoundingClientRect().width;
      document.getElementById('dag-resize-handle').style.background = 'var(--amber)';
      // Overlay to capture mouse outside the handle
      const overlay = document.createElement('div');
      overlay.id = 'dag-resize-overlay';
      overlay.style.cssText = 'position:fixed;inset:0;cursor:col-resize;z-index:9999';
      overlay.addEventListener('mousemove', dagResizeMove);
      overlay.addEventListener('mouseup',   dagResizeEnd);
      document.body.appendChild(overlay);
      e.preventDefault();
    }

    export function dagResizeMove(e) {
      if (!state._dagResizing) return;
      const container = document.getElementById('dag-split-container');
      const panel     = document.getElementById('dag-graph-panel');
      if (!container || !panel) return;
      const containerW = container.getBoundingClientRect().width;
      const delta      = e.clientX - state._dagResizeX0;
      const newW       = Math.max(120, Math.min(state._dagResizeW0 + delta, containerW - 180));
      panel.style.width = newW + 'px';
    }

    export function dagResizeEnd() {
      state._dagResizing = false;
      const handle = document.getElementById('dag-resize-handle');
      if (handle) handle.style.background = 'var(--border)';
      const overlay = document.getElementById('dag-resize-overlay');
      if (overlay) overlay.remove();
    }

    export function dagSetEditorCode(code) {
      const ta = document.getElementById('dag-code-textarea');
      if (ta) ta.value = code;
      state._deployedCode = code;
      dagUpdateLineNumbers();
      dagMarkDirty();
      if (state._dagGraphVisible) _renderDagGraph();
    }

    export function setDeployMsg(msg, type) {
      const el = document.getElementById('deploy-msg');
      if (!el) return;
      el.textContent = msg;
      el.className = 'deploy-msg' + (type === 'ok' ? ' deploy-ok' : type === 'err' ? ' deploy-err' : '');
    }

    export function copyDagCode() {
      const code = document.getElementById('dag-code-textarea')?.value;
      if (!code) return;
      navigator.clipboard.writeText(code).then(() => setDeployMsg('⎘ Copiado', 'ok'))
        .catch(() => setDeployMsg('Error al copiar', 'err'));
      setTimeout(() => setDeployMsg('', ''), 2000);
    }

    export function toggleDagTemplates() {
      state._tplOpen = !state._tplOpen;
      const list = document.getElementById('tpl-list-s');
      const tog  = document.getElementById('tpl-toggle-s');
      if (!list) return;
      list.style.display = state._tplOpen ? '' : 'none';
      if (tog) tog.textContent = state._tplOpen ? '▼' : '▶';
      if (state._tplOpen) loadDagTemplates();
    }

    export async function loadDagTemplates() {
      const list = document.getElementById('tpl-list-s');
      if (!list) return;
      try {
        const r = await fetch('/api/dag_templates');
        const d = await r.json();
        const tpls = d.templates || [];
        if (!tpls.length) {
          list.innerHTML = '<div style="padding:12px;color:var(--text3);font-size:10px">Sin plantillas.</div>';
          return;
        }
        list.innerHTML = tpls.map(t => `
          <div class="tpl-item" onclick="applyDagTemplate(${escJsArg(t.id)})">
            <div class="tpl-item-name">◈ ${esc(t.name)}</div>
            <div class="tpl-item-desc">${esc(t.description)}</div>
            <div class="tpl-tags">${(t.tags||[]).map(tag => `<span class="tpl-tag">${esc(tag)}</span>`).join('')}</div>
          </div>`).join('');
      } catch(e) {
        list.innerHTML = `<div style="padding:12px;color:#ff2d55;font-size:10px">Error: ${esc(e.message)}</div>`;
      }
    }

    export async function applyDagTemplate(templateId) {
      const cartridge = _dagCartridge();
      const entity = prompt('Nombre de la entidad (ej. "ProjectDetail"):',
        state._selectedDag && state._selectedDag !== '__new__'
          ? state._selectedDag.replace(cartridge + '_', '').replace(/_/g, '')
          : 'MyEntity');
      if (!entity) return;

      try {
        const r = await fetch(
          `/api/dag_templates/${encodeURIComponent(templateId)}` +
          `?cartridge=${encodeURIComponent(cartridge)}&entity=${encodeURIComponent(entity)}`
        );
        const d = await r.json();
        if (!d.code) { alert('No se pudo cargar la plantilla'); return; }

        dagSetEditorCode(d.code);
        _showDagEditor();
        const nameEl  = document.getElementById('dag-editor-name');
        const badgeEl = document.getElementById('dag-editor-badge');
        if (nameEl)  nameEl.textContent = `${cartridge}_${entity.toLowerCase()} (plantilla)`;
        if (badgeEl) badgeEl.innerHTML = '<span class="dag-badge dag-paused">borrador</span>';
        state._selectedDag = '__new__';
        setDeployMsg('Plantilla cargada — revisa los TODO y despliega', 'ok');
        setTimeout(() => setDeployMsg('', ''), 4000);
      } catch(e) {
        alert(`Error al cargar plantilla: ${e.message}`);
      }
    }

    export function sendDagToAssistantStudio() {
      const dagId  = state._selectedDag === '__new__' ? 'nuevo_dag' : (state._selectedDag || 'dag');
      const source = document.getElementById('dag-code-textarea')?.value || '';
      const hasSource = source && !source.startsWith('# Fuente no encontrada');
      const msg = hasSource
        ? `Quiero modificar el DAG \`${dagId}\`. Código actual:\n\n\`\`\`python\n${source}\n\`\`\`\n\n¿Qué cambios quieres hacer?`
        : `Quiero crear el DAG \`${dagId}\` del cartucho \`${_dagCartridge()}\`. Genera el código completo y despliégalo con infra__airflow_create_dag.`;

      const input = document.getElementById('ai-input');
      if (input) {
        input.value = msg;
        input.style.borderColor = 'var(--cyan)';
        input.focus();
        setTimeout(() => { input.style.borderColor = ''; }, 1800);
      }
    }

    // ── Init ───────────────────────────────────────────────────────────────────
    (async () => {
      await loadCartridges();
      // Auto-select first cartridge if any
      if (state._cartridges.length > 0) {
        document.getElementById('cartridge-sel').value = state._cartridges[0].id;
        await selectCartridge(state._cartridges[0].id);
      }
      goStep(1);

      // Check if we arrived from Pipeline with a DAG to edit
      const pending = sessionStorage.getItem('studio_pending');
      if (pending) {
        sessionStorage.removeItem('studio_pending');
        try {
          const p = JSON.parse(pending);
          if (p.type === 'dag_edit') {
            if (p.cartridge && state._cartridges.find(c => c.id === p.cartridge)) {
              document.getElementById('cartridge-sel').value = p.cartridge;
              await selectCartridge(p.cartridge);
            }
            // Open DAG editor step with the specific DAG selected
            if (p.dag_id) state._selectedDag = p.dag_id;
            goStep(2);
          }
        } catch(_) {}
      }
    })();
