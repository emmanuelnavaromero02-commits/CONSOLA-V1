"use client";

import { ExternalLink, FilePlus2, Pencil, RefreshCw, Rocket, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { airflowDagUrl, studioErrorMessage } from "@/lib/studio/client";
import {
  useDagSource,
  useDagTemplates,
  useDeleteDag,
  useDeployDag,
  useRenameDag,
  useRuntimeConfig,
  useStudioDags,
  useSystemInfo,
} from "@/lib/studio/hooks";
import type { StudioDag, StudioManifest } from "@/lib/studio/types";
import { dagIdError, deployGate, managedDagIds } from "@/lib/studio/validation";
import { cn } from "@/lib/utils";

import { DeployDialog, notifyDeployResult, type DeployRequest } from "./DeployDialog";
import {
  buttonClass,
  codeAreaClass,
  ConfirmDialog,
  dangerButtonClass,
  inputClass,
  Notice,
  primaryButtonClass,
  Spinner,
} from "./ui";

const NEW_DAG = "__new__";
export const PACKAGED_DAG_REASON = "DAG empaquetado por el cartucho: se gestiona desde el cartucho, no desde Studio.";

function dagState(dag: StudioDag): { label: string; tone: string } {
  if (dag.is_paused) return { label: "Pausado", tone: "border-warning/30 bg-warning/10 text-warning" };
  if (dag.is_active) return { label: "Activo", tone: "border-success/30 bg-success/10 text-success" };
  return { label: "Inactivo", tone: "border-border bg-muted text-muted-foreground" };
}

function entityForDag(manifest: StudioManifest | undefined, dagId: string | null): string {
  const entities = manifest?.entities ?? [];
  const match = entities.find((entity) => dagId && entity?.dag_id === dagId);
  return String(match?.entity || match?.name || entities[0]?.entity || entities[0]?.name || "");
}

export function DagsPanel({ cartridge, manifest }: { cartridge: string; manifest?: StudioManifest }) {
  const dags = useStudioDags(cartridge);
  const templates = useDagTemplates();
  const system = useSystemInfo();
  const config = useRuntimeConfig();
  const deploy = useDeployDag();
  const rename = useRenameDag();
  const remove = useDeleteDag();

  const [selected, setSelected] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [newDagId, setNewDagId] = useState("");
  const [entityChoice, setEntityChoice] = useState<Record<string, string>>({});
  const [templateId, setTemplateId] = useState("");
  const [renameValue, setRenameValue] = useState<string | null>(null);
  const [pendingDeploy, setPendingDeploy] = useState<DeployRequest | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const isNew = selected === NEW_DAG;
  const existingId = selected && !isNew ? selected : null;
  const source = useDagSource(cartridge, existingId);
  const managed = useMemo(() => managedDagIds(manifest), [manifest]);
  const gate = deployGate(system.data, system.isError);
  const entities = (manifest?.entities ?? [])
    .map((entity) => String(entity?.entity || entity?.name || ""))
    .filter(Boolean);
  const editorKey = selected ?? "";
  const code = drafts[editorKey] ?? (existingId ? source.data?.source_code ?? "" : "");
  const entity = entityChoice[editorKey] ?? entityForDag(manifest, existingId);
  const targetDagId = isNew ? newDagId.trim() : existingId ?? "";
  const isPackaged = Boolean(existingId && managed.has(existingId));
  const selectedDag = dags.data?.dags.find((dag) => dag.dag_id === existingId);
  const template = templates.data?.find((item) => item.id === templateId);
  const airflowUrl = existingId ? airflowDagUrl(config.data?.airflow_url, existingId) : null;
  const newIdProblem = isNew && newDagId ? dagIdError(newDagId, cartridge) : null;

  const deployBlockReason = !gate.enabled
    ? gate.reason
    : isPackaged
      ? PACKAGED_DAG_REASON
      : !selected
        ? "Selecciona un DAG o crea uno nuevo."
        : null;

  function selectDag(id: string) {
    setSelected(id);
    setRenameValue(null);
  }

  function requestDeploy() {
    if (deployBlockReason) {
      toast.error(deployBlockReason);
      return;
    }
    const problem = isNew ? dagIdError(newDagId, cartridge) : null;
    if (problem) {
      toast.error(problem);
      return;
    }
    if (!entity) {
      toast.error("Selecciona la entidad del DAG.");
      return;
    }
    const useTemplate = isNew && Boolean(templateId);
    if (!useTemplate && !code.trim()) {
      toast.error("El editor está vacío: escribe el código o elige una plantilla.");
      return;
    }
    setPendingDeploy({
      cartridge,
      entity,
      dag_id: targetDagId,
      ...(useTemplate ? { template_id: templateId, templateName: template?.name || templateId } : { code }),
      description: `DAG del cartucho ${cartridge}`,
    });
  }

  function requestRename() {
    if (!existingId || renameValue === null) return;
    const next = renameValue.trim();
    if (next === existingId) {
      setRenameValue(null);
      return;
    }
    const problem = dagIdError(next, cartridge);
    if (problem) {
      toast.error(problem);
      return;
    }
    if (!code.trim()) {
      toast.error("No hay código fuente para renombrar este DAG.");
      return;
    }
    setPendingDeploy({ cartridge, entity: entity || "Entity", dag_id: next, code, renameFrom: existingId });
  }

  function confirmDeploy() {
    const request = pendingDeploy;
    if (!request) return;
    if (request.renameFrom) {
      const oldId = request.renameFrom;
      rename.mutate(
        { cartridge, entity: request.entity, oldId, newId: request.dag_id, code: request.code ?? "" },
        {
          onSuccess: ({ deploy: result, deleted }) => {
            if (result.status === "deployed" && deleted) {
              toast.success(`DAG renombrado: ${oldId} → ${request.dag_id}.`);
              setSelected(request.dag_id);
              setRenameValue(null);
            } else {
              notifyDeployResult(result, request.dag_id);
            }
          },
          onError: (error) => toast.error(studioErrorMessage(error, "No se pudo renombrar el DAG.")),
          onSettled: () => setPendingDeploy(null),
        },
      );
      return;
    }
    deploy.mutate(request, {
      onSuccess: (result) => {
        notifyDeployResult(result, request.dag_id);
        if (result.status === "deployed") {
          setDrafts((current) => {
            const next = { ...current };
            delete next[editorKey];
            return next;
          });
          if (isNew) {
            setSelected(request.dag_id);
            setNewDagId("");
          }
        }
      },
      onError: (error) => toast.error(studioErrorMessage(error, "No se pudo desplegar el DAG.")),
      onSettled: () => setPendingDeploy(null),
    });
  }

  function confirmDelete() {
    const dagId = pendingDelete;
    if (!dagId) return;
    remove.mutate(
      { cartridge, dagId },
      {
        onSuccess: () => {
          toast.success(`DAG ${dagId} eliminado.`);
          setSelected(null);
        },
        onError: (error) => toast.error(studioErrorMessage(error, "No se pudo eliminar el DAG.")),
        onSettled: () => setPendingDelete(null),
      },
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
      <section aria-label="DAGs del cartucho" className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-sm font-semibold">DAGs ({dags.data?.total ?? 0})</h3>
          <div className="flex gap-2">
            <button type="button" className={buttonClass} onClick={() => dags.refetch()} aria-label="Recargar DAGs">
              <RefreshCw aria-hidden className={cn("h-4 w-4", dags.isFetching && "animate-spin")} />
            </button>
            <button type="button" className={buttonClass} onClick={() => selectDag(NEW_DAG)}>
              <FilePlus2 aria-hidden className="h-4 w-4" /> Nuevo DAG
            </button>
          </div>
        </div>
        {dags.isLoading ? (
          <p className="flex items-center gap-2 text-sm text-muted-foreground"><Spinner /> Consultando Airflow…</p>
        ) : dags.isError ? (
          <Notice
            tone="error"
            testId="dags-error"
            title="Airflow no disponible"
            action={
              <button type="button" className={buttonClass} onClick={() => dags.refetch()}>Reintentar</button>
            }
          >
            {studioErrorMessage(dags.error, "No se pudo listar los DAGs.")}
          </Notice>
        ) : !dags.data?.dags.length ? (
          <Notice testId="dags-empty">Airflow no reporta DAGs para este cartucho.</Notice>
        ) : (
          <ul data-testid="dag-list" className="max-h-[520px] space-y-1 overflow-y-auto">
            {dags.data.dags.map((dag) => {
              const state = dagState(dag);
              const active = dag.dag_id === selected;
              return (
                <li key={dag.dag_id}>
                  <button
                    type="button"
                    data-dag-id={dag.dag_id}
                    aria-pressed={active}
                    onClick={() => selectDag(dag.dag_id)}
                    className={cn(
                      "flex min-h-[44px] w-full flex-col items-start gap-1 rounded-md border px-3 py-2 text-left text-sm",
                      "hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      active && "border-primary bg-primary/5",
                    )}
                  >
                    <span className="break-all font-mono text-xs">{dag.dag_id}</span>
                    <span className="flex flex-wrap gap-1">
                      <span className={cn("rounded-full border px-2 py-0.5 text-[11px]", state.tone)}>{state.label}</span>
                      {dag.registered_only ? (
                        <span className="rounded-full border px-2 py-0.5 text-[11px] text-muted-foreground">
                          Solo manifiesto · Airflow no lo reporta
                        </span>
                      ) : null}
                      {managed.has(dag.dag_id) ? (
                        <span className="rounded-full border px-2 py-0.5 text-[11px] text-muted-foreground">Empaquetado</span>
                      ) : null}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section aria-label="Editor de DAG" data-testid="dag-editor" className="space-y-3 rounded-lg border bg-card p-4">
        {!selected ? (
          <Notice>Selecciona un DAG para ver su código o crea uno nuevo.</Notice>
        ) : (
          <>
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <p className="text-xs uppercase text-muted-foreground">{isNew ? "Nuevo DAG" : "DAG seleccionado"}</p>
                <h3 className="break-all font-mono text-sm font-semibold">{isNew ? newDagId || "—" : existingId}</h3>
                {selectedDag?.registered_only ? (
                  <p className="text-xs text-muted-foreground">Declarado en el manifiesto; Airflow no lo reporta.</p>
                ) : null}
              </div>
              {airflowUrl ? (
                <a
                  href={airflowUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  data-testid="dag-airflow-link"
                  title="Ver en Airflow UI"
                  className={buttonClass}
                >
                  <ExternalLink aria-hidden className="h-4 w-4" /> Ver en Airflow
                </a>
              ) : existingId && config.isSuccess ? (
                <span className="text-xs text-muted-foreground">Airflow sin URL pública configurada.</span>
              ) : null}
            </div>

            {isNew ? (
              <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                <label className="flex flex-col gap-1 text-sm">
                  <span className="font-medium">dag_id</span>
                  <input
                    value={newDagId}
                    onChange={(event) => setNewDagId(event.target.value)}
                    placeholder={`${cartridge}_entidad_full`}
                    className={inputClass}
                    aria-invalid={Boolean(newIdProblem)}
                  />
                  {newIdProblem ? <span className="text-xs text-destructive">{newIdProblem}</span> : null}
                </label>
                <label className="flex flex-col gap-1 text-sm">
                  <span className="font-medium">Plantilla</span>
                  <select
                    value={templateId}
                    onChange={(event) => setTemplateId(event.target.value)}
                    className={inputClass}
                    data-testid="templates"
                  >
                    <option value="">Sin plantilla (usar el editor)</option>
                    {(templates.data ?? []).map((item) => (
                      <option key={item.id} value={item.id}>{item.name || item.id}</option>
                    ))}
                  </select>
                  {templates.isError ? (
                    <span className="text-xs text-destructive">No se pudieron cargar las plantillas.</span>
                  ) : template?.description ? (
                    <span className="text-xs text-muted-foreground">{template.description}</span>
                  ) : null}
                </label>
              </div>
            ) : null}

            <label className="flex flex-col gap-1 text-sm md:max-w-sm">
              <span className="font-medium">Entidad</span>
              <select
                value={entity}
                onChange={(event) => setEntityChoice((current) => ({ ...current, [editorKey]: event.target.value }))}
                className={inputClass}
              >
                {!entities.length ? <option value="">Sin entidades en el manifiesto</option> : null}
                {entities.map((name) => (
                  <option key={name} value={name}>{name}</option>
                ))}
              </select>
            </label>

            {existingId && source.isLoading ? (
              <p className="flex items-center gap-2 text-sm text-muted-foreground"><Spinner /> Cargando código fuente…</p>
            ) : existingId && source.isError ? (
              <Notice tone="error">{studioErrorMessage(source.error, "No se pudo leer el código del DAG.")}</Notice>
            ) : existingId && source.data && !source.data.found ? (
              <Notice tone="warning" title="Código fuente no encontrado">
                {source.data.error || "mcp-infra no devolvió el archivo de este DAG."}
              </Notice>
            ) : null}

            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium">Código Python</span>
              <textarea
                name="code"
                value={code}
                onChange={(event) => setDrafts((current) => ({ ...current, [editorKey]: event.target.value }))}
                rows={18}
                spellCheck={false}
                disabled={isNew && Boolean(templateId)}
                placeholder={isNew && templateId ? "El código se genera desde la plantilla en el servidor." : "Código del DAG"}
                className={cn(codeAreaClass, "code-editor")}
              />
              {source.data?.path ? (
                <span className="break-all text-xs text-muted-foreground">Archivo: {source.data.path}</span>
              ) : null}
            </label>

            {renameValue !== null ? (
              <form
                className="flex flex-col gap-2 rounded-md border bg-muted/20 p-3 sm:flex-row sm:items-end"
                onSubmit={(event) => {
                  event.preventDefault();
                  requestRename();
                }}
              >
                <label className="flex flex-1 flex-col gap-1 text-sm">
                  <span className="font-medium">Nuevo nombre</span>
                  <input
                    name="rename-dag"
                    data-testid="rename-input"
                    value={renameValue}
                    onChange={(event) => setRenameValue(event.target.value)}
                    className={inputClass}
                    autoFocus
                  />
                </label>
                <button type="submit" className={primaryButtonClass} disabled={Boolean(deployBlockReason)}>
                  Continuar
                </button>
                <button type="button" className={buttonClass} onClick={() => setRenameValue(null)}>Cancelar</button>
              </form>
            ) : null}

            {deployBlockReason ? (
              <p data-testid="deploy-disabled-reason" className="text-xs text-muted-foreground">{deployBlockReason}</p>
            ) : null}
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={requestDeploy}
                disabled={Boolean(deployBlockReason) || deploy.isPending}
                title={deployBlockReason ?? "Desplegar en Airflow"}
                data-disabled-reason={deployBlockReason ?? ""}
                className={primaryButtonClass}
              >
                {deploy.isPending ? <Spinner /> : <Rocket aria-hidden className="h-4 w-4" />} Deploy a Airflow
              </button>
              {existingId ? (
                <>
                  <button
                    type="button"
                    className={buttonClass}
                    disabled={Boolean(deployBlockReason) || !code.trim()}
                    title={deployBlockReason ?? (code.trim() ? "Renombrar DAG" : "Sin código fuente para renombrar")}
                    onClick={() => setRenameValue(existingId)}
                  >
                    <Pencil aria-hidden className="h-4 w-4" /> Renombrar
                  </button>
                  <button
                    type="button"
                    className={dangerButtonClass}
                    disabled={!gate.enabled}
                    title={gate.reason ?? "Eliminar DAG"}
                    onClick={() => setPendingDelete(existingId)}
                  >
                    <Trash2 aria-hidden className="h-4 w-4" /> Eliminar
                  </button>
                </>
              ) : null}
            </div>
          </>
        )}
      </section>

      <DeployDialog
        request={pendingDeploy}
        pending={deploy.isPending || rename.isPending}
        onConfirm={confirmDeploy}
        onCancel={() => setPendingDeploy(null)}
      />
      <ConfirmDialog
        open={Boolean(pendingDelete)}
        title="Eliminar DAG"
        tone="danger"
        confirmLabel="Eliminar"
        pendingLabel="Eliminando…"
        pending={remove.isPending}
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
        testId="delete-dag-dialog"
        description={`Se eliminará ${pendingDelete ?? ""} de Airflow para el cartucho ${cartridge}. Esta acción no se puede deshacer.`}
      >
        {pendingDelete && managed.has(pendingDelete) ? (
          <Notice tone="warning">
            Este DAG está empaquetado por el cartucho; eliminarlo detiene las extracciones que dependen de él.
          </Notice>
        ) : null}
      </ConfirmDialog>
    </div>
  );
}
