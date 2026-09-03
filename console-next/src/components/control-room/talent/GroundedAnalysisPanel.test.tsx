import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { AnalysisEnvelope } from "@/lib/control-room/types";

import { GroundedAnalysisEnvelopeView } from "./GroundedAnalysisPanel";

const verifiedEnvelope: AnalysisEnvelope = {
  analysis_run_id: "analysis-1",
  status: "verified",
  evidence_pack_id: 42,
  as_of: "2026-09-02T17:00:00Z",
  grounding_status: "verified",
  claims: [
    {
      claim_id: "observed-1",
      claim_type: "observed",
      statement: "La población observada incluye 120 perfiles.",
      value: 120,
      unit: "personas",
      population: 120,
      as_of: "2026-09-02T17:00:00Z",
      completeness: "complete",
      evidence_refs: [{ evidence_item_id: 7, path: "data.population_total" }],
      evidence_item_ids: [7],
      evidence_paths: ["data.population_total"],
      verification_status: "verified",
      verification_reason: null,
    },
    {
      claim_id: "computed-1",
      claim_type: "computed",
      statement: "La cobertura determinista es 75%.",
      value: 75,
      unit: "%",
      population: 120,
      as_of: "2026-09-02T17:00:00Z",
      completeness: "complete",
      evidence_refs: [
        { evidence_item_id: 7, path: "data.population_total" },
        { evidence_item_id: 8, path: "data.ready_count" },
      ],
      evidence_item_ids: [7, 8],
      evidence_paths: ["data.population_total", "data.ready_count"],
      verification_status: "verified",
      verification_reason: null,
    },
    {
      claim_id: "hypothesis-1",
      claim_type: "hypothesis",
      statement: "Conviene revisar la calidad de la captura.",
      value: null,
      unit: null,
      population: 120,
      as_of: "2026-09-02T17:00:00Z",
      completeness: "complete",
      evidence_refs: [{ evidence_item_id: 8, path: "metadata.readiness_status" }],
      evidence_item_ids: [8],
      evidence_paths: ["metadata.readiness_status"],
      verification_status: "verified",
      verification_reason: null,
    },
  ],
  hypotheses: ["La captura incompleta podría explicar la brecha."],
  options: [
    {
      label: "Validar la fuente",
      rationale: "Confirmar la captura antes de decidir.",
      evidence_refs: [
        { evidence_item_id: 7, path: "data.population_total" },
        { evidence_item_id: 8, path: "data.ready_count" },
      ],
      evidence_item_ids: [7, 8],
      evidence_paths: ["data.population_total", "data.ready_count"],
    },
  ],
  assumptions: [
    {
      statement: "La definición del universo no cambió durante el corte.",
      evidence_refs: [{ evidence_item_id: 7, path: "data.population_total" }],
      evidence_item_ids: [7],
      evidence_paths: ["data.population_total"],
    },
  ],
  blockers: [],
  expires_at: "2099-09-03T17:00:00Z",
  model: "claude-sonnet-4-6",
  ruleset_version: "talent-1",
  recommendation_only: true,
  no_writeback: true,
};

describe("GroundedAnalysisEnvelopeView", () => {
  it("separa hechos, cálculos, hipótesis y decisión humana con evidencia visible", () => {
    const markup = renderToStaticMarkup(
      <GroundedAnalysisEnvelopeView envelope={verifiedEnvelope} />,
    );

    expect(markup).toContain("Hecho observado");
    expect(markup).toContain("Cálculo determinista");
    expect(markup).toContain("Hipótesis IA");
    expect(markup).toContain("Decisión humana");
    expect(markup).toContain("Población: 120");
    expect(markup).toContain("Unidad: personas");
    expect(markup).toContain("Completitud: Completa");
    expect(markup).toContain("Ver evidencia");
    expect(markup).toContain("Referencias exactas: #7 · data.population_total, #8 · data.ready_count");
    expect(markup).toContain("write-back automáticos");
  });

  it("oculta todo contenido generativo cuando el envelope no está verificado", () => {
    const markup = renderToStaticMarkup(
      <GroundedAnalysisEnvelopeView
        envelope={{
          ...verifiedEnvelope,
          grounding_status: "insufficient_data",
          blockers: ["El snapshot Gold está incompleto."],
        }}
      />,
    );

    expect(markup).toContain("Datos insuficientes");
    expect(markup).toContain("El snapshot Gold está incompleto");
    expect(markup).not.toContain("La cobertura determinista es 75%");
    expect(markup).not.toContain("La captura incompleta podría explicar");
    expect(markup).not.toContain("Validar la fuente");
  });

  it("oculta un envelope verificado cuando su evidencia ya venció", () => {
    const markup = renderToStaticMarkup(
      <GroundedAnalysisEnvelopeView
        envelope={{
          ...verifiedEnvelope,
          status: "expired",
          expires_at: "2020-01-01T00:00:00Z",
          blockers: ["evidence_expired"],
        }}
      />,
    );

    expect(markup).toContain("Análisis no publicable");
    expect(markup).not.toContain("La cobertura determinista es 75%");
    expect(markup).not.toContain("Validar la fuente");
  });

  it("oculta claims sin verificación o sin referencias exactas", () => {
    const markup = renderToStaticMarkup(
      <GroundedAnalysisEnvelopeView
        envelope={{
          ...verifiedEnvelope,
          claims: [
            {
              ...verifiedEnvelope.claims[0],
              statement: "No debe publicarse",
              verification_status: "rejected",
            },
            {
              ...verifiedEnvelope.claims[1],
              statement: "Tampoco debe publicarse",
              evidence_refs: [],
              evidence_item_ids: [],
            },
          ],
          hypotheses: [],
          options: [],
          assumptions: [],
        }}
      />,
    );

    expect(markup).toContain("2 afirmaciones fueron ocultadas");
    expect(markup).not.toContain("No debe publicarse");
    expect(markup).not.toContain("Tampoco debe publicarse");
  });

  it("enmascara identificadores directos si cruzan accidentalmente el contrato", () => {
    const markup = renderToStaticMarkup(
      <GroundedAnalysisEnvelopeView
        envelope={{
          ...verifiedEnvelope,
          claims: [
            {
              ...verifiedEnvelope.claims[0],
              statement: "Contacto ana@example.com; PERNR=12345678",
              value: "user_id: abc-123",
            },
          ],
          hypotheses: [],
          options: [],
          assumptions: [],
        }}
      />,
    );

    expect(markup).not.toContain("ana@example.com");
    expect(markup).not.toContain("12345678");
    expect(markup).not.toContain("abc-123");
    expect(markup).toContain("dato protegido");
  });
});
