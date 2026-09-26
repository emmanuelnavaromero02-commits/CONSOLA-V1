import { describe, expect, it } from "vitest";

import {
  GRAPH_NODE_WIDTH,
  graphDepths,
  graphNeighbours,
  kindLabel,
  layoutDagGraph,
  nodeLayer,
  uniqueGraph,
} from "./graph-layout";
import type { DagGraphEdge, DagGraphNode } from "./types";

const NODES: DagGraphNode[] = [
  { id: "dataset:silver:orders", kind: "dataset", label: "silver:orders" },
  { id: "entity:Invoice", kind: "entity", label: "Invoice" },
  { id: "cartridge:acme", kind: "cartridge", label: "Acme" },
  { id: "dag:acme_invoice", kind: "dag", label: "acme_invoice" },
  { id: "entity:Customer", kind: "entity", label: "Customer" },
];

const EDGES: DagGraphEdge[] = [
  { source: "cartridge:acme", target: "entity:Invoice" },
  { source: "cartridge:acme", target: "entity:Customer" },
  { source: "entity:Invoice", target: "dag:acme_invoice" },
  { source: "entity:Invoice", target: "dataset:silver:orders" },
  { source: "entity:Missing", target: "dataset:silver:orders" },
];

describe("layoutDagGraph", () => {
  it("places nodes in layers by topological depth: cartridge → entity → dag/dataset", () => {
    const layout = layoutDagGraph(NODES, EDGES);
    const depth = Object.fromEntries(layout.nodes.map((item) => [item.node.id, item.depth]));
    expect(depth).toEqual({
      "cartridge:acme": 0,
      "entity:Customer": 1,
      "entity:Invoice": 1,
      "dag:acme_invoice": 2,
      "dataset:silver:orders": 2,
    });
    expect(layout.columns.map((column) => column.label)).toEqual(["Conector", "Tablas de origen", "Automatizaciones · Plata"]);
  });

  it("gives gold datasets their own Oro column after Plata", () => {
    const fromRaw = graphDepths(
      [
        { id: "entity:Invoice", kind: "entity" },
        { id: "dataset:gold:sales", kind: "dataset", layer: "gold" },
      ],
      [{ source: "entity:Invoice", target: "dataset:gold:sales" }],
    );
    expect(fromRaw.get("dataset:gold:sales")).toBe(3);

    const nodes: DagGraphNode[] = [
      { id: "cartridge:acme", kind: "cartridge", label: "Acme" },
      { id: "entity:Invoice", kind: "entity", label: "Invoice" },
      { id: "dataset:silver:orders", kind: "dataset", label: "silver:orders", layer: "silver" },
      { id: "dataset:gold:sales", kind: "dataset", label: "gold:sales" },
      { id: "dataset:gold:ranking", kind: "dataset", label: "gold:ranking", layer: "GOLD" },
    ];
    const edges: DagGraphEdge[] = [
      { source: "cartridge:acme", target: "entity:Invoice" },
      { source: "entity:Invoice", target: "dataset:silver:orders" },
      { source: "dataset:silver:orders", target: "dataset:gold:sales" },
      { source: "dataset:gold:sales", target: "dataset:gold:ranking" },
    ];
    const depths = graphDepths(nodes, edges);
    expect(depths.get("dataset:silver:orders")).toBe(2);
    expect(depths.get("dataset:gold:sales")).toBe(3);
    expect(depths.get("dataset:gold:ranking")).toBe(4);
    const layout = layoutDagGraph(nodes, edges);
    expect(layout.columns.map((column) => column.label)).toEqual(["Conector", "Tablas de origen", "Plata", "Oro", "Oro"]);
  });

  it("reads the layer from the payload, the dataset id or the entity kind", () => {
    expect(nodeLayer({ id: "dataset:silver:orders", kind: "dataset", layer: " Gold " })).toBe("gold");
    expect(nodeLayer({ id: "dataset:Master:orders", kind: "dataset" })).toBe("master");
    expect(nodeLayer({ id: "dataset:orders", kind: "dataset" })).toBeNull();
    expect(nodeLayer({ id: "entity:Invoice", kind: "entity" })).toBe("bronze");
    expect(nodeLayer({ id: "dag:x", kind: "dag" })).toBeNull();
    expect(nodeLayer({ id: "cartridge:acme", kind: "cartridge", layer: null })).toBeNull();
  });

  it("drops edges whose endpoints are not nodes", () => {
    const layout = layoutDagGraph(NODES, EDGES);
    expect(layout.edges).toHaveLength(4);
    expect(layout.edges.some((edge) => edge.source === "entity:Missing")).toBe(false);
  });

  it("orders nodes inside a column by kind and label and never overlaps them", () => {
    const layout = layoutDagGraph(NODES, EDGES);
    const column = layout.nodes.filter((item) => item.depth === 2);
    expect(column.map((item) => item.node.id)).toEqual(["dag:acme_invoice", "dataset:silver:orders"]);
    const positions = new Set(layout.nodes.map((item) => `${item.x}:${item.y}`));
    expect(positions.size).toBe(layout.nodes.length);
    expect(Math.max(...layout.nodes.map((item) => item.x + GRAPH_NODE_WIDTH))).toBeLessThanOrEqual(layout.width);
  });

  it("is deterministic regardless of input order", () => {
    const a = layoutDagGraph(NODES, EDGES);
    const b = layoutDagGraph([...NODES].reverse(), [...EDGES].reverse());
    const pos = (layout: typeof a) =>
      Object.fromEntries(layout.nodes.map((item) => [item.node.id, [item.x, item.y]]));
    expect(pos(b)).toEqual(pos(a));
  });

  it("uses the longest path for depth and survives cycles", () => {
    const nodes: DagGraphNode[] = [
      { id: "a", kind: "entity" },
      { id: "b", kind: "entity" },
      { id: "c", kind: "entity" },
      { id: "x", kind: "dag" },
      { id: "y", kind: "dag" },
    ];
    const edges: DagGraphEdge[] = [
      { source: "a", target: "b" },
      { source: "b", target: "c" },
      { source: "a", target: "c" },
      { source: "x", target: "y" },
      { source: "y", target: "x" },
    ];
    const depths = graphDepths(nodes, edges);
    expect(depths.get("a")).toBe(1);
    expect(depths.get("b")).toBe(2);
    expect(depths.get("c")).toBe(3);
    expect(depths.get("x")).toBe(2);
    const layout = layoutDagGraph(nodes, edges);
    expect(layout.edges).toHaveLength(5);
    expect(layout.edges.every((edge) => edge.path.startsWith("M "))).toBe(true);
  });

  it("deduplicates nodes, self-loops and repeated edges", () => {
    const { nodes, edges } = uniqueGraph(
      [
        { id: "a", kind: "entity", label: "first" },
        { id: "a", kind: "entity", label: "second" },
        { id: "b", kind: "dag" },
      ],
      [
        { source: "a", target: "b" },
        { source: "a", target: "b" },
        { source: "a", target: "a" },
      ],
    );
    expect(nodes.map((node) => node.label ?? node.id)).toEqual(["first", "b"]);
    expect(edges).toEqual([{ source: "a", target: "b" }]);
  });

  it("returns an empty canvas for an empty graph", () => {
    const layout = layoutDagGraph([], []);
    expect(layout.nodes).toEqual([]);
    expect(layout.edges).toEqual([]);
    expect(layout.width).toBeGreaterThan(0);
  });

  it("reports neighbours and human labels", () => {
    expect(graphNeighbours("entity:Invoice", EDGES)).toEqual({
      incoming: ["cartridge:acme"],
      outgoing: ["dag:acme_invoice", "dataset:silver:orders"],
    });
    expect(kindLabel("dataset")).toBe("Dataset");
    expect(kindLabel("unknown")).toBe("Nodo");
  });
});
