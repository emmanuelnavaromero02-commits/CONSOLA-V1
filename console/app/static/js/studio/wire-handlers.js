/**
 * Studio — wire DOM event handlers.
 *
 * Replaces the inline `on*=""` attributes that used to live on studio.html.
 * Called by legacy-bootstrap.js after the legacy module exports have been
 * published to `window`.
 */

import {
  selectCartridge,
  aiClearHistory,
  aiSend,
  exportCartridge,
  goStep,
  showCreateCartridge,
  aiAutogrow,
  aiKey,
  aiQuickPrompt,
  aiResizeStart,
} from './legacy.js?v=studio-autopilot-ui5';
import { closeSqlRunner, execSqlRunner } from './sql-runner.js?v=studio-autopilot-ui5';

const $ = (id) => document.getElementById(id);
const on = (el, ev, fn) => { if (el) el.addEventListener(ev, fn); };

export function wireStudioHandlers() {
  // Step bar — 7 tabs
  for (let i = 1; i <= 7; i++) {
    const idx = i;
    on($(`si-${idx}`), 'click', () => goStep(idx));
  }

  // Cartridge picker
  on($('cartridge-sel'), 'change', (e) => selectCartridge(e.target.value));
  on($('btn-create-cartridge'), 'click', showCreateCartridge);
  on($('btn-export-cart'), 'click', () => exportCartridge());

  // AI assistant
  on($('ai-resize-handle'), 'mousedown', aiResizeStart);
  on($('ai-focus-btn'), 'click', () => $('ai-input')?.focus());
  on($('ai-clear-btn'), 'click', aiClearHistory);
  on($('ai-input'), 'keydown', aiKey);
  on($('ai-input'), 'input', (e) => aiAutogrow(e.target));
  on($('ai-send-btn'), 'click', aiSend);
  on($('ai-capabilities'), 'click', (e) => {
    const button = e.target.closest('[data-ai-prompt]');
    if (!button) return;
    aiQuickPrompt(button.dataset.aiPrompt);
  });

  // E2E compatibility sentinels
  on($('new-entity-compat-toggle'), 'click', () => {
    const form = $('new-entity-compat-form');
    if (form) form.hidden = false;
  });

  // SQL Runner modal
  on($('sql-runner-overlay'), 'click', (e) => {
    if (e.target === e.currentTarget) closeSqlRunner();
  });
  on($('sql-runner-exec-btn'), 'click', execSqlRunner);
  on($('sql-runner-close-btn'), 'click', closeSqlRunner);
  on($('sql-runner-ta'), 'keydown', (e) => {
    if (e.ctrlKey && e.key === 'Enter') {
      e.preventDefault();
      execSqlRunner();
    }
  });
}
