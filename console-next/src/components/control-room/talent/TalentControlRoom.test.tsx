import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type {
  SfTalentConfianza,
  SfTalentDesempenoCohort,
  SfTalentNineBoxCell,
  SfTalentRosterPayload,
} from "@/lib/control-room/types";

import {
  ConfianzaTilesPanel,
  DesempenoDisponiblePanel,
  MaskedTalentRoster,
  NineBoxMatrix,
  SindicalizadoPanel,
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
          employee_key: "tal_abc123456789",  // gitleaks:allow
          display_name: "Colaborador 6789",
          role: "Manager",
          unit: "People",
          region: "Monterrey",
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

  it("renders minimal public projection fields without undefined values", () => {
    const cells: SfTalentNineBoxCell[] = [
      {
        display_order: 0,
        employee_count: 0,
        ready_count: 0,
        blocked_count: 0,
      },
    ];
    const payload: SfTalentRosterPayload = {
      status: "empty",
      count: 1,
      box: { display_order: 0 },
      roster: [{}],
    };

    const markup = `${renderToStaticMarkup(<NineBoxMatrix cells={cells} onSelect={vi.fn()} />)}${renderToStaticMarkup(<MaskedTalentRoster payload={payload} loading={false} />)}`;

    expect(markup).toContain("Segmento de talento");
    expect(markup).toContain("Colaborador enmascarado");
    expect(markup).not.toContain("undefined");
  });
});

describe("TalentControlRoom copy en español", () => {
  it("no conserva los textos en inglés de la pantalla de talento", () => {
    const markup = renderToStaticMarkup(<TalentControlRoom />);

    expect(markup).not.toContain("Recommendation only");
    expect(markup).not.toContain("PII safe");
    expect(markup).not.toContain("Gold Talent");
    expect(markup).toContain("Solo recomendación");
    expect(markup).toContain("Datos personales protegidos");
    expect(markup).toContain("Talento (capa oro)");
  });

  it("el tooltip de pendiente ya no contradice a la deducción por trayectoria", () => {
    const cohort: SfTalentDesempenoCohort = {
      count: 1,
      band_counts: { high: 1, medium: 0, low: 0 },
      roster: [
        {
          employee_key: "tal_1",
          display_name: "M. R.",
          role: "Analista",
          unit: "Finanzas",
          performance_band_available: "high",
          potential_pending: true,
          fit_band: "insufficient_data",
        },
      ],
      roster_truncated: false,
    };
    const markup = renderToStaticMarkup(<DesempenoDisponiblePanel cohort={cohort} />);

    expect(markup).not.toContain("No se infiere del desempeño.");
    expect(markup).toContain("nunca rellena desempeño faltante o inválido");
    expect(markup).toContain("ni C/P/A ni la trayectoria son calculables");
  });
});

describe("Potencial deducido de trayectoria", () => {
  it("muestra el conteo deducido en la celda con su tooltip honesto", () => {
    const cells: SfTalentNineBoxCell[] = [
      {
        box_id: "estrella",
        box_label: "Estrella",
        potential_band: "high",
        performance_band: "high",
        movement_action: "Sucesion",
        display_order: 1,
        employee_count: 3,
        ready_count: 3,
        deduced_count: 2,
        blocked_count: 0,
        status: "ready",
      },
    ];

    const markup = renderToStaticMarkup(<NineBoxMatrix cells={cells} onSelect={vi.fn()} />);

    expect(markup).toContain("2 con potencial deducido de trayectoria");
    expect(markup).toContain(
      "Potencial calculado por trayectoria y desempeño real observado (sin PII expuesta)",
    );
  });

  it("no muestra la insignia deducida cuando el conteo es cero", () => {
    const cells: SfTalentNineBoxCell[] = [
      {
        box_id: "core",
        box_label: "Core",
        display_order: 1,
        employee_count: 2,
        ready_count: 2,
        deduced_count: 0,
        blocked_count: 0,
        status: "ready",
      },
    ];

    const markup = renderToStaticMarkup(<NineBoxMatrix cells={cells} onSelect={vi.fn()} />);

    expect(markup).not.toContain("potencial deducido de trayectoria");
  });

  it("marca en el roster las filas con potencial deducido", () => {
    const payload: SfTalentRosterPayload = {
      status: "ready",
      count: 2,
      box: { box_id: "estrella", box_label: "Estrella", display_order: 1 },
      roster: [
        {
          employee_key: "tal_abc123456789", // gitleaks:allow
          display_name: "Colaborador 6789",
          role: "Manager",
          unit: "People",
          potential_basis: "trayectoria_observada",
          fit_band: "high",
          data_status: "ready",
        },
        {
          employee_key: "tal_def123456789", // gitleaks:allow
          display_name: "Colaborador 3456",
          role: "Analista",
          unit: "Finanzas",
          potential_basis: "cpa_observado",
          fit_band: "high",
          data_status: "ready",
        },
      ],
      blockers: [],
    };

    const markup = renderToStaticMarkup(<MaskedTalentRoster payload={payload} loading={false} />);

    expect(markup.match(/Potencial deducido/g)).toHaveLength(1);
  });
});

