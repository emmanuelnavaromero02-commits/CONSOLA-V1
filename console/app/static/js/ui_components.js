(function () {
  'use strict';

  if (window.__omegaUiComponentsLoaded) return;
  window.__omegaUiComponentsLoaded = true;

  const TOAST_MAX = 3;
  let toastContainer = null;
  const toastQueue = [];

  function ensureToastContainer() {
    if (toastContainer && document.body.contains(toastContainer)) {
      return toastContainer;
    }
    toastContainer = document.createElement('div');
    toastContainer.className = 'toast-stack';
    toastContainer.setAttribute('role', 'region');
    toastContainer.setAttribute('aria-live', 'polite');
    toastContainer.setAttribute('aria-label', 'Notifications');
    document.body.appendChild(toastContainer);
    return toastContainer;
  }

  function showToast(message, type, duration) {
    type = type || 'info';
    duration = (typeof duration === 'number') ? duration : 4000;

    const node = document.createElement('div');
    node.className = 'toast toast-' + type;
    node.textContent = message;
    node.setAttribute('role', type === 'error' ? 'alert' : 'status');

    const container = ensureToastContainer();
    container.appendChild(node);
    toastQueue.push(node);

    while (toastQueue.length > TOAST_MAX) {
      const oldest = toastQueue.shift();
      if (oldest && oldest.parentNode) oldest.parentNode.removeChild(oldest);
    }

    if (duration > 0) {
      setTimeout(function () {
        if (node.parentNode) node.parentNode.removeChild(node);
        const i = toastQueue.indexOf(node);
        if (i >= 0) toastQueue.splice(i, 1);
      }, duration);
    }
    return node;
  }

  let activeModal = null;
  let lastFocused = null;

  function closeActiveModal() {
    if (!activeModal) return;
    if (activeModal.parentNode) activeModal.parentNode.removeChild(activeModal);
    document.removeEventListener('keydown', activeModal.__keyHandler);
    activeModal = null;
    if (lastFocused && typeof lastFocused.focus === 'function') {
      lastFocused.focus();
    }
  }

  function showModal(opts) {
    opts = opts || {};
    closeActiveModal();
    lastFocused = document.activeElement;

    const overlay = document.createElement('div');
    overlay.className = 'modal-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    if (opts.title) overlay.setAttribute('aria-label', opts.title);

    const modal = document.createElement('div');
    modal.className = 'modal';

    if (opts.title) {
      const header = document.createElement('div');
      header.className = 'modal-header';
      header.textContent = opts.title;
      modal.appendChild(header);
    }

    const body = document.createElement('div');
    body.className = 'modal-body';
    if (opts.body instanceof Node) {
      body.appendChild(opts.body);
    } else if (typeof opts.body === 'string') {
      body.textContent = opts.body;
    }
    modal.appendChild(body);

    const footer = document.createElement('div');
    footer.className = 'modal-footer';
    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn-ghost';
    cancelBtn.textContent = opts.cancelText || 'Cancelar';
    cancelBtn.addEventListener('click', function () {
      if (typeof opts.onCancel === 'function') opts.onCancel();
      closeActiveModal();
    });
    footer.appendChild(cancelBtn);

    if (opts.onConfirm || opts.confirmText) {
      const confirmBtn = document.createElement('button');
      confirmBtn.type = 'button';
      confirmBtn.className = 'btn ' + (opts.danger ? 'btn-danger' : 'btn-primary');
      confirmBtn.textContent = opts.confirmText || 'Aceptar';
      confirmBtn.addEventListener('click', function () {
        if (typeof opts.onConfirm === 'function') opts.onConfirm();
        closeActiveModal();
      });
      footer.appendChild(confirmBtn);
    }
    modal.appendChild(footer);

    overlay.appendChild(modal);
    overlay.addEventListener('click', function (event) {
      if (event.target === overlay) {
        if (typeof opts.onCancel === 'function') opts.onCancel();
        closeActiveModal();
      }
    });
    overlay.__keyHandler = function (event) {
      if (event.key === 'Escape') {
        if (typeof opts.onCancel === 'function') opts.onCancel();
        closeActiveModal();
      }
    };
    document.addEventListener('keydown', overlay.__keyHandler);

    document.body.appendChild(overlay);
    activeModal = overlay;

    const target = footer.querySelector('.btn-primary, .btn-danger, .btn-ghost');
    if (target) setTimeout(function () { target.focus(); }, 0);
    return overlay;
  }

  function showConfirm(message, onConfirm, opts) {
    opts = opts || {};
    return showModal({
      title:       opts.title || 'Confirmar',
      body:        message,
      confirmText: opts.confirmText || 'Aceptar',
      cancelText:  opts.cancelText  || 'Cancelar',
      danger:      !!opts.danger,
      onConfirm:   onConfirm,
      onCancel:    opts.onCancel,
    });
  }

  let loadingNode = null;
  function showLoading(message) {
    hideLoading();
    loadingNode = document.createElement('div');
    loadingNode.className = 'modal-overlay';
    loadingNode.setAttribute('role', 'status');
    loadingNode.setAttribute('aria-busy', 'true');
    const inner = document.createElement('div');
    inner.className = 'modal';
    inner.style.maxWidth = '320px';
    inner.style.textAlign = 'center';
    const body = document.createElement('div');
    body.className = 'modal-body';
    const spinner = document.createElement('span');
    spinner.className = 'spinner';
    body.appendChild(spinner);
    const text = document.createElement('div');
    text.style.marginTop = 'var(--space-md, 16px)';
    text.textContent = message || 'Cargando…';
    body.appendChild(text);
    inner.appendChild(body);
    loadingNode.appendChild(inner);
    document.body.appendChild(loadingNode);
    return loadingNode;
  }
  function hideLoading() {
    if (loadingNode && loadingNode.parentNode) {
      loadingNode.parentNode.removeChild(loadingNode);
    }
    loadingNode = null;
  }

  window.showToast    = showToast;
  window.showModal    = showModal;
  window.showConfirm  = showConfirm;
  window.showLoading  = showLoading;
  window.hideLoading  = hideLoading;
  window.closeModal   = closeActiveModal;
})();
