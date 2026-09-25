import { api, apiFetch, publicErrorMessage, toApiError } from "@/lib/api";
import type {
  Conversation,
  ConversationDetailResponse,
  ConversationListResponse,
  AskWithContextResponse,
  BriefingV2Highlight,
  CopilotGoal,
  CopilotGoalDiagnosisResponse,
  CopilotContextSnapshot,
  CopilotLesson,
  CopilotRecommendation,
  CopilotWatchdog,
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


export async function streamMessage(
  conversationId: string,
  message: string,
  handlers: StreamMessageHandlers = {},
): Promise<SendMessageResponse> {
  const response = await apiFetch(
    `/api/copilot/chat/${encodeURIComponent(conversationId)}/stream`,
    {
      method: "POST",
      json: { message },
    },
  );

  const requestId = response.headers.get("x-request-id") || undefined;

  if (!response.ok || !response.body) {
    const raw = await response.text().catch(() => "");
    let payload: unknown = null;
    try {
      payload = JSON.parse(raw);
    } catch {
      payload = null;
    }
    throw toApiError(
      publicErrorMessage(response.status, payload, requestId),
      response.status,
      payload,
      requestId,
    );
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
      const code = typeof data?.code === "string" ? data.code : undefined;
      throw toApiError(publicErrorMessage(502, null, requestId), 502, { code }, requestId);
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


export async function listMemory(): Promise<MemoryResponse> {
  const { data } = await api.get<MemoryResponse>("/api/copilot/memory");
  return data;
}


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


export async function generateDraft(request: DraftRequest): Promise<Draft> {
  const { data } = await api.post<GenerateDraftResponse>(
    "/api/copilot/drafts/generate",
    request,
  );
  return data.draft;
}


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


export async function listCopilotGoals(limit = 5): Promise<CopilotGoal[]> {
  const { data } = await api.get<CopilotGoal[]>(
    `/api/copilot/goals?limit=${encodeURIComponent(String(limit))}`,
  );
  return data ?? [];
}


export async function createCopilotGoal(goalText: string): Promise<CopilotGoal> {
  const { data } = await api.post<CopilotGoal>(
    "/api/copilot/goals",
    { goal_text: goalText },
  );
  return data;
}


export async function diagnoseCopilotGoal(
  goalId: string,
): Promise<CopilotGoalDiagnosisResponse> {
  const { data } = await api.post<CopilotGoalDiagnosisResponse>(
    `/api/copilot/goals/${encodeURIComponent(goalId)}/diagnose`,
    {},
  );
  return data;
}


export async function listCopilotBriefingV2(limit = 5): Promise<BriefingV2Highlight[]> {
  const { data } = await api.get<BriefingV2Highlight[]>(
    `/api/copilot/briefing/v2?limit=${encodeURIComponent(String(limit))}`,
  );
  return data ?? [];
}


export async function listCopilotWatchdogs(limit = 8): Promise<CopilotWatchdog[]> {
  const { data } = await api.get<CopilotWatchdog[]>(
    `/api/copilot/watchdogs?limit=${encodeURIComponent(String(limit))}`,
  );
  return data ?? [];
}


export async function matchCopilotWatchdogs(
  intent: string,
  limit = 5,
): Promise<CopilotWatchdog[]> {
  const params = new URLSearchParams({
    intent,
    limit: String(limit),
  });
  const { data } = await api.get<CopilotWatchdog[]>(
    `/api/copilot/watchdogs/match?${params.toString()}`,
  );
  return data ?? [];
}


export async function listCopilotLessons(limit = 5): Promise<CopilotLesson[]> {
  const { data } = await api.get<CopilotLesson[]>(
    `/api/copilot/lessons?enabled_only=true&limit=${encodeURIComponent(String(limit))}`,
  );
  return data ?? [];
}


export async function askCopilotWithContext(
  question: string,
  pageContext: Record<string, unknown>,
): Promise<AskWithContextResponse> {
  const { data } = await api.post<AskWithContextResponse>(
    "/api/copilot/ask-with-context",
    { question, page_context: pageContext },
  );
  return data;
}


export async function getCopilotContextSnapshot(): Promise<CopilotContextSnapshot> {
  const { data } = await api.get<CopilotContextSnapshot>("/api/copilot/context/snapshot");
  return data;
}


export async function refreshCopilotContext(): Promise<CopilotContextSnapshot> {
  const { data } = await api.post<CopilotContextSnapshot>("/api/copilot/context/refresh", {});
  return data;
}


export async function listCopilotRecommendations(
  limit = 20,
  includeDismissed = false,
): Promise<CopilotRecommendation[]> {
  const params = new URLSearchParams({
    limit: String(limit),
    include_dismissed: includeDismissed ? "true" : "false",
  });
  const { data } = await api.get<unknown>(`/api/copilot/recommendations?${params.toString()}`);
  if (Array.isArray(data)) return data as CopilotRecommendation[];
  if (data && typeof data === "object") {
    const rows = (data as Record<string, unknown>).recommendations
      ?? (data as Record<string, unknown>).items
      ?? (data as Record<string, unknown>).rows;
    return Array.isArray(rows) ? rows as CopilotRecommendation[] : [];
  }
  return [];
}


export async function dismissCopilotRecommendation(
  recommendationId: string,
): Promise<CopilotRecommendation> {
  const { data } = await api.post<CopilotRecommendation>(
    `/api/copilot/recommendations/${encodeURIComponent(recommendationId)}/dismiss`,
    {},
  );
  return data;
}
