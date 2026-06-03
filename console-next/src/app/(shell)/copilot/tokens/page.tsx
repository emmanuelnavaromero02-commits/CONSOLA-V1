"use client";

import { type FormEvent, type ReactNode, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Coins, DatabaseZap, KeyRound, MessageSquareText, RefreshCw, ShieldCheck, Sigma } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import { getMeAccess } from "@/lib/admin-surfaces";
import { cn } from "@/lib/utils";

interface TokenModelSummary {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  calls: number;
  cost_usd: number;
}

interface TokenSummary {
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  calls: number;
  cost_usd: number;
  models: TokenModelSummary[];
}

interface VaultSecretKeys {
  keys?: string[];
  secrets?: Array<{ key?: string }>;
}

export default function CopilotTokensPage() {
  const queryClient = useQueryClient();
  const [anthropicKey, setAnthropicKey] = useState("");
  const summary = useQuery({
    queryKey: ["copilot", "tokens", "summary"],
    queryFn: async () => {
      const { data } = await api.get<TokenSummary>("/tokens/summary");
      return data;
    },
  });
  const access = useQuery({
    queryKey: ["me", "access"],
    queryFn: getMeAccess,
    staleTime: 60_000,
  });
  const llmSecrets = useQuery({
    queryKey: ["copilot", "llm", "secrets"],
    queryFn: async () => {
      const { data } = await api.get<VaultSecretKeys>("/api/vault/secrets/llm");
      return data;
    },
    enabled: Boolean(access.data?.permissions?.includes("vault.secrets.read_masked")),
    staleTime: 30_000,
  });
  const saveAnthropicKey = useMutation({
    mutationFn: async (value: string) => {
      await api.put("/api/vault/secrets/llm/anthropic_api_key", { value });
    },
    onSuccess: async () => {
      setAnthropicKey("");
      await queryClient.invalidateQueries({ queryKey: ["copilot", "llm", "secrets"] });
      toast.success("Clave Anthropic guardada para este workspace.");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "No se pudo guardar la clave Anthropic.");
    },
  });

  const permissions = new Set(access.data?.permissions ?? []);
  const canManageLlmKey = permissions.has("vault.connections.write");
  const secretKeys = new Set([
    ...(llmSecrets.data?.keys ?? []),
    ...((llmSecrets.data?.secrets ?? []).map((item) => item.key).filter(Boolean) as string[]),
  ]);
  const anthropicConfigured = secretKeys.has("anthropic_api_key");

  function submitAnthropicKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = anthropicKey.trim();
    if (!value) {
      toast.error("Pega una API key de Anthropic antes de guardar.");
      return;
    }
    saveAnthropicKey.mutate(value);
  }

  const totalTokens = Number(summary.data?.input_tokens ?? 0)
    + Number(summary.data?.output_tokens ?? 0)
    + Number(summary.data?.cache_creation_tokens ?? 0)
    + Number(summary.data?.cache_read_tokens ?? 0);

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-6">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Uso de tokens</h1>
          <p className="text-sm text-muted-foreground">
            Consumo acumulado del copiloto por llamadas, tokens, caché y modelo.
          </p>
        </div>
        <button
          type="button"
          onClick={() => summary.refetch()}
          className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RefreshCw aria-hidden className={cn("h-4 w-4", summary.isFetching && "animate-spin")} />
          Refrescar
        </button>
      </header>

      {summary.isError ? (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <span>No se pudo cargar el uso de tokens.</span>
            <button
              type="button"
              onClick={() => summary.refetch()}
              className="inline-flex min-h-[40px] items-center justify-center rounded-md border bg-background px-3 text-sm font-medium text-foreground hover:bg-accent/5"
            >
              Reintentar
            </button>
          </div>
        </div>
      ) : null}

      {canManageLlmKey ? (
        <section className="rounded-lg border bg-card p-4 shadow-sm" aria-label="Clave Anthropic del workspace">
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(320px,420px)] lg:items-end">
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <span className="inline-flex h-9 w-9 items-center justify-center rounded-md bg-primary/10 text-primary">
                  <KeyRound aria-hidden className="h-4 w-4" />
                </span>
                <div>
                  <h2 className="text-base font-semibold">Clave Anthropic del workspace</h2>
                  <p className="text-sm text-muted-foreground">
                    Esta clave se guarda aislada para tu tenant/workspace y solo se usa en tus llamadas del copiloto.
                  </p>
                </div>
              </div>
              <span className={cn(
                "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium",
                anthropicConfigured
                  ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                  : "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
              )}>
                <ShieldCheck aria-hidden className="h-3.5 w-3.5" />
                {llmSecrets.isLoading ? "Revisando..." : anthropicConfigured ? "Configurada" : "Sin configurar"}
              </span>
            </div>
            <form onSubmit={submitAnthropicKey} className="flex flex-col gap-2 sm:flex-row">
              <input
                type="password"
                value={anthropicKey}
                onChange={(event) => setAnthropicKey(event.target.value)}
                className="min-h-[44px] min-w-0 flex-1 rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                placeholder="sk-ant-..."
                autoComplete="off"
              />
              <button
                type="submit"
                disabled={saveAnthropicKey.isPending}
                className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
              >
                {saveAnthropicKey.isPending ? "Guardando..." : "Guardar clave"}
              </button>
            </form>
          </div>
        </section>
      ) : null}

      <section className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-5" aria-label="Resumen de tokens">
        <MetricCard icon={MessageSquareText} label="Llamadas" value={formatInteger(summary.data?.calls ?? 0)} loading={summary.isLoading} />
        <MetricCard icon={Sigma} label="Tokens totales" value={formatInteger(totalTokens)} loading={summary.isLoading} />
        <MetricCard icon={Activity} label="Entrada" value={formatInteger(summary.data?.input_tokens ?? 0)} loading={summary.isLoading} />
        <MetricCard icon={DatabaseZap} label="Caché" value={formatInteger(Number(summary.data?.cache_creation_tokens ?? 0) + Number(summary.data?.cache_read_tokens ?? 0))} loading={summary.isLoading} />
        <MetricCard icon={Coins} label="Costo" value={formatCurrency(summary.data?.cost_usd ?? 0)} loading={summary.isLoading} />
      </section>

      <section className="rounded-lg border bg-card shadow-sm">
        <header className="flex flex-col gap-1 border-b px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 className="text-base font-semibold">Modelos</h2>
            <p className="text-xs text-muted-foreground">Distribución de uso registrada por proveedor/modelo.</p>
          </div>
          <span className="rounded-md border bg-background px-2 py-1 text-xs text-muted-foreground">
            {summary.isLoading ? "... modelos" : `${summary.data?.models?.length ?? 0} modelos`}
          </span>
        </header>

        {summary.isLoading ? (
          <SkeletonRows rows={5} />
        ) : summary.isError && !summary.data ? (
          <EmptyState label="No hay datos disponibles." />
        ) : (
          <ModelsTable rows={summary.data?.models ?? []} />
        )}
      </section>
    </main>
  );
}

