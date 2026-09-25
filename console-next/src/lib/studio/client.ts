import {
  api,
  apiFetch,
  isApiError,
  publicErrorMessage,
  toApiError,
} from "@/lib/api";
import { postSseStream, type SseStreamOptions } from "@/lib/copilot/client";

import type {
  CartridgeProbe,
  CreateCartridgeInput,
  CreateEntityInput,
  CreateEntityResult,
  DagGraphPayload,
  DagSourcePayload,
  DagTemplate,
  DeleteDagResult,
  DeleteDatasetResult,
  DeployDagInput,
  DeployDagResult,
  EntityPatch,
  EntityRenameResult,
  EntityUpdateResult,
  IntrospectionResult,
  LayerPreviewPayload,
  RefreshDatasetResult,
  RuntimeConfig,
  SaveDatasetInput,
  SaveDatasetResult,
  StudioCartridgeSummary,
  StudioChatHandlers,
  StudioChatInput,
  StudioChatResult,
  StudioDagsPayload,
  StudioEntitiesPayload,
  StudioLayer,
  StudioManifest,
  SupersetDatasetResult,
  SystemInfo,
  UploadSpecResult,
} from "./types";

export const MAX_SPEC_BYTES = 2 * 1024 * 1024;
export const MAX_IMPORT_ZIP_BYTES = 25 * 1024 * 1024;

export const SUPERSET_INTERNAL_ONLY_COPY =
  "Superset está disponible solo internamente por seguridad. Solicita acceso interno/VPN para abrir dashboards.";

const enc = encodeURIComponent;

function withQuery(path: string, params: Record<string, string | number | null | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    query.set(key, String(value));
  }
  const text = query.toString();
  return text ? `${path}?${text}` : path;
}

export const studioPaths = {
  cartridges: () => "/studio/cartridges",
  cartridge: (id: string) => `/studio/cartridges/${enc(id)}`,
  cartridgeStatus: (id: string) => `/studio/cartridges/${enc(id)}/status`,
  cartridgeExport: (id: string) => `/studio/cartridges/${enc(id)}/export`,
  cartridgeImport: () => "/studio/import",
  dagGraph: (cartridge: string) => withQuery("/api/studio/dag-graph", { cartridge }),
  dags: (cartridge: string) => withQuery("/api/studio/dags", { cartridge }),
  dagSource: (cartridge: string, dagId: string) =>
    withQuery(`/api/studio/dags/${enc(dagId)}/source`, { cartridge }),
  dag: (cartridge: string, dagId: string) => withQuery(`/api/studio/dags/${enc(dagId)}`, { cartridge }),
  templates: () => "/api/studio/templates",
  deploy: () => "/api/studio/dag-deploy",
  layerPreview: (layer: StudioLayer, cartridge: string, dataset: string, limit: number) =>
    withQuery(`/api/studio/${layer}/preview`, { cartridge, dataset, limit }),
  superset: () => "/api/studio/superset/dataset",
  entities: (cartridge: string) => withQuery("/api/studio/entities", { cartridge }),
  entity: (cartridge: string, entity: string) =>
    `/studio/cartridges/${enc(cartridge)}/entities/${enc(entity)}`,
  entityRename: (cartridge: string, entity: string) =>
    `/studio/cartridges/${enc(cartridge)}/entities/${enc(entity)}/rename`,
  newEntity: () => "/api/studio/entity",
  entitiesUpload: (cartridge: string) => withQuery("/api/studio/entities/upload", { cartridge }),
  introspect: () => "/api/studio/introspect-source",
  systemInfo: () => "/api/system/info",
  config: () => "/api/config",
  datasetSave: () => "/api/datasets/save",
  datasetRefresh: (name: string) => `/datasets/${enc(name)}/refresh`,
  datasetDelete: (name: string) => withQuery("/api/datasets", { name }),
  chatStream: () => "/studio/chat/stream",
} as const;

function safeText(value: unknown, max = 240): string | null {
  if (typeof value !== "string") return null;
  const flat = value.replace(/\s+/g, " ").trim();
  if (!flat) return null;
  return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

async function readPayload(response: Response): Promise<unknown> {
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) return response.json().catch(() => null);
  const text = await response.text().catch(() => "");
  return text || null;
}

async function ensureOk(response: Response): Promise<unknown> {
  const requestId = response.headers.get("x-request-id") || undefined;
  const payload = await readPayload(response);
  if (!response.ok) {
    throw toApiError(publicErrorMessage(response.status, payload, requestId), response.status, payload, requestId);
  }
  return payload;
}

export function assertFileSize(file: File, maxBytes: number): void {
  if (file.size > maxBytes) {
    const mb = Math.round((maxBytes / (1024 * 1024)) * 10) / 10;
    throw toApiError(`El archivo supera el máximo de ${mb} MB.`);
  }
}

