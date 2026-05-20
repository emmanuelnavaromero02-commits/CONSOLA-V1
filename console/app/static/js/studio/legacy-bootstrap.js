/**
 * Studio Legacy Bootstrap
 * --------------------------
 * Carga los módulos legacy.js y sql-runner.js y publica en `window` cada
 * función exportada. studio.html (y el HTML que legacy.js inyecta en tiempo
 * de ejecución) llama a esas funciones desde atributos `onclick=`, `oninput=`,
 * etc. — necesitan vivir en el scope global, como antes del refactor a ES
 * Module.
 *
 * Originalmente el script inline de studio.html declaraba 141 funciones top
 * level dentro de un `<script>` no estricto → todas quedaban como propiedades
 * de `window`. Tras la modularización las funciones viven en el scope del
 * módulo. Este bootstrap re-expone exactamente la misma superficie para
 * preservar comportamiento. La eliminación de los handlers inline (y por
 * ende de este shim) se planea para Fase 4C/4D.
 */

import * as legacy from './legacy.js';
import * as sqlRunner from './sql-runner.js';
import { wireStudioHandlers } from './wire-handlers.js';

const LEGACY_EVENTS = [
  'click',
  'change',
  'input',
  'keydown',
  'blur',
  'mouseover',
  'mouseout',
  'mousedown',
  'mouseenter',
  'mouseleave',
];

for (const [name, value] of Object.entries(legacy)) {
  if (typeof value === 'function') window[name] = value;
}
for (const [name, value] of Object.entries(sqlRunner)) {
  if (typeof value === 'function') window[name] = value;
}

// Wire DOM listeners that replaced studio.html's former inline on*="" attrs.
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => {
    wireStudioHandlers();
    wireLegacyRuntimeHandlers();
  });
} else {
  wireStudioHandlers();
  wireLegacyRuntimeHandlers();
}

