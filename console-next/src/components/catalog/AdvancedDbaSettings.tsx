"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link2, Loader2, Save, ShieldCheck, XCircle } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { getMeAccess, type MeAccessResponse } from "@/lib/admin-surfaces";
import { cardinalitySentence, type CatalogEdge } from "@/lib/catalog/relations";
import {
  getDatasetRows,
  registerCatalogRelationship,
  rejectCatalogRelationship,
  upsertCatalogEntry,
} from "@/lib/data/client";
import type { CatalogCardinality, CatalogDataset, CatalogRelationshipInput, DatasetRow } from "@/lib/data/types";

export const JOIN_TYPES = [
  { value: "LEFT", label: "LEFT · conserva todos los registros de origen" },
  { value: "INNER", label: "INNER · solo registros con coincidencia" },
  { value: "RIGHT", label: "RIGHT · conserva todos los registros de destino" },
  { value: "FULL", label: "FULL · conserva ambos lados" },
] as const;

export const CARDINALITIES: Array<{ value: "" | CatalogCardinality; label: string }> = [
  { value: "", label: "Sin especificar" },
  { value: "N:1", label: "N:1 · muchos a uno" },
  { value: "1:1", label: "1:1 · uno a uno" },
  { value: "1:N", label: "1:N · uno a muchos" },
  { value: "N:N", label: "N:N · muchos a muchos" },
];

export function canManageCatalog(access: MeAccessResponse | undefined): boolean {
  if (!access) return false;
  return access.role?.global === "admin" || access.workspace?.workspace_role === "workspace_admin";
}

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
  cardinality: "" | CatalogCardinality;
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
  joinHint: "LEFT",
  cardinality: "N:1",
  description: "",
};

interface AdvancedDbaSettingsProps {
  datasets: Record<string, CatalogDataset>;
  edges: readonly CatalogEdge[];
  labelOf: (name: string) => string;
}

export function AdvancedDbaSettings({ datasets, edges, labelOf }: AdvancedDbaSettingsProps) {
  const access = useQuery({ queryKey: ["me", "access"], queryFn: getMeAccess, staleTime: 60_000 });
  if (!canManageCatalog(access.data)) return null;
  return <DbaPanel datasets={datasets} edges={edges} labelOf={labelOf} />;
}

