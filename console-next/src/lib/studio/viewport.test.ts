import { describe, expect, it } from "vitest";

import {
  actualSizeView,
  atMaxZoom,
  atMinZoom,
  clampZoom,
  fitView,
  IDENTITY_VIEW,
  panBy,
  revealBox,
  wheelFactor,
  ZOOM_MAX,
  ZOOM_MIN,
  zoomAt,
  zoomPercent,
} from "./viewport";

describe("viewport", () => {
  it("clamps zoom to 0.4x–2.5x", () => {
    expect(clampZoom(10)).toBe(ZOOM_MAX);
    expect(clampZoom(0.01)).toBe(ZOOM_MIN);
    expect(clampZoom(Number.NaN)).toBe(1);
    expect(clampZoom(1.3)).toBe(1.3);
  });

  it("zooms around the pointer keeping the world point fixed", () => {
    expect(zoomAt({ x: 0, y: 0, k: 1 }, 2, { x: 100, y: 50 })).toEqual({ k: 2, x: -100, y: -50 });
    const capped = zoomAt({ x: 0, y: 0, k: 2 }, 2, { x: 100, y: 50 });
    expect(capped.k).toBe(2.5);
    expect(capped.x).toBeCloseTo(-25);
    expect(capped.y).toBeCloseTo(-12.5);
  });

  it("returns the same view at the bounds", () => {
    const max = { x: 3, y: 4, k: ZOOM_MAX };
    expect(zoomAt(max, 2, { x: 10, y: 10 })).toBe(max);
    const min = { x: 3, y: 4, k: ZOOM_MIN };
    expect(zoomAt(min, 0.5, { x: 10, y: 10 })).toBe(min);
    expect(atMaxZoom(max)).toBe(true);
    expect(atMinZoom(min)).toBe(true);
    expect(atMinZoom({ x: 0, y: 0, k: 1 })).toBe(false);
  });

  it("pans by a delta", () => {
    expect(panBy({ x: 10, y: 20, k: 1.5 }, -48, 48)).toEqual({ x: -38, y: 68, k: 1.5 });
  });

  it("centres content that fits and never enlarges it", () => {
    expect(fitView({ width: 480, height: 160 }, { width: 1000, height: 600 })).toEqual({ k: 1, x: 260, y: 220 });
  });

  it("left/top aligns oversize content at the padding and respects the minimum zoom", () => {
    expect(fitView({ width: 3000, height: 2000 }, { width: 1000, height: 600 })).toEqual({ k: 0.4, x: 24, y: 24 });
    const partial = fitView({ width: 1500, height: 300 }, { width: 1000, height: 600 });
    expect(partial.k).toBeCloseTo(952 / 1500);
    expect(partial.x).toBeCloseTo(24);
    expect(partial.y).toBeCloseTo((600 - 300 * partial.k) / 2);
  });

  it("returns identity when a size is unknown (jsdom)", () => {
    expect(fitView({ width: 480, height: 160 }, { width: 0, height: 0 })).toBe(IDENTITY_VIEW);
    expect(fitView({ width: 0, height: 160 }, { width: 800, height: 600 })).toBe(IDENTITY_VIEW);
    expect(actualSizeView({ width: 480, height: 160 }, { width: 0, height: 600 })).toBe(IDENTITY_VIEW);
  });

  it("places the 1:1 view like the fit", () => {
    expect(actualSizeView({ width: 480, height: 160 }, { width: 1000, height: 600 })).toEqual({ k: 1, x: 260, y: 220 });
    expect(actualSizeView({ width: 3000, height: 160 }, { width: 1000, height: 600 })).toEqual({ k: 1, x: 24, y: 220 });
  });

  it("turns wheel deltas into bounded zoom factors", () => {
    expect(wheelFactor(100)).toBeLessThan(1);
    expect(wheelFactor(-100)).toBeGreaterThan(1);
    expect(wheelFactor(-100000)).toBeCloseTo(Math.exp(0.75));
    expect(wheelFactor(1000)).toBeCloseTo(Math.exp(-0.75));
    expect(wheelFactor(3, 1)).toBeCloseTo(Math.exp(-48 * 0.0015));
    expect(wheelFactor(1, 2)).toBeCloseTo(Math.exp(-400 * 0.0015));
    expect(wheelFactor(Number.NaN)).toBe(1);
  });

  it("formats the zoom as a whole percentage", () => {
    expect(zoomPercent(1)).toBe(100);
    expect(zoomPercent(1.2)).toBe(120);
    expect(zoomPercent(0.4)).toBe(40);
    expect(zoomPercent(2.5)).toBe(250);
  });

  it("pans just enough to reveal a box, keeping the padding and the drawer inset clear", () => {
    const view = { x: 24, y: 106, k: 1 };
    const box = { x: 912, y: 52, width: 200, height: 56 };
    expect(revealBox(view, box, { width: 800, height: 420 })).toEqual({ x: -336, y: 106, k: 1 });
    expect(revealBox(view, box, { width: 800, height: 420 }, 24, 420)).toEqual({ x: -756, y: 106, k: 1 });
    expect(revealBox(view, { x: 0, y: 900, width: 200, height: 56 }, { width: 800, height: 420 })).toEqual({ x: 24, y: -560, k: 1 });
    expect(revealBox({ x: -500, y: -40, k: 0.5 }, { x: 24, y: 52, width: 200, height: 56 }, { width: 800, height: 420 })).toEqual({
      x: 12,
      y: -2,
      k: 0.5,
    });
  });

  it("keeps the left edge visible when the box is wider than the free space and ignores unknown sizes", () => {
    expect(revealBox({ x: 0, y: 0, k: 1 }, { x: 500, y: 24, width: 200, height: 56 }, { width: 300, height: 420 }, 24, 200)).toEqual({
      x: -476,
      y: 0,
      k: 1,
    });
    const visible = { x: 24, y: 24, k: 1 };
    expect(revealBox(visible, { x: 0, y: 0, width: 200, height: 56 }, { width: 800, height: 420 })).toBe(visible);
    expect(revealBox(visible, { x: 5000, y: 0, width: 200, height: 56 }, { width: 0, height: 0 })).toBe(visible);
  });
});
