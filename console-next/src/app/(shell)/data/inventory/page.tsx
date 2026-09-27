"use client";

import { useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Database, Link2, RefreshCw, Search, ShieldAlert, Tags } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { AdvancedDbaSettings } from "@/components/catalog/AdvancedDbaSettings";
import { AutoCatalogStatus } from "@/components/catalog/AutoCatalogStatus";
import { ColumnChips } from "@/components/catalog/ColumnChips";
import { CopilotBadge } from "@/components/catalog/CopilotBadge";
import { RelationsGraph } from "@/components/catalog/RelationsGraph";
import { RelationsMatrix } from "@/components/catalog/RelationsMatrix";
import { CLASSIFICATION_LABEL, hasPersonalData, orderedClassifications } from "@/lib/catalog/classifications";
import { useAutoCatalog } from "@/lib/catalog/hooks";
import { activeEdges } from "@/lib/catalog/relations";
import { getDataCatalog } from "@/lib/data/client";
import type { CatalogClassification, CatalogColumn, CatalogDataset } from "@/lib/data/types";
import { cn } from "@/lib/utils";

const LAYERS = [
  { value: "bronze", label: "Bronce (fuentes)" },
  { value: "silver", label: "Plata" },
  { value: "gold", label: "Oro" },
] as const;
const LAYER_LABEL: Record<string, string> = { bronze: "Bronce", silver: "Plata", gold: "Oro" };
const SOURCE_LABEL: Record<string, string> = {
  sap_successfactors: "SAP SuccessFactors",
  sap_b1: "SAP Business One",
  sap_hcm: "SAP HCM",
  sap_s4hana: "SAP S/4HANA",
  replicon: "Replicon",
  hubspot: "HubSpot",
  salesforce: "Salesforce",
  banxico: "Banxico",
  inegi: "INEGI",
  sec_edgar: "SEC EDGAR",
};
const COLUMNS_SHOWN = 8;

interface Filters {
  layer: string;
  cartridge: string;
  tags: string;
  datasets: string;
}