function DbaPanel({ datasets, edges, labelOf }: AdvancedDbaSettingsProps) {
  const queryClient = useQueryClient();
  const [entryForm, setEntryForm] = useState<EntryForm>(EMPTY_ENTRY);
  const [relationshipForm, setRelationshipForm] = useState<RelationshipForm>(EMPTY_RELATIONSHIP);
  const names = useMemo(() => Object.keys(datasets).sort(), [datasets]);
  const [technical, setTechnical] = useState("");
  const [preview, setPreview] = useState("");
  const copilotEdges = edges.filter((edge) => edge.origin === "copilot");
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["data", "catalog"] });

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
      void refresh();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo guardar la entrada."),
  });

  const saveRelationship = useMutation({
    mutationFn: () => registerCatalogRelationship(toRelationshipInput(relationshipForm)),
    onSuccess: () => {
      toast.success("Relación guardada.");
      setRelationshipForm(EMPTY_RELATIONSHIP);
      void refresh();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo guardar la relación."),
  });

  const reject = useMutation({
    mutationFn: (edge: CatalogEdge) => rejectCatalogRelationship({
      from_dataset: edge.from,
      from_column: edge.fromColumn,
      to_dataset: edge.to,
      to_column: edge.toColumn,
    }),
    onSuccess: () => {
      toast.success("Relación rechazada; el Copiloto no la volverá a proponer.");
      void refresh();
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo rechazar la relación."),
  });

  function submitEntry() {
    if (!entryForm.dataset.trim() || !entryForm.columnName.trim()) {
      toast.error("Tabla y columna son requeridas.");
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
    <details className="group rounded-lg border bg-card shadow-sm">
      <summary className="flex min-h-[44px] cursor-pointer list-none items-center gap-2 px-4 py-3 text-base font-semibold">
        <ShieldCheck aria-hidden className="h-4 w-4 text-primary" />
        Ajustes Avanzados para DBAs
      </summary>
      <div className="grid grid-cols-1 gap-4 border-t p-4 xl:grid-cols-2">
        <section className="space-y-3">
          <h3 className="text-sm font-semibold">Nueva entrada</h3>
          <Field label="Tabla" value={entryForm.dataset} onChange={(dataset) => setEntryForm((current) => ({ ...current, dataset }))} />
          <Field label="Columna" value={entryForm.columnName} onChange={(columnName) => setEntryForm((current) => ({ ...current, columnName }))} />
          <Field label="Descripción" value={entryForm.description} onChange={(description) => setEntryForm((current) => ({ ...current, description }))} />
          <Field label="Tags" value={entryForm.tags} onChange={(tags) => setEntryForm((current) => ({ ...current, tags }))} placeholder="pii,finance" />
          <Field label="Ejemplos" value={entryForm.examples} onChange={(examples) => setEntryForm((current) => ({ ...current, examples }))} placeholder="valor1,valor2" />
          <div className="grid grid-cols-2 gap-2">
            <Checkbox label="Llave" checked={entryForm.isKey} onChange={(isKey) => setEntryForm((current) => ({ ...current, isKey }))} />
            <Checkbox label="Métrica" checked={entryForm.isMetric} onChange={(isMetric) => setEntryForm((current) => ({ ...current, isMetric }))} />
          </div>
          <PrimaryButton onClick={submitEntry} pending={upsertEntry.isPending} icon={<Save aria-hidden className="h-4 w-4" />}>
            Guardar entrada
          </PrimaryButton>
        </section>

        <section className="space-y-3">
          <h3 className="text-sm font-semibold">Relación</h3>
          <Field label="Tabla origen" value={relationshipForm.fromDataset} onChange={(fromDataset) => setRelationshipForm((current) => ({ ...current, fromDataset }))} />
          <Field label="Columna origen" value={relationshipForm.fromColumn} onChange={(fromColumn) => setRelationshipForm((current) => ({ ...current, fromColumn }))} />
          <Field label="Tabla destino" value={relationshipForm.toDataset} onChange={(toDataset) => setRelationshipForm((current) => ({ ...current, toDataset }))} />
          <Field label="Columna destino" value={relationshipForm.toColumn} onChange={(toColumn) => setRelationshipForm((current) => ({ ...current, toColumn }))} />
          <Select
            label="Tipo de unión"
            value={relationshipForm.joinHint}
            onChange={(joinHint) => setRelationshipForm((current) => ({ ...current, joinHint }))}
          >
            {JOIN_TYPES.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </Select>
          <Select
            label="Cardinalidad"
            value={relationshipForm.cardinality}
            onChange={(cardinality) => setRelationshipForm((current) => ({ ...current, cardinality: cardinality as RelationshipForm["cardinality"] }))}
          >
            {CARDINALITIES.map((option) => <option key={option.value || "none"} value={option.value}>{option.label}</option>)}
          </Select>
          <Field label="Descripción" value={relationshipForm.description} onChange={(description) => setRelationshipForm((current) => ({ ...current, description }))} />
          <PrimaryButton onClick={submitRelationship} pending={saveRelationship.isPending} icon={<Link2 aria-hidden className="h-4 w-4" />}>
            Guardar relación
          </PrimaryButton>
        </section>

        <section className="space-y-2 xl:col-span-2">
          <h3 className="text-sm font-semibold">Relaciones detectadas por el Copiloto</h3>
          {copilotEdges.length ? (
            <ul className="divide-y rounded-md border">
              {copilotEdges.map((edge) => (
                <li key={`${edge.from}.${edge.fromColumn}>${edge.to}.${edge.toColumn}`} className="flex flex-col gap-2 px-3 py-2 text-sm sm:flex-row sm:items-center sm:justify-between">
                  <span>
                    {cardinalitySentence(edge, labelOf)}
                    <span className="block text-xs text-muted-foreground">
                      {edge.from}.{edge.fromColumn} → {edge.to}.{edge.toColumn}
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={() => reject.mutate(edge)}
                    disabled={reject.isPending}
                    className="inline-flex min-h-[40px] items-center justify-center gap-2 rounded-md border px-3 text-sm font-medium text-destructive hover:bg-destructive/5 disabled:opacity-60"
                  >
                    <XCircle aria-hidden className="h-4 w-4" />
                    Rechazar relación del Copiloto
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No hay relaciones del Copiloto para revisar.</p>
          )}
        </section>

        <section className="space-y-2">
          <h3 className="text-sm font-semibold">Tipos técnicos</h3>
          <Select label="Tabla" value={technical} onChange={setTechnical}>
            <option value="">Selecciona una tabla</option>
            {names.map((name) => <option key={name} value={name}>{labelOf(name)}</option>)}
          </Select>
          {technical ? (
            <table className="min-w-full divide-y text-xs">
              <thead className="text-muted-foreground">
                <tr>
                  <th className="px-2 py-1 text-left font-medium">Columna</th>
                  <th className="px-2 py-1 text-left font-medium">Tipo técnico</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {(datasets[technical]?.columns ?? []).map((column) => (
                  <tr key={column.name}>
                    <td className="px-2 py-1">{column.name}</td>
                    <td className="px-2 py-1 font-mono">{column.type || "Sin información"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </section>

        <section className="space-y-2">
          <h3 className="text-sm font-semibold">Vista previa de datos Oro</h3>
          <Select label="Tabla Oro" value={preview} onChange={setPreview}>
            <option value="">Selecciona una tabla</option>
            {names
              .filter((name) => datasets[name]?.layer === "gold")
              .map((name) => <option key={name} value={name}>{labelOf(name)}</option>)}
          </Select>
          {preview ? <GoldDatasetPreview dataset={preview} /> : null}
        </section>
      </div>
    </details>
  );
}

export function GoldDatasetPreview({ dataset }: { dataset: string }) {
  const preview = useQuery({
    queryKey: ["data", "dataset-preview", dataset],
    queryFn: () => getDatasetRows(dataset, 20),
  });

  if (preview.isLoading) {
    return <div className="text-xs text-muted-foreground">Cargando vista previa…</div>;
  }
  if (preview.isError) {
    return <div className="text-xs text-destructive">No se pudo cargar la vista previa de {dataset}.</div>;
  }
  const rows = preview.data ?? [];
  if (!rows.length) {
    return <div className="text-xs text-muted-foreground">Sin filas para el espacio de trabajo activo.</div>;
  }
  const columns = Object.keys(rows[0] ?? {}).slice(0, 8);
  return (
    <div className="space-y-2">
      <span className="text-xs font-medium uppercase text-muted-foreground">Primeras 20 filas</span>
      <div className="overflow-x-auto rounded-md border bg-background">
        <table className="min-w-full divide-y text-xs">
          <thead className="bg-muted/40 text-muted-foreground">
            <tr>
              {columns.map((column) => <th key={column} className="px-2 py-2 text-left font-medium">{column}</th>)}
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.slice(0, 20).map((row, index) => (
              <tr key={previewRowKey(row, index)}>
                {columns.map((column) => (
                  <td key={`${index}:${column}`} className="max-w-[220px] truncate px-2 py-2 text-muted-foreground">
                    {formatCell(row[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
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

function Select({
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
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="h-4 w-4 rounded border" />
      {label}
    </label>
  );
}

function PrimaryButton({
  onClick,
  pending,
  icon,
  children,
}: {
  onClick: () => void;
  pending: boolean;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={pending}
      className="inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      {pending ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : icon}
      {children}
    </button>
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
    cardinality: form.cardinality || undefined,
    description: form.description.trim() || undefined,
  };
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function previewRowKey(row: DatasetRow, index: number): string {
  const id = row.user_id || row.company_id || row.location_id || row.department_id || row.manager_id;
  return id ? `${String(id)}:${index}` : String(index);
}
