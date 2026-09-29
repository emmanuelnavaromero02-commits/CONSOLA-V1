import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MiniBar, OriginBadge, ReadinessBadge, originLabels, readinessLabels, readinessTone } from "./StatusBadge";

describe("Control Room readiness states", () => {
  it.each([
    ["ready", "Listo"],
    ["available", "Disponible"],
    ["partial", "Datos parciales"],
    ["stub", "En espera de conexión"],
    ["empty", "Sin datos configurados"],
    ["missing", "En espera de conexión"],
    ["unavailable", "Dependencia no configurada"],
    ["blocked", "Bloqueado"],
    ["no_permission", "Requiere permisos OData"],
    ["error", "Error operativo"],
  ] as const)("renders %s explicitly", (status, label) => {
    const markup = renderToStaticMarkup(<ReadinessBadge status={status} />);

    expect(readinessLabels[status]).toBe(label);
    expect(markup).toContain(label);
    expect(markup).not.toContain("undefined");
  });

  it("never renders a raw machine status", () => {
    expect(readinessLabels.blocked_by_sap).toBe("Requiere permisos en SAP");
    const markup = renderToStaticMarkup(<ReadinessBadge status={"weird_internal_state" as never} />);
    expect(markup).toContain("Sin información");
    expect(markup).not.toContain("weird_internal_state");
  });

  it("keeps blocked and no_permission visually distinct from ready", () => {
    expect(readinessTone("ready")).toContain("emerald");
    expect(readinessTone("blocked")).toContain("orange");
    expect(readinessTone("no_permission")).toContain("orange");
    expect(readinessTone("error")).toContain("destructive");
  });

  it("distingues 'available' del verde 'ready' y del naranja 'blocked'", () => {
    expect(readinessTone("available")).toContain("teal");
    expect(readinessTone("available")).not.toContain("emerald");
    expect(readinessTone("available")).not.toContain("orange");
  });

  it("renders a bounded lightweight chart without external libraries", () => {
    const markup = renderToStaticMarkup(<MiniBar value={30} max={100} label="Listos para decidir" />);

    expect(markup).toContain("Listos para decidir");
    expect(markup).toContain("30%");
  });

  it.each([
    ["rule", "Regla"],
    ["generic_gold_signal", "Gold generico"],
    ["intelligence_signal", "Intelligence"],
    ["bayesian_calibration", "Historial operativo"],
    ["monte_carlo", "Análisis operativo"],
    ["agent_alert", "Agent"],
  ] as const)("renders origin badge %s", (origin, label) => {
    const markup = renderToStaticMarkup(<OriginBadge origin={origin} />);

    expect(originLabels[origin]).toBe(label);
    expect(markup).toContain(label);
  });
});
