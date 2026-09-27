import { ChevronDown } from "lucide-react";
import { useId, useState } from "react";

import type { ExperienceExceptionV2 } from "@/lib/control-room/experience-contract";
import { formatObservedAt } from "@/lib/control-room/experience-presenter";
import type { OpenExperienceAction } from "@/lib/control-room/use-control-room-experience-action";
import { cn } from "@/lib/utils";

export function ExperienceExceptions({
  exceptions,
  onAction,
}: {
  exceptions: readonly ExperienceExceptionV2[];
  onAction: OpenExperienceAction;
}) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  if (exceptions.length === 0) return null;

  return (
    <section aria-label="Excepciones aprobadas" className="border-t py-7">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex min-h-10 items-center gap-2 text-lg font-semibold text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ChevronDown
          aria-hidden
          className={cn("h-5 w-5 transition-transform", !open && "-rotate-90")}
        />
        {`Excepciones aprobadas (${exceptions.length})`}
      </button>
      {open ? (
        <ul id={panelId} className="mt-4 space-y-3">
          {exceptions.map((exception, index) => (
            <li key={index} className="rounded-md border bg-card p-4 text-sm">
              <p className="break-words font-semibold text-card-foreground">
                {exception.title}
              </p>
              {exception.entity_label ? (
                <p className="break-words text-muted-foreground">{exception.entity_label}</p>
              ) : null}
              <dl className="mt-3 space-y-1 text-xs text-muted-foreground">
                <div className="flex flex-wrap gap-x-1">
                  <dt>Motivo:</dt>
                  <dd className="break-words text-card-foreground">
                    {exception.reason ?? "Sin información"}
                  </dd>
                </div>
                <div className="flex flex-wrap gap-x-1">
                  <dt>Aprobada:</dt>
                  <dd>
                    {exception.approved_at ? (
                      <time dateTime={exception.approved_at}>
                        {formatObservedAt(exception.approved_at)}
                      </time>
                    ) : (
                      "Sin información"
                    )}
                    {exception.approved_by_you ? " · por ti" : null}
                  </dd>
                </div>
                <div className="flex flex-wrap gap-x-1">
                  <dt>Dato del:</dt>
                  <dd>
                    <time dateTime={exception.observed_at}>
                      {formatObservedAt(exception.observed_at)}
                    </time>
                  </dd>
                </div>
              </dl>
              {exception.actions.map((action) => (
                <button
                  key={action.action_handle}
                  type="button"
                  disabled={!action.enabled}
                  aria-label={`${action.label} — ${exception.title}`}
                  onClick={
                    action.enabled
                      ? (event) => onAction(exception, action, event.currentTarget)
                      : undefined
                  }
                  className="mt-3 inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-4 text-sm font-medium text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {action.label}
                </button>
              ))}
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
