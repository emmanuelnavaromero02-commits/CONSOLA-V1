import type { ControlAlert, ControlItem } from "@/lib/control-room/types";

import { SAP_B1_CARTRIDGE } from "./client";

// GET /api/control-room/alerts carries the alert state in `status` (open, acknowledged,
// assigned, snoozed) and drops false positives and closed items; it has no decision or
// date. Those come from the dashboard item with the same id: `status` / `omega.decision.status`
// is "decision_created" once a decision is linked, and `detected_at` is the detection date.
export type AgentAlertState = "decision" | "false_positive" | "open" | "acknowledged" | "assigned" | "snoozed";

export const AGENT_ALERT_STATE_LABELS: Record<AgentAlertState, string> = {
  decision: "Decisión registrada",
  false_positive: "Falso positivo",
  open: "Abierta",
  acknowledged: "Reconocida",
  assigned: "Asignada",
  snoozed: "Pospuesta",
};

export const ALERT_SEVERITY_LABELS: Record<string, string> = {
  critical: "Crítica",
  high: "Alta",
  medium: "Media",
  low: "Baja",
};

const DECISION_STATUSES = new Set(["decision_created", "approved"]);

export interface AgentAlertRow {
  itemId: string;
  title: string;
  severity: string | null;
  detectedAt: string | null;
  state: AgentAlertState;
  alertStatus: string;
  hasDecision: boolean;
  falsePositive: boolean;
}

export interface AgentAlertLocalMarks {
  decided?: ReadonlySet<string>;
  falsePositives?: ReadonlySet<string>;
}

function hasDecision(item: ControlItem | undefined): boolean {
  if (!item) return false;
  return DECISION_STATUSES.has(item.status ?? "") || DECISION_STATUSES.has(item.omega?.decision?.status ?? "");
}

function alertState(status: string): AgentAlertState {
  return status === "acknowledged" || status === "assigned" || status === "snoozed" ? status : "open";
}

function timeOf(value: string | null): number {
  if (!value) return Number.NEGATIVE_INFINITY;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
}

export function sapB1AgentAlertRows(
  alerts: readonly ControlAlert[] | null | undefined,
  items: readonly ControlItem[] | null | undefined,
  marks: AgentAlertLocalMarks = {},
): AgentAlertRow[] {
  const byId = new Map((items ?? []).map((item) => [item.id, item]));
  const rows = (alerts ?? []).flatMap((alert, index) => {
    const itemId = alert.item_id;
    if (alert.cartridge !== SAP_B1_CARTRIDGE || !itemId) return [];
    const item = byId.get(itemId);
    const alertStatus = alert.status ?? "open";
    const falsePositive = Boolean(marks.falsePositives?.has(itemId));
    const decided = hasDecision(item) || Boolean(marks.decided?.has(itemId));
    const row: AgentAlertRow = {
      itemId,
      title: alert.title || item?.title || "Alerta sin título",
      severity: alert.severity ?? item?.severity ?? null,
      detectedAt: item?.detected_at || alert.detected_at || null,
      state: falsePositive ? "false_positive" : decided ? "decision" : alertState(alertStatus),
      alertStatus,
      hasDecision: decided,
      falsePositive,
    };
    return [{ row, index }];
  });
  rows.sort((a, b) => timeOf(b.row.detectedAt) - timeOf(a.row.detectedAt) || a.index - b.index);
  return rows.map(({ row }) => row);
}
