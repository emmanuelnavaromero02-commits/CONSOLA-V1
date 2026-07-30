import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { ControlRoomAgentsOpsPayload } from "@/lib/control-room/types";

import { AgentsOpsPanel } from "./AgentsOpsPanel";

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
    // generated_at del contrato visible como marca de actualización semántica.
    expect(markup).toContain("Actualizado:");
    expect(markup).toContain('dateTime="2026-07-26T10:00:00Z"');
    expect(markup).not.toContain("undefined");
    expect(markup).not.toContain("Identificador interno");
    expect(markup).not.toContain("tools_used");
    expect(markup).not.toContain("cartridge_id");
  });

  it("shows a dash instead of fabricated zeros when the payload is unavailable", () => {
    const markup = renderToStaticMarkup(
      <AgentsOpsPanel payload={null} loading={false} error="falló" collapsed={false} onToggle={() => undefined} />,
    );

    expect(markup).toContain("—");
    expect(markup).not.toContain("0 registrados");
    expect(markup).not.toContain("0 con contrato");
    expect(markup).not.toContain("0 con error");
    expect(markup).not.toContain("0 históricas");
    expect(markup).not.toContain("Actualizado:");
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
