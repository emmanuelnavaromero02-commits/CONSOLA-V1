"use client";

import { useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Database, KeyRound, Link2, Loader2, RefreshCw, Save, Search, Tags } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { toast } from "sonner";

import {
  getDataCatalog,
  registerCatalogRelationship,
  upsertCatalogEntry,
} from "@/lib/data/client";
import type { CatalogColumn, CatalogDataset, CatalogRelationshipInput } from "@/lib/data/types";
import { cn } from "@/lib/utils";

const LAYERS = ["bronze", "silver", "gold", "master"] as const;
const CARTRIDGES = ["replicon", "hubspot", "sap_hcm", "sap_s4hana", "sap_successfactors"] as const;
const JOIN_HINTS = ["many_to_one", "one_to_many", "one_to_one", "many_to_many"] as const;

interface EntryForm {
  dataset: string;
  columnName: string;
  description: string;
  tags: string;
  examples: string;
  isKey: boolean;
  isMetric: boolean;
}

interface RelationshipForm {
  fromDataset: string;
  fromColumn: string;
  toDataset: string;
  toColumn: string;
  joinHint: string;
  description: string;
}

const EMPTY_ENTRY: EntryForm = {
  dataset: "",
  columnName: "",
  description: "",
  tags: "",
  examples: "",
  isKey: false,
  isMetric: false,
};

const EMPTY_RELATIONSHIP: RelationshipForm = {
  fromDataset: "",
  fromColumn: "",
  toDataset: "",
  toColumn: "",
  joinHint: "many_to_one",
  description: "",
};

