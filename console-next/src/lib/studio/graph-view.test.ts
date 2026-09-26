import { Database, Plug, Table, Zap } from "lucide-react";
import { describe, expect, it } from "vitest";

import {
  collectDownstream,
  edgeRole,
  nodeCaption,
  nodeIcon,
  nodeRef,
  nodeStage,
  smartTruncate,
  STAGE_LABEL,
  STAGE_STYLE,
} from "./graph-view";
import type { DagGraphEdge } from "./types";

describe("graph view helpers", () => {
  it("maps each node to its stage", () => {
    expect(nodeStage({ id: "cartridge:acme", kind: "cartridge" })).toBe("origen");
    expect(nodeStage({ id: "entity:Invoice", kind: "entity" })).toBe("origen");
    expect(nodeStage({ id: "dag:acme", kind: "dag" })).toBe("extraccion");
    expect(nodeStage({ id: "dataset:silver:orders", kind: "dataset" })).toBe("plata");
    expect(nodeStage({ id: "dataset:x:orders", kind: "dataset", layer: "gold" })).toBe("oro");
    expect(nodeStage({ id: "dataset:master:orders", kind: "dataset" })).toBe("otro");
    expect(nodeStage({ id: "raro", kind: "otro" })).toBe("otro");
    expect(STAGE_STYLE.oro).toContain("emerald-500");
    expect(STAGE_STYLE.origen).toContain("sky-500");
    expect(STAGE_LABEL.extraccion).toBe("Extracción");
  });

  it("picks an icon and a caption per kind", () => {
    expect(nodeIcon({ id: "cartridge:acme", kind: "cartridge" })).toBe(Plug);
    expect(nodeIcon({ id: "entity:Invoice", kind: "entity" })).toBe(Database);
    expect(nodeIcon({ id: "dag:acme", kind: "dag" })).toBe(Zap);
    expect(nodeIcon({ id: "dataset:gold:sales", kind: "dataset" })).toBe(Table);
    expect(nodeCaption({ id: "cartridge:acme", kind: "cartridge" })).toBe("Conector");
    expect(nodeCaption({ id: "entity:Invoice", kind: "entity" })).toBe("Tabla de origen");
    expect(nodeCaption({ id: "dag:acme", kind: "dag" })).toBe("Automatización");
    expect(nodeCaption({ id: "dataset:gold:sales", kind: "dataset" })).toBe("Oro");
  });

  it("truncates keeping the head and the tail of technical names", () => {
    expect(smartTruncate("sap_b1_ar_credit_memo_lines", 20)).toBe("sap_b1_ar_cr…o_lines");
    expect(smartTruncate("sap_b1_ar_credit_memo_lines", 20)).toHaveLength(20);
    expect(smartTruncate("Facturacionmensualcompleta", 10)).toBe("Facturaci…");
    expect(smartTruncate("corto", 24)).toBe("corto");
    expect(smartTruncate("abc", 1)).toBe("…");
  });

  it("reads the kind, name and layer of a node", () => {
    expect(nodeRef({ id: "dataset:silver:orders", kind: "dataset" })).toEqual({ kind: "dataset", name: "orders", layer: "silver" });
    expect(nodeRef({ id: "entity:Invoice", kind: "entity" })).toEqual({ kind: "entity", name: "Invoice", layer: "bronze" });
    expect(nodeRef({ id: "dag:acme_invoice", kind: "dag" })).toEqual({ kind: "dag", name: "acme_invoice", layer: null });
    expect(nodeRef({ id: "cartridge:acme", kind: "cartridge" })).toEqual({ kind: "cartridge", name: "acme", layer: null });
  });

  it("collects everything downstream once, even with cycles", () => {
    const edges: DagGraphEdge[] = [
      { source: "a", target: "b" },
      { source: "b", target: "c" },
      { source: "c", target: "a" },
      { source: "b", target: "d" },
      { source: "x", target: "a" },
    ];
    expect(collectDownstream("a", edges)).toEqual(["b", "c", "d"]);
    expect(collectDownstream("d", edges)).toEqual([]);
  });

  it("tells whether an edge enters or leaves the active node", () => {
    const edge = { source: "a", target: "b" };
    expect(edgeRole(edge, "b")).toBe("in");
    expect(edgeRole(edge, "a")).toBe("out");
    expect(edgeRole(edge, "c")).toBeNull();
    expect(edgeRole(edge, null)).toBeNull();
  });
});
