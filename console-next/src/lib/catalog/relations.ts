import type { CatalogRelationship } from "@/lib/data/types";

export const GRAPH_MAX_NODES = 40;

export interface CatalogEdge {
  from: string;
  fromColumn: string;
  to: string;
  toColumn: string;
  cardinality: string | null;
  origin: "manual" | "packaged" | "copilot";
  confidence: number | null;
  relationship: CatalogRelationship;
}

export interface RelationMatrix {
  rows: string[];
  columns: string[];
  cells: Map<string, CatalogEdge[]>;
  total: number;
  truncated: boolean;
}

export interface LayoutNode {
  id: string;
  x: number;
  y: number;
  degree: number;
}

export interface CircularLayout {
  nodes: LayoutNode[];
  width: number;
  height: number;
  total: number;
  truncated: boolean;
}

export function activeEdges(relationships: readonly CatalogRelationship[]): CatalogEdge[] {
  const edges: CatalogEdge[] = [];
  const seen = new Set<string>();
  for (const relationship of relationships) {
    const from = String(relationship.from_dataset ?? "");
    const to = String(relationship.to_dataset ?? "");
    const fromColumn = String(relationship.from_column ?? "");
    const toColumn = String(relationship.to_column ?? "");
    if (!from || !to || !fromColumn || !toColumn) continue;
    if (relationship.status && relationship.status !== "active") continue;
    const key = `${from}.${fromColumn}>${to}.${toColumn}`;
    if (seen.has(key)) continue;
    seen.add(key);
    edges.push({
      from,
      fromColumn,
      to,
      toColumn,
      cardinality: relationship.cardinality ?? null,
      origin: relationship.origin === "copilot" || relationship.origin === "packaged" ? relationship.origin : "manual",
      confidence: typeof relationship.confidence === "number" ? relationship.confidence : null,
      relationship,
    });
  }
  return edges;
}

function degrees(edges: readonly CatalogEdge[]): Map<string, number> {
  const out = new Map<string, number>();
  for (const edge of edges) {
    out.set(edge.from, (out.get(edge.from) ?? 0) + 1);
    out.set(edge.to, (out.get(edge.to) ?? 0) + 1);
  }
  return out;
}

function rankNodes(edges: readonly CatalogEdge[]): Array<[string, number]> {
  return [...degrees(edges).entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

export function relationMatrix(edges: readonly CatalogEdge[], max = GRAPH_MAX_NODES): RelationMatrix {
  const ranked = rankNodes(edges);
  const kept = new Set(ranked.slice(0, max).map(([name]) => name));
  const cells = new Map<string, CatalogEdge[]>();
  const rows = new Set<string>();
  const columns = new Set<string>();
  for (const edge of edges) {
    if (!kept.has(edge.from) || !kept.has(edge.to)) continue;
    rows.add(edge.from);
    columns.add(edge.to);
    const key = matrixKey(edge.from, edge.to);
    cells.set(key, [...(cells.get(key) ?? []), edge]);
  }
  return {
    rows: [...rows].sort(),
    columns: [...columns].sort(),
    cells,
    total: ranked.length,
    truncated: ranked.length > max,
  };
}

export function matrixKey(from: string, to: string): string {
  return `${from}\u0000${to}`;
}

export function circularLayout(
  edges: readonly CatalogEdge[],
  max = GRAPH_MAX_NODES,
  size = 640,
): CircularLayout {
  const ranked = rankNodes(edges);
  const kept = ranked.slice(0, max);
  const count = kept.length;
  const center = size / 2;
  const radius = count <= 1 ? 0 : Math.max(60, center - 110);
  const nodes = kept.map(([id, degree], index) => {
    const angle = (2 * Math.PI * index) / Math.max(count, 1) - Math.PI / 2;
    return {
      id,
      degree,
      x: Math.round((center + radius * Math.cos(angle)) * 100) / 100,
      y: Math.round((center + radius * Math.sin(angle)) * 100) / 100,
    };
  });
  return { nodes, width: size, height: size, total: ranked.length, truncated: ranked.length > max };
}

export function cardinalitySentence(edge: CatalogEdge, labelOf: (name: string) => string = (name) => name): string {
  const from = labelOf(edge.from);
  const to = labelOf(edge.to);
  switch (edge.cardinality) {
    case "N:1":
      return `Cada registro de ${from} se vincula con un registro de ${to} (N:1).`;
    case "1:1":
      return `Cada registro de ${from} corresponde a un registro de ${to} (1:1).`;
    case "1:N":
      return `Un registro de ${from} se vincula con varios de ${to} (1:N).`;
    case "N:N":
      return `Varios registros de ${from} se vinculan con varios de ${to} (N:N).`;
    default:
      return `${from} se vincula con ${to}.`;
  }
}
