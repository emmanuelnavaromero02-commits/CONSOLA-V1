import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { SfDecisionModelPayload, SfGoldKpisPayload, SfTalentKpisPayload, SourceStatus } from "@/lib/control-room/types";

import { SuccessFactorsGoldPanel } from "./SuccessFactorsGoldPanel";

const source: SourceStatus = {
  cartridge: "sap_successfactors",
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
      generated_at: "2026-06-09T01:00:00Z",
      widgets: [
        {
          id: "employee_360",
          title: "Employee 360",
          value: 1288,
          rows: [
            { label: "Monterrey", headcount: 640 },
            { label: "CDMX", headcount: 320 },
          ],
        },
      ],
    };
    const blocked: SourceStatus = {
      ...source,
      module: "Jerarquía organizacional",
      count: 0,
      status: "blocked",
      data_readiness: "no_permission",
    };

    const markup = renderToStaticMarkup(
      <SuccessFactorsGoldPanel payload={payload} loading={false} error="" sources={[source, blocked]} decisionModel={decisionModel} />,
    );

    expect(markup).toContain("1,288");
    expect(markup).toContain("Monterrey");
    expect(markup).toContain("Vista de personal");
    expect(markup).toContain("Decisiones OMEGA");
    expect(markup).toContain("Distribución de plantilla");
    expect(markup).toContain("Requiere permisos OData");
    expect(markup).not.toContain("Identificador interno");
    expect(markup).not.toContain("Origen interno");
    expect(markup).not.toContain("Preview data");
    expect(markup).not.toContain("Schema");
    expect(markup).not.toContain("/api/data/sap_successfactors_employee_360?limit=20");
  });

  it("surfaces an operational error without exposing its technical detail", () => {
    const technicalError = "S3_PRIVATE_BUCKET_404";
    const markup = renderToStaticMarkup(
      <SuccessFactorsGoldPanel payload={null} loading={false} error={technicalError} sources={[]} />,
    );

    expect(markup).toContain("No se pudo actualizar información ejecutiva");
    expect(markup).not.toContain(technicalError);
  });

  it("renders Talent WisdomBit blockers and recommendation-only signals", () => {
    const talent: SfTalentKpisPayload = {
      generated_at: "2026-06-22T18:00:00Z",
      profile: {
        industry: "retail",
        company_profile: "femsa",
        decision_mode: "recommendation_only",
        compensation_enabled: false,
        write_back_enabled: false,
      },
      readiness: {
        ready_min: 80,
        near_min: 60,
        profiled_employees: 12,
        calculable_employees: 0,
        insufficient_data_employees: 12,
        nine_box_available: 0,
        status: "partial",
      },
      widgets: [
        { id: "sf_talent_roles_profiled", title: "Roles derivados", value: 3, status: "partial" },
      ],
      signals: [
        {
          id: "talent_cpa_missing_inputs",
          severity: "medium",
          title: "Fit Score bloqueado por falta de C/P/A",
          affected_count: 12,
          recommendation: "Habilitar desempeno, competencias y aspiracion para calcular readiness real.",
          status: "recommendation_only",
        },
      ],
      blockers: [
        {
          id: "talent_cpa_inputs_missing",
          status: "blocked",
          title: "C/P/A pendiente",
        },
      ],
    };

    const markup = renderToStaticMarkup(
      <SuccessFactorsGoldPanel payload={{ widgets: [] }} loading={false} error="" sources={[source]} talent={talent} />,
    );

    expect(markup).toContain("WB-TALENTO");
    expect(markup).toContain("Readiness calculable");
    expect(markup).toContain("0/12");
    expect(markup).toContain("C/P/A pendiente");
    expect(markup).toContain("Fit Score bloqueado");
    expect(markup).toContain("recommendation_only");
  });

  it("renders the Workforce Trends section from the single backend bundle without fetching data", () => {
    const talent: SfTalentKpisPayload = {
      generated_at: "2026-07-09T18:00:00Z",
      profile: {
        industry: "retail",
        company_profile: "femsa",
        decision_mode: "recommendation_only",
        compensation_enabled: false,
        write_back_enabled: false,
      },
      readiness: {
        ready_min: 80,
        near_min: 60,
        profiled_employees: 1288,
        calculable_employees: 0,
        insufficient_data_employees: 1288,
        nine_box_available: 0,
        status: "partial",
      },
      widgets: [],
      signals: [],
      blockers: [],
      workforce_trends: {
        status: "ready",
        kpis: {
          active_headcount: 1288,
          avg_tenure_months: 174.39,
          attrition_rate: 0.0,
          history_months: 36,
        },
        series: {
          months: ["2026-05", "2026-06", "2026-07"],
          headcount: [1286, 1287, 1288],
          avg_tenure_months: [172.4, 173.4, 174.4],
          attrition_rate: [0.0, 0.0, 0.0],
        },
      },
    };

    const markup = renderToStaticMarkup(
      <SuccessFactorsGoldPanel payload={{ widgets: [] }} loading={false} error="" sources={[source]} talent={talent} />,
    );

    expect(markup).toContain("Workforce Trends");
    expect(markup).toContain("Plantilla activa");
    expect(markup).toContain("Antigüedad promedio");
    expect(markup).toContain("Rotación");
    expect(markup).toContain("Meses de historia");
    expect(markup).toContain("años");
    expect(markup).toContain("14.5 años");
    expect(markup).toContain("36");
    expect(markup).toContain("serie mensual por cohorte · una sola fuente");
    expect(markup).toContain("<polyline");
    expect(markup).not.toContain("/api/data/");
  });

  it("hides Workforce Trends when the bundle is absent (honest empty state)", () => {
    const talent: SfTalentKpisPayload = {
      profile: { industry: "retail", company_profile: "femsa", decision_mode: "recommendation_only", compensation_enabled: false, write_back_enabled: false },
      readiness: { ready_min: 80, near_min: 60, profiled_employees: 0, calculable_employees: 0, insufficient_data_employees: 0, nine_box_available: 0, status: "partial" },
      widgets: [],
      signals: [],
      blockers: [],
    };
    const markup = renderToStaticMarkup(
      <SuccessFactorsGoldPanel payload={{ widgets: [] }} loading={false} error="" sources={[source]} talent={talent} />,
    );
    expect(markup).not.toContain("Workforce Trends");
  });

  it("covers all SuccessFactors business fronts and translates the decision model", () => {
    const businessSources: SourceStatus[] = [
      source,
      { ...source, module: "Org Structure", count: 515 },
      { ...source, module: "Recruiting", count: 12, data_readiness: "partial" },
      { ...source, module: "Turnover and Termination", count: 0, data_readiness: "partial" },
      { ...source, module: "Compensation", count: 44 },
      { ...source, module: "Time Management", count: 18 },
      { ...source, module: "Learning", count: 21 },
      { ...source, module: "Performance", count: 9 },
    ];

    const markup = renderToStaticMarkup(
      <SuccessFactorsGoldPanel
        payload={{
          widgets: [
            { id: "headcount_by_company", title: "Headcount by company", value: 1288, rows: [] },
            { id: "manager_hierarchy", title: "Manager hierarchy", value: 555, rows: [] },
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
    expect(markup).toContain("Datos parciales");
    expect(markup).toContain("Fuera de alcance actual");
    expect(markup).not.toContain("/viewer?type=schema");
    expect(markup).not.toContain("/api/data/");
    expect(markup).not.toContain("Catálogo Gold");
    expect(markup).not.toContain("Schema PerPerson");
    expect(markup).not.toContain("Preview data");
  });

  it("renders a minimal public projection without undefined or technical labels", () => {
    const markup = renderToStaticMarkup(
      <SuccessFactorsGoldPanel
        payload={{ widgets: [{ id: "employee_360", title: "Plantilla", value: 7 }] }}
        loading={false}
        error=""
        sources={[{ count: 7 }]}
        talent={{}}
      />,
    );

    expect(markup).toContain("Plantilla activa");
    expect(markup).toContain(">7<");
    expect(markup).not.toContain("undefined");
    expect(markup).not.toContain("Identificador interno");
    expect(markup).not.toContain("Origen interno");
    expect(markup).not.toContain("dataset");
  });
});
