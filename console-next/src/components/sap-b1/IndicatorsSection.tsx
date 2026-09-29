"use client";

import { ExternalLink } from "lucide-react";

import { useSapB1Indicators, useSapB1View } from "@/lib/sap-b1/hooks";
import {
  analyticAppHref,
  CASES,
  countLabel,
  METRIC_VIEW,
  metricLabel,
  metricState,
  metricSummary,
} from "@/lib/sap-b1/present";
import type { KpiMetric, SapB1Case, SapB1Indicator, SapB1ViewName } from "@/lib/sap-b1/types";

import { Fact, LoadingBlock, MetricStatePill, Notice, Panel, QueryError, RefreshButton } from "./ui";

type ViewQuery = ReturnType<typeof useSapB1View>;

const CASE_VIEWS: Record<SapB1Case, readonly SapB1ViewName[]> = {
  finanzas: ["sap_b1_margin_kpis"],
  ventas: ["sap_b1_sales_kpis", "sap_b1_expiry_kpis"],
  compras: ["sap_b1_supply_kpis"],
};

const CASE_DESCRIPTIONS: Record<SapB1Case, string> = {
  finanzas: "Margen bruto y de contribución, destructores, concentración y margen por vendedor, tal como los calcula la plataforma.",
  ventas: "Sell-in contra sell-out por distribuidora, días de inventario en canal, sell-out por clínica y caducidad de lotes.",
  compras: "Cobertura de materia prima, órdenes de compra contra necesidad y costo real contra estándar.",
};

function metricFrom(views: Partial<Record<SapB1ViewName, ViewQuery>>, metricId: string): { metric?: KpiMetric; query?: ViewQuery } {
  const viewName = METRIC_VIEW[metricId];
  const query = viewName ? views[viewName] : undefined;
  const metrics = (query?.data?.metrics ?? {}) as Record<string, KpiMetric | undefined>;
  return { metric: metrics[metricId], query };
}

function ValueBlock({ metricId, metric, query }: { metricId: string; metric?: KpiMetric; query?: ViewQuery }) {
  if (query?.isPending) return <p className="text-sm text-muted-foreground">Calculando…</p>;
  if (query?.isError) return <p className="text-sm text-destructive">No se pudo leer el valor actual.</p>;
  const summary = metricSummary(metricId, metric);
  return (
    <div>
      <p className="text-2xl font-semibold tracking-tight text-foreground dark:text-white">{summary.value}</p>
      {summary.detail ? <p className="mt-0.5 break-words text-xs text-muted-foreground">{summary.detail}</p> : null}
      {metric?.period ? <p className="mt-0.5 text-xs text-muted-foreground">Periodo: {metric.period}</p> : null}
    </div>
  );
}

function IndicatorCard({ indicator, metric, query }: { indicator: SapB1Indicator; metric?: KpiMetric; query?: ViewQuery }) {
  const breaches = metric?.breaches ?? [];
  return (
    <article className="flex min-w-0 flex-col gap-3 rounded-lg border bg-background p-4 dark:border-sky-400/15 dark:bg-[#06111f]">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <h4 className="min-w-0 text-sm font-semibold text-foreground dark:text-white">{indicator.name}</h4>
        {query?.data ? <MetricStatePill state={metricState(metric)} /> : null}
      </div>
      <ValueBlock metricId={indicator.id} metric={metric} query={query} />
      {breaches.length ? (
        <div className="rounded-md border border-red-500/30 bg-red-500/5 p-2 text-xs text-red-800 dark:text-red-200">
          <p className="font-semibold">{breaches.length} hallazgo{breaches.length === 1 ? "" : "s"}</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {breaches.slice(0, 3).map((breach, index) => <li key={`${index}:${breach}`}>{breach}</li>)}
          </ul>
        </div>
      ) : null}
      <details className="text-xs">
        <summary className="cursor-pointer font-medium text-foreground dark:text-white">Definición</summary>
        <p className="mt-2 text-muted-foreground">{indicator.formula}</p>
        <dl className="mt-2 grid gap-2 sm:grid-cols-2">
          <Fact label="Unidad" value={indicator.unit || "N/D"} />
          <Fact label="Granularidad" value={indicator.granularity || "N/D"} />
          <Fact label="Dimensiones" value={indicator.dimensions.length ? indicator.dimensions.join(", ") : "N/D"} />
          <Fact label="Umbrales" value={indicator.thresholds.length ? <span className="font-mono">{indicator.thresholds.join(", ")}</span> : "sin umbral"} />
          <Fact label="Dataset" value={<span className="font-mono">{indicator.dataset}</span>} />
          <Fact label="Filtro" value={indicator.filter ? <span className="font-mono">{indicator.filter}</span> : "sin filtro"} />
        </dl>
      </details>
    </article>
  );
}

