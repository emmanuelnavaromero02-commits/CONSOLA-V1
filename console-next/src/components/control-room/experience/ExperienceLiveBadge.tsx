"use client";

import { WifiOff } from "lucide-react";

import { formatObservedAt } from "@/lib/control-room/experience-presenter";
import { useNow } from "@/lib/time/use-now";
import { cn } from "@/lib/utils";

export const SOURCE_AGE_WARNING_MS = 30 * 24 * 60 * 60_000;

export function formatCheckedAgo(checkedAt: number, now: number): string {
  const seconds = Math.max(0, Math.floor((now - checkedAt) / 1_000));
  if (seconds < 1) return "hace un momento";
  if (seconds < 60) return `hace ${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `hace ${minutes} min`;
  return `hace ${Math.floor(minutes / 60)} h`;
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
  const sourceOld = !Number.isNaN(sourceMs) && now - sourceMs > SOURCE_AGE_WARNING_MS;

  return (
    <div className="mt-2 space-y-1 text-xs" data-testid="control-room-live-badge">
      {offline ? (
        <p role="status" className="inline-flex items-center gap-1.5 font-medium text-warning">
          <WifiOff aria-hidden className="h-3.5 w-3.5" />
          Sin conexión en vivo · mostrando última información
        </p>
      ) : checkedAt !== null ? (
        <p className="inline-flex items-center gap-1.5 text-muted-foreground">
          <span aria-hidden className="h-2 w-2 rounded-full bg-success" />
          <span>
            En vivo · consultado{" "}
            <time dateTime={new Date(checkedAt).toISOString()}>
              {formatCheckedAgo(checkedAt, now)}
            </time>
          </span>
        </p>
      ) : null}
      {sourceObservedAt && !Number.isNaN(sourceMs) ? (
        <p className={cn(sourceOld ? "font-medium text-warning" : "text-muted-foreground")}>
          Datos del origen al{" "}
          <time dateTime={sourceObservedAt}>{formatObservedAt(sourceObservedAt)}</time>
        </p>
      ) : null}
    </div>
  );
}
