"use client";

import { useRef } from "react";

import Link from "next/link";
import { Sparkles } from "lucide-react";

import { useBriefing } from "@/lib/copilot/useBriefing";

import { BriefingCard } from "./BriefingCard";

/**
 * v1.44.4 Task B — proactive briefing section.
 *
 * Mounted ABOVE the KPI grid on /dashboard so the operator
 * sees actionable alerts the moment they land. Polling cadence
 * (60 s) lives in the useBriefing hook; this component is the
 * presentation orchestrator (loading / error / empty / list).
 *
 * Round 1 review fixes:
 *   - Skeleton count remembers last-known length instead of
 *     hard-coding 3-up, so a 1-card real state doesn't jump
 *     the grid two columns to the left (UX P1).
 *   - Critical-count badge surfaces in the header when any
 *     critical alert is present (UX P2 promoted to P1 — the
 *     severity signal was getting lost in a sea of muted-grey).
 *
 * Grid responsiveness:
 *   - mobile (< sm): single column
 *   - tablet (≥ sm): 2 columns
 *   - desktop (≥ lg): 3 columns
 *
 * Accessibility:
 *   - section is role="region" with aria-label
 *   - empty / error states use role="status" / role="alert"
 *   - cards carry role="article" implicitly via <article>
 */
function SkeletonCard() {
  return (
    <div
      className="flex animate-pulse flex-col gap-3 rounded-lg border border-l-4 border-l-muted bg-card p-4 shadow-sm"
      aria-hidden
    >
      <div className="flex items-start gap-3 pr-14">
        <span className="h-9 w-9 shrink-0 rounded-full bg-muted" />
        <div className="flex-1 space-y-2">
          <span className="block h-4 w-3/4 rounded bg-muted" />
          <span className="block h-3 w-full rounded bg-muted" />
          <span className="block h-3 w-2/3 rounded bg-muted" />
        </div>
      </div>
      <span className="h-10 w-32 rounded-md bg-muted" />
    </div>
  );
}


export function BriefingSection() {
  const { briefingQuery, dismissWithUndo } = useBriefing();

  const isLoading      = briefingQuery.isLoading;
  const isError        = briefingQuery.isError;
  const highlights     = briefingQuery.data ?? [];
  const isEmpty        = !isLoading && !isError && highlights.length === 0;

  // Remember the last-known count so the skeleton renders the
  // right number of placeholders on refetch (avoids layout
  // jump when real data is 1 / 2 / 4 / 5 cards).
  const lastKnownCount = useRef<number>(0);
  if (!isLoading && !isError && highlights.length > 0) {
    lastKnownCount.current = highlights.length;
  }
  const skeletonCount = Math.max(1, Math.min(lastKnownCount.current || 3, 6));

  const criticalCount = highlights.filter(
    (h) => h.severity === "critical",
  ).length;

  return (
    <section
      aria-label="Resumen del día"
      role="region"
      className="space-y-3"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
          Resumen del día
        </h2>
        {!isLoading && !isError && highlights.length > 0 ? (
          <p className="text-xs text-muted-foreground">
            {highlights.length === 1
              ? "1 aviso"
              : `${highlights.length} avisos`}
            {criticalCount > 0 ? (
              <>
                {" · "}
                <span className="font-semibold text-red-600 dark:text-red-400">
                  {criticalCount === 1
                    ? "1 crítico"
                    : `${criticalCount} críticos`}
                </span>
              </>
            ) : null}
          </p>
        ) : null}
      </header>

      {isLoading ? (
        <div
          aria-busy="true"
          className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3"
        >
          {Array.from({ length: skeletonCount }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      ) : null}

      {isError ? (
        <div
          role="alert"
          aria-live="polite"
          className="flex flex-col gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm sm:flex-row sm:items-center sm:justify-between"
        >
          <p className="font-medium text-destructive">
            No se pudo cargar el resumen.
          </p>
          <button
            type="button"
            onClick={() => briefingQuery.refetch()}
            className="inline-flex min-h-[44px] items-center justify-center rounded-md border border-destructive/40 px-3 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40"
          >
            Reintentar
          </button>
        </div>
      ) : null}

      {isEmpty ? (
        <div
          role="status"
          className="flex flex-col items-start gap-3 rounded-lg border bg-card p-5 sm:flex-row sm:items-center sm:justify-between"
        >
          <div className="flex items-center gap-3">
            <span
              aria-hidden
              className="inline-flex h-10 w-10 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
            >
              <Sparkles className="h-5 w-5" />
            </span>
            <div>
              <p className="text-sm font-semibold tracking-tight">
                Todo en orden{" "}
                <span aria-hidden>🎉</span>
              </p>
              <p className="text-xs text-muted-foreground">
                No hay alertas por ahora. Pregúntale al copiloto si necesitas algo.
              </p>
            </div>
          </div>
          <Link
            href="/workspace"
            className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-3 text-xs font-medium transition-colors hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Ir al copiloto →
          </Link>
        </div>
      ) : null}

      {!isLoading && !isError && highlights.length > 0 ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {highlights.map((h) => (
            <BriefingCard
              key={h.id}
              highlight={h}
              onDismiss={dismissWithUndo}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}