export function CaseIndicatorsSection({ caseId }: { caseId: SapB1Case }) {
  const item = CASES.find((entry) => entry.id === caseId) ?? CASES[0];
  const catalog = useSapB1Indicators();
  const enabled = new Set<SapB1ViewName>(CASE_VIEWS[item.id]);
  const views: Partial<Record<SapB1ViewName, ViewQuery>> = {
    sap_b1_margin_kpis: useSapB1View("sap_b1_margin_kpis", enabled.has("sap_b1_margin_kpis")) as ViewQuery,
    sap_b1_sales_kpis: useSapB1View("sap_b1_sales_kpis", enabled.has("sap_b1_sales_kpis")) as ViewQuery,
    sap_b1_expiry_kpis: useSapB1View("sap_b1_expiry_kpis", enabled.has("sap_b1_expiry_kpis")) as ViewQuery,
    sap_b1_supply_kpis: useSapB1View("sap_b1_supply_kpis", enabled.has("sap_b1_supply_kpis")) as ViewQuery,
  };
  const activeViews = CASE_VIEWS[item.id].map((view) => views[view]);
  const caseIndicators = (catalog.data?.indicators ?? []).filter((indicator) => indicator.case === item.id);
  const busy = catalog.isFetching || activeViews.some((query) => query?.isFetching);
  const refresh = () => {
    void catalog.refetch();
    activeViews.forEach((query) => void query?.refetch());
  };

  return (
    <Panel
      eyebrow="Indicadores"
      title={`Indicadores de ${item.label}`}
      description={CASE_DESCRIPTIONS[item.id]}
      actions={
        <>
          <a
            href={analyticAppHref(item.app)}
            className="inline-flex min-h-[32px] items-center gap-1.5 rounded-md border bg-background px-3 text-xs font-semibold text-foreground hover:bg-muted dark:border-sky-400/20 dark:bg-[#06111f]"
          >
            Abrir app {item.appLabel}
            <ExternalLink aria-hidden className="h-3.5 w-3.5" />
          </a>
          <RefreshButton onClick={refresh} busy={busy} />
        </>
      }
    >
      {catalog.isPending ? <LoadingBlock label="Leyendo el catálogo de indicadores…" /> : null}
      {catalog.isError ? <QueryError error={catalog.error} onRetry={() => void catalog.refetch()} /> : null}
      {catalog.data && !caseIndicators.length ? <Notice tone="empty" title="Sin indicadores para este caso" /> : null}
      {caseIndicators.length ? (
        <section aria-labelledby={`caso-${item.id}`} className="space-y-3">
          <h3 id={`caso-${item.id}`} className="sr-only">
            {item.label} · {countLabel(caseIndicators.length, "indicador", "indicadores")}
          </h3>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {caseIndicators.map((indicator) => {
              const { metric, query } = metricFrom(views, indicator.id);
              return <IndicatorCard key={indicator.id} indicator={indicator} metric={metric} query={query} />;
            })}
          </div>
          {item.extras.length ? (
            <div className="grid gap-2 sm:grid-cols-2">
              {item.extras.map((metricId) => {
                const { metric, query } = metricFrom(views, metricId);
                const summary = metricSummary(metricId, metric);
                return (
                  <div key={metricId} className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-dashed px-3 py-2 text-sm dark:border-sky-400/20">
                    <span className="text-muted-foreground">{metricLabel(metricId)}</span>
                    <span className="font-medium text-foreground dark:text-white">
                      {query?.isPending ? "Calculando…" : query?.isError ? "No disponible" : summary.value}
                    </span>
                  </div>
                );
              })}
            </div>
          ) : null}
        </section>
      ) : null}
    </Panel>
  );
}
