"use client";

import { useMutation } from "@tanstack/react-query";
import { Database, FilePlus2, Play, Save, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { queryBronze } from "@/lib/data/client";
import type { BronzeQueryPayload } from "@/lib/data/types";
import { useDatasetDetail, useDatasets } from "@/lib/monitor/hooks";
import type { DatasetDetail } from "@/lib/monitor/types";
import { studioErrorMessage } from "@/lib/studio/client";
import { datasetsForLayer } from "@/lib/studio/datasets";
import { useDeleteDataset, useRefreshDataset, useSaveDataset } from "@/lib/studio/hooks";
import { editorSources } from "@/lib/studio/sources";
import type { StudioEditorTarget, StudioLayer, StudioManifest } from "@/lib/studio/types";
import { identifierError } from "@/lib/studio/validation";
import { cn } from "@/lib/utils";

import { CodeEditor } from "./CodeEditor";
import { DataTable } from "./DataTable";
import {
  buttonClass,
  ConfirmDialog,
  dangerButtonClass,
  inputClass,
  Notice,
  primaryButtonClass,
  Spinner,
} from "./ui";

const NEW_DATASET = "__new__";

interface RefineForm {
  name: string;
  layer: StudioLayer;
  entity: string;
  description: string;
  sql: string;
}

const EMPTY_FORM: RefineForm = { name: "", layer: "silver", entity: "", description: "", sql: "" };

function formFromDetail(detail: DatasetDetail | undefined, name: string): RefineForm {
  const layer = String(detail?.layer ?? "").toLowerCase() === "gold" ? "gold" : "silver";
  return {
    name,
    layer,
    entity: "",
    description: String(detail?.metadata?.description ?? ""),
    sql: String(detail?.sql ?? ""),
  };
}

function previewRows(payload: BronzeQueryPayload | null): Array<Record<string, unknown>> {
  const rows = payload?.rows ?? payload?.data ?? payload?.result;
  return Array.isArray(rows)
    ? rows.filter((row): row is Record<string, unknown> => typeof row === "object" && row !== null && !Array.isArray(row))
    : [];
}

function previewColumns(payload: BronzeQueryPayload | null): string[] {
  if (Array.isArray(payload?.columns) && payload.columns.length) return payload.columns.map(String);
  const schema = (payload as { schema?: unknown } | null)?.schema;
  if (Array.isArray(schema)) {
    return schema
      .map((item) => (item && typeof item === "object" ? String((item as { name?: unknown }).name ?? "") : String(item)))
      .filter(Boolean);
  }
  return [];
}

function initialDrafts(target: StudioEditorTarget | null | undefined): Record<string, RefineForm> {
  return target?.entity && !target.dataset ? { [NEW_DATASET]: { ...EMPTY_FORM, entity: target.entity } } : {};
}

export function RefinePanel({
  cartridge,
  manifest,
  initialTarget,
}: {
  cartridge: string;
  manifest?: StudioManifest;
  initialTarget?: StudioEditorTarget | null;
}) {
  const datasets = useDatasets();
  const save = useSaveDataset();
  const refresh = useRefreshDataset();
  const remove = useDeleteDataset();
  const [selected, setSelected] = useState<string | null>(
    initialTarget?.dataset ?? (initialTarget?.entity ? NEW_DATASET : null),
  );
  const [drafts, setDrafts] = useState<Record<string, RefineForm>>(() => initialDrafts(initialTarget));
  const [confirmDelete, setConfirmDelete] = useState(false);
  const existing = selected && selected !== NEW_DATASET ? selected : null;
  const detail = useDatasetDetail(existing);
  const list = useMemo(
    () => datasetsForLayer(datasets.data ?? [], cartridge, null).filter((dataset) => {
      const layer = String(dataset.layer ?? "").toLowerCase();
      return layer === "silver" || layer === "gold";
    }),
    [datasets.data, cartridge],
  );
  const entities = (manifest?.entities ?? []).map((entity) => String(entity?.entity || entity?.name || "")).filter(Boolean);
  const key = selected ?? "";
  const form = drafts[key] ?? (existing ? formFromDetail(detail.data, existing) : EMPTY_FORM);
  const sources = editorSources({
    detailSources: existing ? detail.data?.sources ?? detail.data?.metadata?.sources ?? [] : [],
    cartridge,
    entity: form.entity,
    sql: form.sql,
  });

  const preview = useMutation({
    mutationFn: () => queryBronze({ sql: form.sql.trim(), limit: 50, sources }),
  });
  const rows = previewRows(preview.data ?? null);
  const columns = previewColumns(preview.data ?? null);
  const previewError = preview.data?.error
    ? String(preview.data.error)
    : preview.isError
      ? studioErrorMessage(preview.error, "La consulta falló.")
      : null;

  function update(patch: Partial<RefineForm>) {
    setDrafts((current) => ({ ...current, [key]: { ...form, ...patch } }));
  }

  function select(name: string) {
    setSelected(name);
    preview.reset();
  }

  function validate(): string | null {
    return identifierError(form.name, "El nombre") ?? (form.sql.trim() ? null : "El SQL es obligatorio.");
  }

  function runPreview() {
    if (!form.sql.trim()) {
      toast.error("Escribe una consulta SQL.");
      return;
    }
    preview.mutate();
  }

  function runSave() {
    const problem = validate();
    if (problem) {
      toast.error(problem);
      return;
    }
    const name = form.name.trim();
    save.mutate(
      { name, layer: form.layer, sql: form.sql.trim(), description: form.description.trim(), cartridge, sources },
      {
        onSuccess: () => {
          toast.success(`Dataset ${name} guardado.`);
          setDrafts((current) => {
            const next = { ...current };
            delete next[key];
            return next;
          });
          setSelected(name);
        },
        onError: (error) => toast.error(studioErrorMessage(error, "No se pudo guardar el dataset.")),
      },
    );
  }

  function runRefresh() {
    if (!existing) return;
    refresh.mutate(existing, {
      onSuccess: (result) =>
        toast.success(
          typeof result.row_count === "number"
            ? `Dataset ${existing} materializado: ${result.row_count} filas.`
            : `Dataset ${existing} materializado.`,
        ),
      onError: (error) => toast.error(studioErrorMessage(error, "No se pudo materializar el dataset.")),
    });
  }

  function runDelete() {
    if (!existing) return;
    remove.mutate(existing, {
      onSuccess: () => {
        toast.success(`Dataset ${existing} eliminado.`);
        setSelected(null);
      },
      onError: (error) => toast.error(studioErrorMessage(error, "No se pudo eliminar el dataset.")),
      onSettled: () => setConfirmDelete(false),
    });
  }

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[280px_minmax(0,1fr)]">
      <section aria-label="Datasets del cartucho" className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">Datasets</h3>
          <button type="button" className={buttonClass} onClick={() => select(NEW_DATASET)}>
            <FilePlus2 aria-hidden className="h-4 w-4" /> Nuevo dataset
          </button>
        </div>
        {datasets.isLoading ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground"><Spinner /> Cargando datasets…</p>
        ) : datasets.isError ? (
          <Notice tone="error">{studioErrorMessage(datasets.error, "No se pudo listar los datasets.")}</Notice>
        ) : !list.length ? (
          <Notice>No hay datasets Silver ni Gold para {cartridge}.</Notice>
        ) : (
          <ul className="max-h-[520px] space-y-1 overflow-y-auto">
            {list.map((dataset) => (
              <li key={dataset.name}>
                <button
                  type="button"
                  aria-pressed={selected === dataset.name}
                  onClick={() => select(dataset.name)}
                  className={cn(
                    "flex min-h-[44px] w-full flex-col items-start rounded-md border px-3 py-2 text-left text-sm",
                    "hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    selected === dataset.name && "border-primary bg-primary/5",
                  )}
                >
                  <span className="break-all font-mono text-xs">{dataset.name}</span>
                  <span className="text-[11px] uppercase text-muted-foreground">{dataset.layer}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label="Editor de dataset" data-testid="refine-editor" className="min-w-0 space-y-3 rounded-lg border bg-card p-4">
        {!selected ? (
          <Notice>Selecciona un dataset o crea uno nuevo para escribir su SQL sobre bronze.</Notice>
        ) : existing && detail.isLoading ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground"><Spinner /> Cargando definición…</p>
        ) : (
          <>
            {existing && detail.isError ? (
              <Notice tone="error">{studioErrorMessage(detail.error, "No se pudo leer la definición del dataset.")}</Notice>
            ) : null}
            {existing && detail.data?.status === "unavailable" ? (
              <Notice tone="warning" title="Esquema no disponible">{detail.data.error || "Sin detalle del backend."}</Notice>
            ) : null}
            <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
              <label className="flex flex-col gap-1 text-sm md:col-span-2">
                <span className="font-medium">Nombre</span>
                <input
                  value={form.name}
                  onChange={(event) => update({ name: event.target.value })}
                  disabled={Boolean(existing)}
                  className={inputClass}
                />
              </label>
              <label className="flex flex-col gap-1 text-sm">
                <span className="font-medium">Capa</span>
                <select
                  value={form.layer}
                  onChange={(event) => update({ layer: event.target.value === "gold" ? "gold" : "silver" })}
                  className={inputClass}
                >
                  <option value="silver">silver</option>
                  <option value="gold">gold</option>
                </select>
              </label>
              <label className="flex flex-col gap-1 text-sm">
                <span className="font-medium">Entidad bronze</span>
                <select value={form.entity} onChange={(event) => update({ entity: event.target.value })} className={inputClass}>
                  <option value="">—</option>
                  {entities.map((name) => <option key={name} value={name}>{name}</option>)}
                </select>
              </label>
            </div>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium">Descripción</span>
              <input value={form.description} onChange={(event) => update({ description: event.target.value })} className={inputClass} />
            </label>
            <div className="flex flex-col gap-1 text-sm">
              <label htmlFor="studio-sql" className="font-medium">SQL</label>
              <CodeEditor
                id="studio-sql"
                name="sql"
                value={form.sql}
                onChange={(sql) => update({ sql })}
                rows={12}
                placeholder={`select * from read_parquet('raw/${cartridge}/<entidad>') limit 50`}
              />
            </div>
            <p className="break-all text-xs text-muted-foreground">
              Fuentes: {sources.length ? sources.join(", ") : "ninguna declarada"}
            </p>
            <div className="flex flex-wrap gap-2">
              <button type="button" className={buttonClass} onClick={runPreview} disabled={preview.isPending}>
                {preview.isPending ? <Spinner /> : <Play aria-hidden className="h-4 w-4" />} Previsualizar
              </button>
              <button type="button" className={primaryButtonClass} onClick={runSave} disabled={save.isPending}>
                {save.isPending ? <Spinner /> : <Save aria-hidden className="h-4 w-4" />} Guardar
              </button>
              {existing ? (
                <>
                  <button type="button" className={buttonClass} onClick={runRefresh} disabled={refresh.isPending}>
                    {refresh.isPending ? <Spinner /> : <Database aria-hidden className="h-4 w-4" />} Materializar
                  </button>
                  <button type="button" className={dangerButtonClass} onClick={() => setConfirmDelete(true)}>
                    <Trash2 aria-hidden className="h-4 w-4" /> Eliminar
                  </button>
                </>
              ) : null}
            </div>
            {previewError ? <Notice tone="error">{previewError}</Notice> : null}
            {preview.isSuccess && !previewError ? (
              rows.length ? (
                <DataTable
                  columns={columns}
                  rows={rows}
                  caption="Resultado de la vista previa"
                  exportName={form.name.trim() || "vista-previa"}
                />
              ) : (
                <Notice>La consulta no devolvió filas.</Notice>
              )
            ) : null}
          </>
        )}
      </section>

      <ConfirmDialog
        open={confirmDelete}
        title="Eliminar dataset"
        tone="danger"
        confirmLabel="Eliminar"
        pendingLabel="Eliminando…"
        pending={remove.isPending}
        onConfirm={runDelete}
        onCancel={() => setConfirmDelete(false)}
        testId="delete-dataset-dialog"
        description={`Se eliminará el registro de ${existing ?? ""} y sus datos materializados. Esta acción no se puede deshacer.`}
      />
    </div>
  );
}
