import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

interface KpiCardProps {
  label:        string;
  value:        ReactNode;
  numericValue?: number;
  hint?:        ReactNode;
  trend?:       "up" | "down" | "flat";
  loading?:     boolean;
}

export function KpiCard({
  label,
  value,
  numericValue,
  hint,
  trend,
  loading,
}: KpiCardProps) {
  const numericLabel =
    typeof numericValue === "number" && Number.isFinite(numericValue)
      ? String(numericValue)
      : "";

  return (
    <div
      className="flex flex-col gap-2 rounded-lg border bg-card p-5 shadow-sm"
      data-testid="kpi-card"
      data-label={label}
    >
      <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
        {numericLabel ? `${label} ${numericLabel}` : label}
      </span>
      {loading ? (
        <span className="h-8 w-24 animate-pulse rounded bg-muted" aria-hidden />
      ) : (
        <span
          className="text-3xl font-semibold tracking-tight"
          data-testid="kpi-card-value"
          {...(typeof numericValue === "number" && Number.isFinite(numericValue)
            ? { "data-numeric-value": String(numericValue) }
            : {})}
        >
          {value}
        </span>
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
