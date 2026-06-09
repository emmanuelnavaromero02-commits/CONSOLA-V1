import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { SfGoldKpisPayload, SourceStatus } from "@/lib/control-room/types";

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

describe("SuccessFactorsGoldPanel", () => {
  it("does not render synthetic KPI values when the backend payload is empty", () => {
    const markup = renderToStaticMarkup(
      <SuccessFactorsGoldPanel payload={{ widgets: [] }} loading={false} error="" sources={[]} />,
    );

    expect(markup).toContain("Sin Gold visible");
    expect(markup).toContain("N/D");
    expect(markup).not.toContain("1,288");
    expect(markup).not.toContain("1288");
  });

  it("renders real Gold widgets, data links and explicit non-ready states", () => {
    const payload: SfGoldKpisPayload = {
      connection_id: "femsa_sf",
      generated_at: "2026-06-09T01:00:00Z",
      widgets: [
        {
          id: "employee_360",
          title: "Employee 360",
          value: 1288,
          dataset: "sap_successfactors_employee_360",
          href: "/data/catalog?dataset=sap_successfactors_employee_360",
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
      <SuccessFactorsGoldPanel payload={payload} loading={false} error="" sources={[source, blocked]} />,
    );

    expect(markup).toContain("1,288");
    expect(markup).toContain("Monterrey");
    expect(markup).toContain("/api/data/sap_successfactors_employee_360?limit=20");
    expect(markup).toContain("Sin permiso");
    expect(markup).toContain("RLS blocked scoped read");
  });

  it("surfaces backend errors as operational errors", () => {
    const markup = renderToStaticMarkup(
      <SuccessFactorsGoldPanel payload={null} loading={false} error="S3 404" sources={[]} />,
    );

    expect(markup).toContain("Error operativo visible");
    expect(markup).toContain("S3 404");
  });
});
