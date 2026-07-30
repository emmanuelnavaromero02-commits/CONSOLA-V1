import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { DatasetSummary } from "@/lib/monitor/types";

import { DatasetTable, SchemaPanel } from "./page";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

describe("SchemaPanel", () => {
  it("normalizes object-shaped partitions and columns before rendering", () => {
    const markup = renderToStaticMarkup(
      <SchemaPanel
        payload={{
          partitions: {
            partitions: [{ load_date: "2026-06-26", batch_id: "batch-1" }],
            latest: { load_date: "2026-06-26", batch_id: "batch-1" },
            sql_latest: "SELECT * FROM source",
          },
          preview: {
            columns: [{ name: "employee_id", type: "VARCHAR" }, { column_name: "status" }],
            rows: [{ employee_id: "masked-1", status: "ready" }],
          },
        }}
      />,
    );

    expect(markup).toContain("2026-06-26 · batch-1");
    expect(markup).toContain("employee_id");
    expect(markup).toContain("status");
    expect(markup).not.toContain("[object Object]");
  });

  it("shows explicit empty states when no parquet schema is available", () => {
    const markup = renderToStaticMarkup(<SchemaPanel payload={{ partitions: { partitions: [] }, preview: { rows: [] } }} />);

    expect(markup).toContain("Sin parquet materializado");
    expect(markup).toContain("Sin columnas inferidas");
  });

  it("shows partial diagnostics without hiding inferred columns", () => {
    const markup = renderToStaticMarkup(
      <SchemaPanel
        payload={{
          status: "partial",
          message: "datos parciales",
          errors: [{ stage: "particiones", reason: "permission_denied", message: "sin permisos para leer la fuente" }],
          partitions: { partitions: [] },
          preview: {
            schema: [{ name: "userId", type: "VARCHAR" }],
            data: [],
          },
        }}
      />,
    );

    expect(markup).toContain("datos parciales");
    expect(markup).toContain("sin permisos para leer la fuente");
    expect(markup).toContain("userId");
  });
});

describe("DatasetTable freshness", () => {
  function makeDataset(isStale: boolean | null | undefined): DatasetSummary {
    return { name: "gold.revenue", layer: "gold", cartridge: "hubspot", is_stale: isStale };
  }

  it("renders a neutral no-data label when is_stale is null (absence is not freshness)", () => {
    const markup = renderToStaticMarkup(<DatasetTable rows={[makeDataset(null)]} />);

    expect(markup).toContain("Sin dato de frescura");
    expect(markup).not.toContain("Fresca");
    expect(markup).not.toContain("Antigua");
  });

  it("renders a neutral no-data label when is_stale is undefined", () => {
    const markup = renderToStaticMarkup(<DatasetTable rows={[makeDataset(undefined)]} />);

    expect(markup).toContain("Sin dato de frescura");
  });

  it("keeps honest fresh/stale labels when is_stale is boolean", () => {
    const fresh = renderToStaticMarkup(<DatasetTable rows={[makeDataset(false)]} />);
    const stale = renderToStaticMarkup(<DatasetTable rows={[makeDataset(true)]} />);

    expect(fresh).toContain("Fresca");
    expect(fresh).not.toContain("Sin dato de frescura");
    expect(stale).toContain("Antigua");
    expect(stale).not.toContain("Sin dato de frescura");
  });
});
