import { describe, expect, it } from "vitest";

import { CREATIVE, CURRENT_STYLE_LABEL, PRECISION, RESPONSE_STYLES, responseStyleFor, temperatureFor } from "./presets";

describe("response styles", () => {
  it("offers precision and creative presets with fixed temperatures", () => {
    expect(RESPONSE_STYLES.map((style) => [style.id, style.label, style.temperature])).toEqual([
      ["precision", "Máxima Precisión (Hechos exactos)", 0.1],
      ["creative", "Creativo y Exploratorio", 0.8],
    ]);
    expect(PRECISION).toBe(0.1);
    expect(CREATIVE).toBe(0.8);
    expect(temperatureFor("precision")).toBe(0.1);
    expect(temperatureFor("creative")).toBe(0.8);
  });

  it("keeps any other stored value as the current style", () => {
    expect(responseStyleFor(0.1)).toBe("precision");
    expect(responseStyleFor(0.8)).toBe("creative");
    expect(responseStyleFor(0.4)).toBe("current");
    expect(responseStyleFor(null)).toBe("current");
    expect(responseStyleFor(Number.NaN)).toBe("current");
    expect(CURRENT_STYLE_LABEL).toBe("Equilibrado (valor actual)");
  });
});
