"use client";

import { CheckCircle2, CircleHelp, Clock3 } from "lucide-react";

import { useSapB1Overview } from "@/lib/sap-b1/hooks";
import { deliveryLabel, formatCount, setupChecklist, TRANSPORT_LABELS } from "@/lib/sap-b1/present";
import { cn } from "@/lib/utils";

import { CheckPill, Fact, LoadingBlock, Notice, Panel, QueryError, RefreshButton } from "./ui";

export function SetupSection() {
  const overview = useSapB1Overview();
  const items = overview.data ? setupChecklist(overview.data) : [];
  const done = items.filter((item) => item.state === "ok").length;
  const last = overview.data?.digest?.last;

  return (
    <Panel
      eyebrow="Ensayo"
      title="Puesta en marcha"
      description="Lo que debe estar listo antes del primer semáforo de las 8:00. Cada punto se lee del sistema, no se captura a mano."
      actions={<RefreshButton onClick={() => void overview.refetch()} busy={overview.isFetching} />}
    >
      {overview.isPending ? <LoadingBlock label="Revisando la instalación…" /> : null}
      {overview.isError ? <QueryError error={overview.error} onRetry={() => void overview.refetch()} /> : null}
      {overview.data ? (
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            <strong className="text-foreground dark:text-white">{done} de {items.length}</strong> puntos listos.
          </p>
          <ul className="grid gap-2 md:grid-cols-2" aria-label="Lista de puesta en marcha">
            {items.map((item) => {
              const Icon = item.state === "ok" ? CheckCircle2 : item.state === "pendiente" ? Clock3 : CircleHelp;
              return (
                <li key={item.id} className="flex items-start gap-3 rounded-lg border bg-background p-3 dark:border-sky-400/15 dark:bg-[#06111f]">
                  <Icon
                    aria-hidden
                    className={cn(
                      "mt-0.5 h-5 w-5 shrink-0",
                      item.state === "ok" && "text-emerald-600 dark:text-emerald-400",
                      item.state === "pendiente" && "text-amber-600 dark:text-amber-400",
                      item.state === "desconocido" && "text-slate-500",
                    )}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="text-sm font-semibold text-foreground dark:text-white">{item.label}</span>
                      <CheckPill state={item.state} />
                    </div>
                    <p className="mt-1 break-words text-xs text-muted-foreground">{item.detail}</p>
                  </div>
                </li>
              );
            })}
          </ul>
          <dl className="grid gap-3 rounded-lg border p-3 sm:grid-cols-3 dark:border-sky-400/15">
            <Fact label="Transporte de correo" value={TRANSPORT_LABELS[overview.data.digest?.transport] ?? overview.data.digest?.transport ?? "N/D"} />
            <Fact label="Destinatarios del semáforo" value={formatCount(overview.data.digest?.recipients ?? 0)} />
            <Fact
              label="Último envío"
              value={last ? `${last.local_date} · ${deliveryLabel(last.status)} · ${formatCount(last.delivered)} de ${formatCount(last.recipients)} entregados` : "Sin envíos todavía"}
            />
          </dl>
          {overview.data.digest?.transport === "sin_configurar" ? (
            <Notice tone="warning" title="Transporte de correo sin configurar">
              El semáforo se calcula, pero el correo de las 8:00 no saldrá hasta configurar SES o SMTP.
            </Notice>
          ) : null}
        </div>
      ) : null}
    </Panel>
  );
}