export function studioErrorMessage(error: unknown, fallback: string): string {
  if (isApiError(error)) {
    if (error.status === 504) return "Airflow no respondió a tiempo (HTTP 504). Reintenta en unos segundos.";
    if (error.status === 413) return "El archivo supera el tamaño máximo que acepta el servidor.";
  }
  if (error instanceof Error && error.message) return error.message;
  return fallback;
}

export function supersetErrorMessage(error: unknown): string {
  if (isApiError(error) && error.status === 503) return SUPERSET_INTERNAL_ONLY_COPY;
  return studioErrorMessage(error, "No se pudo crear el dataset en Superset.");
}

export async function listStudioCartridges(): Promise<StudioCartridgeSummary[]> {
  const { data } = await api.get<{ cartridges?: StudioCartridgeSummary[] }>(studioPaths.cartridges());
  return Array.isArray(data?.cartridges) ? data.cartridges.filter((item) => Boolean(item?.id)) : [];
}

export async function getStudioCartridge(id: string): Promise<StudioManifest> {
  const { data } = await api.get<StudioManifest>(studioPaths.cartridge(id));
  return data;
}

export async function getCartridgeStatus(id: string): Promise<CartridgeProbe> {
  const { data } = await api.get<CartridgeProbe>(studioPaths.cartridgeStatus(id));
  return data;
}

export async function createCartridge(input: CreateCartridgeInput): Promise<StudioManifest> {
  const { data } = await api.post<StudioManifest>(studioPaths.cartridges(), {
    id: input.id.trim(),
    name: input.name.trim(),
    description: input.description.trim(),
  });
  return data;
}

export function exportFilename(disposition: string | null, id: string): string {
  const match = disposition?.match(/filename="?([^";]+)"?/i);
  const raw = match?.[1] ?? `${id}.zip`;
  const clean = raw.replace(/[^A-Za-z0-9_.-]/g, "_");
  return clean.toLowerCase().endsWith(".zip") ? clean : `${clean}.zip`;
}

export async function exportCartridge(id: string): Promise<{ blob: Blob; filename: string }> {
  const response = await apiFetch(studioPaths.cartridgeExport(id), {
    headers: { Accept: "application/zip" },
    timeoutMs: 120_000,
  });
  if (!response.ok) await ensureOk(response);
  const blob = await response.blob();
  return { blob, filename: exportFilename(response.headers.get("content-disposition"), id) };
}

export async function importCartridge(file: File): Promise<StudioManifest> {
  assertFileSize(file, MAX_IMPORT_ZIP_BYTES);
  const form = new FormData();
  form.append("file", file);
  const response = await apiFetch(studioPaths.cartridgeImport(), {
    method: "POST",
    body: form,
    timeoutMs: 120_000,
  });
  return (await ensureOk(response)) as StudioManifest;
}

export async function getDagGraph(cartridge: string): Promise<DagGraphPayload> {
  const { data } = await api.get<Partial<DagGraphPayload>>(studioPaths.dagGraph(cartridge));
  return {
    format: data?.format,
    nodes: Array.isArray(data?.nodes) ? data.nodes.filter((node) => typeof node?.id === "string") : [],
    edges: Array.isArray(data?.edges)
      ? data.edges.filter((edge) => typeof edge?.source === "string" && typeof edge?.target === "string")
      : [],
  };
}

export async function listDags(cartridge: string): Promise<StudioDagsPayload> {
  const { data } = await api.get<Partial<StudioDagsPayload>>(studioPaths.dags(cartridge));
  const dags = Array.isArray(data?.dags) ? data.dags.filter((dag) => Boolean(dag?.dag_id)) : [];
  return { cartridge: data?.cartridge ?? cartridge, dags, total: data?.total ?? dags.length };
}

export async function getDagSource(cartridge: string, dagId: string): Promise<DagSourcePayload> {
  const { data } = await api.get<DagSourcePayload>(studioPaths.dagSource(cartridge, dagId));
  return {
    dag_id: data?.dag_id ?? dagId,
    found: Boolean(data?.found),
    source_code: typeof data?.source_code === "string" ? data.source_code : "",
    path: data?.path ?? null,
    error: safeText(data?.error),
  };
}

export async function listTemplates(): Promise<DagTemplate[]> {
  const { data } = await api.get<{ templates?: DagTemplate[] }>(studioPaths.templates());
  return Array.isArray(data?.templates) ? data.templates.filter((item) => Boolean(item?.id)) : [];
}

