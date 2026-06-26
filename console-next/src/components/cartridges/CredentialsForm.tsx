"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ArrowRight, CheckCircle2, DatabaseZap, ExternalLink, Loader2, Plug, ShieldCheck, XCircle } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import type { ConnectorSchema, TestConnectionResult as Result } from "@/lib/cartridges";
import { useTestConnection } from "@/lib/hooks/useCartridges";
import { useVaultConnections } from "@/lib/operations/hooks";
import type { VaultConnection } from "@/lib/operations/types";
import {
  getActiveCartridgeSyncRun,
  getCartridgeSyncRun,
  hasSyncRunId,
  isSyncTerminal,
  startCartridgeSyncNow,
  type SyncRunPayload,
  type SyncRunStep,
} from "@/lib/sync-now";
import { TestConnectionResult } from "./TestConnectionResult";

interface Props {
  cartridgeId: string;
  schema: ConnectorSchema;
}

const CARTRIDGE_SYNC_STORAGE_PREFIX = "omega.cartridge.sync_run";

function cartridgeSyncStorageKey(cartridgeId: string): string {
  return `${CARTRIDGE_SYNC_STORAGE_PREFIX}.${cartridgeId}`;
}

function rememberCartridgeSyncRun(cartridgeId: string, runId: string): void {
  if (typeof window === "undefined" || !runId) return;
  try {
    window.sessionStorage.setItem(cartridgeSyncStorageKey(cartridgeId), runId);
  } catch {
    // Best-effort only; backend state is still queried by run id.
  }
}

function readRememberedCartridgeSyncRun(cartridgeId: string): string {
  if (typeof window === "undefined") return "";
  try {
    return window.sessionStorage.getItem(cartridgeSyncStorageKey(cartridgeId)) || "";
  } catch {
    return "";
  }
}

function forgetCartridgeSyncRun(cartridgeId: string): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.removeItem(cartridgeSyncStorageKey(cartridgeId));
  } catch {
    // Nothing to clean up.
  }
}

