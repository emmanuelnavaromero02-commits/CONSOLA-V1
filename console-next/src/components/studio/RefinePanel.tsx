"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { Database, FilePlus2, Play, Save, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { AssistedExplorer } from "@/components/explorer/AssistedExplorer";
import { TechnicalSqlDisclosure } from "@/components/explorer/TechnicalSqlDisclosure";
import { queryBronze } from "@/lib/data/client";
import type { BronzeQueryPayload } from "@/lib/data/types";
import { describeSource, exploreData, exploreRowsAsRecords } from "@/lib/explorer/client";
import {
  BRONZE_ROW_CAP,
  EMPTY_SPEC,
  buildExploreRequest,
  sourceFromKey,
  specProblems,
  type ExplorerSpec,
} from "@/lib/explorer/spec";
import { useDatasetDetail, useDatasets } from "@/lib/monitor/hooks";
import type { DatasetDetail } from "@/lib/monitor/types";
import { studioErrorMessage } from "@/lib/studio/client";
import { datasetsForLayer } from "@/lib/studio/datasets";
import { plural } from "@/lib/studio/format";
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
const PREVIEW_ROWS = 50;

type EditMode = "builder" | "technical";

interface PendingSave {
  sql: string;
  sources: string[];
}

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
  const [specs, setSpecs] = useState<Record<string, ExplorerSpec>>({});
  const [modes, setModes] = useState<Record<string, EditMode>>({});
  const [generated, setGenerated] = useState<Record<string, string>>({});
  const [sqlShown, setSqlShown] = useState(false);
  const [pendingSave, setPendingSave] = useState<PendingSave | null>(null);
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

  // Existing datasets keep arbitrary SQL that cannot be mapped back to the builder.
  const mode: EditMode = modes[key] ?? (existing ? "technical" : "builder");
  const spec = specs[key] ?? EMPTY_SPEC;
  const builderSource = form.entity ? sourceFromKey(`raw/${cartridge}/${form.entity}`) : null;
  const builderActive = Boolean(selected) && mode === "builder";
  const generatedSql = generated[key] ?? null;
  const displaySources = mode === "builder" && builderSource ? [`raw/${cartridge}/${form.entity}`] : sources;
  const schema = useQuery({
    queryKey: ["explorer", "schema", builderSource ? `raw/${cartridge}/${form.entity}` : ""],
    queryFn: () => describeSource(builderSource!),
    enabled: builderActive && Boolean(builderSource),
    retry: false,
    staleTime: 60_000,
  });
  const builderColumns = schema.data?.available_columns ?? [];

  const preview = useMutation({
    mutationFn: async (runMode: EditMode): Promise<BronzeQueryPayload> => {
      if (runMode === "technical") return queryBronze({ sql: form.sql.trim(), limit: PREVIEW_ROWS, sources });
      const response = await exploreData(
        buildExploreRequest(builderSource!, { ...spec, limit: PREVIEW_ROWS }, { execute: true }),
      );
      return {
        columns: response.columns,
        rows: exploreRowsAsRecords(response),
        sql_definition: response.sql_definition,
      };
    },
  });
  const compile = useMutation({
    mutationFn: () => exploreData(buildExploreRequest(builderSource!, spec, { execute: false })),
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

  function setMode(next: EditMode) {
    if (next === mode) return;
    setModes((current) => ({ ...current, [key]: next }));
    preview.reset();
  }

  function rememberGenerated(sql: string | null | undefined) {
    if (sql) setGenerated((current) => ({ ...current, [key]: sql }));
  }

  function editGenerated() {
    if (!generatedSql) return;
    if (form.sql.trim() && form.sql.trim() !== generatedSql) {
      toast.error("El editor SQL técnico ya tiene otra consulta; bórrala antes de copiar la generada.");
      return;
    }
    update({ sql: generatedSql });
    setMode("technical");
  }

  function builderProblem(): string | null {
    if (!builderSource) return "Elige la entidad bronze para usar el constructor.";
    if (schema.isLoading) return "Espera a que carguen las columnas de la entidad.";
    return specProblems(spec, builderColumns)[0] ?? null;
  }

  function select(name: string) {
    setSelected(name);
    setSqlShown(false);
    preview.reset();
  }

  function validate(): string | null {
    return identifierError(form.name, "El nombre") ?? (form.sql.trim() ? null : "El SQL es obligatorio.");
  }

  function runPreview() {
    if (mode === "builder") {
      const problem = builderProblem();
      if (problem) {
        toast.error(problem);
        return;
      }
      preview.mutate("builder", { onSuccess: (payload) => rememberGenerated(payload.sql_definition as string | null) });
      return;
    }
    if (!form.sql.trim()) {
      toast.error("Escribe una consulta SQL.");
      return;
    }
    preview.mutate("technical");
  }

  function runSave() {
    if (mode === "builder") {
      const problem = identifierError(form.name, "El nombre") ?? builderProblem();
      if (problem) {
        toast.error(problem);
        return;
      }
      compile.mutate(undefined, {
        onSuccess: (compiled) => {
          if (!compiled.sql_definition || !compiled.sources.length) {
            toast.error("El servidor no devolvió la definición SQL.");
            return;
          }
          const next = { sql: compiled.sql_definition, sources: compiled.sources };
          const typed = form.sql.trim();
          rememberGenerated(next.sql);
          if (typed && typed !== next.sql && typed !== generatedSql) {
            setPendingSave(next);
            return;
          }
          persistBuilder(next);
        },
        onError: (error) => toast.error(studioErrorMessage(error, "No se pudo generar la consulta del dataset.")),
      });
      return;
    }
    const problem = validate();
    if (problem) {
      toast.error(problem);
      return;
    }
    persist(form.sql.trim(), sources);
  }

  function persistBuilder(next: PendingSave) {
    update({ sql: next.sql });
    persist(next.sql, next.sources);
  }

  function persist(sql: string, declaredSources: string[]) {
    const name = form.name.trim();
    save.mutate(
      { name, layer: form.layer, sql, description: form.description.trim(), cartridge, sources: declaredSources },
      {
        onSuccess: () => {
          toast.success(`Dataset ${name} guardado.`);
          setDrafts((current) => {
            const next = { ...current };
            delete next[key];
            return next;
          });
          setModes((current) => ({ ...current, [name]: "technical" }));
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
            ? `Dataset ${existing} materializado: ${plural(result.row_count, "fila", "filas")}.`
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
      <section aria-label="Datasets de la fuente de datos" className="space-y-2">
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
            <div role="radiogroup" aria-label="Modo de edición" className="inline-flex flex-wrap gap-1 rounded-md border p-1">
              {([
                ["builder", "Constructor visual"],
                ["technical", "SQL técnico"],
              ] as const).map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={mode === value}
                  onClick={() => setMode(value)}
                  className={cn(
                    "min-h-[40px] rounded px-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    mode === value ? "bg-primary text-primary-foreground" : "hover:bg-accent/5",
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
            {mode === "builder" ? (
              <section aria-label="Constructor de consulta" className="space-y-3 rounded-md border p-3">
                {existing ? (
                  <Notice tone="warning">
                    Este dataset ya tiene SQL propio. Si guardas desde el constructor, la consulta se reemplaza por una nueva
                    generada a partir de la entidad bronze elegida.
                  </Notice>
                ) : null}
                {!form.entity ? (
                  <Notice>Elige la entidad bronze para construir la consulta sin escribir SQL.</Notice>
                ) : !builderSource ? (
                  <Notice tone="error">La entidad «{form.entity}» no tiene un nombre compatible con el constructor.</Notice>
                ) : (
                  <AssistedExplorer
                    columns={builderColumns}
                    spec={spec}
                    onSpecChange={(next) => setSpecs((current) => ({ ...current, [key]: next }))}
                    rowCap={BRONZE_ROW_CAP}
                    showRowLimit={false}
                    latestAvailable={builderColumns.some((column) => column.name === "load_date")}
                    loading={schema.isLoading}
                    error={schema.isError ? studioErrorMessage(schema.error, "No se pudo leer el esquema de la entidad.") : null}
                    disabled={preview.isPending || compile.isPending}
                  />
                )}
                <TechnicalSqlDisclosure
                  open={sqlShown}
                  onToggle={setSqlShown}
                  generatedSql={generatedSql}
                  onUseGenerated={editGenerated}
                >
                  {generatedSql ? null : (
                    <p className="text-xs text-muted-foreground">
                      Previsualiza o guarda para ver la consulta SQL que genera el servidor.
                    </p>
                  )}
                </TechnicalSqlDisclosure>
              </section>
            ) : null}
            <div hidden={mode !== "technical"} className="flex flex-col gap-1 text-sm">
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
              Fuentes: {displaySources.length ? displaySources.join(", ") : "ninguna declarada"}
            </p>
            <div className="flex flex-wrap gap-2">
              <button type="button" className={buttonClass} onClick={runPreview} disabled={preview.isPending}>
                {preview.isPending ? <Spinner /> : <Play aria-hidden className="h-4 w-4" />} Previsualizar
              </button>
              <button type="button" className={primaryButtonClass} onClick={runSave} disabled={save.isPending || compile.isPending}>
                {save.isPending || compile.isPending ? <Spinner /> : <Save aria-hidden className="h-4 w-4" />} Guardar
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
        open={pendingSave !== null}
        title="Reemplazar el SQL técnico"
        confirmLabel="Reemplazar y guardar"
        pendingLabel="Guardando…"
        pending={save.isPending}
        onConfirm={() => {
          if (pendingSave) persistBuilder(pendingSave);
          setPendingSave(null);
        }}
        onCancel={() => setPendingSave(null)}
        testId="replace-sql-dialog"
        description="El SQL escrito en el modo «SQL técnico» se reemplazará por la consulta que genera el constructor visual."
      />
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
