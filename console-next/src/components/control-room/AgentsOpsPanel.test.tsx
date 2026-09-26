import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ControlRoomAgentsOpsPayload } from "@/lib/control-room/types";

import { AgentsOpsPanel, wisdomBitCards, type WisdomBitScope } from "./AgentsOpsPanel";

const summary: ControlRoomAgentsOpsPayload["summary"] = {
  agents_total: 1,
  active_agents: 1,
  monitor_agents: 1,
  recent_runs: 1,
  failed_recent_runs: 0,
  open_agent_alerts: 0,
  agent_alerts_total: 2,
  configured_engines: 1,
  monte_carlo_simulations: 4,
  bayesian_calibration_states: 2,
  bayesian_calibration_samples: 8,
  decision_orchestrations: 3,
};

describe("AgentsOpsPanel public projection", () => {
  it("renders public agent, run, engine and diagnostic fields only", () => {
    const payload: ControlRoomAgentsOpsPayload = {
      generated_at: "2026-07-26T10:00:00Z",
      summary,
      agents: [
        {
          name: "Monitor de talento",
          active: true,
          role: "Supervisión operativa",
          monitor: true,
          operationally_ready: true,
          operational_tools_count: 2,
          last_run: {
            agent_name: "Monitor de talento",
            status: "ok",
            started_at: "2026-07-26T09:00:00Z",
            finished_at: "2026-07-26T09:01:00Z",
            tool_count: 2,
          },
          alerts: { total: 2, open: 0, last_seen_at: "2026-07-26T09:01:00Z" },
        },
      ],
      recent_runs: [
        {
          agent_name: "Monitor de talento",
          status: "ok",
          started_at: "2026-07-26T09:00:00Z",
          finished_at: "2026-07-26T09:01:00Z",
          tool_count: 2,
        },
      ],
      engines: [
        {
          engine: "monte_carlo",
          configured: 1,
          evidence_count: 4,
          sample_count: 4,
          status: "ready",
        },
      ],
      origins: [{ origin: "agent_alert", count: 2 }],
      operational_diagnostics: [
        { diagnostic: "Cobertura reciente", scope: "workspace", state_count: 2, sample_count: 8 },
      ],
    };

    const markup = renderToStaticMarkup(
      <AgentsOpsPanel payload={payload} loading={false} error="" collapsed={false} onToggle={() => undefined} />,
    );

    expect(markup).toContain("Monitor de talento");
    expect(markup).toContain("2 capacidades operativas");
    expect(markup).toContain("Cobertura reciente");
    expect(markup).toContain("Análisis operativo");
    expect(markup).not.toContain("undefined");
    expect(markup).not.toContain("Identificador interno");
    expect(markup).not.toContain("tools_used");
    expect(markup).not.toContain("cartridge_id");
  });

  it("accepts a minimal public payload with optional collections omitted", () => {
    const markup = renderToStaticMarkup(
      <AgentsOpsPanel payload={{ summary }} loading={false} error="" collapsed={false} onToggle={() => undefined} />,
    );

    expect(markup).toContain("Sin agentes visibles");
    expect(markup).toContain("Sin capacidades registradas");
    expect(markup).not.toContain("undefined");
  });
});

describe("AgentsOpsPanel scoped by WisdomBit prefix", () => {
  const scope: WisdomBitScope = {
    prefix: "WB-B1-",
    expected: ["WB-B1-MARGEN", "WB-B1-SEMAFORO"],
    stages: { "WB-B1-MARGEN": "Detección" },
    now: new Date("2026-09-25T12:00:00Z"),
    monitors: [
      {
        id: "agent-1",
        name: "Agente de Detección de margen SAP Business One",
        slug: "sap_b1_margin_monitor",
        cartridgeId: "sap_b1",
        wisdomBitId: "WB-B1-MARGEN",
        description: "Detección diaria de margen",
        active: true,
        cron: "20 7 * * *",
        timeZone: "America/Mexico_City",
      },
    ],
    runs: {
      "agent-1": {
        loading: false,
        failed: false,
        runs: [
          { id: 2, status: "failed", started_at: "2026-09-25T13:20:00Z", finished_at: "2026-09-25T13:21:00Z" },
          { id: 1, status: "success", started_at: "2026-09-24T13:20:00Z" },
        ],
      },
    },
  };

  it("renders only the prefix monitors with schedule, stage, runs and the missing ones", () => {
    const payload: ControlRoomAgentsOpsPayload = {
      summary,
      agents: [
        {
          name: "Agente de Detección de margen SAP Business One",
          operational_tools_count: 9,
          alerts: { total: 4, open: 2 },
        },
        { name: "Monitor de talento", operational_tools_count: 2, alerts: { total: 9, open: 9 } },
      ],
    };
    const markup = renderToStaticMarkup(
      <AgentsOpsPanel payload={payload} loading={false} error="" collapsed={false} onToggle={() => undefined} scope={scope} />,
    );

    expect(markup).toContain("Monitores WB-B1-*");
    expect(markup).toContain("WB-B1-MARGEN");
    expect(markup).toContain("Detección");
    expect(markup).toContain("Horario 07:20 America/Mexico_City");
    expect(markup).toContain("2 abiertas");
    expect(markup).toContain("Monitores sin registrar en este workspace");
    expect(markup).toContain("WB-B1-SEMAFORO");
    expect(markup).not.toContain("Monitor de talento");
    expect(markup).not.toContain("Capacidades");
    expect(markup).not.toContain("undefined");
  });

  it("builds cards with the next run and without alert data when AgentOps is unavailable", () => {
    const [card] = wisdomBitCards(scope, null);
    expect(card.nextRunAt).toBe("2026-09-25T13:20:00.000Z");
    expect(card.status).toBe("failed");
    expect(card.recentStatuses).toEqual(["failed", "success"]);
    expect(card.alertsOpen).toBeNull();
  });
});
