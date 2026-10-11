"use client";

import { AlertTriangle, WifiOff } from "lucide-react";

import { formatObservedAt } from "@/lib/control-room/experience-presenter";
import { useNow } from "@/lib/time/use-now";
import { cn } from "@/lib/utils";

const HOUR_MS = 60 * 60_000;

export const SOURCE_STALE_AFTER_MS = 36 * HOUR_MS;

export function formatCheckedAgo(checkedAt: number, now: number): string {
  const seconds = Math.max(0, Math.floor((now - checkedAt) / 1_000));
  if (seconds < 1) return "hace un momento";
  if (seconds < 60) return `hace ${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `hace ${minutes} min`;
  return `hace ${Math.floor(minutes / 60)} h`;
}

export function formatSourceAge(sourceMs: number, now: number): string {
  const hours = Math.max(0, Math.floor((now - sourceMs) / HOUR_MS));
  if (hours < 48) return `Datos de hace ${hours} h`;
  return `Datos de hace ${Math.floor(hours / 24)} días`;
}

export function ExperienceLiveBadge({
  checkedAt,
  offline,
  sourceObservedAt,
}: {
  checkedAt: number | null;
  offline: boolean;
  sourceObservedAt: string | null;
}) {
  const now = useNow(1_000);
  const sourceMs = sourceObservedAt ? Date.parse(sourceObservedAt) : Number.NaN;
  const sourceKnown = !Number.isNaN(sourceMs);
  const sourceStale = sourceKnown && now - sourceMs > SOURCE_STALE_AFTER_MS;

  return (
    <div className="mt-2 space-y-1 text-xs" data-testid="control-room-live-badge">
      {offline ? (
        <p role="status" className="inline-flex items-center gap-1.5 font-medium text-warning">
          <WifiOff aria-hidden className="h-3.5 w-3.5" />
          Sin conexión en vivo · mostrando última información
        </p>
      ) : checkedAt !== null ? (
        <p className="inline-flex items-center gap-1.5 text-muted-foreground">
          <span
            aria-hidden
            className={cn("h-2 w-2 rounded-full", sourceStale ? "bg-warning" : "bg-success")}
          />
          <span>
            En vivo · consultado{" "}
            <time dateTime={new Date(checkedAt).toISOString()}>
              {formatCheckedAgo(checkedAt, now)}
            </time>
          </span>
        </p>
      ) : null}
      {sourceStale ? (
        <p
          data-testid="control-room-source-stale"
          className="flex w-fit items-center gap-1.5 rounded-md border border-warning/40 bg-warning/10 px-2 py-0.5 font-semibold text-warning"
        >
          <AlertTriangle aria-hidden className="h-3.5 w-3.5" />
          {formatSourceAge(sourceMs, now)}
        </p>
      ) : null}
      {sourceObservedAt && sourceKnown ? (
        <p className={cn(sourceStale ? "font-medium text-warning" : "text-muted-foreground")}>
          Datos del origen al{" "}
          <time dateTime={sourceObservedAt}>{formatObservedAt(sourceObservedAt)}</time>
        </p>
      ) : null}
    </div>
  );
}
