"use client";

import { useMemo } from "react";

import { ArrowRight } from "lucide-react";

import type { Severity } from "@/lib/copilot/types";
import { cn } from "@/lib/utils";

interface FreshnessInfo {
  age_hours: number | null;
  status:    "fresh" | "stale" | "very_stale" | "never";
}

interface Props {
  id:           string;
  name:         string;
  description:  string;
  freshness?:   FreshnessInfo;
  legacyHref:   string;
}

const STATUS_LABEL: Record<FreshnessInfo["status"], string> = {
  fresh:      "Datos frescos",
  stale:      "Datos antiguos",
  very_stale: "Datos muy antiguos",
  never:      "Sin extracciones",
};

const STATUS_SEVERITY: Record<FreshnessInfo["status"], Severity> = {
  fresh:      "info",
  stale:      "warning",
  very_stale: "critical",
  never:      "warning",
};

const SEVERITY_DOT: Record<Severity, string> = {
  info:     "bg-emerald-500",
  warning:  "bg-amber-500",
  critical: "bg-red-500",
};


function formatAge(hours: number | null): string {
  if (hours === null) return "Nunca";
  if (hours < 1)  return "Hace < 1 h";
  if (hours < 48) return `Hace ${Math.round(hours)} h`;
  const days = Math.round(hours / 24);
  return `Hace ${days} d`;
}


/**
 * v1.44.4 Group 1 — Studio cartridge launcher card.
 *
 * Surfaces real freshness data from /api/dashboard/kpis (the
 * only Studio-relevant data the backend exposes today — the
 * full /api/studio/* surface is 100 % stubs) plus a deep link
 * into the legacy /studio?cartridge=<id> UI on :8000 where
 * the actual feature still works.
 *
 * No buttons trigger destructive Studio operations from here;
 * the legacy UI owns Deploy / Materialise / Create / Delete
 * until v1.44.5 graduates the stubs.
 */
export function CartridgeLauncherCard({
  id,
  name,
  description,
  freshness,
  legacyHref,
}: Props) {
  const status   = freshness?.status ?? "never";
  const severity = STATUS_SEVERITY[status];
  const dot      = SEVERITY_DOT[severity];
  const ageLabel = useMemo(
    () => formatAge(freshness?.age_hours ?? null),
    [freshness?.age_hours],
  );

  return (
    <article
      aria-labelledby={`studio-card-${id}-title`}
      className="flex flex-col gap-3 rounded-lg border bg-card p-5 shadow-sm transition-shadow hover:shadow-md"
      data-testid="studio-cartridge-card"
      data-cartridge={id}
    >
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <h2
            id={`studio-card-${id}-title`}
            className="text-base font-semibold tracking-tight"
          >
            {name}
          </h2>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
        <span
          className={cn(
            "inline-flex shrink-0 items-center gap-1.5 rounded-full border bg-background px-2 py-0.5 text-[10px] font-medium",
          )}
          aria-label={`${STATUS_LABEL[status]}, ${ageLabel}`}
        >
          <span aria-hidden className={cn("h-1.5 w-1.5 rounded-full", dot)} />
          {ageLabel}
        </span>
      </header>

      <p className="text-xs text-muted-foreground">
        {STATUS_LABEL[status]}
      </p>

      <a
        href={legacyHref}
        rel="noopener"
        className="mt-auto inline-flex min-h-[44px] items-center justify-center gap-1.5 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        Abrir Studio
        <ArrowRight aria-hidden className="h-4 w-4" />
      </a>
    </article>
  );
}
