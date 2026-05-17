/**
 * v1.44.4 Task A — Copilot API client.
 *
 * Thin axios wrapper that returns the typed response shapes
 * defined in ./types.ts. Re-uses the shared ``api`` instance from
 * ``@/lib/api`` so CSRF + cookie + baseURL semantics stay
 * consistent with the rest of the Next.js console.
 *
 * NOTE on the chat surface: ``sendMessage`` is non-streaming
 * today — the backend's ``run_turn`` returns a complete JSON
 * response with the full assistant reply. The hook layer in
 * ``./useChat.ts`` exposes loading state so the UI can show a
 * "pensando..." indicator while the round-trip is in flight.
 * Streaming via EventSource lands as a follow-up once the
 * backend emits ``StreamingResponse`` (copilot_workflows.py
 * docstring documents the SSE work as next-session scope).
 */
import { api } from "@/lib/api";
import type {
  Conversation,
  ConversationDetailResponse,
  ConversationListResponse,
  Draft,
  DraftRequest,
  MemoryFact,
  MemoryResponse,
  SendMessageResponse,
  Workflow,
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


export async function createFact(
  key: string,
  value: string,
): Promise<MemoryFact> {
  const { data } = await api.post<MemoryFact>(
    "/api/copilot/memory/fact",
    { key, value },
  );
  return data;
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


export async function generateDraft(request: DraftRequest): Promise<Draft> {
  const { data } = await api.post<Draft>(
    "/api/copilot/drafts/generate",
    request,
  );
  return data;
}


// ── Workflows ───────────────────────────────────────────────────────


export async function listWorkflows(): Promise<Workflow[]> {
  const { data } = await api.get<WorkflowListResponse>(
    "/api/copilot/workflow",
  );
  return data.workflows ?? [];
}


export async function getWorkflow(id: string): Promise<Workflow> {
  const { data } = await api.get<Workflow>(
    `/api/copilot/workflow/${encodeURIComponent(id)}`,
  );
  return data;
}
