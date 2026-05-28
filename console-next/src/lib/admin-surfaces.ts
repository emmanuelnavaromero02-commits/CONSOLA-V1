import { api, apiFetch } from "@/lib/api";

export interface SecuritySession {
  session_id?: string;
  token_preview?: string;
  user_id?: number | string | null;
  user_email?: string | null;
  email?: string | null;
  ip?: string | null;
  user_agent?: string | null;
  created_at?: string | null;
  last_seen?: string | null;
  expires_at?: string | null;
}

export interface SystemSetting {
  key: string;
  value: string | number | boolean | null;
  is_secret?: boolean;
  category?: string | null;
  description?: string | null;
  updated_at?: string | null;
}

export interface SettingsResponse {
  settings: SystemSetting[];
}

export interface AnalyticsApp {
  name: string;
  title?: string | null;
  description?: string | null;
  updated_at?: string | null;
}

export interface AppsResponse {
  apps: AnalyticsApp[];
}

export interface ExplorerBucket {
  id: string;
  name: string;
  label?: string | null;
}

export interface ExplorerQuicklink {
  label?: string | null;
  bucket?: string | null;
  prefix?: string | null;
}

export interface ExplorerBucketsResponse {
  buckets: ExplorerBucket[];
  quicklinks?: ExplorerQuicklink[];
}

export interface ExplorerObject {
  key: string;
  size?: number | null;
  last_modified?: string | null;
}

export interface ExplorerListResponse {
  folders?: string[];
  objects?: ExplorerObject[];
  next_token?: string | null;
  is_truncated?: boolean;
}

export interface ExplorerDownloadResponse {
  url?: string | null;
}

export type DecisionStatus = "open" | "closed";
export type DecisionVisibility = "private" | "shared";

export interface Decision {
  id: number;
  title: string;
  description?: string | null;
  commitment_date?: string | null;
  status?: DecisionStatus | string | null;
  outcome?: string | null;
  created_at?: string | null;
  closed_at?: string | null;
  created_by_id?: number | null;
  assignee_id?: number | null;
  visibility?: DecisionVisibility | string | null;
}

export interface DecisionAction {
  id?: number;
  action_text?: string | null;
  note?: string | null;
  actor?: string | null;
  ts?: string | null;
}

export interface DecisionDetail extends Decision {
  actions?: DecisionAction[];
}

export interface DecisionsResponse {
  decisions: Decision[];
}

export interface AgentRecord {
  id: string;
  name: string;
  slug?: string | null;
  cartridge_id?: string | null;
  description?: string | null;
  instructions?: string | null;
  personality?: string | null;
  is_active?: boolean | null;
  allowed_tools?: string[] | null;
  rag_filter?: AgentRagFilter | null;
  model?: string | null;
  max_tokens?: number | null;
  temperature?: number | null;
  extra?: AgentExtra | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface AgentsResponse {
  agents: AgentRecord[];
}

export interface AgentRagFilter {
  kinds?: string[];
  source_ids?: number[];
  tags?: string[];
  [key: string]: unknown;
}

export interface AgentSchedule {
  cron?: string;
  cron_expression?: string;
  tz?: string;
  prompt?: string;
  enabled?: boolean;
  [key: string]: unknown;
}

export interface AgentExtra {
  variables?: Record<string, string>;
  schedule?: AgentSchedule;
  [key: string]: unknown;
}

export interface AgentUpsertPayload {
  cartridge_id: string;
  slug: string;
  name: string;
  description?: string;
  instructions: string;
  personality?: string;
  allowed_tools: string[];
  rag_filter: AgentRagFilter;
  model: string;
  max_tokens: number;
  temperature: number;
  extra: AgentExtra;
  is_active: boolean;
}

export interface AgentTool {
  name: string;
  description?: string | null;
  risk?: string | null;
  requires_approval?: boolean | null;
  category?: string | null;
  [key: string]: unknown;
}

export interface AgentToolCatalogResponse {
  servers: Record<string, AgentTool[]>;
}

export interface AgentRun {
  id: number;
  agent_id?: string | null;
  status?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  n_tool_calls?: number | null;
  out_chars?: number | null;
  error_message?: string | null;
}

export interface AgentRunsResponse {
  runs: AgentRun[];
}

export interface AgentRunDetail extends AgentRun {
  input_messages?: unknown;
  output_text?: string | null;
  tool_calls?: unknown;
}

export interface AgentChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface AgentStreamEvent {
  type: "tool_use" | "tool_result" | "text" | "done" | "error";
  text?: string;
  tool?: string;
  server?: string;
  summary?: string;
  message?: string;
  run_id?: number | string;
  [key: string]: unknown;
}

export interface MeAccessResponse {
  user?: {
    id?: number | string;
    email?: string;
    name?: string | null;
  };
  role?: {
    global?: string;
    is_platform_admin?: boolean;
  };
  workspace?: {
    tenant_id?: string | null;
    workspace_id?: string | null;
    workspace_role?: string | null;
  };
  permissions?: string[];
  cartridges?: {
    allowed?: Array<{ cartridge_id?: string; product_name?: string; status?: string }>;
    denied?: Array<{ cartridge_id?: string; product_name?: string; reason?: string; installation_status?: string | null }>;
  };
  ui_capabilities?: Record<string, boolean>;
  [key: string]: unknown;
}

export interface MeProfile {
  id?: number | string;
  email?: string;
  name?: string | null;
  role?: string;
  created_at?: string | null;
  last_login?: string | null;
  must_change_password?: boolean;
  [key: string]: unknown;
}

function queryString(params: Record<string, string | number | boolean | null | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    query.set(key, String(value));
  }
  const text = query.toString();
  return text ? `?${text}` : "";
}

