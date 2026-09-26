export interface StudioCartridgeSummary {
  id: string;
  name?: string | null;
  version?: string | null;
  description?: string | null;
  pattern?: string | null;
  entities?: number | null;
  source?: string | null;
}

export interface StudioManifestDag {
  dag_id?: string | null;
  cartridge_id?: string | null;
  description?: string | null;
  dag_role?: string | null;
  [key: string]: unknown;
}

export interface StudioManifestEntity {
  entity?: string | null;
  name?: string | null;
  display_name?: string | null;
  mode?: string | null;
  primary_key?: string | null;
  dag_id?: string | null;
  trigger_type?: string | null;
  cron_expression?: string | null;
  description?: string | null;
  enabled?: boolean | null;
  [key: string]: unknown;
}

export interface StudioManifest {
  id: string;
  name?: string | null;
  description?: string | null;
  pattern?: string | null;
  dags?: Array<StudioManifestDag | string> | null;
  entities?: StudioManifestEntity[] | null;
  connections?: Array<Record<string, unknown>> | null;
  [key: string]: unknown;
}

export type CartridgeProbeStatus = "operational" | "degraded" | "offline" | "registered";

export interface CartridgeProbe {
  cartridge_id?: string;
  status: CartridgeProbeStatus | string;
  reason?: unknown;
  detail?: string | null;
}

export interface CreateCartridgeInput {
  id: string;
  name: string;
  description: string;
}

export type DagGraphKind = "cartridge" | "entity" | "dag" | "dataset" | string;

export interface DagGraphNode {
  id: string;
  kind: DagGraphKind;
  label?: string | null;
  layer?: string | null;
}

export interface DagGraphEdge {
  source: string;
  target: string;
}

export interface StudioEditorTarget {
  dataset?: string;
  entity?: string;
}

export interface DagGraphPayload {
  format?: string;
  nodes: DagGraphNode[];
  edges: DagGraphEdge[];
}

export interface StudioDag {
  dag_id: string;
  id?: string;
  is_paused: boolean;
  is_active: boolean;
  tags?: Array<string | { name?: string }> | null;
  cartridge_id?: string | null;
  registered_only?: boolean;
}

export interface StudioDagsPayload {
  cartridge?: string | null;
  dags: StudioDag[];
  total: number;
}

export interface DagSourcePayload {
  dag_id?: string;
  found: boolean;
  source_code?: string | null;
  path?: string | null;
  error?: string | null;
}

export interface DagTemplate {
  id: string;
  name?: string | null;
  description?: string | null;
  tags?: string[] | null;
}

export interface DeployDagInput {
  cartridge: string;
  entity: string;
  dag_id: string;
  code?: string;
  template_id?: string;
  description?: string;
}

export type DeployStatus = "needs_input" | "managed" | "deployed" | "failed";

export interface DeployDagResult {
  status: DeployStatus | string;
  dag_id?: string | null;
  message?: string | null;
  error?: string | null;
  result?: unknown;
}

export interface DeleteDagResult {
  deleted: boolean;
  dag_id?: string;
}

export interface SystemInfo {
  version?: string;
  env?: string;
  app_env?: string;
  dev_mode?: boolean;
  rce_tools_enabled?: boolean;
  dag_deploy_enabled?: boolean;
}

export interface RuntimeConfig {
  workspace_url?: string | null;
  console_url?: string | null;
  airflow_url?: string | null;
  superset_url?: string | null;
  s3_bucket?: string | null;
}

export type StudioLayer = "silver" | "gold";

export interface LayerPreviewPayload {
  layer: string;
  dataset?: string | null;
  columns: string[];
  rows: Array<Record<string, unknown>>;
  total: number;
  available?: boolean;
  reason?: string | null;
  source?: string | null;
}

export interface SupersetDatasetResult {
  created?: boolean;
  existing?: boolean;
  available?: boolean;
  needs_materialization?: boolean;
  table?: string | null;
  schema?: string | null;
  message?: string | null;
  error?: string | null;
  [key: string]: unknown;
}

export interface StudioEntity {
  id?: string;
  name: string;
  entity?: string;
  cartridge?: string;
  display_name?: string | null;
  mode?: string | null;
  dag_id?: string | null;
  description?: string | null;
  source?: string | null;
  spec?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface StudioEntitiesPayload {
  cartridge?: string;
  entities: StudioEntity[];
  total: number;
}

export interface EntityPatch {
  display_name?: string;
  mode?: string;
  primary_key?: string;
  dag_id?: string;
  trigger_type?: string;
  cron_expression?: string | null;
  description?: string;
}

export interface EntityUpdateResult {
  updated: boolean;
  entity: string;
  [key: string]: unknown;
}

export interface EntityRenameResult {
  renamed: boolean;
  old_name?: string;
  new_name?: string;
  reason?: string;
}

export interface CreateEntityInput {
  cartridge: string;
  entity: string;
  display_name?: string;
  mode?: string;
  primary_key?: string;
  dag_id?: string;
  description?: string;
}

export interface CreateEntityResult {
  created: boolean;
  entity_id?: string;
  error?: string;
}

export interface UploadSpecResult {
  accepted: boolean;
  accepted_count: number;
  uploaded?: string | null;
  entities?: string[];
  errors?: Array<string | Record<string, unknown>>;
  error?: string;
}

export interface IntrospectionField {
  name?: string;
  type?: string;
  primary_key?: boolean;
  [key: string]: unknown;
}

export interface IntrospectionEntity {
  name?: string;
  entity?: string;
  display_name?: string;
  primary_key?: string;
  fields?: IntrospectionField[];
  [key: string]: unknown;
}

export interface IntrospectionResult {
  cartridge_id?: string;
  source?: string | null;
  reason?: string | null;
  entities?: IntrospectionEntity[];
  endpoint?: string;
  [key: string]: unknown;
}

export interface SaveDatasetInput {
  name: string;
  layer: StudioLayer;
  sql: string;
  description: string;
  cartridge: string;
  sources: string[];
}

export interface SaveDatasetResult {
  saved?: boolean;
  name?: string;
  [key: string]: unknown;
}

export interface RefreshDatasetResult {
  row_count?: number | null;
  [key: string]: unknown;
}

export interface DeleteDatasetResult {
  deleted: boolean;
  name?: string;
  layer?: string;
  steps?: string[];
}

export interface StudioChatInput {
  message: string;
  history: unknown[];
  step: number;
  cartridge_id: string | null;
}

export interface StudioChatHandlers {
  onTextDelta?: (text: string) => void;
  onToolUse?: (tool: string, args: Record<string, unknown>) => void;
  onToolResult?: (tool: string, summary: string) => void;
}

export interface StudioChatResult {
  reply: string;
  history: unknown[];
  viewerUrls: Array<{ url: string; label: string }>;
}