export function CredentialsForm({ cartridgeId, schema }: Props) {
  const testMut = useTestConnection(cartridgeId);
  const vaultConnections = useVaultConnections(cartridgeId);
  const queryClient = useQueryClient();

  const [lastTest, setLastTest] = useState<Result | null>(null);
  const [selectedConnId, setSelectedConnId] = useState("");
  const [syncRunId, setSyncRunId] = useState("");
  const hasConnectorSchema = (schema.fields ?? []).length > 0;
  const connectionOptions = useMemo(
    () => uniqueConnectionIds(vaultConnections.data?.connections ?? []),
    [vaultConnections.data?.connections],
  );
  const connIdForTest = selectedConnId || connectionOptions[0] || "";
  const syncRun = useQuery<SyncRunPayload>({
    queryKey: ["cartridge-sync-now", cartridgeId, syncRunId],
    queryFn: () => getCartridgeSyncRun(cartridgeId, syncRunId),
    enabled: Boolean(cartridgeId && syncRunId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return isSyncTerminal(status) ? false : 3000;
    },
  });
  const syncMut = useMutation({
    mutationFn: () =>
      startCartridgeSyncNow(cartridgeId, {
        mode: "incremental",
        target: "all",
        ...(connIdForTest ? { conn_id: connIdForTest } : {}),
      }),
    onSuccess: (payload) => {
      if (!hasSyncRunId(payload)) {
        forgetCartridgeSyncRun(cartridgeId);
        setSyncRunId("");
        toast("No hay sincronización activa para restaurar.");
        return;
      }
      rememberCartridgeSyncRun(cartridgeId, payload.run_id);
      setSyncRunId(payload.run_id);
      toast.success("Sincronización enviada.");
      void queryClient.invalidateQueries({ queryKey: ["dashboard", "kpis"] });
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "No se pudo iniciar la sincronización.");
    },
  });

  const onTest = async () => {
    try {
      const r = await testMut.mutateAsync(connIdForTest || undefined);
      setLastTest(r);
      if (r.ok) {
        toast.success("Conexion OK.");
      } else {
        const detail = r.message?.trim() || "Sin detalles";
        toast.error(`Error de conexion: ${detail}`);
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error desconocido";
      setLastTest({ ok: false, message, latency_ms: 0 });
      toast.error(`No se pudo probar: ${message}`);
    }
  };

  useEffect(() => {
    if (!cartridgeId || syncRunId) return undefined;
    let cancelled = false;

    const restoreSyncRun = async () => {
      const rememberedRunId = readRememberedCartridgeSyncRun(cartridgeId);
      if (rememberedRunId) {
        setSyncRunId(rememberedRunId);
        return;
      }
      try {
        const current = await getActiveCartridgeSyncRun(cartridgeId, {
          mode: "incremental",
          target: "all",
          ...(connIdForTest ? { conn_id: connIdForTest } : {}),
        });
        if (cancelled || !hasSyncRunId(current) || isSyncTerminal(current.status)) return;
        rememberCartridgeSyncRun(cartridgeId, current.run_id);
        setSyncRunId(current.run_id);
      } catch {
        // No active sync to restore.
      }
    };

    void restoreSyncRun();
    return () => {
      cancelled = true;
    };
  }, [cartridgeId, connIdForTest, syncRunId]);

  useEffect(() => {
    if (!cartridgeId || !syncRun.data || !isSyncTerminal(syncRun.data.status)) return;
    forgetCartridgeSyncRun(cartridgeId);
  }, [cartridgeId, syncRun.data]);

  return (
    <div className="space-y-6">
      <section className="space-y-4 rounded-lg border bg-card p-6 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="space-y-1">
            <div className="flex items-center gap-2 text-sm font-medium">
              <ShieldCheck size={18} aria-hidden />
              Vault scoped
            </div>
            <p className="max-w-2xl text-sm text-muted-foreground">
              Las credenciales se administran en Vault; esta pantalla solo prueba conexiones guardadas.
            </p>
          </div>
          <a
            href="/operations/vault"
            className="inline-flex min-h-[44px] items-center justify-center gap-1.5 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Configurar en Vault
            <ExternalLink size={16} aria-hidden />
          </a>
        </div>

        {hasConnectorSchema ? null : (
          <p className="text-sm text-muted-foreground">
            Este cartucho se valida con las conexiones disponibles en Vault.
          </p>
        )}

        <div className="flex flex-wrap items-end gap-2 pt-2">
          {connectionOptions.length ? (
            <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
              Conexión a probar
              <select
                value={connIdForTest}
                onChange={(event) => setSelectedConnId(event.target.value)}
                className="min-h-[44px] rounded-md border bg-background px-3 text-sm text-foreground"
              >
                {connectionOptions.map((connId) => (
                  <option key={connId} value={connId}>
                    {connId}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <button
            type="button"
            onClick={onTest}
            disabled={testMut.isPending}
            className="inline-flex min-h-[44px] items-center justify-center gap-1.5 rounded-md border bg-background px-4 text-sm font-medium transition-colors hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
          >
            <Plug size={16} aria-hidden />
            {testMut.isPending ? "Probando..." : "Probar conexión"}
          </button>
          <button
            type="button"
            onClick={() => syncMut.mutate()}
            disabled={syncMut.isPending}
            className="inline-flex min-h-[44px] items-center justify-center gap-1.5 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
          >
            {syncMut.isPending ? <Loader2 size={16} aria-hidden className="animate-spin" /> : <DatabaseZap size={16} aria-hidden />}
            {syncMut.isPending ? "Sincronizando..." : "Sincronizar ahora"}
          </button>
        </div>
      </section>

      <TestConnectionResult result={lastTest} />

      {syncRun.data ? (
        <SyncRunStatusCard cartridgeId={cartridgeId} payload={syncRun.data} loading={syncRun.isFetching && !isSyncTerminal(syncRun.data.status)} />
      ) : syncRunId && syncRun.isLoading ? (
        <section className="rounded-lg border bg-card p-4 text-sm text-muted-foreground">
          Cargando estado de sincronización...
        </section>
      ) : null}
    </div>
  );
}

function SyncRunStatusCard({
  cartridgeId,
  payload,
  loading,
}: {
  cartridgeId: string;
  payload: SyncRunPayload;
  loading: boolean;
}) {
  const tone = payload.status === "success" ? "text-emerald-600" : payload.status === "failed" ? "text-destructive" : "text-amber-600";
  return (
    <section className="space-y-4 rounded-lg border bg-card p-5 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-1">
          <p className="text-sm font-semibold">Sincronización completa</p>
          <p className="text-xs text-muted-foreground">
            {payload.triggered_entities.length} entidades disparadas · {payload.errors.length} errores iniciales
          </p>
        </div>
        <span className={`inline-flex items-center gap-1.5 rounded-full border bg-background px-2.5 py-1 text-xs font-medium ${tone}`}>
          {loading ? <Loader2 aria-hidden className="h-3.5 w-3.5 animate-spin" /> : payload.status === "failed" ? <XCircle aria-hidden className="h-3.5 w-3.5" /> : <CheckCircle2 aria-hidden className="h-3.5 w-3.5" />}
          {payload.status}
        </span>
      </div>
      <div className="grid gap-2 md:grid-cols-4">
        {payload.steps.map((step) => (
          <SyncStepCard key={step.id} step={step} />
        ))}
      </div>
      {payload.error_message ? (
        <p className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
          {payload.error_message}
        </p>
      ) : null}
      {isSyncTerminal(payload.status) ? (
        <div className="flex flex-wrap gap-2">
          <Link
            href="/control-room"
            className="inline-flex min-h-[40px] items-center justify-center gap-1.5 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90"
          >
            Ver Control Room
            <ArrowRight aria-hidden className="h-3.5 w-3.5" />
          </Link>
          <Link
            href={`/viewer?type=pipeline&cartridge=${encodeURIComponent(cartridgeId)}`}
            className="inline-flex min-h-[40px] items-center justify-center gap-1.5 rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5"
          >
            Ver Pipeline
            <ArrowRight aria-hidden className="h-3.5 w-3.5" />
          </Link>
        </div>
      ) : null}
    </section>
  );
}

function SyncStepCard({ step }: { step: SyncRunStep }) {
  const color =
    step.status === "success"
      ? "border-emerald-500/30 bg-emerald-500/5 text-emerald-700 dark:text-emerald-300"
      : step.status === "failed"
        ? "border-destructive/30 bg-destructive/5 text-destructive"
        : step.status === "partial"
          ? "border-amber-500/30 bg-amber-500/5 text-amber-700 dark:text-amber-300"
          : "border-border bg-background text-muted-foreground";
  return (
    <div className={`min-h-28 rounded-md border p-3 ${color}`}>
      <p className="text-xs font-semibold uppercase tracking-wide">{step.label}</p>
      <p className="mt-2 text-sm font-medium">{step.status}</p>
      {step.detail ? <p className="mt-1 text-xs opacity-80">{step.detail}</p> : null}
      {step.error ? <p className="mt-1 text-xs opacity-80">{step.error}</p> : null}
    </div>
  );
}

function uniqueConnectionIds(connections: VaultConnection[]): string[] {
  const ids = connections
    .map((conn) => String(conn.conn_id ?? conn.id ?? "").trim())
    .filter(Boolean);
  return Array.from(new Set(ids));
}
