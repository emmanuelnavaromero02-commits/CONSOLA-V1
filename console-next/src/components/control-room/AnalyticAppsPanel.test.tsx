import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { AppsResponse } from "@/lib/admin-surfaces";
import type { ControlRoomAgentsOpsPayload, SourceStatus } from "@/lib/control-room/types";

import { AnalyticAppsPanel } from "./AnalyticAppsPanel";

const emptyActions = {
  onSelectedApp: () => undefined,
  onRefresh: () => undefined,
};

const agentSummary: ControlRoomAgentsOpsPayload["summary"] = {
  agents_total: 1,
  active_agents: 1,
  monitor_agents: 1,
  recent_runs: 1,
  failed_recent_runs: 0,
  open_agent_alerts: 0,
  agent_alerts_total: 0,
  configured_engines: 1,
  monte_carlo_simulations: 3,
  bayesian_calibration_states: 0,
  bayesian_calibration_samples: 0,
  decision_orchestrations: 1,
};

describe("AnalyticAppsPanel public projection", () => {
  it("matches SuccessFactors with public labels and renders public widgets and sources", () => {
    const payload: AppsResponse = {
      apps: [
        {
          name: "successfactors_overview",
          title: "SuccessFactors Ejecutivo",
          description: "Indicadores ejecutivos de personal",
          cartridge: "sap_successfactors",
          data_status: "ready",
          updated_at: "2026-07-26T10:00:00Z",
        },
      ],
    };
    const sources: SourceStatus[] = [
      {
        cartridge: "sap_successfactors",
        domain: "Recursos Humanos",
        module: "Employee Central",
        status: "ok",
        count: 14,
        data_readiness: "ready",
        operationally_ready: true,
        checked_at: "2026-07-26T10:00:00Z",
      },
    ];

    const markup = renderToStaticMarkup(
      <AnalyticAppsPanel
        payload={payload}
        loading={false}
        error=""
        selectedApp="successfactors_overview"
        cartridge="sap_successfactors"
        sources={sources}
        sfGoldKpis={{ widgets: [{ id: "employee_360", title: "Plantilla activa", value: 14 }] }}
        {...emptyActions}
      />,
    );

    expect(markup).toContain("SuccessFactors Ejecutivo");
    expect(markup).toContain("Plantilla activa");
    expect(markup).toContain("Employee Central");
    expect(markup).toContain("14 rows");
    expect(markup).not.toContain("undefined");
    expect(markup).not.toContain("Identificador interno");
    expect(markup).not.toContain("Origen interno");
    expect(markup).not.toContain("datasets_used");
  });

  it("renders AgentOps from its minimal public payload without IDs or tool names", () => {
    const agentsOps: ControlRoomAgentsOpsPayload = {
      summary: agentSummary,
      agents: [
        {
          name: "Monitor público",
          active: true,
          role: "Monitor",
          monitor: true,
          operationally_ready: true,
          operational_tools_count: 1,
          alerts: { total: 0, open: 0 },
        },
      ],
      recent_runs: [
        {
          agent_name: "Monitor público",
          status: "ok",
          started_at: "2026-07-26T09:00:00Z",
          finished_at: "2026-07-26T09:01:00Z",
          tool_count: 1,
        },
      ],
      engines: [{ engine: "wisdom_bit", configured: 1, evidence_count: 1, status: "ready" }],
    };

    const markup = renderToStaticMarkup(
      <AnalyticAppsPanel
        payload={{ apps: [] }}
        loading={false}
        error=""
        selectedApp="omega_agentops"
        cartridge=""
        sources={[]}
        agentsOps={agentsOps}
        {...emptyActions}
      />,
    );

    expect(markup).toContain("AgentOps y Simulacion");
    expect(markup).toContain("Monitor público");
    expect(markup).toContain("1 capacidades operativas");
    expect(markup).toContain("WisdomBit");
    expect(markup).not.toContain("undefined");
    expect(markup).not.toContain("tools_used");
    expect(markup).not.toContain("agent_id");
    expect(markup).not.toContain("run_id");
  });

  it("rolls up module status worst-first: one ready source does not hide a blocked one", () => {
    const payload: AppsResponse = {
      apps: [
        {
          name: "successfactors_overview",
          title: "SuccessFactors Ejecutivo",
          cartridge: "sap_successfactors",
        },
      ],
    };
    const sources: SourceStatus[] = [
      {
        cartridge: "sap_successfactors",
        module: "Employee Central",
        status: "ok",
        count: 14,
        data_readiness: "ready",
      },
      {
        cartridge: "sap_successfactors",
        module: "Jerarquía organizacional",
        status: "blocked",
        count: 0,
        data_readiness: "no_permission",
      },
    ];

    const markup = renderToStaticMarkup(
      <AnalyticAppsPanel
        payload={payload}
        loading={false}
        error=""
        selectedApp="successfactors_overview"
        cartridge="sap_successfactors"
        sources={sources}
        {...emptyActions}
      />,
    );

    // Peor-estado-primero: la mezcla ready+blocked no puede reportarse como lista.
    expect(markup).toContain("Bloqueado");
    expect(markup).toContain("0/2 con datos listos");
  });

  it("surfaces hidden_unready_count and unavailable_datasets from apps_readiness", () => {
    const payload: AppsResponse = {
      apps: [
        {
          name: "successfactors_overview",
          title: "SuccessFactors Ejecutivo",
          cartridge: "sap_successfactors",
          data_status: "ready",
        },
      ],
      apps_readiness: {
        mode: "hide_unready",
        hidden_unready_count: 3,
        unavailable_datasets: ["gold_headcount", "gold_turnover"],
        message: "Algunos módulos requieren materializar Gold.",
      },
    };

    const markup = renderToStaticMarkup(
      <AnalyticAppsPanel
        payload={payload}
        loading={false}
        error=""
        selectedApp="successfactors_overview"
        cartridge="sap_successfactors"
        sources={[]}
        {...emptyActions}
      />,
    );

    expect(markup).toContain("Módulos ocultos por falta de datos");
    expect(markup).toContain("3 módulos se ocultaron");
    expect(markup).toContain("gold_headcount, gold_turnover");
    expect(markup).toContain("Algunos módulos requieren materializar Gold.");
  });

  it("shows the widget contract status on widget panels", () => {
    const markup = renderToStaticMarkup(
      <AnalyticAppsPanel
        payload={{ apps: [{ name: "successfactors_overview", title: "SuccessFactors Ejecutivo", cartridge: "sap_successfactors", data_status: "ready" }] }}
        loading={false}
        error=""
        selectedApp="successfactors_overview"
        cartridge="sap_successfactors"
        sources={[]}
        sfGoldKpis={{ widgets: [{ id: "employee_360", title: "Plantilla activa", value: 14, status: "partial" }] }}
        {...emptyActions}
      />,
    );

    expect(markup).toContain("Plantilla activa");
    expect(markup).toContain("Datos parciales");
  });
});
