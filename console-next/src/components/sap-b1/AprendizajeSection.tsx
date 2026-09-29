"use client";

import { useSapB1View } from "@/lib/sap-b1/hooks";
import { formatCount, metricReason, metricState } from "@/lib/sap-b1/present";

import { LoadingBlock, MetricStatePill, Notice, Panel, QueryError, RefreshButton, TableShell, TD, TH } from "./ui";

export function AprendizajeSection() {
  const view = useSapB1View("sap_b1_learning_kpis");
  const metric = view.data?.metrics?.aprendizaje;
  const sources = metric?.sources ?? [];
  const suggestions = metric?.suggestions ?? [];
  return (
    <Panel
      eyebrow="Aprendizaje"
      title="Decisiones y resultados"
      description={
        <>
          Cada alerta de estos agentes llega a{" "}
          <a className="font-medium text-primary underline-offset-2 hover:underline" href="/control-room">Control Room</a>, donde se ve si ya
          tiene una decisión registrada. Este registro cuenta, por agente, las alertas, las decisiones tomadas sobre ellas, los resultados
          medidos y los falsos positivos de los últimos 90 días: ahí quedan documentadas las 3 decisiones por caso. Las decisiones también se
          consultan en <a className="font-medium text-primary underline-offset-2 hover:underline" href="/control-room?fase=ejecuta">Decisiones</a>.
        </>
      }
      actions={
        <>
          {metric ? <MetricStatePill state={metricState(metric)} /> : null}
          <RefreshButton onClick={() => void view.refetch()} busy={view.isFetching} />
        </>
      }
    >
      {view.isPending ? <LoadingBlock label="Leyendo el registro de decisiones…" /> : null}
      {view.isError ? <QueryError error={view.error} onRetry={() => void view.refetch()} /> : null}
      {view.data && !sources.length ? (
        <Notice tone="empty" title="Sin decisiones todavía">{metricReason(metric) ?? "Todavía no hay alertas de SAP Business One en la ventana."}</Notice>
      ) : null}
      {sources.length ? (
        <div className="space-y-3">
          <TableShell label="Decisiones por agente">
            <thead>
              <tr>
                <th className={TH}>Agente</th>
                <th className={`${TH} text-right`}>Alertas</th>
                <th className={`${TH} text-right`}>Decisiones</th>
                <th className={`${TH} text-right`}>Resultados</th>
                <th className={`${TH} text-right`}>Falsos positivos</th>
                <th className={`${TH} text-right`}>Alcanzaron objetivo</th>
                <th className={`${TH} text-right`}>No lo alcanzaron</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((row) => (
                <tr key={row.source ?? "sin-fuente"}>
                  <td className={`${TD} font-mono text-xs`}>{row.source ?? "N/D"}</td>
                  <td className={`${TD} text-right tabular-nums`}>{formatCount(row.alerts)}</td>
                  <td className={`${TD} text-right tabular-nums`}>{formatCount(row.decisions)}</td>
                  <td className={`${TD} text-right tabular-nums`}>{formatCount(row.outcomes)}</td>
                  <td className={`${TD} text-right tabular-nums`}>{formatCount(row.false_positives)}</td>
                  <td className={`${TD} text-right tabular-nums`}>{formatCount(row.achieved)}</td>
                  <td className={`${TD} text-right tabular-nums`}>{formatCount(row.not_achieved)}</td>
                </tr>
              ))}
            </tbody>
          </TableShell>
          {metric?.window_days ? <p className="text-xs text-muted-foreground">Ventana: últimos {metric.window_days} días.</p> : null}
        </div>
      ) : null}
      {suggestions.length ? (
        <div className="mt-3 space-y-2">
          <h3 className="text-sm font-semibold text-foreground dark:text-white">Umbrales que conviene revisar</h3>
          <ul className="space-y-2">
            {suggestions.map((item, index) => (
              <li key={`${item.source}:${index}`} className="rounded-md border border-amber-500/30 bg-amber-500/5 p-2 text-sm">
                <span className="font-mono text-xs">{item.source}</span> · <span className="font-mono text-xs">{(item.thresholds ?? []).join(", ")}</span>
                <p className="mt-1 text-muted-foreground">{item.reason}</p>
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted-foreground">El agente solo propone; el umbral se cambia en Parámetros, por quien es dueño del caso.</p>
        </div>
      ) : null}
    </Panel>
  );
}
