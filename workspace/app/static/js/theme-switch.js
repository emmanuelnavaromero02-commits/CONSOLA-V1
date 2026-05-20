/**
 * Global dark/light theme switch.
 *   - Reads / writes `mod-theme` in localStorage so the choice survives
 *     refreshes and travels across pages.
 *   - On load, applies `document.documentElement.dataset.theme`.
 *   - Inserts a fixed switch button in the bottom-right corner of every
 *     page that loads this script.
 * The CSS variables for the two themes live in /static/css/main.css
 * (selector: `html[data-theme=light]`).
 */
(function () {
  const STORAGE_KEY = 'mod-theme';

  function systemTheme() {
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
      ? 'dark'
      : 'light';
  }

  function resolve(theme) {
    return theme === 'system' ? systemTheme() : theme;
  }

  function apply(theme) {
    const preference = theme || 'light';
    document.documentElement.dataset.theme = resolve(preference);
    document.documentElement.dataset.themePreference = preference;
    try { localStorage.setItem(STORAGE_KEY, preference); } catch (_) { /* ignore */ }
  }

  function current() {
    try { return localStorage.getItem(STORAGE_KEY) || 'light'; }
    catch (_) { return 'light'; }
  }

  // 1. Apply on parse (before paint).
  apply(current());

  // 2. Insert switch when DOM is ready.
  function mount() {
    if (document.getElementById('mod-theme-switch')) return;  // already there
    const btn = document.createElement('button');
    btn.id = 'mod-theme-switch';
    btn.type = 'button';
    btn.title = 'Cambiar tema (claro/oscuro)';
    btn.style.cssText = [
      'position:fixed', 'bottom:14px', 'right:14px', 'z-index:9999',
      'width:38px', 'height:38px', 'border-radius:50%',
      'border:1px solid var(--border,#30363d)',
      'background:var(--bg2,#161b22)',
      'color:var(--text2,#8b949e)',
      'cursor:pointer', 'font-size:16px', 'line-height:1',
      'box-shadow:0 2px 8px rgba(0,0,0,.25)',
      'display:flex', 'align-items:center', 'justify-content:center',
      'transition:transform .15s ease, color .15s ease',
    ].join(';');
    function paint() {
      const preference = current();
      btn.textContent = preference === 'system' ? '◐' : preference === 'light' ? '☾' : '☀';
    }
    paint();
    btn.addEventListener('click', function () {
      const preference = current();
      apply(preference === 'light' ? 'dark' : preference === 'dark' ? 'system' : 'light');
      paint();
    });
    btn.addEventListener('mouseenter', function () {
      btn.style.color = 'var(--green,#3fb950)';
      btn.style.transform = 'scale(1.08)';
    });
    btn.addEventListener('mouseleave', function () {
      btn.style.color = 'var(--text2,#8b949e)';
      btn.style.transform = 'scale(1)';
    });
    document.body.appendChild(btn);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }

  window.addEventListener('storage', function (event) {
    if (event.key === STORAGE_KEY) {
      apply(event.newValue || 'light');
      const btn = document.getElementById('mod-theme-switch');
      if (btn) btn.textContent = current() === 'system' ? '◐' : current() === 'light' ? '☾' : '☀';
    }
  });

  window.matchMedia?.('(prefers-color-scheme: dark)').addEventListener?.('change', function () {
    if (current() === 'system') apply('system');
  });
})();