function ModelsTable({ rows }: { rows: TokenModelSummary[] }) {
  if (rows.length === 0) return <EmptyState label="Todavía no hay consumo registrado por modelo." />;

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y text-sm">
        <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-4 py-3 text-left font-medium">Modelo</th>
            <th className="px-4 py-3 text-right font-medium">Llamadas</th>
            <th className="px-4 py-3 text-right font-medium">Entrada</th>
            <th className="px-4 py-3 text-right font-medium">Salida</th>
            <th className="px-4 py-3 text-right font-medium">Caché</th>
            <th className="px-4 py-3 text-right font-medium">Costo</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((row) => (
            <tr key={row.model}>
              <td className="max-w-[280px] truncate px-4 py-3 font-medium">{row.model || "sin modelo"}</td>
              <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">{formatInteger(row.calls)}</td>
              <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">{formatInteger(row.input_tokens)}</td>
              <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">{formatInteger(row.output_tokens)}</td>
              <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">
                {formatInteger(Number(row.cache_creation_tokens ?? 0) + Number(row.cache_read_tokens ?? 0))}
              </td>
              <td className="px-4 py-3 text-right tabular-nums text-muted-foreground">{formatCurrency(row.cost_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  loading,
}: {
  icon: LucideIcon;
  label: string;
  value: ReactNode;
  loading?: boolean;
}) {
  return (
    <div className="rounded-lg border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold">{loading ? "..." : value}</p>
        </div>
        <span className="inline-flex h-10 w-10 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Icon aria-hidden className="h-5 w-5" />
        </span>
      </div>
    </div>
  );
}

function SkeletonRows({ rows }: { rows: number }) {
  return (
    <div className="divide-y">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="grid grid-cols-6 gap-4 px-4 py-4">
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}

function EmptyState({ label }: { label: string }) {
  return <div className="px-4 py-10 text-center text-sm text-muted-foreground">{label}</div>;
}

function formatInteger(value: number): string {
  return new Intl.NumberFormat("es-ES", { maximumFractionDigits: 0 }).format(Number(value ?? 0));
}

function formatCurrency(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 4,
  }).format(Number(value ?? 0));
}
