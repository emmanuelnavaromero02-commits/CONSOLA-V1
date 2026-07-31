import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { PipelineEntity } from "@/lib/monitor/types";
import { PipelineTable } from "./PipelineTable";

vi.mock("@/lib/monitor/hooks", () => ({
  useVaultConnections: () => ({ data: [] }),
}));

vi.mock("@/lib/api", () => ({
  api: { post: vi.fn() },
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

function makeRow(bronze: Partial<PipelineEntity["bronze"]>): PipelineEntity {
  return {
    entity: "employees",
    cartridge: "replicon",
    modes: ["incremental"],
    watermark: null,
    last_run: null,
    last_job: null,
    bronze: {
      source: "bronze.replicon_employees",
      latest_date: "2026-07-29",
      status: "fresh",
      ...bronze,
    },
    silver: [],
    gold: [],
  };
}

describe("PipelineTable bronze summary", () => {
  it("shows N/D when record_count is null and the table is not flagged empty", () => {
    const markup = renderToStaticMarkup(
      <PipelineTable rows={[makeRow({ record_count: null, empty: false })]} />,
    );

    expect(markup).toContain("N/D filas");
    expect(markup).not.toContain("0 filas");
  });

  it("shows N/D when record_count is missing entirely", () => {
    const markup = renderToStaticMarkup(
      <PipelineTable rows={[makeRow({})]} />,
    );

    expect(markup).toContain("N/D filas");
    expect(markup).not.toContain("0 filas");
  });

  it("keeps the explicit empty state when empty is true", () => {
    const markup = renderToStaticMarkup(
      <PipelineTable rows={[makeRow({ record_count: null, empty: true })]} />,
    );

    expect(markup).toContain("Sin filas extraídas");
    expect(markup).not.toContain("N/D filas");
  });

  it("shows the real count when record_count is present, including 0", () => {
    const withCount = renderToStaticMarkup(
      <PipelineTable rows={[makeRow({ record_count: 42, empty: false })]} />,
    );
    const withZero = renderToStaticMarkup(
      <PipelineTable rows={[makeRow({ record_count: 0, empty: false })]} />,
    );

    expect(withCount).toContain("42 filas");
    expect(withZero).toContain("0 filas");
    expect(withCount).not.toContain("N/D filas");
  });
});
