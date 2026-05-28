"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, RefreshCw, Trash2 } from "lucide-react";

import { deleteAgent, listAgents, updateAgentStatus, type AgentRecord } from "@/lib/admin-surfaces";

function agentKey(agent: AgentRecord, index: number): string {
  return agent.id || agent.slug || `${agent.name}-${index}`;
}

function toolsLabel(agent: AgentRecord): string {
  const tools = agent.allowed_tools ?? [];
  if (tools.length === 0) return "Sin herramientas fijadas";
  if (tools.length <= 3) return tools.join(", ");
  return `${tools.slice(0, 3).join(", ")} +${tools.length - 3}`;
}

export function AgentsConsole() {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");

  const agents = useQuery({
    queryKey: ["agents"],
    queryFn: listAgents,
    staleTime: 30_000,
  });

  const status = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => updateAgentStatus(id, active),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agents"] }),
  });

  const remove = useMutation({
    mutationFn: deleteAgent,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agents"] }),
  });

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return agents.data ?? [];
    return (agents.data ?? []).filter((agent) => (
      [
        agent.name,
        agent.slug,
        agent.cartridge_id,
        agent.description,
        ...(agent.allowed_tools ?? []),
      ]
        .filter((value): value is string => typeof value === "string")
        .some((value) => value.toLowerCase().includes(needle))
    ));
  }, [agents.data, query]);

  if (agents.isLoading) {
    return (
      <div aria-busy="true" className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <span key={i} className="block h-44 animate-pulse rounded-lg bg-muted" aria-hidden />
        ))}
      </div>
    );
  }

  if (agents.isError) {
    return (
      <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
        <p className="font-medium text-destructive">No se pudieron cargar los agentes.</p>
        <button
          type="button"
          onClick={() => agents.refetch()}
          className="mt-2 inline-flex min-h-[44px] items-center gap-2 rounded-md border px-3 text-xs font-medium"
        >
          <RefreshCw aria-hidden className="h-4 w-4" />
          Reintentar
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="flex min-w-72 flex-1 flex-col gap-1.5 text-sm">
          <span className="font-medium">Buscar</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="nombre, cartucho, herramienta..."
            className="min-h-[44px] rounded-md border bg-background px-3 text-sm"
          />
        </label>
        <button
          type="button"
          onClick={() => agents.refetch()}
          className="inline-flex min-h-[44px] items-center gap-2 self-end rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5"
        >
          <RefreshCw aria-hidden className="h-4 w-4" />
          Refrescar
        </button>
      </div>

      {filtered.length === 0 ? (
        <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">
          No hay agentes que coincidan.
        </p>
      ) : (
        <section aria-label="Agentes" className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {filtered.map((agent, index) => {
            const active = agent.is_active !== false;
            return (
              <article key={agentKey(agent, index)} className="flex flex-col gap-4 rounded-lg border bg-card p-5">
                <div className="flex items-start gap-3">
                  <span
                    aria-hidden
                    className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary"
                  >
                    <Bot className="h-5 w-5" />
                  </span>
                  <div className="min-w-0 flex-1 space-y-1">
                    <h2 className="truncate text-base font-semibold tracking-tight">{agent.name}</h2>
                    <p className="font-mono text-xs text-muted-foreground">{agent.slug || agent.id}</p>
                  </div>
                  <span className={
                    "rounded-md px-2 py-1 text-xs font-semibold " +
                    (active ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-muted text-muted-foreground")
                  }>
                    {active ? "Activo" : "Inactivo"}
                  </span>
                </div>

                <p className="text-sm text-muted-foreground">{agent.description || "Sin descripción."}</p>
                <dl className="grid grid-cols-1 gap-2 text-xs sm:grid-cols-2">
                  <div className="rounded-md bg-muted/30 p-3">
                    <dt className="text-muted-foreground">Cartucho</dt>
                    <dd className="font-medium">{agent.cartridge_id || "—"}</dd>
                  </div>
                  <div className="rounded-md bg-muted/30 p-3">
                    <dt className="text-muted-foreground">Herramientas</dt>
                    <dd className="font-medium">{toolsLabel(agent)}</dd>
                  </div>
                </dl>

                <div className="mt-auto flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => status.mutate({ id: agent.id, active: !active })}
                    disabled={status.isPending}
                    className="inline-flex min-h-[44px] flex-1 items-center justify-center rounded-md border px-3 text-sm font-medium hover:bg-accent/5 disabled:opacity-50"
                  >
                    {active ? "Desactivar" : "Activar"}
                  </button>
                  <button
                    type="button"
                    onClick={() => remove.mutate(agent.id)}
                    disabled={remove.isPending}
                    aria-label={`Eliminar ${agent.name}`}
                    className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md border border-destructive/40 text-destructive hover:bg-destructive/10 disabled:opacity-50"
                  >
                    <Trash2 aria-hidden className="h-4 w-4" />
                  </button>
                </div>
              </article>
            );
          })}
        </section>
      )}

      {status.isError || remove.isError ? (
        <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          No se pudo completar la operación del agente.
        </p>
      ) : null}
    </div>
  );
}
