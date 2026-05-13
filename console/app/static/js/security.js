// Sprint v1.11 phase 3 — extracted from security.html for strict CSP.

document.documentElement.dataset.theme = localStorage.getItem('mod-theme') || 'light';

const state = {
  sessions: [],
  audit: []
};

let els = null;

function setText(el, value) {
  el.textContent = value == null || value === '' ? '—' : String(value);
}

function setStatus(el, label, type) {
  el.classList.toggle('warning', type === 'warning');
  el.classList.toggle('error', type === 'error');
  const text = el.querySelector('span:last-child');
  text.textContent = label;
}

function showError(el, message) {
  el.style.display = message ? 'block' : 'none';
  el.textContent = message || '';
}

const humanizeError = (err) => window.ModLabels?.humanizeError?.(err) || (err?.message || String(err || 'Error'));

function formatDate(value) {
  if (!value) return '—';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return String(value);
  return d.toLocaleString();
}

function tokenPreview(session) {
  if (session.token_preview) return String(session.token_preview);
  if (session.token) {
    const token = String(session.token);
    return token.length > 6 ? '...' + token.slice(-6) : '***';
  }
  return 'No visible';
}

function revocationToken(session) {
  if (session.session_id) return String(session.session_id);
  if (session.token) return String(session.token);
  return '';
}

function clearBody(tbody, colspan, message) {
  tbody.replaceChildren();
  const tr = document.createElement('tr');
  const td = document.createElement('td');
  td.colSpan = colspan;
  td.className = 'state-row';
  td.textContent = message;
  tr.appendChild(td);
  tbody.appendChild(tr);
}

function appendCell(row, value, className) {
  const td = document.createElement('td');
  if (className) td.className = className;
  td.textContent = value == null || value === '' ? '—' : String(value);
  row.appendChild(td);
  return td;
}

function renderSessions() {
  els.sessionsBody.replaceChildren();
  setText(els.activeSessions, state.sessions.length);
  if (!state.sessions.length) {
    clearBody(els.sessionsBody, 7, 'No hay sesiones activas para mostrar.');
    return;
  }

  state.sessions.forEach((session) => {
    const tr = document.createElement('tr');
    appendCell(tr, session.user_email || session.email || session.user_id);
    appendCell(tr, session.ip);
    appendCell(tr, session.user_agent, 'muted');
    appendCell(tr, formatDate(session.created_at));
    appendCell(tr, formatDate(session.last_seen));
    appendCell(tr, tokenPreview(session), 'mono muted');

    const action = document.createElement('td');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'button danger';
    btn.textContent = 'Revocar';
    const token = revocationToken(session);
    if (!token) {
      btn.disabled = true;
      btn.title = 'El API no entrega un identificador revocable sin exponer el token completo.';
    } else {
      btn.addEventListener('click', () => revokeSession(token));
    }
    action.appendChild(btn);
    tr.appendChild(action);
    els.sessionsBody.appendChild(tr);
  });
}

function auditTarget(event) {
  const details = event.details || event.metadata || {};
  if (typeof details === 'object' && details !== null) {
    return details.target || details.entity || details.resource || event.target || '—';
  }
  return event.target || '—';
}

function auditSummary(event) {
  const details = event.details || event.metadata || {};
  if (!details) return '—';
  if (typeof details === 'string') return details;
  try {
    const clean = {};
    Object.keys(details).slice(0, 8).forEach((key) => {
      if (!/token|secret|password|api[_-]?key|cookie/i.test(key)) {
        clean[key] = details[key];
      }
    });
    return JSON.stringify(clean);
  } catch (err) {
    return 'Resumen no disponible';
  }
}

function renderAudit() {
  els.auditBody.replaceChildren();
  setText(els.auditCount, state.audit.length);
  if (!state.audit.length) {
    clearBody(els.auditBody, 5, 'No hay eventos de auditoría recientes.');
    return;
  }

  state.audit.forEach((event) => {
    const tr = document.createElement('tr');
    appendCell(tr, formatDate(event.created_at || event.timestamp));
    appendCell(tr, event.user_email || event.email || event.user_id || 'System');
    appendCell(tr, event.action);
    appendCell(tr, auditTarget(event));
    appendCell(tr, auditSummary(event), 'mono muted');
    els.auditBody.appendChild(tr);
  });
}

async function fetchJson(url) {
  const response = await fetch(url, { credentials: 'same-origin' });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.json();
}

async function loadSessions() {
  setStatus(els.sessionsStatus, 'Cargando', 'warning');
  showError(els.sessionsError, '');
  try {
    const data = await fetchJson('/security/sessions');
    state.sessions = Array.isArray(data) ? data : [];
    renderSessions();
    setStatus(els.sessionsStatus, 'Correcto', 'healthy');
  } catch (err) {
    state.sessions = [];
    setText(els.activeSessions, '—');
    clearBody(els.sessionsBody, 7, 'No se pudieron cargar las sesiones.');
    showError(els.sessionsError, `Sesiones: ${humanizeError(err)}`);
    setStatus(els.sessionsStatus, 'Error', 'error');
  }
}

async function loadAudit() {
  setStatus(els.auditStatus, 'Cargando', 'warning');
  showError(els.auditError, '');
  try {
    const data = await fetchJson('/security/audit');
    state.audit = Array.isArray(data) ? data : [];
    renderAudit();
    setStatus(els.auditStatus, 'Correcto', 'healthy');
  } catch (err) {
    state.audit = [];
    setText(els.auditCount, '—');
    clearBody(els.auditBody, 5, 'No se pudo cargar la auditoría.');
    showError(els.auditError, `Auditoría: ${humanizeError(err)}`);
    setStatus(els.auditStatus, 'Error', 'error');
  }
}

async function revokeSession(token) {
  const confirmed = window.confirm('¿Revocar esta sesión?');
  if (!confirmed) return;
  const response = await fetch(`/security/sessions/${encodeURIComponent(token)}`, {
    method: 'DELETE',
    credentials: 'same-origin'
  });
  if (!response.ok) {
    showError(els.sessionsError, `No se pudo revocar la sesión: ${response.status}`);
    setStatus(els.sessionsStatus, 'Advertencia', 'warning');
    return;
  }
  await loadSessions();
}

async function refreshAll() {
  els.refreshBtn.disabled = true;
  try {
    await Promise.all([loadSessions(), loadAudit()]);
  } finally {
    els.refreshBtn.disabled = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  els = {
    activeSessions: document.getElementById('active-sessions'),
    auditCount: document.getElementById('audit-count'),
    sessionsBody: document.getElementById('sessions-body'),
    auditBody: document.getElementById('audit-body'),
    sessionsStatus: document.getElementById('sessions-status'),
    auditStatus: document.getElementById('audit-status'),
    sessionsError: document.getElementById('sessions-error'),
    auditError: document.getElementById('audit-error'),
    refreshBtn: document.getElementById('refresh-btn')
  };
  els.refreshBtn.addEventListener('click', refreshAll);
  refreshAll();
});
