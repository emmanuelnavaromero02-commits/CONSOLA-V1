import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { SchemaPanel } from "./page";

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
