import { loadCartridges } from './cartridges.js';
import { installModalGlobals } from './modals.js';
import { renderStudioIsland } from './render.js';

installModalGlobals();

async function render(reload = false) {
  const root = document.getElementById('studio-modern-root');
  if (!root) return;
  if (reload || !root.hasChildNodes()) {
    await loadCartridges();
  }
  renderStudioIsland(root, render);
}

export async function initStudioModernIsland() {
  try {
    const legacyRoot = document.getElementById('step-content');
    if (legacyRoot) {
      const root = document.getElementById('studio-modern-root');
      if (root) root.replaceChildren();
      document.body.classList.remove('studio-modern-ready');
      return;
    }
    await render(true);
  } catch (error) {
    console.warn('Studio modern island fallback:', error);
  }
}

document.addEventListener('DOMContentLoaded', initStudioModernIsland);
