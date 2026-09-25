document.addEventListener('DOMContentLoaded', () => {
  setTimeout(() => {
    if (typeof openViewer === 'function') {
      openViewer('/viewer/pipeline', 'Pipeline Monitor');
    }
  }, 2200);

  document.getElementById('theme-btn')?.addEventListener('click', () => cycleTheme());
  document.getElementById('chat-input').addEventListener('keydown', (e) => handleKey(e));
  document.getElementById('btn-exec').addEventListener('click', () => sendMessage());
  document.getElementById('btn-jobs-reload').addEventListener('click', () => loadJobs());
  document.getElementById('btn-servers-reload').addEventListener('click', () => loadServers(true));
  document.getElementById('btn-toggle-viewer')?.addEventListener('click', () => toggleViewerPanel());
  document.getElementById('btn-popout').addEventListener('click', () => popoutViewer());
  document.getElementById('btn-close-viewer').addEventListener('click', () => closeViewerPanel());
  document.getElementById('btn-popout-fallback').addEventListener('click', () => popoutViewer());
  document.getElementById('btn-datasets-reload').addEventListener('click', () => loadDatasets());
  document.getElementById('btn-modal-close').addEventListener('click', () => {
    document.getElementById('datasets-modal').style.display = 'none';
  });

  document.getElementById('datasets-modal').addEventListener('click', (ev) => closeModal(ev));

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

window.addEventListener('message', (ev) => {
  if (!ev.data) return;

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

  if (ev.data.type === 'open_viewer' && ev.data.url) {
    openViewer(ev.data.url, ev.data.label || 'DAG Editor');
    return;
  }
});
