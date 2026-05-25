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
 *   - ``sendMessage`` preserves the legacy JSON route; the chat UI
 *     calls ``streamMessage`` for the SSE path.
 *   - createFact + generateDraft return ENVELOPES
 *     ({ok, fact} / {ok, draft}); we unwrap before returning
 *     so call sites can stay simple.
 *   - getWorkflow returns ``{workflow, steps}`` (NOT a flat
 *     Workflow); the typed wrapper preserves both.
 */
import { api, readCookie } from "@/lib/api";
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

export interface StreamMessageHandlers {
  onToken?: (delta: string) => void;
  onText?:  (text: string) => void;
  onEvent?: (event: string, data: unknown) => void;
}


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

function parseSseFrame(frame: string): { event: string; data: unknown } | null {
  const lines = frame.split(/\r?\n/);
  let event = "message";
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice("event:".length).trim() || "message";
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }
  if (dataLines.length === 0) return null;
  const raw = dataLines.join("\n");
  try {
    return { event, data: JSON.parse(raw) };
  } catch {
    return { event, data: raw };
  }
}


function csrfHeaders(): HeadersInit {
  const token = readCookie("csrf_token");
  return token ? { "X-CSRF-Token": token } : {};
}


export async function streamMessage(
  conversationId: string,
  message: string,
  handlers: StreamMessageHandlers = {},
): Promise<SendMessageResponse> {
  const response = await fetch(
    `/api/copilot/chat/${encodeURIComponent(conversationId)}/stream`,
    {
      method: "POST",
      credentials: "include",
      headers: {
        "Content-Type": "application/json",
        ...csrfHeaders(),
      },
      body: JSON.stringify({ message }),
    },
  );

  if (!response.ok || !response.body) {
    const detail = await response.text().catch(() => "");
    throw new Error(detail || `HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalData: SendMessageResponse | null = null;
  let sawToken = false;

  function consume(frame: string) {
    const parsed = parseSseFrame(frame);
    if (!parsed) return;
    handlers.onEvent?.(parsed.event, parsed.data);
    const data = parsed.data as Record<string, unknown>;

    if (parsed.event === "token") {
      const delta = typeof data?.delta === "string" ? data.delta : "";
      if (delta) {
        sawToken = true;
        handlers.onToken?.(delta);
      }
      return;
    }

    if (parsed.event === "message") {
      const text = typeof data?.text === "string" ? data.text : "";
      if (text && !sawToken) handlers.onText?.(text);
      return;
    }

    if (parsed.event === "done") {
      finalData = data as unknown as SendMessageResponse;
      return;
    }

    if (parsed.event === "error") {
      const detail =
        typeof data?.detail === "string" ? data.detail : "Copilot stream error";
      throw new Error(detail);
    }
  }

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    let idx = buffer.indexOf("\n\n");
    while (idx !== -1) {
      const frame = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 2);
      if (frame && !frame.startsWith(":")) consume(frame);
      idx = buffer.indexOf("\n\n");
    }
    if (done) break;
  }

  const tail = buffer.trim();
  if (tail && !tail.startsWith(":")) consume(tail);
  if (!finalData) throw new Error("Copilot stream ended before completion.");
  return finalData;
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
