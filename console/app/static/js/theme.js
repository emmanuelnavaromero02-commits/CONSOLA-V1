const STORAGE_KEY = 'mod-theme';

function systemTheme() {
  return window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

export function getThemePreference() {
  return localStorage.getItem(STORAGE_KEY) || 'light';
}

export function resolveTheme(value = getThemePreference()) {
  return value === 'system' ? systemTheme() : value;
}

export function applyTheme(value = getThemePreference()) {
  const preference = value || 'light';
  localStorage.setItem(STORAGE_KEY, preference);
  document.documentElement.dataset.theme = resolveTheme(preference);
  document.documentElement.dataset.themePreference = preference;
}

export function cycleTheme() {
  const current = getThemePreference();
  const next = current === 'light' ? 'dark' : current === 'dark' ? 'system' : 'light';
  applyTheme(next);
  return next;
}

applyTheme(getThemePreference());

window.addEventListener('storage', (event) => {
  if (event.key === STORAGE_KEY) applyTheme(event.newValue || 'light');
});

window.matchMedia?.('(prefers-color-scheme: dark)').addEventListener?.('change', () => {
  if (getThemePreference() === 'system') applyTheme('system');
});
