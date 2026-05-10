import { GLOSSARY } from './glossary.js';
import { ERROR_LABELS, PERMISSION_LABELS, ROUTE_LABELS, STATUS_LABELS } from './es-mx.js';

function normalize(value) {
  return String(value || '').trim();
}

export function humanizeTerm(key) {
  const normalized = normalize(key).toLowerCase();
  return GLOSSARY[normalized] || key || '';
}

export function humanizePermission(permission) {
  const key = normalize(permission);
  return PERMISSION_LABELS[key] || (key ? `No tienes el permiso necesario: ${key}.` : 'No tienes permiso para esta acción.');
}

export function humanizeRoute(route) {
  const key = normalize(route);
  return ROUTE_LABELS[key] || key || '';
}

export function humanizeStatus(status) {
  const key = normalize(status).toLowerCase().replace(/\s+/g, '_');
  return STATUS_LABELS[key] || status || STATUS_LABELS.unknown;
}

export function humanizeError(error) {
  const raw = typeof error === 'string' ? error : (error?.message || error?.detail || '');
  const text = normalize(raw);
  if (/401|unauthor/i.test(text)) return ERROR_LABELS.unauthorized;
  if (/403|forbid|permission|permiso/i.test(text)) return ERROR_LABELS.forbidden;
  if (/404|not found/i.test(text)) return ERROR_LABELS.not_found;
  if (/500|server|traceback/i.test(text)) return ERROR_LABELS.server_error;
  if (/failed to fetch|network/i.test(text)) return ERROR_LABELS.network;
  return text || ERROR_LABELS.unknown;
}

export function humanizePermissionKey(key) {
  return normalize(key)
    .replaceAll('_', ' ')
    .replaceAll('.', ' / ')
    .replace(/\biam\b/i, 'IAM')
    .replace(/\bmcp\b/i, 'Servicios internos');
}

export const Labels = {
  humanizeTerm,
  humanizePermission,
  humanizeRoute,
  humanizeStatus,
  humanizeError,
  humanizePermissionKey,
};

if (typeof window !== 'undefined') {
  window.ModLabels = Labels;
}

