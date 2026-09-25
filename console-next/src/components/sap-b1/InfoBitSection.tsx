"use client";

import { Download } from "lucide-react";
import { useMemo } from "react";

import { MiniBar } from "@/components/control-room/StatusBadge";
import { downloadText, mappingCsv, mergeMapping } from "@/lib/sap-b1/csv";
import { useSapB1LoadReconciliation, useSapB1Mapping, useSapB1Overview } from "@/lib/sap-b1/hooks";
import {
  formatAge,
  formatCount,
  formatDateTime,
  formatPct,
  HEARTBEAT_LIMIT_SECONDS,
} from "@/lib/sap-b1/present";
import type { SapB1ConnectorState, SapB1LoadRow } from "@/lib/sap-b1/types";

import { ActionButton, Fact, LoadingBlock, Notice, Panel, Pill, type PillTone, QueryError, RefreshButton, TableShell, TD, TH } from "./ui";

const LOAD_STATUS: Record<string, { label: string; tone: PillTone }> = {
  ok: { label: "completo", tone: "good" },
  faltan: { label: "faltan filas", tone: "warning" },
  sobran: { label: "sobran filas", tone: "danger" },
  sin_conteo: { label: "sin conteo", tone: "neutral" },
};

const MODE_LABELS: Record<string, string> = { full: "completa", incremental: "incremental" };

function loadStatus(status?: string | null) {
  return LOAD_STATUS[status ?? "sin_conteo"] ?? { label: status ?? "sin conteo", tone: "neutral" as PillTone };
}

function cycleLabel(status?: string | null): string {
  if (status === "success") return "exitoso";
  if (status === "failed") return "con errores";
  return status ?? "N/D";
}

function ConnectorCard({ connector }: { connector: SapB1ConnectorState }) {
  const age = connector.age_seconds;
  const online = connector.present && typeof age === "number" && age < HEARTBEAT_LIMIT_SECONDS;
  const initial = connector.initial_load;
  const cycle = connector.last_cycle;
  return (
    <div className="rounded-lg border bg-background p-4 dark:border-sky-400/15 dark:bg-[#06111f]">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-foreground dark:text-white">Conector de Business One</h3>
        <Pill tone={online ? "good" : connector.present ? "warning" : "neutral"}>
          {online ? "en línea" : connector.present ? "sin heartbeat reciente" : "sin heartbeat"}
        </Pill>
      </div>
      {!connector.present ? (
        <Notice tone="empty" title="El conector todavía no reporta">
          {connector.error ? `Respuesta del cartucho: ${connector.error}` : "No hay heartbeat del agente en este workspace."}
        </Notice>
      ) : (
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Fact label="Último heartbeat" value={connector.readable === false ? "ilegible" : `${formatAge(age)} · ${formatDateTime(connector.at)}`} />
          <Fact
            label="Acceso a Business One"
            value={connector.source_ok == null ? "N/D" : connector.source_ok ? `ok · ${formatCount(connector.source_ms)} ms` : "sin acceso"}
          />
          <Fact label="Motor" value={connector.dialect ? connector.dialect.toUpperCase() : "N/D"} />
          <Fact label="Empresas" value={connector.companies?.length ? connector.companies.join(", ") : "N/D"} />
          <Fact
            label="Último ciclo"
            value={cycle ? `${cycleLabel(cycle.status)} · ${formatDateTime(cycle.finished_at ?? cycle.started_at)} · ${formatCount(cycle.entities_ok)} tablas ok, ${formatCount(cycle.entities_failed)} con error` : "Sin ciclos todavía"}
          />
          <Fact label="Próximo ciclo" value={formatDateTime(connector.next_cycle_at)} />
          <Fact
            label="Carga inicial"
            value={!initial
              ? "N/D"
              : initial.state === "done"
                ? `completa · ${formatCount(initial.months_total)} meses`
                : initial.state === "running"
                  ? `en curso · ${formatCount(initial.months_done)} de ${formatCount(initial.months_total)} meses`
                  : "sin iniciar"}
          />
          <Fact label="Versión del agente" value={connector.agent_version ?? "N/D"} />
          {connector.source_error ? (
            <Fact className="sm:col-span-2 lg:col-span-4" label="Error de la fuente" value={connector.source_error} />
          ) : null}
        </dl>
      )}
    </div>
  );
}

