import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

interface KpiCardProps {
  label:    string;
  value:    ReactNode;
  hint?:    ReactNode;
  trend?:   "up" | "down" | "flat";
  loading?: boolean;
}

/**
 * Single KPI tile. Loading state is a skeleton stripe; the static
 * dashboard layout reserves the space so the page doesn't shift
 * when the data arrives.
 */
export function KpiCard({ label, value, hint, trend, loading }: KpiCardProps) {
  return (
    <div className="flex flex-col gap-2 rounded-lg border bg-card p-5 shadow-sm">
      <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      {loading ? (
        <span className="h-8 w-24 animate-pulse rounded bg-muted" aria-hidden />
      ) : (
        <span className="text-3xl font-semibold tracking-tight">{value}</span>
      )}
      {hint ? (
        <span
          className={cn(
            "text-xs",
            trend === "up"   && "text-success",
            trend === "down" && "text-destructive",
            trend === "flat" && "text-muted-foreground",
            !trend && "text-muted-foreground",
          )}
        >
          {hint}
        </span>
      ) : null}
    </div>
  );
}
