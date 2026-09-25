(function () {
  'use strict';

  if (window.__omegaCopilotFabLoaded) return;
  window.__omegaCopilotFabLoaded = true;

  const path = (window.location && window.location.pathname) || '/';
  const SKIP_PREFIXES = [
    '/copilot',           // don't FAB ourselves
    '/login',             // unauthenticated
    '/forgot-password',
    '/reset-password',
    '/activate',
  ];
  if (SKIP_PREFIXES.some(function (p) { return path.indexOf(p) === 0; })) {
    return;
  }

  function inject() {
    if (document.querySelector('.fab[data-fab="copilot"]')) return;
    const fab = document.createElement('button');
    fab.type = 'button';
    fab.className = 'fab';
    fab.setAttribute('data-fab', 'copilot');
    fab.setAttribute('aria-label', 'Abre el copiloto');
    fab.setAttribute('title',      'Abre el copiloto');
    fab.textContent = '🤖';
    fab.addEventListener('click', function () {
      window.location.href = '/copilot';
    });
    document.body.appendChild(fab);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', inject);
  } else {
    inject();
  }
})();
