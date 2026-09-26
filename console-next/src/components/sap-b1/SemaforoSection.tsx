"use client";

import { useSapB1Overview, useSapB1View } from "@/lib/sap-b1/hooks";
import {
  deliveryLabel,
  formatCount,
  formatDateTime,
  semaforoAreas,
  semaforoHeadline,
  TRANSPORT_LABELS,
} from "@/lib/sap-b1/present";
import type { KpiMetric } from "@/lib/sap-b1/types";
import { cn } from "@/lib/utils";

import { AREA_DOT, AreaPill, Fact, LoadingBlock, Notice, Panel, QueryError, RefreshButton } from "./ui";

const AREA_BORDER = {
  rojo: "border-l-red-500 dark:border-l-red-500",
  amarillo: "border-l-amber-400 dark:border-l-amber-400",
  verde: "border-l-emerald-500 dark:border-l-emerald-500",
  sin_datos: "border-l-slate-400 dark:border-l-slate-400",
} as const;

export function SemaforoSection() {
  const view = useSapB1View("sap_b1_semaforo_kpis");
  const overview = useSapB1Overview();
  const areas = semaforoAreas(view.data?.metrics as Record<string, KpiMetric | undefined> | undefined);
  const digest = overview.data?.digest;

  return (
    <Panel
      eyebrow="Semáforo del día"
      title={view.data ? `Semáforo SAP Business One: ${semaforoHeadline(areas)}` : "Semáforo SAP Business One"}
      description="Lo mismo que llega por correo a las 8:00: cada área con su color y sus hallazgos, lo rojo primero. Un área sin datos nunca se pinta de verde."
      actions={<RefreshButton onClick={() => void view.refetch()} busy={view.isFetching} />}
    >
      <div className="space-y-4">
        <dl className="grid gap-3 rounded-lg border p-3 sm:grid-cols-4 dark:border-sky-400/15">
          <Fact label="Calculado" value={formatDateTime(view.data?.generated_at)} />
          <Fact label="Transporte de correo" value={digest ? TRANSPORT_LABELS[digest.transport] ?? digest.transport : overview.isError ? "No disponible" : "…"} />
          <Fact label="Destinatarios" value={digest ? formatCount(digest.recipients) : overview.isError ? "No disponible" : "…"} />
          <Fact
            label="Último correo"
            value={digest?.last ? `${digest.last.local_date} · ${deliveryLabel(digest.last.status)}` : digest ? "Sin envíos todavía" : "…"}
          />
        </dl>
        {digest?.transport === "sin_configurar" ? (
          <Notice tone="warning" title="Transporte de correo sin configurar">El semáforo no se enviará por correo hasta configurar SES o SMTP.</Notice>
        ) : null}
        {digest && digest.recipients === 0 ? (
          <Notice tone="warning" title="Sin destinatarios">Agrega destinatarios en Parámetros para que el correo de las 8:00 salga.</Notice>
        ) : null}
        {view.isPending ? <LoadingBlock label="Calculando el semáforo…" /> : null}
        {view.isError ? <QueryError error={view.error} onRetry={() => void view.refetch()} /> : null}
        {view.data && !areas.length ? <Notice tone="empty" title="Sin datos todavía">El semáforo aún no tiene áreas calculadas.</Notice> : null}
        {areas.length ? (
          <ul className="grid gap-3 lg:grid-cols-2" aria-label="Áreas del semáforo">
            {areas.map((area) => (
              <li
                key={area.metric}
                className={cn("rounded-lg border border-l-4 bg-background p-3 dark:border-sky-400/15 dark:bg-[#06111f]", AREA_BORDER[area.color])}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <h3 className="flex items-center gap-2 text-sm font-semibold text-foreground dark:text-white">
                    <span aria-hidden className={cn("h-2.5 w-2.5 rounded-full", AREA_DOT[area.color])} />
                    {area.label}
                  </h3>
                  <AreaPill color={area.color} />
                </div>
                {area.period ? <p className="mt-1 text-xs text-muted-foreground">Periodo: {area.period}</p> : null}
                {area.findings.length ? (
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-sm text-foreground dark:text-slate-200">
                    {area.findings.map((finding, index) => <li key={`${index}:${finding}`}>{finding}</li>)}
                    {area.findingsTotal > area.findings.length ? (
                      <li className="text-muted-foreground">y {area.findingsTotal - area.findings.length} más en los indicadores</li>
                    ) : null}
                  </ul>
                ) : area.reason ? (
                  <p className="mt-2 text-sm text-muted-foreground">{area.reason}</p>
                ) : (
                  <p className="mt-2 text-sm text-muted-foreground">Sin hallazgos.</p>
                )}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </Panel>
  );
}
