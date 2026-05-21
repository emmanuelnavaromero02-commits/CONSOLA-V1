"use client";

import { useEffect, useRef, useState } from "react";

import { toast } from "sonner";

import { useMemory } from "@/lib/copilot/useMemory";

interface Props {
  open:    boolean;
  onClose: () => void;
}

/**
 * v1.44.4 Task A — memory drawer (Sheet-style side panel).
 *
 * Backend reality (Round 1 review):
 *   - Facts:        ``{id, fact, source?, confidence?, created_at?}``
 *   - Preferences:  ``{pref_key, pref_value, updated_at?}``
 *
 * POST /memory/fact accepts ``{fact, source?}`` with source
 * limited to explicit/extracted. Manual UI writes always use
 * the backend default (explicit); extracted is reserved for the
 * LLM memory hook.
 *
 * Security review (Round 1): bounded maxLength on the inputs
 * (200 chars per fact, 64 per source) so an operator can't DOS
 * the storage layer by pasting a megabyte of text.
 */
const MAX_FACT_CHARS   = 200;


export function MemoryDrawer({ open, onClose }: Props) {
  const {
    memoryQuery,
    createFactMutation,
    deleteFactMutation,
  } = useMemory();
  const facts       = memoryQuery.data?.facts ?? [];
  const preferences = memoryQuery.data?.preferences ?? [];

  const [factText, setFactText]     = useState("");

  // Capture the previously-focused element so we can restore
  // focus on close (WCAG 2.4.3).
  const previousFocus = useRef<HTMLElement | null>(null);
  const firstInputRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    if (!open) return;

    previousFocus.current = (document.activeElement instanceof HTMLElement)
      ? document.activeElement
      : null;

    // Defer focus to next tick so the input is mounted.
    requestAnimationFrame(() => firstInputRef.current?.focus());

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previousFocus.current?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  function submitFact(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = factText.trim();
    if (!trimmed) return;
    createFactMutation.mutate(
      { fact: trimmed },
      {
        onSuccess: () => {
          setFactText("");
        },
        onError: (err) => {
          const msg = err instanceof Error ? err.message : "Error desconocido.";
          toast.error(`No se pudo guardar el hecho: ${msg}`);
        },
      },
    );
  }

  function removeFact(id: number, label: string) {
    deleteFactMutation.mutate(id, {
      onError: (err) => {
        const msg = err instanceof Error ? err.message : "Error desconocido.";
        toast.error(`No se pudo borrar "${label}": ${msg}`);
      },
    });
  }

  return (
    <div
      className="fixed inset-0 z-40 flex"
      role="dialog"
      aria-modal="true"
      aria-labelledby="memory-drawer-title"
    >
      {/* Backdrop owns the click-outside handler so a tap on the
          dimmed area actually closes the drawer (Round 1 P1). */}
      <div
        className="flex-1 bg-black/40"
        onClick={onClose}
        aria-hidden
      />

      <aside className="flex h-full w-full max-w-md flex-col border-l bg-background shadow-xl">
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

          {memoryQuery.isError ? (
            <div
              role="alert"
              className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm"
            >
              <p className="font-medium text-destructive">
                No se pudo cargar la memoria.
              </p>
              <button
                type="button"
                onClick={() => memoryQuery.refetch()}
                className="mt-2 inline-flex min-h-[44px] items-center justify-center rounded-md border border-destructive/40 px-3 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40"
              >
                Reintentar
              </button>
            </div>
          ) : null}

          {!memoryQuery.isLoading && !memoryQuery.isError
            && facts.length === 0 && preferences.length === 0 ? (
            <div className="space-y-2 rounded-md border bg-muted/30 p-4 text-sm">
              <p className="text-muted-foreground">
                El copiloto aún no ha aprendido nada. Conforme converses,
                registrará hechos relevantes para personalizar las respuestas.
              </p>
              <p className="text-xs text-muted-foreground">
                También puedes agregar un hecho manualmente con el formulario
                de abajo — por ejemplo:{" "}
                <code className="rounded bg-background px-1.5 py-0.5">
                  Mi DSO objetivo es 30 días
                </code>.
              </p>
            </div>
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
                    <div className="flex-1 space-y-1">
                      <p className="text-sm">{f.fact}</p>
                      {f.source ? (
                        <p className="text-[11px] text-muted-foreground">
                          Fuente: {f.source}
                        </p>
                      ) : null}
                    </div>
                    <button
                      type="button"
                      onClick={() => removeFact(f.id, f.fact)}
                      aria-label={`Borrar hecho: ${f.fact}`}
                      disabled={deleteFactMutation.isPending}
                      className="inline-flex min-h-[44px] min-w-[44px] shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-destructive/10 hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40 disabled:pointer-events-none disabled:opacity-60"
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
                  <div key={p.pref_key} className="flex justify-between gap-3">
                    <dt className="font-mono text-xs text-muted-foreground">
                      {p.pref_key}
                    </dt>
                    <dd>{p.pref_value}</dd>
                  </div>
                ))}
              </dl>
            </section>
          ) : null}
        </div>

        <form
          onSubmit={submitFact}
          className="grid gap-2 border-t bg-muted/30 p-4 sm:grid-cols-[1fr_auto]"
          aria-label="Agregar hecho"
        >
          <div className="space-y-2">
            <label className="sr-only" htmlFor="new-fact-text">
              Hecho
            </label>
            <input
              id="new-fact-text"
              ref={firstInputRef}
              value={factText}
              onChange={(e) => setFactText(e.target.value)}
              placeholder="Ej. Mi DSO objetivo es 30 días"
              maxLength={MAX_FACT_CHARS}
              className="min-h-[44px] w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </div>
          <button
            type="submit"
            disabled={createFactMutation.isPending || !factText.trim()}
            className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
          >
            {createFactMutation.isPending ? "Guardando…" : "Guardar"}
          </button>
        </form>
      </aside>
    </div>
  );
}
