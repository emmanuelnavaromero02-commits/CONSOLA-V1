"use client";

import { Pencil, Play, Plus, RefreshCw, ScanSearch, Table2, Type, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { useSourceSchema } from "@/lib/monitor/hooks";
import { MAX_SPEC_BYTES, studioErrorMessage } from "@/lib/studio/client";
import {
  useCreateEntity,
  useIntrospectSource,
  useRenameEntity,
  useStudioEntities,
  useUpdateEntity,
  useUploadEntitySpec,
} from "@/lib/studio/hooks";
import type {
  IntrospectionResult,
  StudioEntity,
  StudioManifest,
  StudioManifestEntity,
  UploadSpecResult,
} from "@/lib/studio/types";
import { changedEntityFields, identifierError, type EntityDraft as EditDraft } from "@/lib/studio/validation";
import { cn } from "@/lib/utils";

import { DataTable } from "./DataTable";
import { ExtractionTracker, extractionMode, startExtraction, type ExtractionLaunch } from "./ExtractionTracker";
import { buttonClass, ConfirmDialog, inputClass, Notice, primaryButtonClass, Spinner } from "./ui";

const MODES = ["full", "incremental"];

function manifestEntity(manifest: StudioManifest | undefined, name: string): StudioManifestEntity | undefined {
  return (manifest?.entities ?? []).find((entity) => (entity?.entity || entity?.name) === name);
}

function draftFor(entity: StudioEntity, meta: StudioManifestEntity | undefined): EditDraft {
  return {
    display_name: String(entity.display_name ?? meta?.display_name ?? ""),
    mode: String(entity.mode ?? meta?.mode ?? "full"),
    primary_key: String(meta?.primary_key ?? ""),
    dag_id: String(entity.dag_id ?? meta?.dag_id ?? ""),
    cron_expression: String(meta?.cron_expression ?? ""),
    description: String(entity.description ?? meta?.description ?? ""),
  };
}

function SchemaPreview({ cartridge, entity }: { cartridge: string; entity: string }) {
  const schema = useSourceSchema(`raw/${cartridge}/${entity}`);
  if (schema.isLoading) {
    return <p className="flex items-center gap-2 text-xs text-muted-foreground"><Spinner /> Leyendo esquema de bronze…</p>;
  }
  if (schema.isError) return <Notice tone="error">{studioErrorMessage(schema.error, "No se pudo leer el esquema.")}</Notice>;
  const preview = schema.data?.preview;
  const fields = (preview?.schema ?? []).filter((field) => field?.name);
  const rows = (preview?.data ?? preview?.rows ?? preview?.result ?? []).filter(
    (row): row is Record<string, unknown> => typeof row === "object" && row !== null,
  );
  const messages = (schema.data?.errors ?? [])
    .map((item) => (typeof item === "string" ? item : typeof item?.message === "string" ? item.message : ""))
    .filter(Boolean);
  const latest = schema.data?.partitions?.latest;
  return (
    <div className="space-y-2 text-xs">
      <p className="text-muted-foreground">
        Estado: {schema.data?.status || preview?.status || "sin dato"}
        {typeof latest === "string" && latest ? ` · última partición ${latest}` : ""}
      </p>
      {fields.length ? (
        <ul className="flex flex-wrap gap-1" aria-label="Columnas">
          {fields.map((field) => (
            <li key={String(field.name)} className="rounded border bg-background px-2 py-0.5 font-mono">
              {String(field.name)}
              {field.type ? <span className="text-muted-foreground">: {String(field.type)}</span> : null}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-muted-foreground">Sin columnas en bronze para esta entidad.</p>
      )}
      {rows.length ? (
        <DataTable
          columns={fields.map((field) => String(field.name))}
          rows={rows}
          maxHeight="max-h-56"
          exportName={`${cartridge}_${entity}_bronze`}
        />
      ) : null}
      {messages.length ? (
        <ul className="space-y-0.5 text-muted-foreground">
          {messages.slice(0, 4).map((message, index) => <li key={index}>{message}</li>)}
        </ul>
      ) : null}
    </div>
  );
}

function IntrospectionView({ result }: { result: IntrospectionResult }) {
  const live = result.source === "live";
  return (
    <div data-testid="introspection-result" className="space-y-2">
      {live ? (
        <Notice tone="success" title="Introspección en vivo">
          {(result.entities ?? []).length} entidades leídas de la fuente.
        </Notice>
      ) : (
        <Notice tone="warning" title="Esquema estático (fallback)">
          No se pudo introspeccionar la fuente en vivo{result.reason ? `: ${result.reason}` : "."} Se muestran las
          entidades declaradas en el cartucho.
        </Notice>
      )}
      <ul className="grid grid-cols-1 gap-2 md:grid-cols-2">
        {(result.entities ?? []).map((entity, index) => {
          const name = String(entity.entity || entity.name || `entidad_${index + 1}`);
          const fields = Array.isArray(entity.fields) ? entity.fields : [];
          return (
            <li key={`${name}-${index}`} className="rounded-md border bg-background p-2 text-xs">
              <p className="font-mono font-medium">{name}</p>
              <p className="text-muted-foreground">
                {fields.length} campos{entity.primary_key ? ` · PK ${entity.primary_key}` : ""}
              </p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function UploadResultView({ result }: { result: UploadSpecResult }) {
  return (
    <Notice
      tone={result.accepted ? "success" : "error"}
      title={result.accepted ? `Spec aceptada: ${result.accepted_count} entidades` : "La spec no se aceptó"}
    >
      {result.entities?.length ? <p className="font-mono">{result.entities.join(", ")}</p> : null}
      {result.error ? <p>{result.error}</p> : null}
      {result.errors?.length ? (
        <ul className="mt-1 list-disc pl-4">
          {result.errors.slice(0, 8).map((item, index) => (
            <li key={index}>{typeof item === "string" ? item : JSON.stringify(item)}</li>
          ))}
        </ul>
      ) : null}
    </Notice>
  );
}

const EMPTY_NEW = { entity: "", display_name: "", mode: "full", primary_key: "", dag_id: "", description: "" };

export function EntitiesPanel({ cartridge, manifest }: { cartridge: string; manifest?: StudioManifest }) {
  const entities = useStudioEntities(cartridge);
  const update = useUpdateEntity();
  const renameMutation = useRenameEntity();
  const create = useCreateEntity();
  const upload = useUploadEntitySpec();
  const introspect = useIntrospectSource();
  const fileRef = useRef<HTMLInputElement | null>(null);

  const [editing, setEditing] = useState<{ name: string; before: EditDraft; draft: EditDraft } | null>(null);
  const [renaming, setRenaming] = useState<{ name: string; value: string } | null>(null);
  const [schemaFor, setSchemaFor] = useState<string | null>(null);
  const [extracting, setExtracting] = useState<string | null>(null);
  const [launches, setLaunches] = useState<Record<string, ExtractionLaunch>>({});
  const [creating, setCreating] = useState(false);
  const [newEntity, setNewEntity] = useState(EMPTY_NEW);
  const [uploadResult, setUploadResult] = useState<UploadSpecResult | null>(null);
  const newEntityError = newEntity.entity ? identifierError(newEntity.entity, "La entidad") : null;
  const rows = entities.data?.entities ?? [];

  function startEdit(entity: StudioEntity) {
    const before = draftFor(entity, manifestEntity(manifest, entity.name));
    setEditing({ name: entity.name, before, draft: before });
    setRenaming(null);
  }

  function saveEdit() {
    if (!editing) return;
    const patch = changedEntityFields(editing.before, editing.draft);
    if (!Object.keys(patch).length) {
      setEditing(null);
      return;
    }
    update.mutate(
      { cartridge, entity: editing.name, patch },
      {
        onSuccess: () => {
          toast.success(`Entidad ${editing.name} actualizada.`);
          setEditing(null);
        },
        onError: (error) => toast.error(studioErrorMessage(error, "No se pudo actualizar la entidad.")),
      },
    );
  }

  function saveRename() {
    if (!renaming) return;
    const next = renaming.value.trim();
    const problem = identifierError(next, "El nombre");
    if (problem) {
      toast.error(problem);
      return;
    }
    if (next === renaming.name) {
      setRenaming(null);
      return;
    }
    renameMutation.mutate(
      { cartridge, entity: renaming.name, newName: next },
      {
        onSuccess: (result) => {
          if (result?.renamed) toast.success(`Entidad renombrada: ${renaming.name} → ${next}.`);
          else toast.info(result?.reason || "Sin cambios.");
          setRenaming(null);
        },
        onError: (error) => toast.error(studioErrorMessage(error, "No se pudo renombrar la entidad.")),
      },
    );
  }

  async function runExtraction(entity: StudioEntity) {
    setExtracting(entity.name);
    try {
      const launch = await startExtraction(cartridge, entity.name, extractionMode(entity.mode));
      setLaunches((current) => ({ ...current, [entity.name]: launch }));
      toast.success(`Extracción enviada para ${entity.name}.`);
    } catch (error) {
      toast.error(studioErrorMessage(error, `No se pudo extraer ${entity.name}.`));
    } finally {
      setExtracting(null);
    }
  }

  function submitNewEntity() {
    const problem = identifierError(newEntity.entity, "La entidad");
    if (problem) {
      toast.error(problem);
      return;
    }
    create.mutate(
      {
        cartridge,
        entity: newEntity.entity.trim(),
        display_name: newEntity.display_name.trim() || undefined,
        mode: newEntity.mode,
        primary_key: newEntity.primary_key.trim() || undefined,
        dag_id: newEntity.dag_id.trim() || undefined,
        description: newEntity.description.trim() || undefined,
      },
      {
        onSuccess: () => {
          toast.success(`Entidad ${newEntity.entity.trim()} creada.`);
          setCreating(false);
          setNewEntity(EMPTY_NEW);
        },
        onError: (error) => toast.error(studioErrorMessage(error, "No se pudo crear la entidad.")),
      },
    );
  }

  function handleSpec(file: File | undefined) {
    if (!file) return;
    if (file.size > MAX_SPEC_BYTES) {
      toast.error("La spec supera el máximo de 2 MB.");
      return;
    }
    upload.mutate(
      { cartridge, file },
      {
        onSuccess: (result) => {
          setUploadResult(result);
          if (result.accepted) toast.success(`Spec aceptada: ${result.accepted_count} entidades.`);
          else toast.error(result.error || "La spec tiene errores.");
        },
        onError: (error) => toast.error(studioErrorMessage(error, "No se pudo subir la spec.")),
      },
    );
  }

  function runIntrospection() {
    introspect.mutate(cartridge, {
      onError: (error) => toast.error(studioErrorMessage(error, "No se pudo introspeccionar la fuente.")),
    });
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold">Entidades ({entities.data?.total ?? 0})</h3>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={buttonClass} onClick={() => entities.refetch()} aria-label="Recargar entidades">
            <RefreshCw aria-hidden className={cn("h-4 w-4", entities.isFetching && "animate-spin")} />
          </button>
          <button type="button" className={buttonClass} onClick={() => setCreating(true)}>
            <Plus aria-hidden className="h-4 w-4" /> Nueva entidad
          </button>
          <button
            type="button"
            className={buttonClass}
            onClick={() => fileRef.current?.click()}
            disabled={upload.isPending}
          >
            {upload.isPending ? <Spinner /> : <Upload aria-hidden className="h-4 w-4" />} Subir spec
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".yaml,.yml,.json,.xml,.edmx"
            className="sr-only"
            aria-label="Archivo de spec OpenAPI, YAML u OData (máximo 2 MB)"
            onChange={(event) => {
              handleSpec(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
          <button type="button" className={buttonClass} onClick={runIntrospection} disabled={introspect.isPending}>
            {introspect.isPending ? <Spinner /> : <ScanSearch aria-hidden className="h-4 w-4" />} Introspeccionar fuente
          </button>
        </div>
      </div>

      {uploadResult ? <UploadResultView result={uploadResult} /> : null}
      {introspect.data ? <IntrospectionView result={introspect.data} /> : null}

      {entities.isLoading ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground"><Spinner /> Cargando entidades…</p>
      ) : entities.isError ? (
        <Notice
          tone="error"
          title="No se pudieron cargar las entidades."
          action={<button type="button" className={buttonClass} onClick={() => entities.refetch()}>Reintentar</button>}
        >
          {studioErrorMessage(entities.error, "Error al consultar /api/studio/entities.")}
        </Notice>
      ) : !rows.length ? (
        <Notice testId="entities-empty">El cartucho no tiene entidades registradas.</Notice>
      ) : (
        <div className="overflow-x-auto rounded-lg border">
          <table data-testid="entities-table" className="w-full text-sm">
            <thead className="bg-muted/40 text-left text-xs uppercase text-muted-foreground">
              <tr>
                <th scope="col" className="px-3 py-2 font-medium">Entidad</th>
                <th scope="col" className="px-3 py-2 font-medium">Modo</th>
                <th scope="col" className="px-3 py-2 font-medium">DAG</th>
                <th scope="col" className="px-3 py-2 font-medium">Origen</th>
                <th scope="col" className="px-3 py-2 text-right font-medium">Acciones</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((entity) => {
                const isEditing = editing?.name === entity.name;
                const isRenaming = renaming?.name === entity.name;
                const launch = launches[entity.name];
                return [
                  <tr key={entity.name} className="border-t align-top">
                    <td className="px-3 py-2">
                      <p className="font-mono text-xs font-medium">{entity.name}</p>
                      {entity.display_name && entity.display_name !== entity.name ? (
                        <p className="text-xs text-muted-foreground">{entity.display_name}</p>
                      ) : null}
                      {entity.description ? <p className="text-xs text-muted-foreground">{entity.description}</p> : null}
                    </td>
                    <td className="px-3 py-2 text-xs">{entity.mode || "—"}</td>
                    <td className="break-all px-3 py-2 font-mono text-xs">{entity.dag_id || "—"}</td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">{entity.source || "—"}</td>
                    <td className="px-3 py-2">
                      <div className="flex flex-wrap justify-end gap-1">
                        <button type="button" className={buttonClass} onClick={() => startEdit(entity)} aria-label={`Editar ${entity.name}`}>
                          <Pencil aria-hidden className="h-4 w-4" /> Editar
                        </button>
                        <button
                          type="button"
                          className={buttonClass}
                          onClick={() => {
                            setRenaming({ name: entity.name, value: entity.name });
                            setEditing(null);
                          }}
                          aria-label={`Renombrar ${entity.name}`}
                        >
                          <Type aria-hidden className="h-4 w-4" /> Renombrar
                        </button>
                        <button
                          type="button"
                          className={buttonClass}
                          aria-expanded={schemaFor === entity.name}
                          onClick={() => setSchemaFor((current) => (current === entity.name ? null : entity.name))}
                          aria-label={`Esquema de ${entity.name}`}
                        >
                          <Table2 aria-hidden className="h-4 w-4" /> Esquema
                        </button>
                        <button
                          type="button"
                          className={buttonClass}
                          onClick={() => runExtraction(entity)}
                          disabled={extracting === entity.name}
                          aria-label={`Extraer ahora ${entity.name}`}
                        >
                          {extracting === entity.name ? <Spinner /> : <Play aria-hidden className="h-4 w-4" />} Extraer ahora
                        </button>
                      </div>
                    </td>
                  </tr>,
                  isEditing && editing ? (
                    <tr key={`${entity.name}-edit`} className="border-t bg-muted/10">
                      <td colSpan={5} className="px-3 py-3">
                        <form
                          aria-label={`Editar entidad ${entity.name}`}
                          className="grid grid-cols-1 gap-3 md:grid-cols-3"
                          onSubmit={(event) => {
                            event.preventDefault();
                            saveEdit();
                          }}
                        >
                          {(["display_name", "primary_key", "dag_id", "cron_expression", "description"] as const).map((field) => (
                            <label key={field} className="flex flex-col gap-1 text-xs">
                              <span className="font-medium">
                                {{
                                  display_name: "Nombre visible",
                                  primary_key: "Llave primaria",
                                  dag_id: "DAG",
                                  cron_expression: "Cron (vacío = manual)",
                                  description: "Descripción",
                                }[field]}
                              </span>
                              <input
                                name={field}
                                value={editing.draft[field]}
                                onChange={(event) =>
                                  setEditing((current) =>
                                    current ? { ...current, draft: { ...current.draft, [field]: event.target.value } } : current,
                                  )
                                }
                                className={inputClass}
                              />
                            </label>
                          ))}
                          <label className="flex flex-col gap-1 text-xs">
                            <span className="font-medium">Modo</span>
                            <select
                              name="mode"
                              value={editing.draft.mode}
                              onChange={(event) =>
                                setEditing((current) =>
                                  current ? { ...current, draft: { ...current.draft, mode: event.target.value } } : current,
                                )
                              }
                              className={inputClass}
                            >
                              {[...new Set([...MODES, editing.draft.mode])].map((mode) => (
                                <option key={mode} value={mode}>{mode}</option>
                              ))}
                            </select>
                          </label>
                          <div className="flex items-end gap-2 md:col-span-3">
                            <button type="submit" className={primaryButtonClass} disabled={update.isPending}>
                              {update.isPending ? <Spinner /> : null} Guardar cambios
                            </button>
                            <button type="button" className={buttonClass} onClick={() => setEditing(null)}>Cancelar</button>
                          </div>
                        </form>
                      </td>
                    </tr>
                  ) : null,
                  isRenaming && renaming ? (
                    <tr key={`${entity.name}-rename`} className="border-t bg-muted/10">
                      <td colSpan={5} className="px-3 py-3">
                        <form
                          className="flex flex-col gap-2 sm:flex-row sm:items-end"
                          onSubmit={(event) => {
                            event.preventDefault();
                            saveRename();
                          }}
                        >
                          <label className="flex flex-1 flex-col gap-1 text-xs">
                            <span className="font-medium">Nuevo nombre de {entity.name}</span>
                            <input
                              name="rename-entity"
                              value={renaming.value}
                              onChange={(event) => setRenaming({ name: entity.name, value: event.target.value })}
                              className={inputClass}
                            />
                          </label>
                          <button type="submit" className={primaryButtonClass} disabled={renameMutation.isPending}>
                            {renameMutation.isPending ? <Spinner /> : null} Renombrar
                          </button>
                          <button type="button" className={buttonClass} onClick={() => setRenaming(null)}>Cancelar</button>
                        </form>
                      </td>
                    </tr>
                  ) : null,
                  schemaFor === entity.name ? (
                    <tr key={`${entity.name}-schema`} className="border-t">
                      <td colSpan={5} className="px-3 py-3">
                        <SchemaPreview cartridge={cartridge} entity={entity.name} />
                      </td>
                    </tr>
                  ) : null,
                  launch ? (
                    <tr key={`${entity.name}-run`} className="border-t">
                      <td colSpan={5} className="px-3 py-3">
                        <ExtractionTracker cartridge={cartridge} launch={launch} />
                      </td>
                    </tr>
                  ) : null,
                ];
              })}
            </tbody>
          </table>
        </div>
      )}

      <ConfirmDialog
        open={creating}
        title="Nueva entidad"
        description={`Se registrará en el cartucho ${cartridge}.`}
        confirmLabel="Crear entidad"
        pendingLabel="Creando…"
        pending={create.isPending}
        confirmDisabled={!newEntity.entity.trim() || Boolean(newEntityError)}
        onConfirm={submitNewEntity}
        onCancel={() => {
          setCreating(false);
          setNewEntity(EMPTY_NEW);
        }}
        testId="new-entity-dialog"
      >
        <label className="flex flex-col gap-1">
          <span className="font-medium">Entidad</span>
          <input
            name="entity"
            value={newEntity.entity}
            onChange={(event) => setNewEntity((current) => ({ ...current, entity: event.target.value }))}
            className={inputClass}
            aria-invalid={Boolean(newEntityError)}
          />
          {newEntityError ? <span className="text-xs text-destructive">{newEntityError}</span> : null}
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-medium">Nombre visible</span>
          <input
            value={newEntity.display_name}
            onChange={(event) => setNewEntity((current) => ({ ...current, display_name: event.target.value }))}
            className={inputClass}
          />
        </label>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="flex flex-col gap-1">
            <span className="font-medium">Modo</span>
            <select
              name="mode"
              value={newEntity.mode}
              onChange={(event) => setNewEntity((current) => ({ ...current, mode: event.target.value }))}
              className={inputClass}
            >
              {MODES.map((mode) => <option key={mode} value={mode}>{mode}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="font-medium">Llave primaria</span>
            <input
              value={newEntity.primary_key}
              onChange={(event) => setNewEntity((current) => ({ ...current, primary_key: event.target.value }))}
              className={inputClass}
            />
          </label>
        </div>
        <label className="flex flex-col gap-1">
          <span className="font-medium">DAG</span>
          <input
            value={newEntity.dag_id}
            onChange={(event) => setNewEntity((current) => ({ ...current, dag_id: event.target.value }))}
            className={inputClass}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-medium">Descripción</span>
          <input
            value={newEntity.description}
            onChange={(event) => setNewEntity((current) => ({ ...current, description: event.target.value }))}
            className={inputClass}
          />
        </label>
      </ConfirmDialog>
    </div>
  );
}
