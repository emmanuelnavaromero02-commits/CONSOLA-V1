"use client";

import { useMemo, useState } from "react";

import { useSapB1View } from "@/lib/sap-b1/hooks";
import { formatCount, formatPct, metricReason, metricState } from "@/lib/sap-b1/present";
import type { EntityModelRow } from "@/lib/sap-b1/types";
import { cn } from "@/lib/utils";

import { LoadingBlock, MetricStatePill, Notice, Panel, QueryError, RefreshButton, TableShell, TD, TH } from "./ui";

const GROUP = "grupo";

const ENTITY_LABELS: Record<string, string> = {
  cliente: "Cliente",
  producto: "Producto",
  canal: "Canal",
  distribuidora: "Distribuidora",
  materia_prima: "Materia prima",
  proveedor: "Proveedor",
  vendedor: "Vendedor",
  lote: "Lote",
};

export function EntityModelSection() {
  const view = useSapB1View("sap_b1_margin_kpis");
  const metric = view.data?.metrics?.modelo_entidades;
  const rows = useMemo(() => metric?.entities ?? [], [metric]);
  const companies = useMemo(() => {
    const names = [...new Set(rows.map((row) => row.company).filter((name): name is string => Boolean(name)))];
    return names.sort((a, b) => (a === GROUP ? -1 : b === GROUP ? 1 : a.localeCompare(b)));
  }, [rows]);
  const [selected, setSelected] = useState(GROUP);
  const company = companies.includes(selected) ? selected : companies[0] ?? GROUP;
  const visible: EntityModelRow[] = rows.filter((row) => row.company === company);
  const state = metricState(metric);

  return (
    <Panel
      eyebrow="WisdomBit"
      title="Modelo de entidades del grupo"
      description="Las ocho entidades unificadas de las empresas: cuántos registros hay en Business One, cuántas identidades quedan al unificar, cuáles comparten las empresas, qué tan completas están sus relaciones y cuántas referencias apuntan a algo que no existe."
      actions={
        <>
          {metric ? <MetricStatePill state={state} /> : null}
          <RefreshButton onClick={() => void view.refetch()} busy={view.isFetching} />
        </>
      }
    >
      {view.isPending ? <LoadingBlock label="Calculando el modelo de entidades…" /> : null}
      {view.isError ? <QueryError error={view.error} onRetry={() => void view.refetch()} /> : null}
      {view.data && !rows.length ? (
        <Notice tone="empty" title="Sin datos todavía">{metricReason(metric) ?? "El modelo de entidades aún no se publica para este workspace."}</Notice>
      ) : null}
      {rows.length ? (
        <div className="space-y-3">
          <div className="flex flex-wrap gap-2" role="group" aria-label="Empresa">
            {companies.map((name) => (
              <button
                key={name}
                type="button"
                onClick={() => setSelected(name)}
                aria-pressed={name === company}
                className={cn(
                  "min-h-[32px] rounded-full border px-3 text-xs font-semibold transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  name === company ? "border-primary bg-primary text-primary-foreground" : "bg-background text-foreground hover:bg-muted dark:border-sky-400/20 dark:bg-[#06111f]",
                )}
              >
                {name === GROUP ? "Grupo" : name}
              </button>
            ))}
          </div>
          <TableShell label="Modelo de entidades">
            <thead>
              <tr>
                <th className={TH}>Entidad</th>
                <th className={`${TH} text-right`}>Registros</th>
                <th className={`${TH} text-right`}>Identidades</th>
                <th className={`${TH} text-right`}>Compartidas</th>
                <th className={`${TH} text-right`}>Relaciones completas</th>
                <th className={`${TH} text-right`}>Huérfanos</th>
                <th className={TH}>Regla</th>
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => (
                <tr key={`${row.entity}:${row.company}`}>
                  <td className={`${TD} font-medium`}>{ENTITY_LABELS[row.entity ?? ""] ?? row.entity ?? "N/D"}</td>
                  <td className={`${TD} text-right tabular-nums`}>{formatCount(row.records)}</td>
                  <td className={`${TD} text-right tabular-nums`}>{formatCount(row.identities)}</td>
                  <td className={`${TD} text-right tabular-nums`}>{formatCount(row.shared_identities)}</td>
                  <td className={`${TD} text-right tabular-nums`}>
                    {formatPct(row.completeness_pct)}
                    <div className="text-xs text-muted-foreground">{formatCount(row.complete_records)} registros</div>
                  </td>
                  <td className={cn(TD, "text-right tabular-nums", (row.orphans ?? 0) > 0 && "font-semibold text-red-700 dark:text-red-300")}>
                    {formatCount(row.orphans)}
                  </td>
                  <td className={`${TD} text-xs text-muted-foreground`}>{row.relation_rule ?? "N/D"}</td>
                </tr>
              ))}
            </tbody>
          </TableShell>
          {metric?.breaches?.length ? (
            <Notice tone="warning" title="Huérfanos en el grupo">
              <ul className="list-disc pl-4">
                {metric.breaches.map((breach, index) => <li key={`${index}:${breach}`}>{breach}</li>)}
              </ul>
            </Notice>
          ) : null}
          {metric?.period ? <p className="text-xs text-muted-foreground">Periodo: {metric.period}</p> : null}
        </div>
      ) : null}
    </Panel>
  );
}
