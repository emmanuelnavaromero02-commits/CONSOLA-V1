import type { DatasetSummary } from "@/lib/monitor/types";

import { datasetsForLayer } from "./datasets";
import type { StudioCartridgeSummary, StudioManifest } from "./types";

export interface CartridgeHealth {
  tables: number | null;
  goldReady: number | null;
  goldTotal: number | null;
  lastRefresh: string | null;
}

function validTime(value: string | null | undefined): number | null {
  if (typeof value !== "string" || !value.trim()) return null;
  const time = Date.parse(value);
  return Number.isNaN(time) ? null : time;
}

export function isGoldReady(dataset: DatasetSummary): boolean {
  return (
    String(dataset.layer ?? "").toLowerCase() === "gold"
    && dataset.is_stale !== true
    && validTime(dataset.last_refresh) !== null
  );
}

export function cartridgeHealth({
  cartridge,
  manifest,
  summary,
  datasets,
}: {
  cartridge: string | null;
  manifest: StudioManifest | null | undefined;
  summary: StudioCartridgeSummary | null | undefined;
  datasets: DatasetSummary[] | null | undefined;
}): CartridgeHealth {
  const tables = manifest ? (manifest.entities ?? []).length : typeof summary?.entities === "number" ? summary.entities : null;
  if (!cartridge || !datasets) return { tables, goldReady: null, goldTotal: null, lastRefresh: null };
  const gold = datasetsForLayer(datasets, cartridge, "gold");
  let latest: { time: number; value: string } | null = null;
  for (const dataset of datasetsForLayer(datasets, cartridge, null)) {
    const time = validTime(dataset.last_refresh);
    if (time !== null && (!latest || time > latest.time)) latest = { time, value: String(dataset.last_refresh) };
  }
  return {
    tables,
    goldReady: gold.filter(isGoldReady).length,
    goldTotal: gold.length,
    lastRefresh: latest?.value ?? null,
  };
}