describe("ConfianzaTilesPanel", () => {
  const confianza: SfTalentConfianza = {
    estrellas_en_riesgo: { count: 4, employee_keys: ["tal_abc123456789"] }, // gitleaks:allow
    vacantes_criticas_sin_sucesor: { count: 2, roles: ["Manager", "Representante"] },
    cobertura_certificaciones: { coverage_pct: 60, completed_events: 60, learning_events: 100 },
    exposicion_monetaria: {
      totals: [
        {
          risk_band: "high",
          currency: "MXN",
          headcount: 11,
          annualized_comp_total: 1000000,
          annualized_comp_avg: 90909.09,
        },
      ],
    },
  };

  it("pinta los cuatro tiles con datos reales del payload", () => {
    const markup = renderToStaticMarkup(<ConfianzaTilesPanel confianza={confianza} />);

    expect(markup).toContain("Estrellas en riesgo de fuga");
    expect(markup).toContain("Vacantes críticas sin sucesor");
    expect(markup).toContain("Manager, Representante");
    expect(markup).toContain("Cobertura de certificaciones");
    expect(markup).toContain("60%");
    expect(markup).toContain("Exposición monetaria");
    expect(markup).toContain("riesgo Alto");
    expect(markup).toContain("grupos de 5+ personas");
    expect(markup).toContain("cifras redondeadas para proteger la privacidad");
    expect(markup).not.toContain("Sin información");
  });

  it("cae a Sin información cuando las fuentes no existen", () => {
    const markup = renderToStaticMarkup(<ConfianzaTilesPanel confianza={null} />);

    expect(markup).toContain("Sin información (requiere Riesgo de retención conectado)");
    expect(markup).toContain(
      "Sin información (requiere proyección de Sucesión publicada y criticidad por rol)",
    );
    expect(markup).toContain("Sin información (requiere Aprendizaje conectado)");
    expect(markup).toContain("Sin información (los montos de compensación están protegidos)");
    expect(markup).not.toContain("$");
    expect(markup).not.toContain("undefined");
  });

  it("mantiene Vacantes en estado de espera aunque el resto tenga datos", () => {
    const markup = renderToStaticMarkup(
      <ConfianzaTilesPanel confianza={{ ...confianza, vacantes_criticas_sin_sucesor: null }} />,
    );

    expect(markup).toContain(
      "Sin información (requiere proyección de Sucesión publicada y criticidad por rol)",
    );
  });
});

describe("SindicalizadoPanel", () => {
  it("nombra las fuentes requeridas y la futura llave employee_class", () => {
    const markup = renderToStaticMarkup(<SindicalizadoPanel confianza={null} />);

    expect(markup).toContain("En espera de conexión");
    expect(markup).toContain("Escalafón");
    expect(markup).toContain("Tabulador salarial");
    expect(markup).toContain("Contrato colectivo");
    expect(markup).toContain("employee_class (EmpEmployment)");
    expect(markup).toContain("Cobertura de certificaciones (global, sin segmentar)");
    expect(markup).toContain("Sin información (requiere Aprendizaje conectado)");
  });

  it("enciende solo la señal real disponible: cobertura de certificaciones", () => {
    const markup = renderToStaticMarkup(
      <SindicalizadoPanel
        confianza={{
          cobertura_certificaciones: {
            coverage_pct: 42.5,
            completed_events: 17,
            learning_events: 40,
          },
        }}
      />,
    );

    expect(markup).toContain("dato real disponible");
    expect(markup).toContain("42.5%");
    expect(markup).not.toContain("Sin información (requiere Aprendizaje conectado)");
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
    expect(markup).toContain("Requiere Competencias y Aspiración");
    expect(markup).not.toContain("C + A");
    expect(markup).toContain("Alto");
    expect(markup).toContain("Medio");
    expect(markup).not.toContain("Listo");
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