export default function DataCatalogPage() {
  const [filters, setFilters] = useState<Filters>(() => initialFiltersFromUrl());
  const [search, setSearch] = useState("");
  const [classification, setClassification] = useState<"" | CatalogClassification>("");
  const includeSources = filters.layer === "bronze";

  const catalog = useQuery({
    queryKey: ["data", "catalog", filters],
    queryFn: () => getDataCatalog({ ...filters, include_sources: includeSources }),
  });
  const autoStatus = useAutoCatalog({ cartridge: filters.cartridge || null, includeSources });

  const datasets = useMemo(() => catalog.data?.datasets ?? {}, [catalog.data?.datasets]);
  const labelOf = useMemo(
    () => (name: string) => datasets[name]?.display_name || name,
    [datasets],
  );
  const edges = useMemo(() => activeEdges(catalog.data?.relationships ?? []), [catalog.data?.relationships]);

  const sourceOptions = useMemo(() => {
    const values = new Set(Object.values(datasets).map((dataset) => dataset.cartridge || "").filter(Boolean));
    if (filters.cartridge) values.add(filters.cartridge);
    return [...values].sort();
  }, [datasets, filters.cartridge]);

  const datasetRows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return Object.entries(datasets)
      .map(([name, dataset]) => ({ name, dataset }))
      .filter(({ name, dataset }) => {
        const columns = dataset.columns ?? [];
        if (classification && !columns.some((column) => orderedClassifications(column.classifications).includes(classification))) {
          return false;
        }
        if (!needle) return true;
        return (
          name.toLowerCase().includes(needle)
          || (dataset.display_name ?? "").toLowerCase().includes(needle)
          || (dataset.description ?? "").toLowerCase().includes(needle)
          || columns.some((column) => (
            column.name.toLowerCase().includes(needle)
            || (column.description ?? "").toLowerCase().includes(needle)
            || (column.tags ?? []).some((tag) => tag.toLowerCase().includes(needle))
          ))
        );
      })
      .sort((a, b) => (a.dataset.display_name || a.name).localeCompare(b.dataset.display_name || b.name, "es"));
  }, [datasets, search, classification]);

  const metrics = useMemo(() => {
    const list = Object.values(datasets);
    const columns = list.flatMap((dataset) => dataset.columns ?? []);
    return {
      tables: list.length,
      columns: columns.length,
      relationships: edges.length,
      copilotRelationships: edges.filter((edge) => edge.origin === "copilot").length,
      personalColumns: columns.filter(hasPersonalData).length,
    };
  }, [datasets, edges]);

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-6">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Catálogo de datos</h1>
          <p className="text-sm text-muted-foreground">
            Tablas documentadas automáticamente: descripción, datos sensibles y relaciones, sin configuración manual.
          </p>
          <AutoCatalogStatus status={autoStatus} />
        </div>
        <button
          type="button"
          onClick={() => catalog.refetch()}
          className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RefreshCw aria-hidden className={cn("h-4 w-4", catalog.isFetching && "animate-spin")} />
          Refrescar
        </button>
      </header>

      <section className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4" aria-label="Resumen del catálogo">
        <MetricCard icon={Database} label="Tablas" value={metrics.tables} />
        <MetricCard icon={Tags} label="Columnas" value={metrics.columns} />
        <MetricCard
          icon={Link2}
          label="Relaciones"
          value={metrics.relationships}
          hint={`${metrics.copilotRelationships} detectadas por Copiloto`}
        />
        <MetricCard icon={ShieldAlert} label="Columnas con datos personales" value={metrics.personalColumns} />
      </section>

      <section className="rounded-lg border bg-card p-4 shadow-sm">
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(220px,1fr)_160px_200px_220px_200px]">
          <label className="space-y-1 text-sm">
            <span className="text-xs font-medium uppercase text-muted-foreground">Buscar</span>
            <div className="relative">
              <Search aria-hidden className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                className="min-h-[44px] w-full rounded-md border bg-background pl-9 pr-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                placeholder="Tabla, columna o descripción"
              />
            </div>
          </label>
          <FilterSelect label="Capa" value={filters.layer} onChange={(layer) => setFilters((current) => ({ ...current, layer }))}>
            <option value="">Todas</option>
            {LAYERS.map((layer) => <option key={layer.value} value={layer.value}>{layer.label}</option>)}
          </FilterSelect>
          <FilterSelect
            label="Fuente de datos"
            value={filters.cartridge}
            onChange={(cartridge) => setFilters((current) => ({ ...current, cartridge }))}
          >
            <option value="">Todas</option>
            {sourceOptions.map((source) => <option key={source} value={source}>{sourceLabel(source)}</option>)}
          </FilterSelect>
          <FilterSelect
            label="Clasificación"
            value={classification}
            onChange={(value) => setClassification(value as "" | CatalogClassification)}
          >
            <option value="">Todas</option>
            {(Object.keys(CLASSIFICATION_LABEL) as CatalogClassification[]).map((name) => (
              <option key={name} value={name}>{CLASSIFICATION_LABEL[name]}</option>
            ))}
          </FilterSelect>
          <Field
            label="Tags"
            value={filters.tags}
            onChange={(tags) => setFilters((current) => ({ ...current, tags }))}
            placeholder="finance,kpi"
          />
        </div>
      </section>

      {catalog.data?.annotations_degraded ? (
        <p className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-100">
          Las anotaciones del Copiloto no están disponibles en este momento; se muestra la información publicada.
        </p>
      ) : null}

      <section className="rounded-lg border bg-card shadow-sm" aria-label="Tablas">
        <header className="border-b px-4 py-3">
          <h2 className="text-base font-semibold">Tablas</h2>
        </header>
        {catalog.isError ? (
          <ErrorPanel message="No se pudo cargar el catálogo." onRetry={() => catalog.refetch()} />
        ) : catalog.isLoading ? (
          <SkeletonRows rows={6} />
        ) : datasetRows.length === 0 ? (
          <EmptyState label="Sin tablas publicadas para estos filtros." />
        ) : (
          <ul className="divide-y">
            {datasetRows.map(({ name, dataset }) => (
              <DatasetCard key={name} name={name} dataset={dataset} />
            ))}
          </ul>
        )}
      </section>

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-2" aria-label="Relaciones entre tablas">
        <div className="rounded-lg border bg-card p-4 shadow-sm">
          <h2 className="mb-3 text-base font-semibold">Mapa de relaciones</h2>
          <RelationsGraph edges={edges} labelOf={labelOf} />
        </div>
        <div className="rounded-lg border bg-card p-4 shadow-sm">
          <h2 className="mb-3 text-base font-semibold">Matriz de relaciones</h2>
          <RelationsMatrix edges={edges} labelOf={labelOf} />
        </div>
      </section>

      <AdvancedDbaSettings datasets={datasets} edges={edges} labelOf={labelOf} />
    </main>
  );
}

