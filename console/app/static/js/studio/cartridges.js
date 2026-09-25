import { createCartridge as apiCreateCartridge, getCartridge, getCartridgeStatus, listCartridges } from './api.js';
import { setState, state } from './state.js';

async function attachStatus(cartridge) {
  if (!cartridge?.id) return cartridge;
  try {
    const probe = await getCartridgeStatus(cartridge.id);
    return { ...cartridge, status: probe.status, statusReason: probe.reason || probe.detail || null };
  } catch (error) {
    return { ...cartridge, status: 'unknown', statusReason: error.message };
  }
}

export async function loadCartridges() {
  setState({ loading: true, error: null });
  try {
    const cartridges = await listCartridges();
    const selectedId = window._currentCartridge?.id || document.getElementById('cartridge-sel')?.value || cartridges[0]?.id;
    let selected = cartridges.find((item) => item.id === selectedId) || cartridges[0] || null;
    if (selected?.id) {
      selected = await getCartridge(selected.id).catch(() => selected);
      selected = await attachStatus(selected);
    }
    setState({ cartridges, selectedCartridge: selected, loading: false });
  } catch (error) {
    setState({ cartridges: [], selectedCartridge: null, loading: false, error: error.message });
  }
}

export async function selectCartridge(id) {
  if (!id) return;
  let selected = await getCartridge(id).catch(() => state.cartridges.find((item) => item.id === id) || null);
  if (!selected) {
    setState({ selectedCartridge: null, error: `No se encontró el cartucho ${id}` });
    return;
  }
  selected = await attachStatus(selected);
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
