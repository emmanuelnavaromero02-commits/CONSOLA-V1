import { useId } from "react";
import { CalendarDays, CheckCircle2, Clock3 } from "lucide-react";

import type { ExperienceFactV2 } from "@/lib/control-room/experience-contract";
import type { OpenExperiencePreview } from "@/lib/control-room/use-control-room-experience-preview";
import {
  decisionLabel,
  formatMetric,
  formatObservedAt,
} from "@/lib/control-room/experience-presenter";
import { cn } from "@/lib/utils";

const severityStyle = {
  critical: "border-destructive/30 bg-destructive/10 text-destructive",
  high: "border-warning/40 bg-warning/10 text-warning",
  medium: "border-primary/25 bg-primary/10 text-primary",
  low: "border-success/30 bg-success/10 text-success",
} as const;

const severityLabel = {
  critical: "Crítica",
  high: "Alta",
  medium: "Media",
  low: "Baja",
} as const;

export function ExperienceFact({
  fact,
  onPreviewAction,
}: {
  fact: ExperienceFactV2;
  onPreviewAction: OpenExperiencePreview;
}) {
  const reasonId = useId();
  return (
    <article className="flex min-h-56 min-w-0 flex-col rounded-md border bg-card p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p
            className={cn(
              "mb-3 inline-flex rounded border px-2 py-1 text-xs font-semibold",
              severityStyle[fact.severity],
            )}
          >
            {severityLabel[fact.severity]}
          </p>
          <h3 className="break-words text-base font-semibold leading-6 text-card-foreground">
            {fact.title}
          </h3>
          {fact.entity_label ? (
            <p className="mt-1 break-words text-sm text-muted-foreground">
              {fact.entity_label}
            </p>
          ) : null}
        </div>
        {fact.stale ? (
          <span className="inline-flex shrink-0 items-center gap-1.5 text-xs font-medium text-warning">
            <Clock3 aria-hidden className="h-4 w-4" />
            Información anterior
          </span>
        ) : null}
      </div>

      {fact.metric ? (
        <div className="mt-5">
          <p className="break-words text-xs font-medium text-muted-foreground">
            {fact.metric.name}
          </p>
          <p className="mt-1 break-words text-3xl font-semibold text-card-foreground">
            {formatMetric(fact.metric)}
          </p>
        </div>
      ) : null}

      {fact.actions.length > 0 ? (
        <div className="mt-5 space-y-3 border-t pt-4">
          {fact.actions.map((action, index) => {
            const disabledReasonId = `${reasonId}-${index}`;
            return (
              <div key={index} className="space-y-2">
                <p className="break-words text-sm font-medium text-card-foreground">
                  {action.label}
                </p>
                <button
                  type="button"
                  disabled={!action.enabled}
                  aria-describedby={
                    !action.enabled && action.disabled_reason
                      ? disabledReasonId
                      : undefined
                  }
                  onClick={
                    action.enabled
                      ? (event) =>
                          onPreviewAction(fact, action, event.currentTarget)
                      : undefined
                  }
                  className="inline-flex min-h-[44px] w-full items-center justify-center rounded-md border bg-background px-4 text-sm font-medium text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
                >
                  Generar preview
                </button>
                {!action.enabled && action.disabled_reason ? (
                  <p
                    id={disabledReasonId}
                    className="break-words text-xs text-muted-foreground"
                  >
                    {action.disabled_reason}
                  </p>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}

      <div className="mt-auto flex flex-wrap items-center gap-x-5 gap-y-2 pt-5 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <CalendarDays aria-hidden className="h-4 w-4" />
          <time dateTime={fact.observed_at}>{formatObservedAt(fact.observed_at)}</time>
        </span>
        {fact.decision ? (
          <span className="inline-flex items-center gap-1.5 font-medium text-success">
            <CheckCircle2 aria-hidden className="h-4 w-4" />
            {decisionLabel(fact.decision)}
          </span>
        ) : null}
      </div>
    </article>
  );
}
