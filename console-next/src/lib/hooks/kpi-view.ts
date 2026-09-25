import type { KpiPayload } from "./useKpis";

export interface KpiView {
  cartridges: { total: number | null; connected: number | null; disconnected: number | null };
  extractions: { today: number | null; week: number | null };
  users: { activeToday: number | null; total: number | null };
  copilot: { toolsToday: number | null; convsToday: number | null };
  audit: { eventsToday: number | null; destructiveToday: number | null };
  freshnessRows: Array<{ cartridge: string; ageHours: number | null; status: FreshnessStatus }>;
}

export type FreshnessStatus = "fresh" | "stale" | "very_stale" | "never";

const FRESHNESS_STATUSES = new Set<FreshnessStatus>(["fresh", "stale", "very_stale", "never"]);

function freshnessStatus(value: unknown): FreshnessStatus {
  return typeof value === "string" && FRESHNESS_STATUSES.has(value as FreshnessStatus)
    ? (value as FreshnessStatus)
    : "never";
}

function finiteOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function sectionOf(data: unknown, key: string): Record<string, unknown> {
  if (data && typeof data === "object") {
    const section = (data as Record<string, unknown>)[key];
    if (section && typeof section === "object" && !Array.isArray(section)) {
      return section as Record<string, unknown>;
    }
  }
  return {};
}

export function toKpiView(data: KpiPayload | undefined | null): KpiView | null {
  if (!data || typeof data !== "object") return null;

  const cartridges = sectionOf(data, "cartridges");
  const extractions = sectionOf(data, "extractions");
  const users = sectionOf(data, "users");
  const copilot = sectionOf(data, "copilot");
  const audit = sectionOf(data, "audit");
  const freshness = sectionOf(data, "data_freshness");

  return {
    cartridges: {
      total: finiteOrNull(cartridges.total),
      connected: finiteOrNull(cartridges.connected),
      disconnected: finiteOrNull(cartridges.disconnected),
    },
    extractions: {
      today: finiteOrNull(extractions.today),
      week: finiteOrNull(extractions.week),
    },
    users: {
      activeToday: finiteOrNull(users.active_today),
      total: finiteOrNull(users.total),
    },
    copilot: {
      toolsToday: finiteOrNull(copilot.tools_invoked_today),
      convsToday: finiteOrNull(copilot.conversations_today),
    },
    audit: {
      eventsToday: finiteOrNull(audit.events_today),
      destructiveToday: finiteOrNull(audit.destructive_actions_today),
    },
    freshnessRows: Object.entries(freshness).flatMap(([cartridge, info]) => {
      if (!info || typeof info !== "object") return [];
      const record = info as Record<string, unknown>;
      return [{
        cartridge,
        ageHours: finiteOrNull(record.age_hours),
        status: freshnessStatus(record.status),
      }];
    }),
  };
}
