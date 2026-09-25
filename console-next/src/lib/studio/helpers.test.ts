import { describe, expect, it } from "vitest";

import { datasetsForLayer, goldTableName } from "./datasets";
import { editorSources, normalizeStorageSourceRef } from "./sources";
import {
  cartridgeIdError,
  changedEntityFields,
  dagIdError,
  deployGate,
  identifierError,
  managedDagIds,
  type EntityDraft,
} from "./validation";

describe("editorSources", () => {
  it("normalises storage references to layer/cartridge/entity", () => {
    expect(normalizeStorageSourceRef("s3://lakehouse/raw/acme/Invoice/dt=2026-01-01/x.parquet")).toBe(
      "raw/acme/Invoice",
    );
    expect(normalizeStorageSourceRef("s3://{bucket}/silver/acme/orders")).toBe("silver/acme/orders");
    expect(normalizeStorageSourceRef("/gold/acme/sales/data.parquet")).toBe("gold/acme/sales");
    expect(normalizeStorageSourceRef("bronze/acme/x")).toBe("");
    expect(normalizeStorageSourceRef(null)).toBe("");
  });

  it("merges registered dataset sources, the chosen entity and read_* calls in the SQL", () => {
    const sources = editorSources({
      detailSources: ["s3://lakehouse/silver/acme/orders/data.parquet", "not-a-path"],
      cartridge: "acme",
      entity: "Invoice",
      sql: "select * from read_parquet('raw/acme/Customer/*.parquet') join read_csv(\"gold/acme/sales\")",
    });
    expect(sources).toEqual(["silver/acme/orders", "raw/acme/Invoice", "raw/acme/Customer", "gold/acme/sales"]);
  });

  it("does not invent sources when nothing is declared", () => {
    expect(editorSources({ cartridge: "acme", entity: "", sql: "select 1" })).toEqual([]);
  });
});

describe("validation", () => {
  it("validates cartridge ids, identifiers and dag ids like the backend", () => {
    expect(cartridgeIdError("acme_erp")).toBeNull();
    expect(cartridgeIdError("Acme")).not.toBeNull();
    expect(cartridgeIdError("")).not.toBeNull();
    expect(identifierError("Invoice_2", "La entidad")).toBeNull();
    expect(identifierError("2bad", "La entidad")).toContain("La entidad");
    expect(dagIdError("acme_invoice_full", "acme")).toBeNull();
    expect(dagIdError("other_invoice", "acme")).toContain("acme_");
    expect(dagIdError("acme-bad", "acme")).toContain("inválido");
  });

  it("collects packaged DAG ids from the manifest", () => {
    const ids = managedDagIds({
      id: "acme",
      dags: [{ dag_id: "acme_all" }, "acme_legacy", { dag_id: null }],
      entities: [{ entity: "Invoice", dag_id: "acme_invoice" }, { entity: "Customer", dag_id: "" }],
    });
    expect([...ids].sort()).toEqual(["acme_all", "acme_invoice", "acme_legacy"]);
    expect(managedDagIds(undefined).size).toBe(0);
  });

  it("explains why deploy is disabled", () => {
    expect(deployGate({ dev_mode: true, rce_tools_enabled: true, dag_deploy_enabled: true }, false)).toEqual({
      enabled: true,
      reason: null,
    });
    expect(deployGate({ dev_mode: false, dag_deploy_enabled: false }, false).reason).toContain("desarrollo");
    expect(deployGate({ dev_mode: true, dag_deploy_enabled: false }, false).reason).toContain("ALLOW_RCE_TOOLS");
    expect(deployGate(undefined, true).enabled).toBe(false);
    expect(deployGate(undefined, false).enabled).toBe(false);
  });

  it("sends only the changed entity fields and derives the trigger type from cron", () => {
    const before: EntityDraft = {
      display_name: "Factura",
      mode: "full",
      primary_key: "id",
      dag_id: "acme_invoice",
      cron_expression: "0 8 * * *",
      description: "",
    };
    expect(changedEntityFields(before, before)).toEqual({});
    expect(changedEntityFields(before, { ...before, mode: "incremental", cron_expression: "" })).toEqual({
      mode: "incremental",
      cron_expression: null,
      trigger_type: "manual",
    });
    expect(changedEntityFields(before, { ...before, display_name: "  Facturas ", cron_expression: "0 9 * * *" })).toEqual({
      display_name: "Facturas",
      cron_expression: "0 9 * * *",
      trigger_type: "scheduled",
    });
  });
});

describe("datasets", () => {
  const datasets = [
    { name: "orders", layer: "silver", cartridge: "acme" },
    { name: "gold_sales", layer: "gold", cartridge: "acme" },
    { name: "margin", layer: "gold", cartridge: "" },
    { name: "other", layer: "silver", cartridge: "beta" },
  ];

  it("filters by cartridge and layer the same way the preview endpoint does", () => {
    expect(datasetsForLayer(datasets, "acme", "gold").map((item) => item.name)).toEqual(["gold_sales", "margin"]);
    expect(datasetsForLayer(datasets, "acme", "silver").map((item) => item.name)).toEqual(["orders"]);
    expect(datasetsForLayer(datasets, "beta", null).map((item) => item.name)).toEqual(["margin", "other"]);
  });

  it("derives the registered Gold table name", () => {
    expect(goldTableName({ name: "sales" })).toBe("gold_sales");
    expect(goldTableName({ name: "gold_sales" })).toBe("gold_sales");
  });
});