function wireLegacyRuntimeHandlers() {
  const wire = (root = document) => {
    const selector = LEGACY_EVENTS.map((name) => `[on${name}]`).join(',');
    const nodes = root.querySelectorAll ? Array.from(root.querySelectorAll(selector)) : [];
    if (root.matches?.(selector)) nodes.unshift(root);
    nodes.forEach((node) => {
      LEGACY_EVENTS.forEach((eventName) => {
        const attr = `on${eventName}`;
        const code = node.getAttribute(attr);
        if (!code || node.dataset[`wired${eventName}`] === '1') return;
        node.dataset[`wired${eventName}`] = '1';
        node.removeAttribute(attr);
        node.addEventListener(eventName, (event) => runLegacyHandler(code, node, event));
      });
    });
  };
  wire();
  const observer = new MutationObserver((mutations) => {
    mutations.forEach((mutation) => {
      mutation.addedNodes.forEach((node) => {
        if (node.nodeType === Node.ELEMENT_NODE) wire(node);
      });
    });
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

function runLegacyHandler(code, el, event) {
  const text = String(code || '').trim();
  if (!text) return;
  if (handleComplexKeyHandler(text, el, event)) return;
  for (const statement of splitTopLevel(text, ';')) {
    runStatement(statement.trim(), el, event);
  }
}

function handleComplexKeyHandler(text, el, event) {
  if (!text.includes('event.ctrlKey')) return false;
  if (text.includes("event.ctrlKey&&event.shiftKey&&event.key==='Enter'")) {
    if (event.ctrlKey && event.shiftKey && event.key === 'Enter') {
      event.preventDefault();
      callWindow('_openRunnerFromActiveTextarea', [], el, event);
      return true;
    }
    if (event.ctrlKey && event.key === 'Enter') {
      callWindow('previewDS', [], el, event);
      return true;
    }
  }
  if (text.includes("event.ctrlKey&&event.key==='Enter'") && event.ctrlKey && event.key === 'Enter') {
    const body = text.match(/\{([^{}]+)\}/)?.[1] || '';
    runLegacyHandler(body, el, event);
    return true;
  }
  return false;
}

function runStatement(statement, el, event) {
  if (!statement) return;
  statement = statement.replace(/^return\s+/, '').trim();
  if (statement === 'event.stopPropagation()') { event.stopPropagation(); return; }
  if (statement === 'event.preventDefault()') { event.preventDefault(); return; }
  if (statement === 'this.blur()') { el.blur?.(); return; }

  const keyMatch = statement.match(/^if\s*\(\s*event\.key\s*===\s*['"]([^'"]+)['"]\s*\)\s*(?:\{(.*)\}|(.+))$/);
  if (keyMatch) {
    if (event.key === keyMatch[1]) runLegacyHandler(keyMatch[2] || keyMatch[3] || '', el, event);
    return;
  }

  const stateDirty = statement.match(/^state\._dsEditorDirty\s*=\s*(true|false)$/);
  if (stateDirty) { window.state._dsEditorDirty = stateDirty[1] === 'true'; return; }

  const catFilter = statement.match(/^state\._catFilter\.(search|layer)\s*=\s*this\.value$/);
  if (catFilter) {
    window.state._catFilter = window.state._catFilter || {};
    window.state._catFilter[catFilter[1]] = el.value;
    return;
  }

  const styleSet = statement.match(/^this\.style\.([A-Za-z]+)\s*=\s*['"]([^'"]*)['"]$/);
  if (styleSet) { el.style[styleSet[1]] = styleSet[2]; return; }

  const styleTernary = statement.match(
    /^this\.style\.([A-Za-z]+)\s*=\s*state\._([A-Za-z0-9_]+)\s*\?\s*['"]([^'"]*)['"]\s*:\s*['"]([^'"]*)['"]$/,
  );
  if (styleTernary) {
    el.style[styleTernary[1]] = window.state?.[`_${styleTernary[2]}`] ? styleTernary[3] : styleTernary[4];
    return;
  }

  const hideById = statement.match(/^document\.getElementById\(['"]([^'"]+)['"]\)\.style\.display\s*=\s*['"]([^'"]*)['"]$/);
  if (hideById) {
    const target = document.getElementById(hideById[1]);
    if (target) target.style.display = hideById[2];
    return;
  }

  if (statement.includes('.style.display ===')) {
    const id = statement.match(/document\.getElementById\(['"]([^'"]+)['"]\)/)?.[1];
    const target = id ? document.getElementById(id) : null;
    if (target) target.style.display = target.style.display === 'none' ? '' : 'none';
    return;
  }

  const call = statement.match(/^([A-Za-z_$][\w$]*)\((.*)\)$/s);
  if (call) {
    callWindow(call[1], parseArgs(call[2], el, event), el, event);
  }
}

function callWindow(name, args, el, event) {
  const fn = window[name];
  if (typeof fn !== 'function') {
    console.warn(`[studio] legacy handler not found: ${name}`);
    return;
  }
  try {
    const result = fn(...args);
    if (result && typeof result.catch === 'function') result.catch((err) => console.error(err));
  } catch (err) {
    console.error(err);
  }
}

function parseArgs(text, el, event) {
  if (!text.trim()) return [];
  return splitTopLevel(text, ',').map((arg) => parseArg(arg.trim(), el, event));
}

function parseArg(arg, el, event) {
  if (arg === 'this') return el;
  if (arg === 'this.value') return el.value;
  if (arg === 'this.checked') return Boolean(el.checked);
  if (arg === 'event') return event;
  if (arg === 'true') return true;
  if (arg === 'false') return false;
  if (arg === 'null') return null;
  if (arg === 'undefined') return undefined;
  if (/^-?\d+(\.\d+)?$/.test(arg)) return Number(arg);
  if ((arg.startsWith('"') && arg.endsWith('"')) || (arg.startsWith("'") && arg.endsWith("'"))) {
    return unquote(arg);
  }
  if (arg.startsWith('{') || arg.startsWith('[')) {
    try { return JSON.parse(arg); } catch (_) { return arg; }
  }
  return arg;
}

function unquote(value) {
  const q = value[0];
  const body = value.slice(1, -1).replace(/&quot;/g, '"').replace(/&#39;/g, "'");
  if (q === '"') {
    try { return JSON.parse(`"${body.replace(/"/g, '\\"')}"`); } catch (_) { return body; }
  }
  return body.replace(/\\'/g, "'").replace(/\\"/g, '"');
}

function splitTopLevel(text, sep) {
  const out = [];
  let cur = '';
  let quote = '';
  let escape = false;
  let depth = 0;
  for (const ch of text) {
    if (escape) { cur += ch; escape = false; continue; }
    if (ch === '\\') { cur += ch; escape = true; continue; }
    if (quote) {
      cur += ch;
      if (ch === quote) quote = '';
      continue;
    }
    if (ch === '"' || ch === "'" || ch === '`') { quote = ch; cur += ch; continue; }
    if (ch === '(' || ch === '[' || ch === '{') depth += 1;
    if (ch === ')' || ch === ']' || ch === '}') depth = Math.max(0, depth - 1);
    if (ch === sep && depth === 0) {
      out.push(cur);
      cur = '';
    } else {
      cur += ch;
    }
  }
  if (cur) out.push(cur);
  return out;
}