export default function DataCatalogPage() {
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState({ layer: "", cartridge: "", tags: "", datasets: "" });
  const [search, setSearch] = useState("");
  const [entryForm, setEntryForm] = useState<EntryForm>(EMPTY_ENTRY);
  const [relationshipForm, setRelationshipForm] = useState<RelationshipForm>(EMPTY_RELATIONSHIP);

  const catalog = useQuery({
    queryKey: ["data", "catalog", filters],
    queryFn: () => getDataCatalog(filters),
  });

  const datasetRows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return Object.entries(catalog.data?.datasets ?? {})
      .map(([name, dataset]) => ({ name, dataset }))
      .filter(({ name, dataset }) => {
        if (!needle) return true;
        const columns = dataset.columns ?? [];
        return (
          name.toLowerCase().includes(needle)
          || (dataset.description ?? "").toLowerCase().includes(needle)
          || columns.some((column) => (
            column.name.toLowerCase().includes(needle)
            || (column.description ?? "").toLowerCase().includes(needle)
            || (column.tags ?? []).some((tag) => tag.toLowerCase().includes(needle))
          ))
        );
      });
  }, [catalog.data?.datasets, search]);

  const metrics = useMemo(() => {
    const datasets = Object.values(catalog.data?.datasets ?? {});
    return {
      datasets: datasets.length,
      columns: datasets.reduce((total, dataset) => total + (dataset.columns?.length ?? 0), 0),
      relationships: catalog.data?.relationships.length ?? 0,
    };
  }, [catalog.data]);

  const upsertEntry = useMutation({
    mutationFn: () => upsertCatalogEntry({
      dataset: entryForm.dataset.trim(),
      column_name: entryForm.columnName.trim(),
      description: entryForm.description.trim() || undefined,
      tags: splitCsv(entryForm.tags),
      example_values: splitCsv(entryForm.examples),
      is_key: entryForm.isKey,
      is_metric: entryForm.isMetric,
    }),
    onSuccess: () => {
      toast.success("Entrada guardada.");
      setEntryForm(EMPTY_ENTRY);
      queryClient.invalidateQueries({ queryKey: ["data", "catalog"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo guardar la entrada."),
  });

  const saveRelationship = useMutation({
    mutationFn: () => registerCatalogRelationship(toRelationshipInput(relationshipForm)),
    onSuccess: () => {
      toast.success("Relación guardada.");
      setRelationshipForm(EMPTY_RELATIONSHIP);
      queryClient.invalidateQueries({ queryKey: ["data", "catalog"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo guardar la relación."),
  });

  function submitEntry() {
    if (!entryForm.dataset.trim() || !entryForm.columnName.trim()) {
      toast.error("Dataset y columna son requeridos.");
      return;
    }
    upsertEntry.mutate();
  }

  function submitRelationship() {
    const required = [
      relationshipForm.fromDataset,
      relationshipForm.fromColumn,
      relationshipForm.toDataset,
      relationshipForm.toColumn,
    ];
    if (required.some((value) => !value.trim())) {
      toast.error("Completa origen y destino de la relación.");
      return;
    }
    saveRelationship.mutate();
  }

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-6">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Catálogo de datos</h1>
          <p className="text-sm text-muted-foreground">
            Inventario operativo de datasets, columnas, términos y relaciones.
          </p>
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

      <section className="grid grid-cols-1 gap-4 md:grid-cols-3" aria-label="Resumen del catálogo">
        <MetricCard icon={Database} label="Datasets" value={metrics.datasets} />
        <MetricCard icon={Tags} label="Columnas" value={metrics.columns} />
        <MetricCard icon={Link2} label="Relaciones" value={metrics.relationships} />
      </section>

      <section className="rounded-lg border bg-card p-4 shadow-sm">
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(220px,1fr)_160px_180px_220px_220px]">
          <label className="space-y-1 text-sm">
            <span className="text-xs font-medium uppercase text-muted-foreground">Buscar</span>
            <div className="relative">
              <Search aria-hidden className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                className="min-h-[44px] w-full rounded-md border bg-background pl-9 pr-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                placeholder="Dataset, columna o tag"
              />
            </div>
          </label>
          <FilterSelect label="Capa" value={filters.layer} onChange={(layer) => setFilters((current) => ({ ...current, layer }))}>
            <option value="">Todas</option>
            {LAYERS.map((layer) => <option key={layer} value={layer}>{layer}</option>)}
          </FilterSelect>
          <FilterSelect
            label="Cartucho"
            value={filters.cartridge}
            onChange={(cartridge) => setFilters((current) => ({ ...current, cartridge }))}
          >
            <option value="">Todos</option>
            {CARTRIDGES.map((cartridge) => <option key={cartridge} value={cartridge}>{cartridge}</option>)}
          </FilterSelect>
          <TextFilter
            label="Tags"
            value={filters.tags}
            onChange={(tags) => setFilters((current) => ({ ...current, tags }))}
            placeholder="finance,kpi"
          />
          <TextFilter
            label="Datasets"
            value={filters.datasets}
            onChange={(datasets) => setFilters((current) => ({ ...current, datasets }))}
            placeholder="silver_sales,gold_revenue"
          />
        </div>
      </section>

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)]">
        <div className="rounded-lg border bg-card shadow-sm">
          <header className="border-b px-4 py-3">
            <h2 className="text-base font-semibold">Entradas</h2>
          </header>
          {catalog.isError ? (
            <ErrorPanel message="No se pudo cargar el catálogo." onRetry={() => catalog.refetch()} />
          ) : catalog.isLoading ? (
            <SkeletonRows rows={6} />
          ) : (
            <CatalogTable rows={datasetRows} />
          )}
        </div>

        <div className="space-y-4">
          <section className="rounded-lg border bg-card p-4 shadow-sm">
            <h2 className="text-base font-semibold">Nueva entrada</h2>
            <div className="mt-4 space-y-3">
              <Field label="Dataset" value={entryForm.dataset} onChange={(dataset) => setEntryForm((current) => ({ ...current, dataset }))} />
              <Field label="Columna" value={entryForm.columnName} onChange={(columnName) => setEntryForm((current) => ({ ...current, columnName }))} />
              <Field
                label="Descripción"
                value={entryForm.description}
                onChange={(description) => setEntryForm((current) => ({ ...current, description }))}
              />
              <Field
                label="Tags"
                value={entryForm.tags}
                onChange={(tags) => setEntryForm((current) => ({ ...current, tags }))}
                placeholder="pii,finance"
              />
              <Field
                label="Ejemplos"
                value={entryForm.examples}
                onChange={(examples) => setEntryForm((current) => ({ ...current, examples }))}
                placeholder="valor1,valor2"
              />
              <div className="grid grid-cols-2 gap-2">
                <Checkbox
                  label="Clave"
                  checked={entryForm.isKey}
                  onChange={(isKey) => setEntryForm((current) => ({ ...current, isKey }))}
                />
                <Checkbox
                  label="Métrica"
                  checked={entryForm.isMetric}
                  onChange={(isMetric) => setEntryForm((current) => ({ ...current, isMetric }))}
                />
              </div>
              <button
                type="button"
                onClick={submitEntry}
                disabled={upsertEntry.isPending}
                className="inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {upsertEntry.isPending ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Save aria-hidden className="h-4 w-4" />}
                Guardar entrada
              </button>
            </div>
          </section>

          <section className="rounded-lg border bg-card p-4 shadow-sm">
            <h2 className="text-base font-semibold">Relación</h2>
            <div className="mt-4 space-y-3">
              <Field label="Dataset origen" value={relationshipForm.fromDataset} onChange={(fromDataset) => setRelationshipForm((current) => ({ ...current, fromDataset }))} />
              <Field label="Columna origen" value={relationshipForm.fromColumn} onChange={(fromColumn) => setRelationshipForm((current) => ({ ...current, fromColumn }))} />
              <Field label="Dataset destino" value={relationshipForm.toDataset} onChange={(toDataset) => setRelationshipForm((current) => ({ ...current, toDataset }))} />
              <Field label="Columna destino" value={relationshipForm.toColumn} onChange={(toColumn) => setRelationshipForm((current) => ({ ...current, toColumn }))} />
              <FilterSelect
                label="Join"
                value={relationshipForm.joinHint}
                onChange={(joinHint) => setRelationshipForm((current) => ({ ...current, joinHint }))}
              >
                {JOIN_HINTS.map((hint) => <option key={hint} value={hint}>{hint}</option>)}
              </FilterSelect>
              <Field label="Descripción" value={relationshipForm.description} onChange={(description) => setRelationshipForm((current) => ({ ...current, description }))} />
              <button
                type="button"
                onClick={submitRelationship}
                disabled={saveRelationship.isPending}
                className="inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {saveRelationship.isPending ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Link2 aria-hidden className="h-4 w-4" />}
                Guardar relación
              </button>
            </div>
          </section>
        </div>
      </section>
    </main>
  );
}

function CatalogTable({ rows }: { rows: Array<{ name: string; dataset: CatalogDataset }> }) {
  if (rows.length === 0) {
    return <EmptyState label="Sin entradas de catálogo." />;
  }

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full divide-y text-sm">
        <thead className="bg-muted/40 text-xs uppercase text-muted-foreground">
          <tr>
            <th className="px-4 py-3 text-left font-medium">Dataset</th>
            <th className="px-4 py-3 text-left font-medium">Capa</th>
            <th className="px-4 py-3 text-left font-medium">Columnas</th>
            <th className="px-4 py-3 text-left font-medium">Descripción</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map(({ name, dataset }) => {
            const readiness = datasetReadiness(dataset.description);
            return (
              <tr key={name} className="align-top">
                <td className="px-4 py-3">
                  <div className="font-medium">{name}</div>
                  <div className="text-xs text-muted-foreground">{dataset.cartridge || "sin cartucho"}</div>
                  {readiness ? (
                    <span className="mt-2 inline-flex items-center gap-1 rounded-md border border-amber-300 bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
                      <AlertTriangle aria-hidden className="h-3.5 w-3.5" />
                      {readiness}
                    </span>
                  ) : null}
                </td>
                <td className="px-4 py-3">
                  <Badge>{dataset.layer || "n/a"}</Badge>
                </td>
                <td className="px-4 py-3">
                  <div className="space-y-2">
                    {(dataset.columns ?? []).slice(0, 6).map((column) => (
                      <ColumnPill key={`${name}:${column.name}`} column={column} />
                    ))}
                    {(dataset.columns?.length ?? 0) > 6 ? (
                      <div className="text-xs text-muted-foreground">+{(dataset.columns?.length ?? 0) - 6} columnas</div>
                    ) : null}
                  </div>
                </td>
                <td className="max-w-md px-4 py-3 text-muted-foreground">{dataset.description || "Sin descripción"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function datasetReadiness(description?: string | null) {
  const text = (description ?? "").toLowerCase();
  if (!text) return "";
  if (text.includes("pendiente") || text.includes("parcial") || text.includes(" no extra")) {
    return "Parcial";
  }
  return "";
}

function ColumnPill({ column }: { column: CatalogColumn }) {
  return (
    <div className="rounded-md border bg-background px-2 py-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{column.name}</span>
        {column.type ? <span className="text-xs text-muted-foreground">{column.type}</span> : null}
        {column.is_key ? <KeyRound aria-label="Clave" className="h-3.5 w-3.5 text-primary" /> : null}
        {column.is_metric ? <Badge>Métrica</Badge> : null}
      </div>
      {column.description ? <p className="mt-1 text-xs text-muted-foreground">{column.description}</p> : null}
      {column.tags?.length ? (
        <div className="mt-1 flex flex-wrap gap-1">
          {column.tags.map((tag) => <Badge key={tag}>{tag}</Badge>)}
        </div>
      ) : null}
    </div>
  );
}

function MetricCard({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: ReactNode }) {
  return (
    <div className="rounded-lg border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium uppercase text-muted-foreground">{label}</p>
          <p className="mt-1 text-2xl font-semibold">{value}</p>
        </div>
        <span className="inline-flex h-10 w-10 items-center justify-center rounded-md bg-primary/10 text-primary">
          <Icon aria-hidden className="h-5 w-5" />
        </span>
      </div>
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

function TextFilter({
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
  return <Field label={label} value={value} onChange={onChange} placeholder={placeholder} />;
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

function Checkbox({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return (
    <label className="flex min-h-[44px] items-center gap-2 rounded-md border bg-background px-3 text-sm">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="h-4 w-4 rounded border"
      />
      {label}
    </label>
  );
}

function Badge({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-md border bg-muted/40 px-2 py-0.5 text-xs font-medium text-muted-foreground">
      {children}
    </span>
  );
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

function splitCsv(value: string): string[] {
  return value.split(",").map((part) => part.trim()).filter(Boolean);
}

function toRelationshipInput(form: RelationshipForm): CatalogRelationshipInput {
  return {
    from_dataset: form.fromDataset.trim(),
    from_column: form.fromColumn.trim(),
    to_dataset: form.toDataset.trim(),
    to_column: form.toColumn.trim(),
    join_hint: form.joinHint,
    description: form.description.trim() || undefined,
  };
}
