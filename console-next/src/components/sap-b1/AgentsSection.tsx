"use client";

import { useState } from "react";

import { AgentsOpsPanel } from "@/components/control-room/AgentsOpsPanel";
import { useWisdomBitMonitors } from "@/lib/control-room/use-wisdom-bit-monitors";
import { SAP_B1_CARTRIDGE } from "@/lib/sap-b1/client";
import { useSapB1View } from "@/lib/sap-b1/hooks";
import { formatCount, metricReason, metricState } from "@/lib/sap-b1/present";

import { errorText, LoadingBlock, MetricStatePill, Notice, Panel, QueryError, RefreshButton, TableShell, TD, TH } from "./ui";

export const SAP_B1_WISDOM_BIT_PREFIX = "WB-B1-";

export const SAP_B1_MONITOR_STAGES: Record<string, string> = {
  "WB-B1-MARGEN": "Detección",
  "WB-B1-CADUCIDAD": "Detección + Opciones",
  "WB-B1-ABASTO": "Recálculo + Opciones",
  "WB-B1-APRENDIZAJE": "Aprendizaje",
  "WB-B1-SEMAFORO": "Semáforo de las 8:00",
};

const EXPECTED = Object.keys(SAP_B1_MONITOR_STAGES);

function LearningPanel() {
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
          consultan en <a className="font-medium text-primary underline-offset-2 hover:underline" href="/decisions">Decisiones</a>.
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

export function AgentsSection({ canReadAgents }: { canReadAgents: boolean | null }) {
  const [collapsed, setCollapsed] = useState(false);
  const { agents, monitors, runsByAgent, ops } = useWisdomBitMonitors(SAP_B1_CARTRIDGE, SAP_B1_WISDOM_BIT_PREFIX, canReadAgents === true);

  return (
    <div className="space-y-4">
      <Panel
        eyebrow="Agentes"
        title="Los cinco agentes del plan"
        description="Corren cada mañana (hora de la Ciudad de México) sobre los indicadores; solo recomiendan, nada se escribe en Business One."
        actions={
          <RefreshButton
            busy={agents.isFetching || ops.isFetching}
            onClick={() => {
              void agents.refetch();
              void ops.refetch();
            }}
          />
        }
      >
        {canReadAgents === null ? (
          <LoadingBlock label="Revisando permisos…" />
        ) : !canReadAgents ? (
          <Notice tone="warning" title="Sin permiso para ver agentes">Tu rol no incluye la lectura de agentes.</Notice>
        ) : (
          <div className="overflow-hidden rounded-lg border dark:border-sky-400/15">
            <AgentsOpsPanel
              payload={ops.data ?? null}
              loading={ops.isPending}
              error={ops.isError ? errorText(ops.error) : ""}
              collapsed={collapsed}
              onToggle={() => setCollapsed((value) => !value)}
              scope={{
                prefix: SAP_B1_WISDOM_BIT_PREFIX,
                monitors,
                runs: runsByAgent,
                expected: EXPECTED,
                stages: SAP_B1_MONITOR_STAGES,
                loading: agents.isPending,
                error: agents.isError ? errorText(agents.error) : undefined,
              }}
            />
          </div>
        )}
      </Panel>
      <LearningPanel />
    </div>
  );
}
