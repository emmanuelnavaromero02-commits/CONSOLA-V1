import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { AgentRecord } from "@/lib/admin-surfaces";

import {
  cartridgeAgentsPath,
  listCartridgeAgents,
  nextDailyRun,
  scheduleLabel,
  wisdomBitMonitorsFrom,
} from "./wisdom-bit-monitors";

vi.mock("@/lib/api", () => ({ api: { get: vi.fn() } }));

function agent(id: string, wisdomBitId: string | null, cron: string | null, extra: Partial<AgentRecord> = {}): AgentRecord {
  return {
    id,
    name: `Agente ${id}`,
    slug: `${id}_monitor`,
    cartridge_id: "sap_b1",
    is_active: true,
    extra: {
      role: "monitor",
      monitor: wisdomBitId ? { wisdom_bit_id: wisdomBitId } : {},
      schedule: cron ? { cron, tz: "America/Mexico_City" } : {},
    },
    ...extra,
  };
}

describe("WisdomBit monitors", () => {
  beforeEach(() => vi.mocked(api.get).mockReset());

  it("lists a cartridge's agents including inactive ones", async () => {
    vi.mocked(api.get).mockResolvedValueOnce({ data: { agents: [agent("a", "WB-B1-MARGEN", "20 7 * * *")] }, status: 200, headers: new Headers(), requestId: "r" });
    await expect(listCartridgeAgents("sap_b1")).resolves.toHaveLength(1);
    expect(api.get).toHaveBeenCalledWith("/api/agents?cartridge_id=sap_b1&include_inactive=true");
    expect(cartridgeAgentsPath("x y")).toBe("/api/agents?cartridge_id=x+y&include_inactive=true");
  });

  it("keeps only monitors whose WisdomBit id has the prefix, ordered by schedule", () => {
    const monitors = wisdomBitMonitorsFrom(
      [
        agent("semaforo", "WB-B1-SEMAFORO", "0 8 * * *"),
        agent("talento", "WB-TALENTO", "0 6 * * *"),
        agent("margen", "WB-B1-MARGEN", "20 7 * * *", { is_active: false }),
        agent("chat", null, null),
      ],
      "WB-B1-",
    );
    expect(monitors.map((monitor) => [monitor.wisdomBitId, monitor.active, monitor.cron, monitor.timeZone])).toEqual([
      ["WB-B1-MARGEN", false, "20 7 * * *", "America/Mexico_City"],
      ["WB-B1-SEMAFORO", true, "0 8 * * *", "America/Mexico_City"],
    ]);
  });

  it("formats daily schedules and computes the next run in the monitor's time zone", () => {
    expect(scheduleLabel("20 7 * * *")).toBe("07:20");
    expect(scheduleLabel("*/5 * * * *")).toBe("*/5 * * * *");
    expect(scheduleLabel(null)).toBeNull();

    const beforeRun = new Date("2026-09-25T12:00:00Z");
    expect(nextDailyRun("20 7 * * *", "America/Mexico_City", beforeRun)?.toISOString()).toBe("2026-09-25T13:20:00.000Z");
    const afterRun = new Date("2026-09-25T14:30:00Z");
    expect(nextDailyRun("0 8 * * *", "America/Mexico_City", afterRun)?.toISOString()).toBe("2026-09-26T14:00:00.000Z");
    expect(nextDailyRun("30 7 * * *", null, beforeRun)?.toISOString()).toBe("2026-09-26T07:30:00.000Z");
    expect(nextDailyRun("*/5 * * * *", "UTC", beforeRun)).toBeNull();
    expect(nextDailyRun("0 8 * * *", "Zona/Inexistente", beforeRun)).toBeNull();
  });
});
