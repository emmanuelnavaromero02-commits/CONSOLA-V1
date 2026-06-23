import { api } from "@/lib/api";

export type SyncRunStatus = "queued" | "running" | "success" | "partial" | "failed" | "skipped";
export type SyncTarget = "all" | "foundation" | "talent";

export interface SyncRunStep {
  id: string;
  label: string;
  status: SyncRunStatus;
  detail?: string;
  attempts?: number;
  error?: string;
}

export interface SyncRunPayload {
  run_id: string;
  cartridge_id: string;
  status: SyncRunStatus;
  mode: "incremental" | "full";
  target: SyncTarget;
  steps: SyncRunStep[];
  triggered_entities: Array<Record<string, unknown>>;
  errors: Array<Record<string, unknown>>;
  control_room_ready: boolean;
  started_at?: string | null;
  finished_at?: string | null;
  error_message?: string | null;
}

export interface StartSyncNowInput {
  conn_id?: string;
  mode?: "incremental" | "full";
  request_id?: string;
  target?: SyncTarget;
}

export function isSyncTerminal(status: string | undefined): boolean {
  return status === "success" || status === "partial" || status === "failed";
}

function safeRequestSegment(value: string): string {
  return value.replace(/[^A-Za-z0-9_.:-]/g, "_").slice(0, 48) || "unknown";
}

export function createSyncNowRequestId(
  cartridgeId: string,
  input: Pick<StartSyncNowInput, "mode" | "target"> = {},
): string {
  const random =
    globalThis.crypto?.randomUUID?.() ??
    `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
  return [
    "sync-now",
    safeRequestSegment(cartridgeId),
    safeRequestSegment(input.mode ?? "incremental"),
    safeRequestSegment(input.target ?? "all"),
    safeRequestSegment(random),
  ]
    .join(":")
    .slice(0, 128);
}

export async function startCartridgeSyncNow(
  cartridgeId: string,
  input: StartSyncNowInput = {},
): Promise<SyncRunPayload> {
  const request_id = input.request_id ?? createSyncNowRequestId(cartridgeId, input);
  const { data } = await api.post<SyncRunPayload>(
    `/api/cartridges/${encodeURIComponent(cartridgeId)}/sync-now`,
    { ...input, request_id },
  );
  return data;
}

export async function getCartridgeSyncRun(
  cartridgeId: string,
  runId: string,
): Promise<SyncRunPayload> {
  const { data } = await api.get<SyncRunPayload>(
    `/api/cartridges/${encodeURIComponent(cartridgeId)}/sync-runs/${encodeURIComponent(runId)}`,
  );
  return data;
}
