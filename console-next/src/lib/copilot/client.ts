/**
 * v1.44.4 Task A — Copilot API client.
 *
 * Thin axios wrapper that returns the typed response shapes
 * defined in ./types.ts. Re-uses the shared ``api`` instance
 * from ``@/lib/api`` so CSRF + cookie + baseURL semantics stay
 * consistent with the rest of the Next.js console.
 *
 * Backend reality (see types.ts module doc for the audit
 * findings):
 *   - run_turn (``sendMessage``) is non-streaming for now —
 *     SSE is v1.44.4.1 backend scope.
 *   - createFact + generateDraft return ENVELOPES
 *     ({ok, fact} / {ok, draft}); we unwrap before returning
 *     so call sites can stay simple.
 *   - getWorkflow returns ``{workflow, steps}`` (NOT a flat
 *     Workflow); the typed wrapper preserves both.
 */
import { api } from "@/lib/api";
import type {
  Conversation,
  ConversationDetailResponse,
  ConversationListResponse,
  CreateFactResponse,
  Draft,
  DraftRequest,
  GenerateDraftResponse,
  MemoryFact,
  MemoryResponse,
  SendMessageResponse,
  Workflow,
  WorkflowDetailResponse,
  WorkflowListResponse,
} from "./types";


// ── Conversations ───────────────────────────────────────────────────


export async function listConversations(): Promise<Conversation[]> {
  const { data } = await api.get<ConversationListResponse>(
    "/api/copilot/conversations",
  );
  return data.conversations ?? [];
}


export async function createConversation(
  title?: string,
): Promise<Conversation> {
  const { data } = await api.post<Conversation>(
    "/api/copilot/conversations",
    { title: title ?? null },
  );
  return data;
}


export async function getConversation(
  id: string,
): Promise<ConversationDetailResponse> {
  const { data } = await api.get<ConversationDetailResponse>(
    `/api/copilot/conversations/${encodeURIComponent(id)}`,
  );
  return data;
}


export async function sendMessage(
  conversationId: string,
  message: string,
): Promise<SendMessageResponse> {
  const { data } = await api.post<SendMessageResponse>(
    `/api/copilot/conversations/${encodeURIComponent(conversationId)}/messages`,
    { message },
  );
  return data;
}


export async function approveAction(
  conversationId: string,
  messageId: string,
): Promise<SendMessageResponse> {
  const { data } = await api.post<SendMessageResponse>(
    `/api/copilot/conversations/${encodeURIComponent(conversationId)}` +
    `/approve/${encodeURIComponent(messageId)}`,
    {},
  );
  return data;
}


// ── Memory ──────────────────────────────────────────────────────────


export async function listMemory(): Promise<MemoryResponse> {
  const { data } = await api.get<MemoryResponse>("/api/copilot/memory");
  return data;
}


/**
 * POST /api/copilot/memory/fact accepts ``{fact, source?}`` and
 * returns ``{ok, fact}`` — we unwrap to the inner fact for the
 * caller's convenience.
 */
export async function createFact(
  fact: string,
  source?: string,
): Promise<MemoryFact> {
  const { data } = await api.post<CreateFactResponse>(
    "/api/copilot/memory/fact",
    source ? { fact, source } : { fact },
  );
  return data.fact;
}


export async function deleteFact(id: number): Promise<void> {
  await api.delete(`/api/copilot/memory/fact/${id}`);
}


export async function setPreference(
  key: string,
  value: string,
): Promise<void> {
  await api.put(
    `/api/copilot/memory/preference/${encodeURIComponent(key)}`,
    { value },
  );
}


// ── Drafts ──────────────────────────────────────────────────────────


/**
 * POST /api/copilot/drafts/generate. Backend requires
 * ``{kind, about, tone?, audience?, title?, metadata?}`` and
 * returns ``{ok, draft}``. We unwrap to the inner draft.
 */
export async function generateDraft(request: DraftRequest): Promise<Draft> {
  const { data } = await api.post<GenerateDraftResponse>(
    "/api/copilot/drafts/generate",
    request,
  );
  return data.draft;
}


// ── Workflows ───────────────────────────────────────────────────────


export async function listWorkflows(): Promise<Workflow[]> {
  const { data } = await api.get<WorkflowListResponse>(
    "/api/copilot/workflow",
  );
  return data.workflows ?? [];
}


export async function getWorkflow(id: string): Promise<WorkflowDetailResponse> {
  const { data } = await api.get<WorkflowDetailResponse>(
    `/api/copilot/workflow/${encodeURIComponent(id)}`,
  );
  return data;
}
