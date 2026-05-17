"use client";

import { useMemo } from "react";

import type { Citation } from "@/lib/copilot/types";

interface Props {
  citation: Citation;
}

/**
 * v1.44.4 Task A — citation card.
 *
 * Shows the source label + freshness signal under an assistant
 * reply ("📊 Replicon · time_entries · hace 4h"). If the
 * backend supplies an ``href`` it renders as a deep link;
 * otherwise the snippet (when present) is shown as a title
 * attribute so a tooltip hover gives the operator context.
 *
 * Freshness is computed client-side from ``fetched_at`` —
 * displays in Spanish ("hace 4h", "hace 2d", etc.) per the
 * sprint copy convention. No external i18n library needed for
 * a single relative-time formatter.
 */
function formatFreshness(fetchedAt: string | undefined): string | null {
  if (!fetchedAt) return null;
  const ts = Date.parse(fetchedAt);
  if (!Number.isFinite(ts)) return null;
  const deltaMs = Date.now() - ts;
  if (deltaMs < 0) return null;
  const minutes = Math.floor(deltaMs / 60_000);
  if (minutes < 1)  return "ahora";
  if (minutes < 60) return `hace ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48)   return `hace ${hours} h`;
  const days = Math.floor(hours / 24);
  if (days < 14)    return `hace ${days} d`;
  const weeks = Math.floor(days / 7);
  return `hace ${weeks} sem`;
}


export function CitationCard({ citation }: Props) {
  const freshness = useMemo(() => {
    return formatFreshness(
      typeof citation.fetched_at === "string" ? citation.fetched_at : undefined,
    );
  }, [citation.fetched_at]);

  const source  = typeof citation.source === "string" ? citation.source : "fuente";
  const snippet = typeof citation.snippet === "string" ? citation.snippet : undefined;
  const href    = typeof citation.href === "string" ? citation.href : undefined;

  const inner = (
    <span className="inline-flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
      <span aria-hidden>📊</span>
      <span className="font-medium text-foreground">{source}</span>
      {freshness ? (
        <>
          <span aria-hidden>·</span>
          <span>{freshness}</span>
        </>
      ) : null}
    </span>
  );

  if (href) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        title={snippet}
        className="inline-flex w-full items-center rounded-md border bg-background/60 px-2.5 py-1.5 transition-colors hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {inner}
      </a>
    );
  }

  return (
    <span
      title={snippet}
      className="inline-flex w-full items-center rounded-md border bg-background/60 px-2.5 py-1.5"
    >
      {inner}
    </span>
  );
}
