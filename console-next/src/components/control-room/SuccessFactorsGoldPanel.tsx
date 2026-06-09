import { Database, Layers3, LineChart, Users } from "lucide-react";

import type { SfGoldKpisPayload, SourceStatus } from "@/lib/control-room/types";
import { cn } from "@/lib/utils";

import { CommandMetric, MiniBar, OperationalNotice, ReadinessBadge } from "./StatusBadge";

function formatNumber(value: number): string {
  return new Intl.NumberFormat("es-MX").format(value);
}

function rowLabel(row: Record<string, unknown>): string {
  const candidates = [
    row.label,
    row.company_name,
    row.location_name,
    row.department_name,
    row.division_name,
    row.business_unit_name,
    row.user_id,
    row.id,
  ];
  const value = candidates.find((item) => typeof item === "string" && item.trim().length > 0);
  return String(value || "Registro");
}

function catalogHref(dataset?: string): string {
  if (!dataset) return "/data/catalog?layer=gold&cartridge=sap_successfactors";
  return `/data/catalog?layer=gold&cartridge=sap_successfactors&dataset=${encodeURIComponent(dataset)}`;
}

function dataHref(dataset?: string): string {
  if (!dataset) return "/data/catalog?layer=gold&cartridge=sap_successfactors";
  return `/api/data/${encodeURIComponent(dataset)}?limit=20`;
}

function schemaHref(source?: string): string {
  if (!source) return "/viewer?type=schema";
  return `/viewer?type=schema&source=${encodeURIComponent(source)}`;
}

