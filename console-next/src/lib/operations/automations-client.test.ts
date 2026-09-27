import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import { AUTOMATIONS_ENDPOINT, automationSchema, listAutomations } from "./automations-client";

vi.mock("@/lib/api", () => ({ api: { get: vi.fn() } }));

const apiGet = vi.mocked(api.get);
const automation = {
  dag_id: "sap_b1_refresh",
  label: "Refresco SAP B1",
  cartridge_id: "sap_b1",
  kind: "scheduled",
  schedule_description: "Cada 10 minutos",
  state: "paused_by_operator",
  state_note_es: "En pausa por un operador de plataforma; no se ejecutará en su horario",
  active_runs: 0,
  last_run: null,
  runs_known: true,
};

beforeEach(() => apiGet.mockReset());

describe("automations client", () => {
  it("reads the read-only endpoint once and parses the strict payload", async () => {
    apiGet.mockResolvedValue({
      data: {
        schema_version: "pipeline-automations/v1",
        checked_at: "2026-09-26T10:00:00Z",
        airflow_available: true,
        automations: [automation],
      },
      status: 200,
      headers: new Headers(),
      requestId: "request-1",
    });
    const response = await listAutomations();
    expect(apiGet).toHaveBeenCalledWith(AUTOMATIONS_ENDPOINT);
    expect(response.automations[0].state).toBe("paused_by_operator");
  });

  it("rejects unknown states, extra keys and malformed identifiers", () => {
    const parse = (value: unknown) => automationSchema.safeParse(value).success;
    expect(parse(automation)).toBe(true);
    expect(parse({ ...automation, state: "running" })).toBe(false);
    expect(parse({ ...automation, toggle: "pause" })).toBe(false);
    expect(parse({ ...automation, dag_id: "../dags" })).toBe(false);
    expect(parse({ ...automation, active_runs: -1 })).toBe(false);
    expect(parse({ ...automation, runs_known: false })).toBe(false);
    expect(parse({ ...automation, runs_known: false, active_runs: null })).toBe(true);
  });
});
