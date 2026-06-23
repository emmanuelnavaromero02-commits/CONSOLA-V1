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
  target?: SyncTarget;
}

export function isSyncTerminal(status: string | undefined): boolean {
  return status === "success" || status === "partial" || status === "failed";
}

export async function startCartridgeSyncNow(
  cartridgeId: string,
  input: StartSyncNowInput = {},
): Promise<SyncRunPayload> {
  const { data } = await api.post<SyncRunPayload>(
    `/api/cartridges/${encodeURIComponent(cartridgeId)}/sync-now`,
    input,
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
