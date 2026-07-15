import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { MarketDecisionValidationPayload } from "@/lib/control-room/types";

import { MarketDecisionEvidenceView } from "./MarketDecisionEvidencePanel";


const payload: MarketDecisionValidationPayload = {
  status: "partial",
  source: {
    dataset: "sap_successfactors_talent_simulation_inputs",
    source_id: "WB-TALENTO",
    input_status: "ready",
    source_mode: "benchmark_internal",
    employee_count: 1288,
    confidence: 0.6,
  },
  market_context: {
    provider: "banxico",
    metric_name: "usd_mxn_fix",
    as_of: "2026-07-10",
    unit: "mxn_per_usd",
    confidence: 1,
    freshness_status: "ready",
    distribution: { low: 17.1, mode: 17.5, high: 17.9 },
  },
  simulation: {
    simulation_id: "mc-e2e",
    model_version: "sf_market_validation.v1",
    output_metric: "cost",
    p10: 40,
    p50: 80,
    p90: 180,
    market_evidence_count: 1,
  },
  bayes: {
    status: "insufficient_data",
    calibration_group: "sap_successfactors:talent_readiness",
    sample_count: 0,
    evidence_policy: "evidence_only",
  },
  orchestration: {
    orchestration_id: "orch-e2e",
    problem_type: "risk_forecast",
    action_recommended: false,
    external_action_id: null,
  },
  policy: {
    recommendation_only: true,
    causal_claim: false,
    financial_forecast: false,
    creates_calibration_observation: false,
    automatic_action: false,
    external_writeback: false,
  },
};


describe("MarketDecisionEvidenceView", () => {
  it("shows governed evidence and the recommendation-only boundary", () => {
    const markup = renderToStaticMarkup(
      <MarketDecisionEvidenceView
        payload={payload}
        loading={false}
        running={false}
        error=""
        onRun={vi.fn()}
      />,
    );

    expect(markup).toContain("1,288 empleados");
    expect(markup).toContain("USD/MXN al 2026-07-10");
    expect(markup).toContain("Banda P10–P90: 40–180");
    expect(markup).toContain("evidencia externa sin recalibración automática");
    expect(markup).toContain("Sin causalidad declarada, acción automática ni write-back");
    expect(markup).toContain("Referencia interna");
  });

  it("does not invent values before the first validation", () => {
    const markup = renderToStaticMarkup(
      <MarketDecisionEvidenceView
        payload={null}
        loading={false}
        running={false}
        error=""
        onRun={vi.fn()}
      />,
    );

    expect(markup).toContain("Sin contexto usado todavía");
    expect(markup).toContain("Pendiente de ejecución manual");
    expect(markup).not.toContain("1,288");
  });
});
