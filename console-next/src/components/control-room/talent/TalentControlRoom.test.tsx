import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { SfTalentDesempenoCohort, SfTalentNineBoxCell, SfTalentRosterPayload } from "@/lib/control-room/types";

import {
  DesempenoDisponiblePanel,
  MaskedTalentRoster,
  NineBoxMatrix,
  TalentControlRoom,
} from "./TalentControlRoom";

describe("TalentControlRoom native panels", () => {
  it("does not publish legacy actions without an authorized V2 action", () => {
    const markup = renderToStaticMarkup(<TalentControlRoom />);

    expect(markup).not.toContain("Generar preview");
    expect(markup).not.toContain("Ciclo OMEGA");
    expect(markup).not.toContain("Simulacion");
    expect(markup).toContain("exclusivamente de lectura");
    expect(markup).toContain("sin preview ni write-back");
    expect(markup).not.toContain("decisiones y previews");
    expect(markup).not.toContain("preview supervisado");
  });

  it("renders the 9-box matrix as native React buttons", () => {
    const cells: SfTalentNineBoxCell[] = [
      {
        box_id: "estrella",
        box_label: "Estrella",
        potential_band: "high",
        performance_band: "high",
        movement_action: "Sucesion",
        display_order: 1,
        employee_count: 2,
        ready_count: 2,
        blocked_count: 0,
        status: "ready",
      },
    ];

    const markup = renderToStaticMarkup(
      <NineBoxMatrix cells={cells} selectedBoxId="estrella" onSelect={vi.fn()} />,
    );

    expect(markup).toContain("Estrella");
    expect(markup).toContain("button");
    expect(markup).not.toContain("iframe");
    expect(markup).not.toContain("omega-9box.html");
  });

  it("renders internal reference without marking empty boxes as blocked", () => {
    const cells: SfTalentNineBoxCell[] = [
      {
        box_id: "riesgo",
        box_label: "Riesgo",
        potential_band: "low",
        performance_band: "low",
        movement_action: "Gestionar riesgo",
        display_order: 1,
        employee_count: 8,
        ready_count: 8,
        reference_count: 8,
        blocked_count: 0,
        status: "benchmark_internal",
      },
      {
        box_id: "estrella",
        box_label: "Estrella",
        potential_band: "high",
        performance_band: "high",
        movement_action: "Sucesion",
        display_order: 2,
        employee_count: 0,
        ready_count: 0,
        blocked_count: 0,
        status: "empty",
      },
    ];

    const markup = renderToStaticMarkup(<NineBoxMatrix cells={cells} onSelect={vi.fn()} />);

    expect(markup).toContain("Referencia interna");
    expect(markup).toContain("Sin empleados");
    expect(markup).not.toContain("benchmark_internal");
  });

  it("renders only masked roster fields", () => {
    const payload: SfTalentRosterPayload = {
      status: "ready",
      count: 1,
      box: {
        box_id: "estrella",
        box_label: "Estrella",
        potential_band: "high",
        performance_band: "high",
        movement_action: "Sucesion",
        display_order: 1,
      },
      roster: [
        {
          employee_key: "tal_abc123456789",
          display_name: "Colaborador 6789",
          role: "Manager",
          unit: "People",
          region: "Monterrey",
          readiness_status: "ready",
          box_id: "estrella",
          box_label: "Estrella",
          performance_band: "high",
          potential_band: "high",
          fit_band: "high",
          movement_age_bucket: "12-24m",
          data_status: "ready",
        },
      ],
      blockers: [],
    };

    const markup = renderToStaticMarkup(<MaskedTalentRoster payload={payload} loading={false} />);

    expect(markup).toContain("Colaborador 6789");
    expect(markup).toContain("tal_abc123456789");
    expect(markup).not.toContain("Ana Gomez");
    expect(markup).not.toContain("user_id");
    expect(markup).not.toContain("full_name");
  });
});

describe("DesempenoDisponiblePanel (Opción 1 B + C)", () => {
  const cohort: SfTalentDesempenoCohort = {
    count: 257,
    band_counts: { high: 120, medium: 90, low: 47 },
    roster: [
      { employee_key: "tal_1", display_name: "M. R.", role: "Analista", unit: "Finanzas", performance_band_available: "high", potential_pending: true, fit_band: "insufficient_data" },
      { employee_key: "tal_2", display_name: "A. G.", role: "RH", unit: "RH", performance_band_available: "medium", potential_pending: true, fit_band: "insufficient_data" },
    ],
    roster_truncated: true,
  };

  it("muestra el contador y mantiene la separación Desempeño / Potencial / Fit", () => {
    const markup = renderToStaticMarkup(<DesempenoDisponiblePanel cohort={cohort} />);
    expect(markup).toContain("Desempeño disponible");
    expect(markup).toContain("257");
    expect(markup).toContain("esperando Competencias y Aspiración");
    // Potencial pendiente + Fit no inferido (copy aprobado, sin abreviatura "C + A")
    expect(markup).toContain("Requiere Competencias y Aspiración");
    expect(markup).not.toContain("C + A");
    // Banda ordinal Alto/Medio/Bajo
    expect(markup).toContain("Alto");
    expect(markup).toContain("Medio");
    // Nunca el verde "Listo" (reservado a C/P/A completo)
    expect(markup).not.toContain("Listo");
    // Tooltip explicando por qué Potencial permanece pendiente
    expect(markup).toContain("El Potencial requiere Competencias y Aspiración");
  });

  it("no renderiza nada cuando no hay cohorte", () => {
    expect(renderToStaticMarkup(<DesempenoDisponiblePanel cohort={null} />)).toBe("");
    expect(
      renderToStaticMarkup(
        <DesempenoDisponiblePanel cohort={{ count: 0, band_counts: { high: 0, medium: 0, low: 0 }, roster: [], roster_truncated: false }} />,
      ),
    ).toBe("");
  });
});
