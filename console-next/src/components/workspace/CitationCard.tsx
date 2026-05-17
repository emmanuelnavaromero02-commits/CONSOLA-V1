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
 * reply ("📊 Replicon · time_entries · hace 4h").
 *
 * Security (Round 1 review P0): backend-supplied ``href`` is
 * ULTIMATELY TOOL-DERIVED. A compromised or malicious tool
 * result could emit ``href: "javascript:alert(1)"`` and the
 * browser would execute that script in the console origin on
 * click. ``rel="noopener noreferrer"`` does NOT block
 * ``javascript:`` / ``data:`` schemes. ``safeHref`` allow-lists
 * only ``http://``, ``https://`` and same-origin path
 * references; anything else falls back to a non-clickable
 * label with the snippet shown as a tooltip.
 */
function safeHref(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  if (!trimmed) return undefined;

  // Same-origin path reference (e.g. "/cartridges/replicon").
  if (trimmed.startsWith("/") && !trimmed.startsWith("//")) {
    return trimmed;
  }

  // External absolute URL — only http(s) allowed.
  try {
    const url = new URL(trimmed);
    if (url.protocol === "http:" || url.protocol === "https:") {
      return url.toString();
    }
  } catch {
    /* not a valid absolute URL — fall through */
  }

  return undefined;
}


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
  const href    = safeHref(citation.href);

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
