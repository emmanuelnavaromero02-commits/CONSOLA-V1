export type AssistantSegment = { kind: "text"; text: string } | { kind: "code"; lang: string | null; code: string };

export interface PendingApproval {
  approvalKey: string;
  stepId: string | number | null;
  goalRunId: string | null;
  tool: string | null;
  riskLevel: string | null;
  reason: string | null;
}

const FENCE = /```([A-Za-z0-9_+#.-]*)[^\n`]*\n([\s\S]*?)```/g;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_DEPTH = 8;

export function splitFencedBlocks(text: string): AssistantSegment[] {
  const segments: AssistantSegment[] = [];
  let last = 0;
  for (const match of text.matchAll(FENCE)) {
    const index = match.index ?? 0;
    if (index > last) segments.push({ kind: "text", text: text.slice(last, index) });
    segments.push({ kind: "code", lang: match[1] ? match[1].toLowerCase() : null, code: match[2].replace(/\n$/, "") });
    last = index + match[0].length;
  }
  if (last < text.length) segments.push({ kind: "text", text: text.slice(last) });
  return segments.filter((segment) => segment.kind === "code" || segment.text.trim());
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function toApproval(record: Record<string, unknown>): PendingApproval | null {
  if (record.approval_required !== true) return null;
  const source = typeof record.approval_key === "string"
    ? record
    : isRecord(record.approval)
      ? record.approval
      : null;
  const key = source ? text(source.approval_key) : null;
  if (!source || !key || !UUID.test(key)) return null;
  const step = source.step_id;
  return {
    approvalKey: key,
    stepId: typeof step === "number" && Number.isFinite(step) ? step : text(step),
    goalRunId: text(source.goal_run_id),
    tool: text(source.tool),
    riskLevel: text(source.risk_level),
    reason: text(source.reason),
  };
}

function collect(value: unknown, depth: number, found: Map<string, PendingApproval>): void {
  if (depth > MAX_DEPTH) return;
  if (typeof value === "string") {
    const trimmed = value.trim();
    if (!trimmed.startsWith("{")) return;
    try {
      collect(JSON.parse(trimmed), depth + 1, found);
    } catch {
      return;
    }
    return;
  }
  if (Array.isArray(value)) {
    for (const item of value) collect(item, depth + 1, found);
    return;
  }
  if (!isRecord(value)) return;
  const approval = toApproval(value);
  if (approval && !found.has(approval.approvalKey)) found.set(approval.approvalKey, approval);
  for (const child of Object.values(value)) collect(child, depth + 1, found);
}

export function pendingApprovals(messages: unknown[]): PendingApproval[] {
  const found = new Map<string, PendingApproval>();
  collect(messages, 0, found);
  return [...found.values()];
}

function stepText(approval: PendingApproval): string {
  return approval.stepId === null ? "pendiente" : String(approval.stepId);
}

export function approvalMessage(approval: PendingApproval): string {
  return `Apruebo el paso ${stepText(approval)} (approval_key ${approval.approvalKey}).`;
}

export function rejectionMessage(approval: PendingApproval): string {
  return `Rechazo el paso ${stepText(approval)} (approval_key ${approval.approvalKey}).`;
}
