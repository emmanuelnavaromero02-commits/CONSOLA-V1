import { describe, expect, it } from "vitest";

import {
  analyticAppHref,
  areaColor,
  metricSummary,
  semaforoAreas,
  semaforoHeadline,
  setupChecklist,
} from "./present";
import type { SapB1Overview } from "./types";

const overview: SapB1Overview = {
  installed: "active",
  connection: { present: true },
  parameters: { loaded: true, valid: true, keys_total: 20, keys_set: 18, missing: [], using_default: ["safety_days"], branches: 4, accounts: 2 },
  connector: {
    present: true,
    readable: true,
    age_seconds: 300,
    source_ok: true,
    initial_load: { state: "running", months_done: 10, months_total: 24 },
  },
  dags: [
    { dag_id: "sap_b1_refresh", present: true, paused: false },
    { dag_id: "dataset_refresh_chain", present: true, paused: true },
    { dag_id: "agent_runner", present: null, paused: null },
  ],
  digest: { recipients: 0, transport: "sin_configurar", last: null },
};

describe("sap-b1 presentation rules", () => {
  it("colors areas like the 8 AM email: unavailable, breaches, degraded, else green", () => {
    expect(areaColor({ status: "unavailable", breaches: ["x"] })).toBe("sin_datos");
    expect(areaColor({ status: "ready", breaches: [" ", "Cliente bajo el mínimo"] })).toBe("rojo");
    expect(areaColor({ status: "degraded", breaches: [] })).toBe("amarillo");
    expect(areaColor({ status: "ready" })).toBe("verde");
    expect(areaColor(undefined)).toBe("sin_datos");
  });

  it("orders areas red first and keeps the reason when there are no findings", () => {
    const areas = semaforoAreas({
      calidad_datos: { status: "ready", period: "2026-08" },
      caducidad_lotes: { status: "ready", breaches: ["a", "b", "c", "d", "e", "f"], as_of: "2026-09-25" } as never,
      dias_cobertura: { status: "unavailable", error: "unavailable: dataset" },
      destructores: { status: "degraded", notes: ["sin corrida"] },
    });
    expect(areas.map((area) => [area.metric, area.color])).toEqual([
      ["caducidad_lotes", "rojo"],
      ["destructores", "amarillo"],
      ["dias_cobertura", "sin_datos"],
      ["calidad_datos", "verde"],
    ]);
    expect(areas[0].findings).toHaveLength(5);
    expect(areas[0].findingsTotal).toBe(6);
    expect(areas[0].period).toBe("2026-09-25");
    expect(areas[1].reason).toBe("sin corrida");
    expect(areas[2].reason).toBe("unavailable: dataset");
    expect(semaforoHeadline(areas)).toBe("1 área en rojo");
    expect(semaforoHeadline(areas.slice(1))).toBe("sin rojos, 1 área sin datos");
    expect(semaforoHeadline(areas.slice(3))).toBe("todo en verde");
  });

  it("builds the rehearsal checklist from the overview only", () => {
    const items = Object.fromEntries(setupChecklist(overview).map((item) => [item.id, item]));
    expect(items.installed.state).toBe("ok");
    expect(items.vault.state).toBe("ok");
    expect(items.parameters.state).toBe("ok");
    expect(items.connector.state).toBe("ok");
    expect(items.connector.detail).toContain("hace 5 min");
    expect(items.initial_load.state).toBe("pendiente");
    expect(items.initial_load.detail).toBe("En curso: 10 de 24 meses");
    expect(items["dag:sap_b1_refresh"].state).toBe("ok");
    expect(items["dag:dataset_refresh_chain"].detail).toContain("pausado");
    expect(items["dag:agent_runner"].state).toBe("desconocido");
    expect(items.digest.state).toBe("pendiente");
    expect(items.digest.detail).toContain("Transporte de correo sin configurar");

    const offline = setupChecklist({
      ...overview,
      installed: null,
      connection: { present: false },
      parameters: { loaded: true, valid: false, error: "unknown parameter kind" },
      connector: { present: true, age_seconds: 3600 },
    });
    expect(offline.filter((item) => item.state === "ok").map((item) => item.id)).toEqual(["dag:sap_b1_refresh"]);
    expect(offline.find((item) => item.id === "connector")?.detail).toContain("más de 15 min");
    expect(offline.find((item) => item.id === "initial_load")?.state).toBe("desconocido");
  });

  it("summarizes indicators without inventing values", () => {
    expect(metricSummary("margen_bruto", undefined).value).toBe("Sin datos todavía");
    expect(metricSummary("margen_bruto", { status: "unavailable", error: "sin dataset" })).toEqual({ value: "Sin datos", detail: "sin dataset" });
    const margin = metricSummary("margen_bruto", {
      status: "ready",
      currency: "MXN",
      group: { value: 1500000, pct: 32.45 },
      companies: [{ company: "mx", pct: 30 }],
    } as never);
    expect(margin.value).toBe("32.5 % grupo");
    expect(margin.detail).toBe("1,500,000 MXN · mx 30 %");
    expect(metricSummary("reconciliacion_finanzas", { status: "degraded", rows: 0, notes: ["Finanzas no ha cargado su corrida"] } as never)).toEqual({
      value: "Sin corrida de Finanzas",
      detail: "Finanzas no ha cargado su corrida",
    });
    expect(metricSummary("dias_cobertura", { status: "ready", colors: { rojo: 2 }, stockout_risk: 1, critical_at_risk: 0 } as never).value).toBe("2 en rojo");
    expect(analyticAppHref("sap_b1_margen")).toBe("/analytics/viewer?app=sap_b1_margen");
  });
});
