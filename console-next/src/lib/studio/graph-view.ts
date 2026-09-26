import { Database, Plug, Table, Zap, type LucideIcon } from "lucide-react";

import { nodeLayer } from "./graph-layout";
import type { DagGraphEdge, DagGraphNode } from "./types";

export type NodeStage = "origen" | "extraccion" | "plata" | "oro" | "otro";

export const STAGES: NodeStage[] = ["origen", "extraccion", "plata", "oro"];

export const STAGE_STYLE: Record<NodeStage, string> = {
  origen: "fill-sky-500/10 stroke-sky-500/50",
  extraccion: "fill-amber-500/10 stroke-amber-500/60",
  plata: "fill-indigo-500/10 stroke-indigo-500/60",
  oro: "fill-emerald-500/10 stroke-emerald-500/60",
  otro: "fill-card stroke-border",
};

export const STAGE_TEXT: Record<NodeStage, string> = {
  origen: "text-sky-600 dark:text-sky-400",
  extraccion: "text-amber-600 dark:text-amber-400",
  plata: "text-indigo-600 dark:text-indigo-400",
  oro: "text-emerald-600 dark:text-emerald-400",
  otro: "text-muted-foreground",
};

export const STAGE_LABEL: Record<NodeStage, string> = {
  origen: "Origen",
  extraccion: "Extracción",
  plata: "Plata",
  oro: "Oro",
  otro: "Dataset",
};

const KIND_ICON: Record<string, LucideIcon> = {
  cartridge: Plug,
  entity: Database,
  dag: Zap,
  dataset: Table,
};

const KIND_PREFIX: Record<string, string> = {
  cartridge: "cartridge:",
  entity: "entity:",
  dag: "dag:",
};

const SEPARATORS = /[_:.\-/ ]/;

export function nodeStage(node: DagGraphNode): NodeStage {
  const kind = String(node.kind);
  if (kind === "cartridge" || kind === "entity") return "origen";
  if (kind === "dag") return "extraccion";
  if (kind === "dataset") {
    const layer = nodeLayer(node);
    if (layer === "silver") return "plata";
    if (layer === "gold") return "oro";
  }
  return "otro";
}

export function nodeIcon(node: DagGraphNode): LucideIcon {
  return KIND_ICON[String(node.kind)] ?? Table;
}

export function nodeCaption(node: DagGraphNode): string {
  const kind = String(node.kind);
  if (kind === "cartridge") return "Conector";
  if (kind === "entity") return "Tabla de origen";
  if (kind === "dag") return "Automatización";
  return STAGE_LABEL[nodeStage(node)];
}

export function smartTruncate(text: string, max: number): string {
  if (text.length <= max) return text;
  if (max <= 1) return "…";
  if (SEPARATORS.test(text)) {
    const tail = Math.floor((max - 1) * 0.4);
    const head = max - 1 - tail;
    return `${text.slice(0, head)}…${tail ? text.slice(text.length - tail) : ""}`;
  }
  return `${text.slice(0, max - 1)}…`;
}

export interface NodeRef {
  kind: string;
  name: string;
  layer: string | null;
}

export function nodeRef(node: DagGraphNode): NodeRef {
  const kind = String(node.kind);
  const layer = nodeLayer(node);
  let name = node.id;
  if (kind === "dataset") {
    const match = /^dataset:[^:]*:(.+)$/.exec(node.id);
    name = match ? match[1] : node.id.replace(/^dataset:/, "");
  } else if (KIND_PREFIX[kind] && node.id.startsWith(KIND_PREFIX[kind])) {
    name = node.id.slice(KIND_PREFIX[kind].length);
  }
  return { kind, name, layer };
}

export function collectDownstream(id: string, edges: DagGraphEdge[]): string[] {
  const outgoing = new Map<string, string[]>();
  for (const edge of edges) {
    outgoing.set(edge.source, [...(outgoing.get(edge.source) ?? []), edge.target]);
  }
  const seen = new Set<string>([id]);
  const order: string[] = [];
  const queue = [id];
  while (queue.length) {
    const current = queue.shift() as string;
    for (const target of outgoing.get(current) ?? []) {
      if (seen.has(target)) continue;
      seen.add(target);
      order.push(target);
      queue.push(target);
    }
  }
  return order;
}

export type EdgeRole = "in" | "out";

export function edgeRole(edge: DagGraphEdge, activeId: string | null): EdgeRole | null {
  if (!activeId) return null;
  if (edge.target === activeId) return "in";
  if (edge.source === activeId) return "out";
  return null;
}
