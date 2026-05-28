"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Plus, RefreshCw, Trash2 } from "lucide-react";

import {
  addDecisionAction,
  createDecision,
  deleteDecision,
  getDecision,
  listDecisions,
  updateDecision,
  type Decision,
  type DecisionVisibility,
} from "@/lib/admin-surfaces";

type Filter = "all" | "open" | "closed";

function dateShort(value?: string | null): string {
  if (!value) return "—";
  return String(value).slice(0, 10);
}

function statusClass(status?: string | null): string {
  return status === "closed"
    ? "bg-muted text-muted-foreground"
    : "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300";
}

function visibilityLabel(value?: string | null): string {
  return value === "shared" ? "Equipo" : "Privada";
}

export function DecisionsBoard() {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<Filter>("all");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [newTitle, setNewTitle] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [newDate, setNewDate] = useState("");
  const [newVisibility, setNewVisibility] = useState<DecisionVisibility>("private");
  const [actionText, setActionText] = useState("");

  const decisions = useQuery({
    queryKey: ["decisions", filter],
    queryFn: () => listDecisions(filter === "all" ? "" : filter),
    staleTime: 30_000,
  });

  const detail = useQuery({
    queryKey: ["decisions", "detail", selectedId],
    queryFn: () => getDecision(selectedId as number),
    enabled: selectedId !== null,
    staleTime: 10_000,
  });

  const create = useMutation({
    mutationFn: createDecision,
    onSuccess: (row) => {
      setNewTitle("");
      setNewDescription("");
      setNewDate("");
      setSelectedId(row.id);
      queryClient.invalidateQueries({ queryKey: ["decisions"] });
    },
  });

  const patch = useMutation({
    mutationFn: ({ id, closed }: { id: number; closed: boolean }) => updateDecision(id, {
      status: closed ? "closed" : "open",
      outcome: closed ? "achieved" : null,
    }),
    onSuccess: (row) => {
      setSelectedId(row.id);
      queryClient.invalidateQueries({ queryKey: ["decisions"] });
    },
  });

  const remove = useMutation({
    mutationFn: deleteDecision,
    onSuccess: () => {
      setSelectedId(null);
      queryClient.invalidateQueries({ queryKey: ["decisions"] });
    },
  });

  const addAction = useMutation({
    mutationFn: ({ id, text }: { id: number; text: string }) => addDecisionAction(id, text),
    onSuccess: () => {
      setActionText("");
      queryClient.invalidateQueries({ queryKey: ["decisions", "detail", selectedId] });
    },
  });

  const rows = decisions.data ?? [];
  const selected = useMemo<Decision | null>(() => {
    if (detail.data) return detail.data;
    return rows.find((row) => row.id === selectedId) ?? null;
  }, [detail.data, rows, selectedId]);

  function submitNewDecision() {
    const title = newTitle.trim();
    if (!title) return;
    create.mutate({
      title,
      description: newDescription.trim(),
      commitment_date: newDate || undefined,
      visibility: newVisibility,
    });
  }

  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-[1.1fr_0.9fr]">
      <section className="space-y-4" aria-label="Listado de decisiones">
        <div className="rounded-lg border bg-card p-4">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium">Título</span>
              <input
                value={newTitle}
                onChange={(event) => setNewTitle(event.target.value)}
                className="min-h-[44px] rounded-md border bg-background px-3 text-sm"
              />
            </label>
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium">Compromiso</span>
              <input
                type="date"
                value={newDate}
                onChange={(event) => setNewDate(event.target.value)}
                className="min-h-[44px] rounded-md border bg-background px-3 text-sm"
              />
            </label>
            <label className="flex flex-col gap-1.5 text-sm md:col-span-2">
              <span className="font-medium">Descripción</span>
              <textarea
                value={newDescription}
                onChange={(event) => setNewDescription(event.target.value)}
                className="min-h-24 rounded-md border bg-background px-3 py-2 text-sm"
              />
            </label>
            <label className="flex flex-col gap-1.5 text-sm">
              <span className="font-medium">Visibilidad</span>
              <select
                value={newVisibility}
                onChange={(event) => setNewVisibility(event.target.value as DecisionVisibility)}
                className="min-h-[44px] rounded-md border bg-background px-3 text-sm"
              >
                <option value="private">Privada</option>
                <option value="shared">Equipo</option>
              </select>
            </label>
            <button
              type="button"
              onClick={submitNewDecision}
              disabled={create.isPending || !newTitle.trim()}
              className="inline-flex min-h-[44px] items-center justify-center gap-2 self-end rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              <Plus aria-hidden className="h-4 w-4" />
              Nueva decisión
            </button>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex flex-wrap gap-2" role="tablist" aria-label="Filtro de decisiones">
            {(["all", "open", "closed"] as Filter[]).map((item) => (
              <button
                key={item}
                type="button"
                onClick={() => setFilter(item)}
                aria-pressed={filter === item}
                className={
                  "inline-flex min-h-[44px] items-center rounded-md border px-3 text-sm font-medium " +
                  (filter === item ? "bg-primary text-primary-foreground" : "bg-background hover:bg-accent/5")
                }
              >
                {item === "all" ? "Todas" : item === "open" ? "Abiertas" : "Cerradas"}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={() => decisions.refetch()}
            className="inline-flex min-h-[44px] items-center gap-2 rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5"
          >
            <RefreshCw aria-hidden className="h-4 w-4" />
            Refrescar
          </button>
        </div>

        {decisions.isLoading ? (
          <div aria-busy="true" className="space-y-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <span key={i} className="block h-14 animate-pulse rounded bg-muted" aria-hidden />
            ))}
          </div>
        ) : decisions.isError ? (
          <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
            No se pudieron cargar decisiones.
          </p>
        ) : rows.length === 0 ? (
          <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">
            No hay decisiones en este filtro.
          </p>
        ) : (
          <div className="overflow-x-auto rounded-lg border bg-card">
            <table className="w-full min-w-[760px] text-sm">
              <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="px-4 py-2 font-medium">Título</th>
                  <th className="px-4 py-2 font-medium">Compromiso</th>
                  <th className="px-4 py-2 font-medium">Visibilidad</th>
                  <th className="px-4 py-2 font-medium">Estado</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.id}
                    className={"border-t " + (selectedId === row.id ? "bg-accent/10" : "")}
                  >
                    <td className="px-4 py-3">
                      <button
                        type="button"
                        onClick={() => setSelectedId(row.id)}
                        className="min-h-[44px] text-left font-medium text-foreground hover:text-primary"
                      >
                        {row.title}
                      </button>
                    </td>
                    <td className="px-4 py-3 text-xs text-muted-foreground">{dateShort(row.commitment_date)}</td>
                    <td className="px-4 py-3 text-xs text-muted-foreground">{visibilityLabel(row.visibility)}</td>
                    <td className="px-4 py-3">
                      <span className={`inline-flex rounded-md px-2 py-1 text-xs font-semibold ${statusClass(row.status)}`}>
                        {row.status || "open"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <aside className="space-y-4 rounded-lg border bg-card p-4" aria-label="Detalle de decisión">
        {!selected ? (
          <p className="text-sm text-muted-foreground">Selecciona una decisión para ver detalle y bitácora.</p>
        ) : (
          <>
            <div className="flex items-start justify-between gap-3">
              <div className="space-y-1">
                <h2 className="text-xl font-semibold tracking-tight">{selected.title}</h2>
                <p className="text-sm text-muted-foreground">{selected.description || "Sin descripción."}</p>
              </div>
              <button
                type="button"
                onClick={() => remove.mutate(selected.id)}
                disabled={remove.isPending}
                className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md border border-destructive/40 text-destructive hover:bg-destructive/10 disabled:opacity-50"
                aria-label="Eliminar decisión"
              >
                <Trash2 aria-hidden className="h-4 w-4" />
              </button>
            </div>

            <dl className="grid grid-cols-2 gap-3 text-sm">
              <div className="rounded-md bg-muted/30 p-3">
                <dt className="text-xs text-muted-foreground">Compromiso</dt>
                <dd className="font-medium">{dateShort(selected.commitment_date)}</dd>
              </div>
              <div className="rounded-md bg-muted/30 p-3">
                <dt className="text-xs text-muted-foreground">Estado</dt>
                <dd className="font-medium">{selected.status || "open"}</dd>
              </div>
              <div className="rounded-md bg-muted/30 p-3">
                <dt className="text-xs text-muted-foreground">Visibilidad</dt>
                <dd className="font-medium">{visibilityLabel(selected.visibility)}</dd>
              </div>
              <div className="rounded-md bg-muted/30 p-3">
                <dt className="text-xs text-muted-foreground">Resultado</dt>
                <dd className="font-medium">{selected.outcome || "—"}</dd>
              </div>
            </dl>

            <button
              type="button"
              onClick={() => patch.mutate({ id: selected.id, closed: selected.status !== "closed" })}
              disabled={patch.isPending}
              className="inline-flex min-h-[44px] items-center gap-2 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 disabled:opacity-50"
            >
              <CheckCircle2 aria-hidden className="h-4 w-4" />
              {selected.status === "closed" ? "Reabrir" : "Cerrar como lograda"}
            </button>

            <section className="space-y-3" aria-label="Bitácora">
              <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">Bitácora</h3>
              <div className="flex gap-2">
                <input
                  value={actionText}
                  onChange={(event) => setActionText(event.target.value)}
                  placeholder="Nueva nota..."
                  className="min-h-[44px] flex-1 rounded-md border bg-background px-3 text-sm"
                />
                <button
                  type="button"
                  onClick={() => selectedId && addAction.mutate({ id: selectedId, text: actionText.trim() })}
                  disabled={!actionText.trim() || addAction.isPending}
                  className="inline-flex min-h-[44px] items-center rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground disabled:opacity-50"
                >
                  Agregar
                </button>
              </div>
              {detail.isFetching ? (
                <p className="text-sm text-muted-foreground">Cargando detalle...</p>
              ) : (detail.data?.actions ?? []).length === 0 ? (
                <p className="text-sm text-muted-foreground">Sin acciones registradas.</p>
              ) : (
                <ol className="space-y-2">
                  {(detail.data?.actions ?? []).map((action, index) => (
                    <li key={`${action.ts ?? ""}-${index}`} className="rounded-md border bg-background p-3 text-sm">
                      <p>{action.action_text || "Acción sin texto"}</p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {action.actor || "usuario"} · {dateShort(action.ts)}
                      </p>
                    </li>
                  ))}
                </ol>
              )}
            </section>
          </>
        )}
      </aside>
    </div>
  );
}
