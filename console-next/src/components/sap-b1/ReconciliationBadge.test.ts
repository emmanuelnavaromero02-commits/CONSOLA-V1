import { describe, expect, it } from "vitest";

import { reconciliationBadgeState } from "./ReconciliationBadge";

describe("reconciliationBadgeState", () => {
  it("says the read failed before claiming there is no Finance run", () => {
    const unavailable = reconciliationBadgeState({ status: "unavailable", error: "unavailable: gold ausente", rows: null });
    expect(unavailable.tone).toBe("neutral");
    expect(unavailable.label).toBe("No disponible por ahora.");
    expect(unavailable.detail).toBe("unavailable: gold ausente");

    const errored = reconciliationBadgeState({ status: "error", rows: 10, within: 10 });
    expect(errored.label).toBe("No disponible por ahora.");
  });

  it("says there is no Finance run only when the read succeeded with no rows", () => {
    expect(reconciliationBadgeState(undefined)).toEqual({
      tone: "neutral",
      label: "Sin corrida de Finanzas cargada",
      detail: null,
      note: null,
    });
    expect(reconciliationBadgeState({ status: "degraded", rows: 0, within: 0 }).label).toBe(
      "Sin corrida de Finanzas cargada",
    );
    expect(reconciliationBadgeState({ status: "ready", rows: null, within: null }).label).toBe(
      "Sin corrida de Finanzas cargada",
    );
  });

  it("is green only when no row is outside the per-row tolerance", () => {
    const state = reconciliationBadgeState({
      status: "ready",
      rows: 10,
      within: 8,
      outside: 0,
      without_platform: 2,
      tolerance_pct: 1,
    });
    expect(state.tone).toBe("good");
    expect(state.label).toBe("Conciliación dentro de tolerancia");
    expect(state.detail).toBe(
      "8 de 10 filas de Finanzas dentro de la tolerancia de 1 % · 2 sin dato de plataforma",
    );
    expect(state.note).toBeNull();
  });

  it("shows the real share outside and never reuses the per-row tolerance as a share threshold", () => {
    const outside = reconciliationBadgeState({ status: "ready", rows: 10, within: 8, outside: 2 });
    expect(outside.tone).toBe("warning");
    expect(outside.label).toBe("20 % fuera de tolerancia");
    expect(outside.detail).toBe("8 de 10 filas de Finanzas dentro de la tolerancia de 1 %");

    const tinyShare = reconciliationBadgeState({
      status: "ready",
      rows: 1000,
      within: 995,
      outside: 5,
      tolerance_pct: 25,
    });
    expect(tinyShare.tone).toBe("warning");
    expect(tinyShare.label).toBe("0.5 % fuera de tolerancia");
    expect(tinyShare.detail).toBe("995 de 1,000 filas de Finanzas dentro de la tolerancia de 25 %");
  });

  it("treats missing outside counts conservatively", () => {
    const state = reconciliationBadgeState({ status: "ready", rows: 10, within: 9 });
    expect(state.tone).toBe("warning");
    expect(state.label).toBe("10 % fuera de tolerancia");
  });

  it("shows the configured tolerance from the payload in the detail", () => {
    const state = reconciliationBadgeState({ status: "ready", rows: 4, within: 4, outside: 0, tolerance_pct: 1.5 });
    expect(state.detail).toBe("4 de 4 filas de Finanzas dentro de la tolerancia de 1.5 %");
  });

  it("flags possibly stale data when the metric is degraded with rows", () => {
    const degraded = reconciliationBadgeState({ status: "degraded", rows: 10, within: 10, outside: 0 });
    expect(degraded.tone).toBe("good");
    expect(degraded.note).toBe("Datos posiblemente desactualizados");

    const ready = reconciliationBadgeState({ status: "ready", rows: 10, within: 10, outside: 0 });
    expect(ready.note).toBeNull();
  });
});
