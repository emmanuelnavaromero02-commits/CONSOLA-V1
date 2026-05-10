export const state = {
  cartridges: [],
  selectedCartridge: null,
  loading: false,
  error: null,
  modalOpen: false,
};

export function setState(patch) {
  Object.assign(state, patch);
}
