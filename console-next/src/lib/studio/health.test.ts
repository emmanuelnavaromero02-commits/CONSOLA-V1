import { describe, expect, it } from "vitest";

import type { DatasetSummary } from "@/lib/monitor/types";

import { cartridgeHealth, isGoldReady } from "./health";

const DATASETS: DatasetSummary[] = [
  { name: "ventas", layer: "gold", cartridge: "acme", is_stale: false, last_refresh: "2026-09-26T11:48:00Z" },
  { name: "margen", layer: "gold", cartridge: "acme", is_stale: true, last_refresh: "2026-09-26T11:50:00Z" },
  { name: "nunca", layer: "gold", cartridge: "acme", is_stale: false },
  { name: "orders", layer: "silver", cartridge: "acme", last_refresh: "2026-09-26T11:55:00Z" },
  { name: "x", layer: "gold", cartridge: "beta", is_stale: false, last_refresh: "2026-09-26T11:59:00Z" },
  { name: "roto", layer: "silver", cartridge: "acme", last_refresh: "no es fecha" },
];

describe("cartridge health", () => {
  it("counts gold as ready only when it is fresh and was materialized", () => {
    expect(isGoldReady(DATASETS[0])).toBe(true);
    expect(isGoldReady(DATASETS[1])).toBe(false);
    expect(isGoldReady(DATASETS[2])).toBe(false);
    expect(isGoldReady(DATASETS[3])).toBe(false);
    expect(isGoldReady({ name: "sin_estado", layer: "GOLD", last_refresh: "2026-09-26T11:48:00Z" })).toBe(true);
  });

  it("summarises tables, gold readiness and the latest refresh of the cartridge", () => {
    expect(
      cartridgeHealth({
        cartridge: "acme",
        manifest: { id: "acme", entities: [{ entity: "Invoice" }, { entity: "Customer" }] },
        summary: { id: "acme", entities: 9 },
        datasets: DATASETS,
      }),
    ).toEqual({ tables: 2, goldReady: 1, goldTotal: 3, lastRefresh: "2026-09-26T11:55:00Z" });
  });

  it("falls back to the summary count and reports nothing it does not know", () => {
    expect(cartridgeHealth({ cartridge: "acme", manifest: undefined, summary: { id: "acme", entities: 4 }, datasets: undefined })).toEqual({
      tables: 4,
      goldReady: null,
      goldTotal: null,
      lastRefresh: null,
    });
    expect(cartridgeHealth({ cartridge: "zeta", manifest: undefined, summary: undefined, datasets: [] })).toEqual({
      tables: null,
      goldReady: 0,
      goldTotal: 0,
      lastRefresh: null,
    });
  });
});