export async function listSecuritySessions(): Promise<SecuritySession[]> {
  const { data } = await api.get<SecuritySession[]>("/security/sessions");
  return Array.isArray(data) ? data : [];
}

export async function revokeSecuritySession(sessionId: string): Promise<void> {
  await api.delete(`/security/sessions/${encodeURIComponent(sessionId)}`);
}

export async function listSettings(category?: string): Promise<SystemSetting[]> {
  const { data } = await api.get<SettingsResponse>(`/api/settings${queryString({ category })}`);
  return data.settings ?? [];
}

export async function updateSetting(key: string, value: string): Promise<SystemSetting> {
  const { data } = await api.put<SystemSetting>(`/api/settings/${encodeURIComponent(key)}`, { value });
  return data;
}

export async function revealSetting(key: string): Promise<SystemSetting> {
  const { data } = await api.post<SystemSetting>(`/api/settings/${encodeURIComponent(key)}/reveal`, {});
  return data;
}

export async function rotateSetting(key: string): Promise<SystemSetting> {
  const { data } = await api.post<SystemSetting>(`/api/settings/${encodeURIComponent(key)}/rotate`, {});
  return data;
}

export async function listApps(): Promise<AnalyticsApp[]> {
  const { data } = await api.get<AppsResponse>("/api/apps");
  return data.apps ?? [];
}

export async function deleteApp(name: string): Promise<void> {
  await api.delete(`/api/apps/${encodeURIComponent(name)}`);
}

export async function listExplorerBuckets(): Promise<ExplorerBucketsResponse> {
  const { data } = await api.get<ExplorerBucketsResponse>("/api/explorer/buckets");
  return { buckets: data.buckets ?? [], quicklinks: data.quicklinks ?? [] };
}

export async function listExplorerObjects(args: {
  bucket: string;
  prefix: string;
  continuationToken?: string;
}): Promise<ExplorerListResponse> {
  const { data } = await api.get<ExplorerListResponse>(
    `/api/explorer/list${queryString({
      bucket: args.bucket,
      prefix: args.prefix,
      max_keys: 200,
      continuation_token: args.continuationToken,
    })}`,
  );
  return data;
}

export async function getExplorerDownload(bucket: string, key: string): Promise<ExplorerDownloadResponse> {
  const { data } = await api.get<ExplorerDownloadResponse>(
    `/api/explorer/download${queryString({ bucket, key })}`,
  );
  return data;
}