function DatasetCard({ name, dataset }: { name: string; dataset: CatalogDataset }) {
  const readiness = datasetReadiness(dataset.description);
  const columns = dataset.columns ?? [];
  const personal = columns.filter(hasPersonalData).length;
  const title = dataset.display_name || name;
  return (
    <li className="space-y-3 px-4 py-4">
      <div className="flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-base font-semibold">{title}</h3>
            {dataset.description_origin === "copilot" ? (
              <CopilotBadge status={dataset.copilot?.status ?? null} />
            ) : null}
            {readiness ? (
              <span className="inline-flex items-center gap-1 rounded-md border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
                <AlertTriangle aria-hidden className="h-3.5 w-3.5" />
                {readiness}
              </span>
            ) : null}
          </div>
          <p className="text-xs text-muted-foreground">
            {[name !== title ? name : null, sourceLabel(dataset.cartridge), layerLabel(dataset.layer)]
              .filter(Boolean)
              .join(" · ")}
          </p>
        </div>
        <dl className="grid grid-cols-3 gap-4 text-xs sm:text-sm">
          <Fact label="Registros" value={formatCount(dataset.row_count)} />
          <Fact label="Actualización" value={formatDate(dataset.last_refresh)} />
          <Fact label="Datos personales" value={personalLabel(personal, Boolean(dataset.copilot))} />
        </dl>
      </div>
      <p className="max-w-4xl text-sm text-muted-foreground">{dataset.description || "Sin descripción todavía."}</p>
      {columns.length ? (
        <ul className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {columns.slice(0, COLUMNS_SHOWN).map((column) => (
            <ColumnItem key={`${name}:${column.name}`} column={column} />
          ))}
        </ul>
      ) : null}
      {columns.length > COLUMNS_SHOWN ? (
        <p className="text-xs text-muted-foreground">+{columns.length - COLUMNS_SHOWN} columnas</p>
      ) : null}
    </li>
  );
}

function ColumnItem({ column }: { column: CatalogColumn }) {
  return (
    <li className="rounded-md border bg-background px-3 py-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-medium">{column.name}</span>
        <ColumnChips column={column} />
      </div>
      {column.description ? <p className="mt-1 text-xs text-muted-foreground">{column.description}</p> : null}
    </li>
  );
}

function personalLabel(count: number, profiled: boolean): string {
  if (count > 0) return count === 1 ? "1 columna" : `${count} columnas`;
  return profiled ? "Ninguno detectado" : "Sin información";
}

function datasetReadiness(description?: string | null) {
  const text = (description ?? "").toLowerCase();
  if (!text) return "";
  if (text.includes("pendiente") || text.includes("parcial") || text.includes(" no extra")) {
    return "Parcial";
  }
  return "";
}

function MetricCard({ icon: Icon, label, value, hint }: { icon: LucideIcon; label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold">{value}</p>
          {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
        </div>
        <span className="inline-flex h-10 w-10 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Icon aria-hidden className="h-5 w-5" />
        </span>
      </div>
    </div>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs uppercase text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="text-xs font-medium uppercase text-muted-foreground">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="min-h-[44px] w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />
    </label>
  );
}

function FilterSelect({
  label,
  value,
  onChange,
  children,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: ReactNode;
}) {
  return (
    <label className="block space-y-1 text-sm">
      <span className="text-xs font-medium uppercase text-muted-foreground">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-[44px] w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {children}
      </select>
    </label>
  );
}

function initialFiltersFromUrl(): Filters {
  if (typeof window === "undefined") return { layer: "", cartridge: "", tags: "", datasets: "" };
  const params = new URLSearchParams(window.location.search);
  return {
    layer: params.get("layer") || "",
    cartridge: params.get("cartridge") || "",
    tags: params.get("tags") || "",
    datasets: params.get("datasets") || "",
  };
}

function sourceLabel(value?: string | null): string {
  if (!value) return "Sin fuente de datos";
  return SOURCE_LABEL[value] ?? value;
}

function layerLabel(value?: string | null): string {
  if (!value) return "";
  return LAYER_LABEL[value] ?? value;
}

function formatCount(value?: number | null): string {
  if (value === null || value === undefined) return "Sin información";
  return new Intl.NumberFormat("es-MX").format(value);
}

function formatDate(value?: string | null): string {
  if (!value) return "Sin información";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString("es-MX", { dateStyle: "medium" });
}

function ErrorPanel({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="m-4 rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <span>{message}</span>
        <button
          type="button"
          onClick={onRetry}
          className="inline-flex min-h-[40px] items-center justify-center rounded-md border bg-background px-3 text-sm font-medium text-foreground hover:bg-accent/5"
        >
          Reintentar
        </button>
      </div>
    </div>
  );
}

function EmptyState({ label }: { label: string }) {
  return <div className="px-4 py-10 text-center text-sm text-muted-foreground">{label}</div>;
}

function SkeletonRows({ rows }: { rows: number }) {
  return (
    <div className="divide-y">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="grid grid-cols-4 gap-4 px-4 py-4">
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
          <div className="h-4 rounded bg-muted" />
        </div>
      ))}
    </div>
  );
}
