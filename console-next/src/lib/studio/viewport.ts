export const ZOOM_MIN = 0.4;
export const ZOOM_MAX = 2.5;
export const ZOOM_STEP = 1.2;
export const FIT_PADDING = 24;
export const PAN_STEP = 48;

const WHEEL_SENSITIVITY = 0.0015;
const WHEEL_MAX_PX = 500;
const LINE_PX = 16;
const PAGE_PX = 400;
const EPSILON = 1e-6;

export interface ViewState {
  x: number;
  y: number;
  k: number;
}

export interface Size {
  width: number;
  height: number;
}

export interface Point {
  x: number;
  y: number;
}

export const IDENTITY_VIEW: ViewState = { x: 0, y: 0, k: 1 };

export function clampZoom(k: number): number {
  if (!Number.isFinite(k)) return 1;
  return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, k));
}

export function atMinZoom(view: ViewState): boolean {
  return view.k <= ZOOM_MIN + EPSILON;
}

export function atMaxZoom(view: ViewState): boolean {
  return view.k >= ZOOM_MAX - EPSILON;
}

export function zoomAt(view: ViewState, factor: number, point: Point): ViewState {
  const k = clampZoom(view.k * factor);
  if (Math.abs(k - view.k) < EPSILON) return view;
  const worldX = (point.x - view.x) / view.k;
  const worldY = (point.y - view.y) / view.k;
  return { k, x: point.x - worldX * k, y: point.y - worldY * k };
}

export function panBy(view: ViewState, dx: number, dy: number): ViewState {
  return { ...view, x: view.x + dx, y: view.y + dy };
}

function hasArea(size: Size): boolean {
  return size.width > 0 && size.height > 0;
}

function place(content: Size, viewport: Size, k: number, pad: number): ViewState {
  const width = content.width * k;
  const height = content.height * k;
  return {
    k,
    x: width + 2 * pad <= viewport.width ? (viewport.width - width) / 2 : pad,
    y: height + 2 * pad <= viewport.height ? (viewport.height - height) / 2 : pad,
  };
}

export function fitView(content: Size, viewport: Size, pad = FIT_PADDING): ViewState {
  if (!hasArea(content) || !hasArea(viewport)) return IDENTITY_VIEW;
  const k = clampZoom(
    Math.min((viewport.width - 2 * pad) / content.width, (viewport.height - 2 * pad) / content.height, 1),
  );
  return place(content, viewport, k, pad);
}

export function actualSizeView(content: Size, viewport: Size, pad = FIT_PADDING): ViewState {
  if (!hasArea(content) || !hasArea(viewport)) return IDENTITY_VIEW;
  return place(content, viewport, 1, pad);
}

export function wheelFactor(deltaY: number, deltaMode = 0): number {
  const scale = deltaMode === 1 ? LINE_PX : deltaMode === 2 ? PAGE_PX : 1;
  const px = Math.max(-WHEEL_MAX_PX, Math.min(WHEEL_MAX_PX, (Number.isFinite(deltaY) ? deltaY : 0) * scale));
  return Math.exp(-px * WHEEL_SENSITIVITY);
}

export function zoomPercent(k: number): number {
  return Math.round(k * 100);
}
