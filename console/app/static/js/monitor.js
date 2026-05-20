// Sprint v1.11 phase 3 — extracted from monitor.html for strict CSP.
//
// Functions like cycleTheme / sendMessage / handleKey / loadJobs /
// loadServers / openViewer / quickCmd / openDatasets / popoutViewer /
// closeViewerPanel / loadDatasets / closeModal live in /static/js/app.js,
// which is loaded BEFORE this file. We re-wire each inline on* handler
// from monitor.html to call those globals via addEventListener / a
// single delegated listener for the dynamic-friendly cases.

// Auto-open Pipeline Monitor after boot sequence completes (~2s).
// We delay until window.openViewer is available (defensive — app.js may
// race for some reason).
document.addEventListener('DOMContentLoaded', () => {
  setTimeout(() => {
    if (typeof openViewer === 'function') {
      openViewer('/viewer/pipeline', 'Pipeline Monitor');
    }
  }, 2200);

  // Static buttons — id-targeted.
  document.getElementById('theme-btn')?.addEventListener('click', () => cycleTheme());
  document.getElementById('chat-input').addEventListener('keydown', (e) => handleKey(e));
  document.getElementById('btn-exec').addEventListener('click', () => sendMessage());
  document.getElementById('btn-jobs-reload').addEventListener('click', () => loadJobs());
  document.getElementById('btn-servers-reload').addEventListener('click', () => loadServers(true));
  document.getElementById('btn-popout').addEventListener('click', () => popoutViewer());
  document.getElementById('btn-close-viewer').addEventListener('click', () => closeViewerPanel());
  document.getElementById('btn-popout-fallback').addEventListener('click', () => popoutViewer());
  document.getElementById('btn-datasets-reload').addEventListener('click', () => loadDatasets());
  document.getElementById('btn-modal-close').addEventListener('click', () => {
    document.getElementById('datasets-modal').style.display = 'none';
  });

  // Modal backdrop: replicates the original `closeModal(event)` semantics.
  document.getElementById('datasets-modal').addEventListener('click', (ev) => closeModal(ev));

  // Quick action buttons → data-action attributes on the static markup.
  document.querySelectorAll('#quick-ops [data-action]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const action = btn.dataset.action;
      if (action === 'open-viewer') {
        openViewer(btn.dataset.url, btn.dataset.label || '');
      } else if (action === 'quick-cmd') {
        quickCmd(btn.dataset.cmd);
      } else if (action === 'open-datasets') {
        openDatasets();
      } else if (action === 'go-studio') {
        window.location = '/studio';
      }
    });
  });
});

// Receive messages from viewer iframes
window.addEventListener('message', (ev) => {
  if (!ev.data) return;

  // DAG-to-assistant: pipeline viewer sends DAG code to chat
  if (ev.data.type === 'dag_to_assistant') {
    const input = document.getElementById('chat-input');
    if (!input) return;
    input.value = ev.data.message || '';
    input.style.borderColor = 'var(--cyan)';
    input.focus();
    input.dispatchEvent(new Event('input'));
    setTimeout(() => { input.style.borderColor = ''; }, 2000);
    return;
  }

  // open_viewer: studio entity row "✏ DAG" button — navigate viewer to pipeline DAGs tab
  if (ev.data.type === 'open_viewer' && ev.data.url) {
    openViewer(ev.data.url, ev.data.label || 'DAG Editor');
    return;
  }
});
