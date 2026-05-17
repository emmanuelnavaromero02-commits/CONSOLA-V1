"use client";

import { useEffect, useRef, useState } from "react";

import { useMemory } from "@/lib/copilot/useMemory";

interface Props {
  open:    boolean;
  onClose: () => void;
}

/**
 * v1.44.4 Task A — memory drawer (Sheet-style side panel).
 *
 * Surfaces facts the copilot has learned about the operator
 * (GET /api/copilot/memory) and lets them delete entries
 * individually. Preferences are listed below the facts so the
 * operator can see "tono: friendly, idioma: es-MX, dso_target:
 * 45" at a glance.
 *
 * The "add fact" inline form is intentionally minimal — the
 * goal is to let operators correct the copilot ("no, mi DSO
 * target es 30 no 45") without leaving the chat surface.
 *
 * Side drawer pattern instead of a Radix Sheet primitive to
 * keep the dependency surface small; transitions in pure
 * Tailwind so dark mode + reduced-motion work consistently
 * with the rest of the console.
 */
export function MemoryDrawer({ open, onClose }: Props) {
  const {
    memoryQuery,
    createFactMutation,
    deleteFactMutation,
  } = useMemory();
  const facts       = memoryQuery.data?.facts ?? [];
  const preferences = memoryQuery.data?.preferences ?? [];

  const [newKey,   setNewKey]   = useState("");
  const [newValue, setNewValue] = useState("");

  const panelRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  function submitFact(e: React.FormEvent) {
    e.preventDefault();
    if (!newKey.trim() || !newValue.trim()) return;
    createFactMutation.mutate(
      { key: newKey.trim(), value: newValue.trim() },
      {
        onSuccess: () => {
          setNewKey("");
          setNewValue("");
        },
      },
    );
  }

  return (
    <div
      className="fixed inset-0 z-40 flex"
      role="dialog"
      aria-modal="true"
      aria-labelledby="memory-drawer-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {/* backdrop */}
      <div className="flex-1 bg-black/40" aria-hidden />

      <aside
        ref={panelRef}
        className="flex h-full w-full max-w-md flex-col border-l bg-background shadow-xl"
      >
        <header className="flex items-center justify-between border-b px-5 py-4">
          <h2
            id="memory-drawer-title"
            className="text-base font-semibold tracking-tight"
          >
            Memoria del copiloto
          </h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="Cerrar memoria"
            className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            ✕
          </button>
        </header>

        <div className="flex-1 overflow-y-auto p-5">
          {memoryQuery.isLoading ? (
            <p className="text-sm text-muted-foreground">Cargando memoria…</p>
          ) : null}

          {!memoryQuery.isLoading && facts.length === 0 && preferences.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              El copiloto aún no ha aprendido nada. Conforme converses, registrará
              hechos relevantes para personalizar las respuestas.
            </p>
          ) : null}

          {facts.length > 0 ? (
            <section aria-label="Hechos aprendidos" className="space-y-2">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Hechos
              </h3>
              <ul className="space-y-2">
                {facts.map((f) => (
                  <li
                    key={f.id}
                    className="flex items-start justify-between gap-3 rounded-md border bg-card p-3"
                  >
                    <div className="flex-1">
                      <p className="text-xs font-mono text-muted-foreground">
                        {f.key}
                      </p>
                      <p className="text-sm">{f.value}</p>
                    </div>
                    <button
                      type="button"
                      onClick={() => deleteFactMutation.mutate(f.id)}
                      aria-label={`Borrar hecho: ${f.key}`}
                      className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40"
                    >
                      ✕
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {preferences.length > 0 ? (
            <section aria-label="Preferencias" className="mt-6 space-y-2">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Preferencias
              </h3>
              <dl className="space-y-1.5 rounded-md border bg-card p-3 text-sm">
                {preferences.map((p) => (
                  <div key={p.key} className="flex justify-between gap-3">
                    <dt className="font-mono text-xs text-muted-foreground">
                      {p.key}
                    </dt>
                    <dd>{p.value}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ) : null}
        </div>

        <form
          onSubmit={submitFact}
          className="grid gap-2 border-t bg-muted/30 p-4 sm:grid-cols-[1fr_1fr_auto]"
        >
          <label className="sr-only" htmlFor="new-fact-key">
            Clave del hecho
          </label>
          <input
            id="new-fact-key"
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
            placeholder="clave (ej. dso_target)"
            className="min-h-[44px] rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <label className="sr-only" htmlFor="new-fact-value">
            Valor del hecho
          </label>
          <input
            id="new-fact-value"
            value={newValue}
            onChange={(e) => setNewValue(e.target.value)}
            placeholder="valor"
            className="min-h-[44px] rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <button
            type="submit"
            disabled={createFactMutation.isPending}
            className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
          >
            Guardar
          </button>
        </form>
      </aside>
    </div>
  );
}
