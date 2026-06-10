import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { SfDecisionModelPayload, SfGoldKpisPayload, SourceStatus } from "@/lib/control-room/types";

import { SuccessFactorsGoldPanel } from "./SuccessFactorsGoldPanel";

const source: SourceStatus = {
  dataset: "sap_successfactors_employee_360",
  cartridge: "sap_successfactors",
  connector_id: "femsa_sf",
  module_id: "sap_successfactors",
  domain: "Recursos Humanos",
  module: "Employee Central",
  status: "ok",
  count: 1288,
  data_readiness: "ready",
  operationally_ready: true,
};

const decisionModel: SfDecisionModelPayload = {
  cartridge: "sap_successfactors",
  entities: [
    {
      entity: "EmpEmployment",
      display_name: "Relación laboral",
      description: "Employee employment profile with start date and class",
      fields: ["user_id", "employee_class", "start_date"],
    },
    {
      entity: "EmpEmploymentTermination",
      display_name: "Bajas",
      description: "Termination events and event reasons for turnover analysis",
      fields: ["user_id", "event_reason", "termination_date"],
    },
    {
      entity: "EmpCompensation",
      display_name: "Compensación",
      description: "Compensation and recurring pay components",
      fields: ["pay_component", "currency", "amount"],
    },
    {
      entity: "EmployeeTime",
      display_name: "Tiempo",
      description: "Absences and employee time balances",
      fields: ["time_type", "start_date", "end_date"],
    },
    {
      entity: "LearningItem",
      display_name: "Aprendizaje",
      description: "Training and learning progress",
      fields: ["course", "status"],
    },
  ],
  server: {
    semantic_model: {
      vocabulary: [
        { term: "headcount", definition: "Active employee count by company, location and department" },
        { term: "manager hierarchy", definition: "Supervisor structure and direct reports" },
        { term: "recruitment funnel", definition: "Job requisitions and candidate pipeline" },
      ],
    },
  },
};

describe("SuccessFactorsGoldPanel", () => {
  it("does not render synthetic KPI values when the backend payload is empty", () => {
    const markup = renderToStaticMarkup(
      <SuccessFactorsGoldPanel payload={{ widgets: [] }} loading={false} error="" sources={[]} decisionModel={null} />,
    );

    expect(markup).toContain("Información ejecutiva no disponible");
    expect(markup).toContain("N/D");
    expect(markup).not.toContain("1,288");
    expect(markup).not.toContain("1288");
  });

  it("renders real executive indicators without sending users to technical pages", () => {
    const payload: SfGoldKpisPayload = {
      connection_id: "femsa_sf",
      generated_at: "2026-06-09T01:00:00Z",
      widgets: [
        {
          id: "employee_360",
          title: "Employee 360",
          value: 1288,
          dataset: "sap_successfactors_employee_360",
          rows: [
            { label: "Monterrey", headcount: 640 },
            { label: "CDMX", headcount: 320 },
          ],
        },
      ],
    };
    const blocked: SourceStatus = {
      ...source,
      dataset: "sap_successfactors_manager_hierarchy",
      count: 0,
      status: "blocked",
      data_readiness: "no_permission",
      error: "RLS blocked scoped read",
    };

    const markup = renderToStaticMarkup(
      <SuccessFactorsGoldPanel payload={payload} loading={false} error="" sources={[source, blocked]} decisionModel={decisionModel} />,
    );

    expect(markup).toContain("1,288");
    expect(markup).toContain("Monterrey");
    expect(markup).toContain("Vista de personal");
    expect(markup).toContain("Decisiones OMEGA");
    expect(markup).toContain("Distribución de plantilla");
    expect(markup).toContain("Bloqueado por permisos");
    expect(markup).toContain("RLS blocked scoped read");
    expect(markup).not.toContain("Preview data");
    expect(markup).not.toContain("Schema");
    expect(markup).not.toContain("/api/data/sap_successfactors_employee_360?limit=20");
  });

  it("surfaces backend errors as operational errors", () => {
    const markup = renderToStaticMarkup(
      <SuccessFactorsGoldPanel payload={null} loading={false} error="S3 404" sources={[]} />,
    );

    expect(markup).toContain("No se pudo actualizar información ejecutiva");
    expect(markup).toContain("S3 404");
  });

  it("covers all SuccessFactors business fronts and translates the decision model", () => {
    const businessSources: SourceStatus[] = [
      source,
      { ...source, dataset: "sap_successfactors_org_structure", module: "Foundation Objects", count: 515 },
      { ...source, dataset: "sap_successfactors_recruitment_funnel", module: "Recruiting", count: 12, data_readiness: "partial" },
      { ...source, dataset: "sap_successfactors_turnover_by_period", module: "Employee Central", count: 0, data_readiness: "partial" },
      { ...source, dataset: "sap_successfactors_compensation_distribution", module: "Compensation", count: 44 },
      { ...source, dataset: "sap_successfactors_employee_time_balance", module: "Time Management", count: 18 },
      { ...source, dataset: "sap_successfactors_learning_completion", module: "Learning", count: 21 },
      { ...source, dataset: "sap_successfactors_performance_distribution", module: "Performance", count: 9 },
    ];

    const markup = renderToStaticMarkup(
      <SuccessFactorsGoldPanel
        payload={{
          widgets: [
            { id: "headcount_by_company", title: "Headcount by company", value: 1288, dataset: "sap_successfactors_headcount_by_company", rows: [] },
            { id: "manager_hierarchy", title: "Manager hierarchy", value: 555, dataset: "sap_successfactors_manager_hierarchy", rows: [] },
          ],
        }}
        loading={false}
        error=""
        sources={businessSources}
        decisionModel={decisionModel}
      />,
    );

    expect(markup).toContain("Personal");
    expect(markup).toContain("Estructura organizacional");
    expect(markup).toContain("Reclutamiento");
    expect(markup).toContain("Desempeño");
    expect(markup).toContain("Aprendizaje");
    expect(markup).toContain("Compensación y pagos");
    expect(markup).toContain("Tiempo y asistencia");
    expect(markup).toContain("Rotación y bajas");
    expect(markup).toContain("Qué se puede decidir con SuccessFactors");
    expect(markup).toContain("Embudo de reclutamiento");
    expect(markup).toContain("Aprendizaje y desempeño");
    expect(markup).not.toContain("/viewer?type=schema");
    expect(markup).not.toContain("/api/data/");
    expect(markup).not.toContain("Catálogo Gold");
    expect(markup).not.toContain("Schema PerPerson");
    expect(markup).not.toContain("Preview data");
  });
});
