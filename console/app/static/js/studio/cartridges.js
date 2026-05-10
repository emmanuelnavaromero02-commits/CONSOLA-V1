import { createCartridge as apiCreateCartridge, getCartridge, listCartridges } from './api.js';
import { setState, state } from './state.js';

function fallbackCartridge() {
  return {
    id: 'replicon',
    name: 'Replicon PSA',
    version: '0.1.0',
    description: 'Cartucho operativo para extracción Bronze, datasets y pipelines Replicon.',
    pattern: 'dag-based',
    category: 'cartridge',
    entities: [],
    healthy: true,
  };
}

export async function loadCartridges() {
  setState({ loading: true, error: null });
  try {
    const cartridges = await listCartridges();
    const selectedId = window._currentCartridge?.id || document.getElementById('cartridge-sel')?.value || cartridges[0]?.id;
    let selected = cartridges.find((item) => item.id === selectedId) || cartridges[0] || fallbackCartridge();
    if (selected?.id) {
      selected = await getCartridge(selected.id).catch(() => selected);
    }
    setState({ cartridges: cartridges.length ? cartridges : [fallbackCartridge()], selectedCartridge: selected, loading: false });
  } catch (error) {
    setState({ cartridges: [fallbackCartridge()], selectedCartridge: fallbackCartridge(), loading: false, error: error.message });
  }
}

export async function selectCartridge(id) {
  if (!id) return;
  const selected = await getCartridge(id).catch(() => state.cartridges.find((item) => item.id === id) || fallbackCartridge());
  setState({ selectedCartridge: selected, error: null });
  if (typeof window.selectCartridge === 'function') {
    await window.selectCartridge(id);
  }
}

export async function createCartridge(payload) {
  const cartridge = await apiCreateCartridge(payload);
  setState({ selectedCartridge: cartridge, error: null });
  if (typeof window.__studioCartridgeCreated === 'function') {
    await window.__studioCartridgeCreated(cartridge, payload.id);
  }
  await loadCartridges();
  return cartridge;
}