export async function deployDag(input: DeployDagInput): Promise<DeployDagResult> {
  const body: Record<string, string> = {
    cartridge: input.cartridge,
    entity: input.entity,
    dag_id: input.dag_id,
  };
  if (input.template_id) body.template_id = input.template_id;
  else if (input.code !== undefined) body.code = input.code;
  if (input.description) body.description = input.description;
  const { data } = await api.post<DeployDagResult>(studioPaths.deploy(), body);
  return {
    ...data,
    status: String(data?.status ?? "failed"),
    message: safeText(data?.message),
    error: safeText(data?.error),
  };
}

export async function deleteDag(cartridge: string, dagId: string): Promise<DeleteDagResult> {
  const { data } = await api.delete<DeleteDagResult>(studioPaths.dag(cartridge, dagId));
  if (!data?.deleted) throw toApiError(`No se pudo eliminar el DAG ${dagId}.`, 502, data);
  return { deleted: true, dag_id: data.dag_id ?? dagId };
}

export function replaceDagId(code: string, newId: string): string {
  return code.replace(/dag_id\s*=\s*['"][^'"]+['"]/g, `dag_id='${newId}'`);
}

export async function renameDag(input: {
  cartridge: string;
  entity: string;
  oldId: string;
  newId: string;
  code: string;
}): Promise<{ deploy: DeployDagResult; deleted: boolean }> {
  const deploy = await deployDag({
    cartridge: input.cartridge,
    entity: input.entity,
    dag_id: input.newId,
    code: replaceDagId(input.code, input.newId),
  });
  if (deploy.status !== "deployed") return { deploy, deleted: false };
  await deleteDag(input.cartridge, input.oldId);
  return { deploy, deleted: true };
}

export async function getSystemInfo(): Promise<SystemInfo> {
  const { data } = await api.get<SystemInfo>(studioPaths.systemInfo());
  return data ?? {};
}

export function safeHttpUrl(value: unknown): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  try {
    const url = new URL(value.trim());
    if (url.protocol !== "http:" && url.protocol !== "https:") return null;
    return url.toString().replace(/\/+$/, "");
  } catch {
    return null;
  }
}

export async function getRuntimeConfig(): Promise<RuntimeConfig> {
  const { data } = await api.get<RuntimeConfig>(studioPaths.config());
  return {
    ...data,
    airflow_url: safeHttpUrl(data?.airflow_url),
    superset_url: safeHttpUrl(data?.superset_url),
  };
}

export function airflowDagUrl(airflowUrl: string | null | undefined, dagId: string): string | null {
  const base = safeHttpUrl(airflowUrl);
  if (!base || !dagId) return null;
  return `${base}/dags/${encodeURIComponent(dagId)}/grid`;
}

export async function getLayerPreview(
  layer: StudioLayer,
  cartridge: string,
  dataset: string,
  limit = 50,
): Promise<LayerPreviewPayload> {
  const { data } = await api.get<Partial<LayerPreviewPayload>>(
    studioPaths.layerPreview(layer, cartridge, dataset, limit),
  );
  const rows = Array.isArray(data?.rows)
    ? data.rows.filter((row): row is Record<string, unknown> => typeof row === "object" && row !== null)
    : [];
  return {
    layer: data?.layer ?? layer,
    dataset: data?.dataset ?? null,
    columns: Array.isArray(data?.columns) ? data.columns.map(String) : [],
    rows,
    total: typeof data?.total === "number" ? data.total : rows.length,
    available: data?.available !== false,
    reason: safeText(data?.reason),
    source: data?.source ?? null,
  };
}

export async function publishSupersetDataset(cartridge: string, tableName: string): Promise<SupersetDatasetResult> {
  const { data } = await api.post<SupersetDatasetResult>(studioPaths.superset(), {
    cartridge,
    table_name: tableName,
  });
  return data ?? {};
}

export async function listStudioEntities(cartridge: string): Promise<StudioEntitiesPayload> {
  const { data } = await api.get<Partial<StudioEntitiesPayload>>(studioPaths.entities(cartridge));
  const entities = Array.isArray(data?.entities) ? data.entities.filter((item) => Boolean(item?.name)) : [];
  return { cartridge: data?.cartridge ?? cartridge, entities, total: data?.total ?? entities.length };
}

export async function updateEntity(cartridge: string, entity: string, patch: EntityPatch): Promise<EntityUpdateResult> {
  const { data } = await api.patch<EntityUpdateResult>(studioPaths.entity(cartridge, entity), patch);
  return data;
}

export async function renameEntity(cartridge: string, entity: string, newName: string): Promise<EntityRenameResult> {
  const { data } = await api.post<EntityRenameResult>(studioPaths.entityRename(cartridge, entity), {
    new_name: newName.trim(),
  });
  return data;
}

export async function createEntity(input: CreateEntityInput): Promise<CreateEntityResult> {
  const { data } = await api.post<CreateEntityResult>(studioPaths.newEntity(), input);
  if (!data?.created) {
    throw toApiError(safeText(data?.error) ?? "No se pudo crear la entidad.", 400, data);
  }
  return data;
}

