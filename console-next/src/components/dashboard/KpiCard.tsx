import { cn } from "@/lib/utils";
import type { ReactNode } from "react";

interface KpiCardProps {
  label:        string;
  value:        ReactNode;
  /**
   * v1.44.3.3 R-Mac Mini-fix: when the KPI surfaces a numeric
   * count (vs. a composite like "5 / 10"), pass the raw number
   * here. It surfaces as a ``data-numeric-value`` attribute on
   * the value <span> so E2E tests can extract a guaranteed-
   * numeric token without parsing display formatting. Tests
   * looking for "card renders a number" assert on this
   * attribute being set; visual display still uses ``value``.
   */
  numericValue?: number;
  hint?:        ReactNode;
  trend?:       "up" | "down" | "flat";
  loading?:     boolean;
}

/**
 * Single KPI tile. Loading state is a skeleton stripe; the static
 * dashboard layout reserves the space so the page doesn't shift
 * when the data arrives.
 */
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
          // v1.44.3.3 R-Mac Mini-fix: expose the raw number for
          // E2E tests. Only set when defined + finite — otherwise
          // the attribute is absent and tests can fall back to
          // the visible text.
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
