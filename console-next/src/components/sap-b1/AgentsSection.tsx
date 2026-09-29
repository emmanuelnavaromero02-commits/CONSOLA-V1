"use client";

import { useMemo, useState } from "react";

import { AgentsOpsPanel } from "@/components/control-room/AgentsOpsPanel";
import { isApiError } from "@/lib/api";
import { useWisdomBitMonitors } from "@/lib/control-room/use-wisdom-bit-monitors";
import {
  AGENT_ALERT_STATE_LABELS,
  ALERT_SEVERITY_LABELS,
  sapB1AgentAlertRows,
  type AgentAlertRow,
  type AgentAlertState,
} from "@/lib/sap-b1/agent-alerts";
import { SAP_B1_CARTRIDGE } from "@/lib/sap-b1/client";
import { useMarkSapB1AlertFalsePositive, useRecordSapB1AlertDecision, useSapB1AgentAlerts } from "@/lib/sap-b1/hooks";
import { formatDateTime } from "@/lib/sap-b1/present";

import {
  ActionButton,
  errorText,
  LoadingBlock,
  Notice,
  Panel,
  Pill,
  QueryError,
  RefreshButton,
  TableShell,
  TD,
  TH,
  type PillTone,
} from "./ui";

export const SAP_B1_WISDOM_BIT_PREFIX = "WB-B1-";

export const SAP_B1_MONITOR_STAGES: Record<string, string> = {
  "WB-B1-MARGEN": "Detección",
  "WB-B1-CADUCIDAD": "Detección + Opciones",
  "WB-B1-ABASTO": "Recálculo + Opciones",
  "WB-B1-APRENDIZAJE": "Aprendizaje",
  "WB-B1-SEMAFORO": "Semáforo de las 8:00",
};

const EXPECTED = Object.keys(SAP_B1_MONITOR_STAGES);

const STATE_TONES: Record<AgentAlertState, PillTone> = {
  decision: "good",
  false_positive: "neutral",
  open: "warning",
  acknowledged: "neutral",
  assigned: "neutral",
  snoozed: "neutral",
};

function severityTone(severity: string | null): PillTone {
  if (severity === "critical" || severity === "high") return "danger";
  return severity === "medium" ? "warning" : "neutral";
}

function actionErrorText(error: unknown): string {
  if (isApiError(error) && error.status === 409) return "La alerta ya cambió de estado; actualiza la lista.";
  return errorText(error);
}

const LINK = "font-medium text-primary underline-offset-2 hover:underline";