export function SuccessFactorsGoldPanel({
  payload,
  loading,
  error,
  sources,
}: {
  payload: SfGoldKpisPayload | null;
  loading: boolean;
  error: string;
  sources: SourceStatus[];
}) {
  const widgets = payload?.widgets ?? [];
  const readySources = sources.filter((source) => source.status === "ok" || source.data_readiness === "ready").length;
  const blockedSources = sources.filter((source) => source.status === "blocked" || source.data_readiness === "blocked" || source.data_readiness === "no_permission").length;
  const totalRows = widgets.reduce((sum, widget) => sum + (Number.isFinite(widget.value) ? widget.value : 0), 0);
  const employeeWidget = widgets.find((widget) => widget.id.includes("employee_360") || widget.dataset.includes("employee_360"));
  const orgWidget = widgets.find((widget) => widget.id.includes("org_structure") || widget.dataset.includes("org_structure"));

  return (
    <section className="overflow-hidden rounded-lg border bg-card" aria-label="SuccessFactors Gold Command Panel">
      <div className="border-b bg-muted/30 p-4">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase text-muted-foreground">SuccessFactors Gold · FEMSA</p>
            <h2 className="mt-1 text-xl font-semibold">Workforce command panel</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Lectura scoped del workspace activo
              {payload?.connection_id ? ` · conexion ${payload.connection_id}` : ""}
              {payload?.generated_at ? ` · actualizado ${new Date(payload.generated_at).toLocaleString("es-MX")}` : ""}
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <a className="inline-flex min-h-[40px] items-center justify-center rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/10" href="/data/catalog?layer=gold&cartridge=sap_successfactors">
              Catalogo Gold
            </a>
            <a className="inline-flex min-h-[40px] items-center justify-center rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/10" href={schemaHref("raw/sap_successfactors/PerPerson")}>
              Schema PerPerson
            </a>
          </div>
        </div>
      </div>

      <div className="space-y-4 p-4">
        {loading ? <OperationalNotice tone="info" title="Cargando KPIs Gold">Consultando endpoints reales de SuccessFactors.</OperationalNotice> : null}
        {error ? <OperationalNotice tone="error" title="Error operativo visible">{error}</OperationalNotice> : null}
        {!loading && !error && widgets.length === 0 ? (
          <OperationalNotice tone="warning" title="Sin Gold visible">El backend no devolvio widgets Gold para este workspace; no se muestran valores sinteticos.</OperationalNotice>
        ) : null}

        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <CommandMetric
            label="Headcount"
            value={employeeWidget ? formatNumber(employeeWidget.value) : "N/D"}
            detail={employeeWidget ? employeeWidget.dataset : "Sin employee_360 visible"}
            icon={Users}
            tone={employeeWidget ? "good" : "warning"}
          />
          <CommandMetric
            label="Estructura org"
            value={orgWidget ? formatNumber(orgWidget.value) : "N/D"}
            detail={orgWidget ? orgWidget.dataset : "Sin org_structure visible"}
            icon={Layers3}
            tone={orgWidget ? "good" : "warning"}
          />
          <CommandMetric
            label="Fuentes data-ready"
            value={`${readySources}/${sources.length}`}
            detail={blockedSources ? `${blockedSources} bloqueadas o sin permiso` : "Sin bloqueos visibles"}
            icon={Database}
            tone={blockedSources ? "warning" : "good"}
          />
          <CommandMetric
            label="Widgets Gold"
            value={widgets.length}
            detail={totalRows ? `${formatNumber(totalRows)} filas agregadas` : "Sin conteo agregado"}
            icon={LineChart}
          />
        </div>

        {widgets.length ? (
          <div className="grid gap-3 xl:grid-cols-2">
            {widgets.map((widget) => {
              const maxHeadcount = Math.max(1, ...widget.rows.map((row) => typeof row.headcount === "number" ? row.headcount : 0));
              return (
                <article key={widget.id} className="rounded-lg border bg-background p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-xs font-semibold uppercase text-muted-foreground">{widget.title}</p>
                      <strong className="mt-1 block text-2xl font-semibold">{formatNumber(widget.value)}</strong>
                      <p className="truncate text-xs text-muted-foreground">{widget.dataset}</p>
                    </div>
                    <ReadinessBadge status={widget.value > 0 ? "ready" : "empty"} compact />
                  </div>
                  <div className="mt-4 space-y-3">
                    {widget.rows.slice(0, 6).map((row, index) => {
                      const headcount = typeof row.headcount === "number" ? row.headcount : 0;
                      return (
                        <div key={`${widget.id}:${index}`} className="space-y-1">
                          <div className="flex items-center justify-between gap-3 text-sm">
                            <span className="min-w-0 truncate text-muted-foreground">{rowLabel(row)}</span>
                            {typeof row.headcount === "number" ? <strong className="tabular-nums">{formatNumber(headcount)}</strong> : null}
                          </div>
                          {typeof row.headcount === "number" ? <MiniBar value={headcount} max={maxHeadcount} /> : null}
                        </div>
                      );
                    })}
                    {!widget.rows.length ? <p className="text-sm text-muted-foreground">Sin muestra de filas para este widget.</p> : null}
                  </div>
                  <div className="mt-4 flex flex-wrap gap-2">
                    <a className="rounded-md border px-2.5 py-1.5 text-xs font-medium hover:bg-accent/10" href={catalogHref(widget.dataset)}>Catalogo</a>
                    <a className="rounded-md border px-2.5 py-1.5 text-xs font-medium hover:bg-accent/10" href={dataHref(widget.dataset)}>Preview data</a>
                  </div>
                </article>
              );
            })}
          </div>
        ) : null}

        {sources.length ? (
          <div className="rounded-lg border bg-background p-3">
            <div className="mb-3 flex items-center justify-between gap-3">
              <p className="text-sm font-semibold">Readiness de fuentes SuccessFactors</p>
              <span className="text-xs text-muted-foreground">{sources.length} datasets</span>
            </div>
            <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              {sources.slice(0, 9).map((source) => (
                <a
                  href={schemaHref(`raw/sap_successfactors/${source.dataset.replace(/^sap_successfactors_/, "")}`)}
                  key={`${source.module_id || source.module}:${source.dataset}`}
                  className={cn("rounded-md border p-3 text-sm hover:bg-accent/10", source.error ? "border-destructive/40" : "")}
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="min-w-0 truncate font-medium">{source.dataset}</span>
                    <ReadinessBadge status={source.data_readiness || source.status} compact />
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{source.count} filas · {source.module}</p>
                  {source.error ? <p className="mt-1 line-clamp-2 text-xs text-destructive">{source.error}</p> : null}
                </a>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </section>
  );
}