export async function deleteExplorerObject(bucket: string, key: string): Promise<void> {
  await api.delete(`/api/explorer/object${queryString({ bucket, key, confirm: key })}`);
}

export async function listDecisions(status: string): Promise<Decision[]> {
  const { data } = await api.get<DecisionsResponse>(`/api/decisions${queryString({ status })}`);
  return data.decisions ?? [];
}

export async function getDecision(id: number): Promise<DecisionDetail> {
  const { data } = await api.get<DecisionDetail>(`/api/decisions/${id}`);
  return data;
}

export async function createDecision(payload: {
  title: string;
  description: string;
  commitment_date?: string;
  visibility: DecisionVisibility;
}): Promise<Decision> {
  const { data } = await api.post<Decision>("/api/decisions", payload);
  return data;
}

export async function updateDecision(
  id: number,
  payload: Partial<Pick<Decision, "title" | "description" | "commitment_date" | "status" | "visibility" | "outcome">>,
): Promise<Decision> {
  const { data } = await api.patch<Decision>(`/api/decisions/${id}`, payload);
  return data;
}

export async function deleteDecision(id: number): Promise<void> {
  await api.delete(`/api/decisions/${id}`);
}

export async function addDecisionAction(id: number, actionText: string): Promise<DecisionAction> {
  const { data } = await api.post<DecisionAction>(`/api/decisions/${id}/actions`, {
    action_text: actionText,
  });
  return data;
}

export async function listAgents(): Promise<AgentRecord[]> {
  const { data } = await api.get<AgentsResponse>("/api/agents?include_inactive=true");
  return data.agents ?? [];
}

export async function listAgentToolCatalog(): Promise<AgentToolCatalogResponse> {
  const { data } = await api.get<AgentToolCatalogResponse>("/api/agents/_tool-catalog");
  return { servers: data.servers ?? {} };
}

export async function createAgent(payload: AgentUpsertPayload): Promise<AgentRecord> {
  const { data } = await api.post<AgentRecord>("/api/agents", payload);
  return data;
}

export async function updateAgent(id: string, payload: Partial<AgentUpsertPayload>): Promise<AgentRecord> {
  const { data } = await api.patch<AgentRecord>(`/api/agents/${encodeURIComponent(id)}`, payload);
  return data;
}

export async function updateAgentStatus(id: string, isActive: boolean): Promise<AgentRecord> {
  return updateAgent(id, { is_active: isActive });
}

export async function deleteAgent(id: string): Promise<void> {
  await api.delete(`/api/agents/${encodeURIComponent(id)}`);
}

export async function listAgentRuns(agentId: string, limit = 30): Promise<AgentRun[]> {
  const { data } = await api.get<AgentRunsResponse>(
    `/api/agents/${encodeURIComponent(agentId)}/runs${queryString({ limit })}`,
  );
  return data.runs ?? [];
}

export async function getAgentRunDetail(runId: number): Promise<AgentRunDetail> {
  const { data } = await api.get<AgentRunDetail>(`/api/agent-runs/${encodeURIComponent(String(runId))}`);
  return data;
}

export async function invokeAgentStream(
  agentId: string,
  payload: { message: string; history: AgentChatMessage[] },
  signal?: AbortSignal,
): Promise<Response> {
  const response = await apiFetch(`/api/agents/${encodeURIComponent(agentId)}/invoke/stream`, {
    method: "POST",
    json: payload,
    signal,
    headers: { Accept: "text/event-stream" },
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    throw new Error(text || `HTTP ${response.status}`);
  }
  return response;
}

export async function getMeAccess(): Promise<MeAccessResponse> {
  const { data } = await api.get<MeAccessResponse>("/api/me/access");
  return data;
}

export async function getMeProfile(): Promise<MeProfile> {
  const { data } = await api.get<MeProfile>("/api/me");
  return data;
}

export async function changeOwnPassword(payload: {
  current_password: string;
  new_password: string;
}): Promise<{ ok?: boolean }> {
  const { data } = await api.post<{ ok?: boolean }>("/api/me/change-password", payload);
  return data;
}