interface CompanyProgress {
  company: string;
  tables: number;
  ok: number;
  pending: number;
  uncounted: number;
  source: number;
  loaded: number;
  countedAt: string | null;
}

function companyProgress(rows: SapB1LoadRow[]): CompanyProgress[] {
  const byCompany = new Map<string, CompanyProgress>();
  for (const row of rows) {
    const item = byCompany.get(row.company) ?? { company: row.company, tables: 0, ok: 0, pending: 0, uncounted: 0, source: 0, loaded: 0, countedAt: null };
    item.tables += 1;
    if (row.status === "ok") item.ok += 1;
    else if (row.status === "sin_conteo") item.uncounted += 1;
    else item.pending += 1;
    const source = typeof row.source_rows === "number" ? row.source_rows : 0;
    const platform = typeof row.platform_rows === "number" ? row.platform_rows : 0;
    item.source += source;
    item.loaded += Math.min(source, platform);
    if (row.counted_at && (!item.countedAt || row.counted_at > item.countedAt)) item.countedAt = row.counted_at;
    byCompany.set(row.company, item);
  }
  return [...byCompany.values()].sort((a, b) => a.company.localeCompare(b.company));
}

export function InfoBitSection() {
  const overview = useSapB1Overview();
  const mapping = useSapB1Mapping();
  const loads = useSapB1LoadReconciliation();
  const rows = useMemo(() => mergeMapping(mapping.data?.entities ?? [], loads.data), [mapping.data, loads.data]);
  const progress = useMemo(() => companyProgress(loads.data ?? []), [loads.data]);

  const exportCsv = () => downloadText(`sap_b1_mapeo_${new Date().toISOString().slice(0, 10)}.csv`, mappingCsv(rows));

  return (
    <div className="space-y-4">
      <Panel
        eyebrow="InfoBit"
        title="Conexión y cargas"
        description="El agente dentro de la red del cliente toma los datos de Business One y los sube; aquí se ve si está vivo y qué tanto de cada tabla ya llegó."
        actions={
          <RefreshButton
            busy={overview.isFetching || loads.isFetching}
            onClick={() => {
              void overview.refetch();
              void loads.refetch();
            }}
          />
        }
      >
        <div className="space-y-4">
          {overview.isPending ? <LoadingBlock label="Leyendo el heartbeat…" /> : null}
          {overview.isError ? <QueryError error={overview.error} onRetry={() => void overview.refetch()} /> : null}
          {overview.data ? <ConnectorCard connector={overview.data.connector ?? { present: false }} /> : null}

          <div>
            <h3 className="mb-2 text-sm font-semibold text-foreground dark:text-white">Avance de la carga por empresa</h3>
            {loads.isPending ? <LoadingBlock label="Leyendo la reconciliación de cargas…" /> : null}
            {loads.isError ? <QueryError error={loads.error} onRetry={() => void loads.refetch()} /> : null}
            {loads.data === null ? (
              <Notice tone="empty" title="Sin datos todavía">La reconciliación de cargas aún no se publica para este workspace.</Notice>
            ) : null}
            {loads.data && loads.data.length === 0 ? (
              <Notice tone="empty" title="Sin conteos todavía">El agente no ha enviado conteos de las tablas en origen.</Notice>
            ) : null}
            {progress.length ? (
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                {progress.map((item) => (
                  <div key={item.company} className="rounded-lg border bg-background p-3 dark:border-sky-400/15 dark:bg-[#06111f]">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <span className="font-mono text-sm font-semibold text-foreground dark:text-white">{item.company}</span>
                      <span className="text-xs text-muted-foreground">{item.source ? formatPct((100 * item.loaded) / item.source) : "N/D"} cargado</span>
                    </div>
                    <MiniBar value={item.ok} max={item.tables} label="Tablas completas" tone={item.ok === item.tables ? "good" : "warning"} />
                    <p className="mt-2 text-xs text-muted-foreground">
                      {formatCount(item.loaded)} de {formatCount(item.source)} filas · {item.pending} con diferencia · {item.uncounted} sin conteo
                    </p>
                    <p className="text-xs text-muted-foreground">Conteo: {formatDateTime(item.countedAt)}</p>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </div>
      </Panel>

      <Panel
        eyebrow="InfoBit"
        title="Mapeo de tablas"
        description="Qué tabla de Business One se toma, cómo se extrae, qué campos y qué datasets la usan, contra lo que ya está en la plataforma. Es el documento que se firma."
        actions={
          <ActionButton onClick={exportCsv} disabled={!rows.length}>
            <Download aria-hidden className="h-4 w-4" />
            Descargar CSV
          </ActionButton>
        }
      >
        {mapping.isPending ? <LoadingBlock label="Leyendo el mapeo…" /> : null}
        {mapping.isError ? <QueryError error={mapping.error} onRetry={() => void mapping.refetch()} /> : null}
        {mapping.data && rows.length === 0 ? <Notice tone="empty" title="Sin tablas en el catálogo del cartucho" /> : null}
        {rows.length ? (
          <TableShell label="Mapeo de tablas de Business One">
            <thead>
              <tr>
                <th className={TH}>Tabla SAP</th>
                <th className={TH}>Nombre de negocio</th>
                <th className={TH}>Modo</th>
                <th className={TH}>Ventana</th>
                <th className={TH}>Campos</th>
                <th className={TH}>Datasets</th>
                <th className={TH}>Carga por empresa</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ entity, loads: entityLoads }) => {
                const loadWindow = entityLoads.find((load) => load.dated && load.window_start);
                return (
                <tr key={entity.entity}>
                  <td className={`${TD} font-mono text-xs font-semibold`}>{entity.entity}</td>
                  <td className={TD}>
                    <div className="font-medium text-foreground dark:text-white">{entity.business_name ?? "N/D"}</div>
                    {entity.description ? <div className="text-xs text-muted-foreground">{entity.description}</div> : null}
                  </td>
                  <td className={TD}>{MODE_LABELS[entity.mode ?? ""] ?? entity.mode ?? "N/D"}</td>
                  <td className={`${TD} text-xs`}>
                    {entity.date_field ? <div>por {entity.date_field}</div> : <div>tabla completa</div>}
                    {loadWindow ? (
                      <div className="text-muted-foreground">
                        {loadWindow.window_start?.slice(0, 10)} a {loadWindow.window_end?.slice(0, 10)}
                      </div>
                    ) : null}
                  </td>
                  <td className={`${TD} text-xs`}>
                    {entity.fields.length ? (
                      <details>
                        <summary className="cursor-pointer">{entity.fields.length} campos</summary>
                        <p className="mt-1 break-words font-mono text-[11px] text-muted-foreground">{entity.fields.join(", ")}</p>
                      </details>
                    ) : "N/D"}
                  </td>
                  <td className={`${TD} text-xs`}>
                    {entity.datasets.length ? (
                      <details>
                        <summary className="cursor-pointer">{entity.datasets.length} datasets</summary>
                        <p className="mt-1 break-words font-mono text-[11px] text-muted-foreground">{entity.datasets.join(", ")}</p>
                      </details>
                    ) : "ninguno"}
                  </td>
                  <td className={`${TD} text-xs`}>
                    {entityLoads.length ? (
                      <ul className="space-y-1">
                        {entityLoads.map((load) => {
                          const status = loadStatus(load.status);
                          return (
                            <li key={load.company} className="flex flex-wrap items-center gap-1.5">
                              <span className="font-mono">{load.company}</span>
                              <span className="text-muted-foreground">
                                {load.status === "sin_conteo"
                                  ? `${formatCount(load.platform_rows)} en plataforma`
                                  : `${formatCount(load.platform_rows)} / ${formatCount(load.source_rows)} · ${formatPct(load.loaded_pct)}`}
                              </span>
                              <Pill tone={status.tone}>{status.label}</Pill>
                            </li>
                          );
                        })}
                      </ul>
                    ) : (
                      <span className="text-muted-foreground">sin conteo</span>
                    )}
                  </td>
                </tr>
                );
              })}
            </tbody>
          </TableShell>
        ) : null}
      </Panel>
    </div>
  );
}
