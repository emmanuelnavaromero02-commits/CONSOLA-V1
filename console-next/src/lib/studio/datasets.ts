import type { DatasetSummary } from "@/lib/monitor/types";

import type { StudioLayer } from "./types";

export function datasetsForLayer(
  datasets: DatasetSummary[],
  cartridge: string,
  layer: StudioLayer | null,
): DatasetSummary[] {
  return datasets
    .filter((dataset) => layer === null || String(dataset.layer ?? "").toLowerCase() === layer)
    .filter((dataset) => !dataset.cartridge || dataset.cartridge === cartridge)
    .sort((a, b) => a.name.localeCompare(b.name));
}

export function goldTableName(dataset: Pick<DatasetSummary, "name">): string {
  return dataset.name.startsWith("gold_") ? dataset.name : `gold_${dataset.name}`;
}
