import { describe, expect, it } from "vitest";

import { reconciliationBadgeState } from "./ReconciliationBadge";

describe("reconciliationBadgeState", () => {
  it("says there is no Finance run instead of inventing a number", () => {
    expect(reconciliationBadgeState(undefined)).toEqual({
      tone: "neutral",
      label: "Sin corrida de Finanzas cargada",
      detail: null,
    });
    expect(reconciliationBadgeState({ rows: 0, within: 0 })).toEqual({
      tone: "neutral",
      label: "Sin corrida de Finanzas cargada",
      detail: null,
    });
    expect(reconciliationBadgeState({ rows: null, within: null }).label).toBe(
      "Sin corrida de Finanzas cargada",
    );
  });

  it("computes the ok share from the real rows when within_pct is missing", () => {
    const state = reconciliationBadgeState({ rows: 10, within: 8 });
    expect(state.label).toBe("Conciliación 80 %");
    expect(state.tone).toBe("warning");
    expect(state.detail).toBe("8 de 10 filas de Finanzas dentro de la tolerancia de 1 %");
  });

  it("is green only when the share outside stays under the tolerance", () => {
    const state = reconciliationBadgeState({ rows: 1000, within: 995, within_pct: 99.5, tolerance_pct: 1 });
    expect(state.tone).toBe("good");
    expect(state.label).toBe("Conciliación 99.5 %");
    expect(state.detail).toBe("995 de 1,000 filas de Finanzas dentro de la tolerancia de 1 %");
  });

  it("uses the tolerance configured in business parameters when the payload carries it", () => {
    const wide = reconciliationBadgeState({ rows: 10, within: 8, within_pct: 80, tolerance_pct: 25 });
    expect(wide.tone).toBe("good");
    expect(wide.label).toBe("Conciliación 80 %");
    expect(wide.detail).toBe("8 de 10 filas de Finanzas dentro de la tolerancia de 25 %");

    const strict = reconciliationBadgeState({ rows: 10, within: 9, within_pct: 90 });
    expect(strict.tone).toBe("warning");
    expect(strict.label).toBe("Conciliación 90 %");
  });
});