function AgentAlertsPanel({ canWrite }: { canWrite: boolean }) {
  const { alerts, dashboard } = useSapB1AgentAlerts();
  const recordDecision = useRecordSapB1AlertDecision();
  const markFalsePositive = useMarkSapB1AlertFalsePositive();
  const [decided, setDecided] = useState<ReadonlySet<string>>(() => new Set());
  const [falsePositives, setFalsePositives] = useState<ReadonlySet<string>>(() => new Set());
  const [lastFalsePositive, setLastFalsePositive] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const rows = useMemo(
    () => sapB1AgentAlertRows(alerts.data?.alerts, dashboard.data?.items, { decided, falsePositives }),
    [alerts.data, dashboard.data, decided, falsePositives],
  );
  const stateKnown = !dashboard.isPending;

  const onRecordDecision = (row: AgentAlertRow) => {
    setActionError(null);
    recordDecision.mutate(row.itemId, {
      onSuccess: () => setDecided((current) => new Set(current).add(row.itemId)),
      onError: (error) => setActionError(`No se pudo registrar la decisión: ${actionErrorText(error)}`),
    });
  };

  const onMarkFalsePositive = (row: AgentAlertRow) => {
    const note = window.prompt(
      `¿Marcar «${row.title}» como falso positivo? Saldrá de las alertas activas y contará en el registro de aprendizaje. Motivo (opcional):`,
      "",
    );
    if (note === null) return;
    setActionError(null);
    markFalsePositive.mutate(
      { itemId: row.itemId, note: note.trim() || undefined },
      {
        onSuccess: () => {
          setFalsePositives((current) => new Set(current).add(row.itemId));
          setLastFalsePositive(row.title);
        },
        onError: (error) => setActionError(`No se pudo marcar como falso positivo: ${actionErrorText(error)}`),
      },
    );
  };

  return (
    <Panel
      eyebrow="Decisiones"
      title="Alertas de los agentes"
      description={
        <>
          Alertas activas de los agentes de SAP Business One, de la más reciente a la más antigua. Registra la decisión tomada sobre cada una
          o márcala como falso positivo: las dos cuentan en el registro de aprendizaje. El resultado (lograda o no lograda) se cierra en{" "}
          <a className={LINK} href="/control-room?fase=ejecuta">Decisiones</a>.
        </>
      }
      actions={
        <RefreshButton
          busy={alerts.isFetching || dashboard.isFetching}
          onClick={() => {
            void alerts.refetch();
            void dashboard.refetch();
          }}
        />
      }
    >
      <div className="space-y-3">
        {alerts.isPending ? <LoadingBlock label="Leyendo las alertas…" /> : null}
        {alerts.isError ? <QueryError error={alerts.error} onRetry={() => void alerts.refetch()} /> : null}
        {dashboard.isError ? (
          <Notice tone="warning" title="No se pudo confirmar qué alertas ya tienen decisión">
            {errorText(dashboard.error)} La columna Estado puede no mostrar decisiones ya registradas.
          </Notice>
        ) : null}
        {actionError ? <Notice tone="error" title="La acción no se completó">{actionError}</Notice> : null}
        {lastFalsePositive ? (
          <Notice tone="info" title="Falso positivo registrado">«{lastFalsePositive}» salió de las alertas activas.</Notice>
        ) : null}
        {alerts.data && !rows.length ? (
          <Notice tone="empty" title="Sin alertas de los agentes">
            Todavía no hay alertas activas de los agentes de SAP Business One. Cuando un agente detecte algo aparecerá aquí.
          </Notice>
        ) : null}
        {rows.length ? (
          <TableShell label="Alertas de los agentes">
            <thead>
              <tr>
                <th className={TH}>Alerta</th>
                <th className={TH}>Severidad</th>
                <th className={TH}>Detectada</th>
                <th className={TH}>Estado</th>
                {canWrite ? <th className={TH}>Acciones</th> : null}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => {
                const deciding = recordDecision.isPending && recordDecision.variables === row.itemId;
                const marking = markFalsePositive.isPending && markFalsePositive.variables?.itemId === row.itemId;
                return (
                  <tr key={row.itemId} data-item-id={row.itemId}>
                    <td className={`${TD} font-medium`}>{row.title}</td>
                    <td className={TD}>
                      {row.severity ? (
                        <Pill tone={severityTone(row.severity)}>{ALERT_SEVERITY_LABELS[row.severity] ?? row.severity}</Pill>
                      ) : (
                        "N/D"
                      )}
                    </td>
                    <td className={`${TD} whitespace-nowrap text-muted-foreground`}>{row.detectedAt ? formatDateTime(row.detectedAt) : "Sin fecha"}</td>
                    <td className={TD}>
                      <div className="flex flex-col items-start gap-1">
                        <Pill tone={STATE_TONES[row.state]}>{AGENT_ALERT_STATE_LABELS[row.state]}</Pill>
                        {row.hasDecision ? (
                          <a className={`${LINK} text-xs`} href="/control-room?fase=ejecuta">Ver en Decisiones</a>
                        ) : null}
                      </div>
                    </td>
                    {canWrite ? (
                      <td className={TD}>
                        <div className="flex flex-wrap gap-2">
                          <ActionButton
                            ariaLabel={`Registrar decisión: ${row.title}`}
                            disabled={!stateKnown || row.hasDecision || row.falsePositive || marking}
                            busy={deciding}
                            onClick={() => onRecordDecision(row)}
                          >
                            Registrar decisión
                          </ActionButton>
                          <ActionButton
                            ariaLabel={`Marcar falso positivo: ${row.title}`}
                            disabled={!stateKnown || row.falsePositive || deciding}
                            busy={marking}
                            onClick={() => onMarkFalsePositive(row)}
                          >
                            Marcar falso positivo
                          </ActionButton>
                        </div>
                      </td>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </TableShell>
        ) : null}
        {!canWrite && rows.length ? (
          <p className="text-xs text-muted-foreground">Registrar decisiones o falsos positivos requiere permiso de escritura en Control Room.</p>
        ) : null}
      </div>
    </Panel>
  );
}

export function AgentsSection({ canReadAgents, canWrite }: { canReadAgents: boolean | null; canWrite: boolean }) {
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
      <AgentAlertsPanel canWrite={canWrite} />
    </div>
  );
}
