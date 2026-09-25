import type { DagGraphEdge, DagGraphNode } from "./types";

export const GRAPH_NODE_WIDTH = 200;
export const GRAPH_NODE_HEIGHT = 56;
const COLUMN_GAP = 96;
const ROW_GAP = 20;
const PAD_X = 24;
const PAD_TOP = 52;
const PAD_BOTTOM = 24;

const KIND_RANK: Record<string, number> = { cartridge: 0, entity: 1, dag: 2, dataset: 2 };
const KIND_ORDER: Record<string, number> = { cartridge: 0, entity: 1, dag: 2, dataset: 3 };
const KIND_LABEL: Record<string, string> = {
  cartridge: "Cartucho",
  entity: "Entidad",
  dag: "DAG",
  dataset: "Dataset",
};
const KIND_COLUMN_LABEL: Record<string, string> = {
  cartridge: "Cartucho",
  entity: "Entidades",
  dag: "DAGs",
  dataset: "Datasets",
};

export interface PositionedNode {
  node: DagGraphNode;
  x: number;
  y: number;
  depth: number;
}

export interface PositionedEdge {
  source: string;
  target: string;
  path: string;
}

export interface GraphColumn {
  depth: number;
  x: number;
  label: string;
}

export interface GraphLayout {
  width: number;
  height: number;
  columns: GraphColumn[];
  nodes: PositionedNode[];
  edges: PositionedEdge[];
}

export function kindLabel(kind: string | null | undefined): string {
  return KIND_LABEL[String(kind ?? "")] ?? "Nodo";
}

function kindRank(kind: string): number {
  return KIND_RANK[kind] ?? 1;
}

function kindOrder(kind: string): number {
  return KIND_ORDER[kind] ?? 4;
}

function nodeLabel(node: DagGraphNode): string {
  return String(node.label || node.id);
}

export function uniqueGraph(
  nodes: DagGraphNode[],
  edges: DagGraphEdge[],
): { nodes: DagGraphNode[]; edges: DagGraphEdge[] } {
  const byId = new Map<string, DagGraphNode>();
  for (const node of nodes) {
    if (node && typeof node.id === "string" && node.id && !byId.has(node.id)) byId.set(node.id, node);
  }
  const seen = new Set<string>();
  const cleanEdges: DagGraphEdge[] = [];
  for (const edge of edges) {
    if (!edge || edge.source === edge.target) continue;
    if (!byId.has(edge.source) || !byId.has(edge.target)) continue;
    const key = `${edge.source}\u0000${edge.target}`;
    if (seen.has(key)) continue;
    seen.add(key);
    cleanEdges.push({ source: edge.source, target: edge.target });
  }
  return { nodes: [...byId.values()], edges: cleanEdges };
}

export function graphDepths(nodes: DagGraphNode[], edges: DagGraphEdge[]): Map<string, number> {
  const depth = new Map<string, number>();
  const indegree = new Map<string, number>();
  const outgoing = new Map<string, string[]>();
  for (const node of nodes) {
    depth.set(node.id, kindRank(String(node.kind)));
    indegree.set(node.id, 0);
    outgoing.set(node.id, []);
  }
  for (const edge of edges) {
    outgoing.get(edge.source)?.push(edge.target);
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
  }
  const queue = nodes.filter((node) => (indegree.get(node.id) ?? 0) === 0).map((node) => node.id);
  while (queue.length) {
    const current = queue.shift() as string;
    const base = depth.get(current) ?? 0;
    for (const target of outgoing.get(current) ?? []) {
      depth.set(target, Math.max(depth.get(target) ?? 0, base + 1));
      const remaining = (indegree.get(target) ?? 0) - 1;
      indegree.set(target, remaining);
      if (remaining === 0) queue.push(target);
    }
  }
  return depth;
}

function edgePath(from: PositionedNode, to: PositionedNode): string {
  const startX = from.x + GRAPH_NODE_WIDTH;
  const startY = from.y + GRAPH_NODE_HEIGHT / 2;
  const endY = to.y + GRAPH_NODE_HEIGHT / 2;
  if (to.x > from.x) {
    const endX = to.x - 6;
    const control = Math.max(32, (endX - startX) / 2);
    return `M ${startX} ${startY} C ${startX + control} ${startY}, ${endX - control} ${endY}, ${endX} ${endY}`;
  }
  const loopX = Math.max(startX, to.x + GRAPH_NODE_WIDTH) + 56;
  const endX = to.x + GRAPH_NODE_WIDTH + 6;
  return `M ${startX} ${startY} C ${loopX} ${startY}, ${loopX} ${endY}, ${endX} ${endY}`;
}

export function layoutDagGraph(rawNodes: DagGraphNode[], rawEdges: DagGraphEdge[]): GraphLayout {
  const { nodes, edges } = uniqueGraph(rawNodes, rawEdges);
  if (!nodes.length) {
    return { width: 480, height: 160, columns: [], nodes: [], edges: [] };
  }
  const depths = graphDepths(nodes, edges);
  const columns = new Map<number, DagGraphNode[]>();
  for (const node of nodes) {
    const depth = depths.get(node.id) ?? 0;
    columns.set(depth, [...(columns.get(depth) ?? []), node]);
  }
  const orderedDepths = [...columns.keys()].sort((a, b) => a - b);
  const positioned: PositionedNode[] = [];
  const columnMeta: GraphColumn[] = [];
  orderedDepths.forEach((depth, columnIndex) => {
    const x = PAD_X + columnIndex * (GRAPH_NODE_WIDTH + COLUMN_GAP);
    const members = [...(columns.get(depth) ?? [])].sort(
      (a, b) =>
        kindOrder(String(a.kind)) - kindOrder(String(b.kind))
        || nodeLabel(a).localeCompare(nodeLabel(b))
        || a.id.localeCompare(b.id),
    );
    const kinds = [...new Set(members.map((node) => String(node.kind)))];
    columnMeta.push({
      depth,
      x,
      label: kinds.map((kind) => KIND_COLUMN_LABEL[kind] ?? "Otros").join(" · "),
    });
    members.forEach((node, row) => {
      positioned.push({ node, depth, x, y: PAD_TOP + row * (GRAPH_NODE_HEIGHT + ROW_GAP) });
    });
  });
  const byId = new Map(positioned.map((item) => [item.node.id, item]));
  const maxRows = Math.max(...orderedDepths.map((depth) => columns.get(depth)?.length ?? 0));
  return {
    width: PAD_X * 2 + orderedDepths.length * GRAPH_NODE_WIDTH + (orderedDepths.length - 1) * COLUMN_GAP + 64,
    height: PAD_TOP + maxRows * (GRAPH_NODE_HEIGHT + ROW_GAP) - ROW_GAP + PAD_BOTTOM,
    columns: columnMeta,
    nodes: positioned,
    edges: edges.flatMap((edge) => {
      const from = byId.get(edge.source);
      const to = byId.get(edge.target);
      return from && to ? [{ ...edge, path: edgePath(from, to) }] : [];
    }),
  };
}

export function graphNeighbours(
  nodeId: string,
  edges: DagGraphEdge[],
): { incoming: string[]; outgoing: string[] } {
  return {
    incoming: edges.filter((edge) => edge.target === nodeId).map((edge) => edge.source),
    outgoing: edges.filter((edge) => edge.source === nodeId).map((edge) => edge.target),
  };
}