export async function uploadEntitySpec(cartridge: string, file: File): Promise<UploadSpecResult> {
  assertFileSize(file, MAX_SPEC_BYTES);
  const form = new FormData();
  form.append("cartridge", cartridge);
  form.append("file", file);
  const response = await apiFetch(studioPaths.entitiesUpload(cartridge), {
    method: "POST",
    body: form,
    timeoutMs: 60_000,
  });
  const data = (await ensureOk(response)) as Partial<UploadSpecResult> | null;
  return {
    accepted: Boolean(data?.accepted),
    accepted_count: Number(data?.accepted_count ?? 0),
    uploaded: data?.uploaded ?? null,
    entities: Array.isArray(data?.entities) ? data.entities.map(String) : [],
    errors: Array.isArray(data?.errors) ? data.errors : [],
    error: safeText(data?.error) ?? undefined,
  };
}

export async function introspectSource(cartridge: string): Promise<IntrospectionResult> {
  const { data } = await api.post<IntrospectionResult>(studioPaths.introspect(), { cartridge_id: cartridge });
  return {
    ...data,
    source: data?.source ?? null,
    reason: safeText(data?.reason),
    entities: Array.isArray(data?.entities) ? data.entities : [],
  };
}

export async function saveDataset(input: SaveDatasetInput): Promise<SaveDatasetResult> {
  const { data } = await api.post<SaveDatasetResult & { error?: unknown; detail?: unknown }>(
    studioPaths.datasetSave(),
    input,
  );
  const failure = safeText(data?.error) ?? safeText(data?.detail);
  if (failure) throw toApiError(failure, 400, data);
  return data;
}

export async function refreshDataset(name: string): Promise<RefreshDatasetResult> {
  const { data } = await api.post<RefreshDatasetResult & { error?: unknown }>(studioPaths.datasetRefresh(name));
  const failure = safeText(data?.error);
  if (failure) throw toApiError(failure, 400, data);
  return data ?? {};
}

export async function deleteDataset(name: string): Promise<DeleteDatasetResult> {
  const { data } = await api.delete<DeleteDatasetResult & { error?: unknown; detail?: unknown }>(
    studioPaths.datasetDelete(name),
  );
  if (!data?.deleted) {
    throw toApiError(
      safeText(data?.error) ?? safeText(data?.detail) ?? `No se pudo eliminar el dataset ${name}.`,
      409,
      data,
    );
  }
  return data;
}

function sameOriginPath(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const path = value.trim();
  if (!path.startsWith("/") || path.startsWith("//") || path.includes("\\")) return null;
  return path;
}

export async function streamStudioChat(
  input: StudioChatInput,
  handlers: StudioChatHandlers = {},
  options: SseStreamOptions = {},
): Promise<StudioChatResult> {
  let done: StudioChatResult | null = null;
  await postSseStream(
    studioPaths.chatStream(),
    input,
    (frame, requestId) => {
      const data = (frame.data && typeof frame.data === "object" ? frame.data : {}) as Record<string, unknown>;
      if (frame.event === "text_delta") {
        if (typeof data.text === "string" && data.text) handlers.onTextDelta?.(data.text);
        return;
      }
      if (frame.event === "tool_use") {
        const args = data.args && typeof data.args === "object" ? (data.args as Record<string, unknown>) : {};
        handlers.onToolUse?.(String(data.tool ?? "herramienta"), args);
        return;
      }
      if (frame.event === "tool_result") {
        handlers.onToolResult?.(String(data.tool ?? "herramienta"), safeText(data.summary, 160) ?? "");
        return;
      }
      if (frame.event === "done") {
        const viewerUrls = Array.isArray(data.viewer_urls)
          ? data.viewer_urls.flatMap((item) => {
              const record = item && typeof item === "object" ? (item as Record<string, unknown>) : {};
              const url = sameOriginPath(record.url);
              return url ? [{ url, label: safeText(record.label, 80) ?? url }] : [];
            })
          : [];
        done = {
          reply: typeof data.reply === "string" ? data.reply : "",
          history: Array.isArray(data.messages) ? data.messages : input.history,
          viewerUrls,
        };
        return;
      }
      if (frame.event === "error") {
        const ref = typeof data.message === "string" ? data.message.match(/error_id=([0-9a-f]{6,64})/i)?.[1] : null;
        throw toApiError(
          ref
            ? `El asistente de Studio no pudo responder. Ref: ${ref}`
            : "El asistente de Studio no pudo responder.",
          502,
          null,
          requestId,
        );
      }
    },
    options,
  );
  if (!done) throw new Error("El asistente de Studio terminó sin respuesta.");
  return done;
}
