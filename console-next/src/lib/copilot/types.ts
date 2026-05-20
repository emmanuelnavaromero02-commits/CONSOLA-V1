/**
 * v1.44.4 Task A — Copilot TypeScript types.
 *
 * Mirrors the real shapes returned by:
 *   console/app/routers/copilot.py
 *   console/app/routers/copilot_memory.py
 *   console/app/routers/copilot_drafts.py
 *   console/app/routers/copilot_workflows.py
 *   console/app/services/copilot_service.py
 *   console/app/services/proactive_service.py
 *
 * v1.44.4 Round 1 backend review caught major drift between an
 * earlier draft and the real backend; everything below is now
 * pinned against the live source as of 2026-05-17:
 *
 *   - Memory facts: ``fact`` (NOT ``value``), keyed by ``id``
 *     with optional ``source`` + ``confidence``. Preferences:
 *     ``pref_key`` / ``pref_value`` (NOT ``key`` / ``value``).
 *   - Memory mutations: server returns an ``{ok, fact}``
 *     envelope (NOT the bare fact).
 *   - Drafts request: ``{kind, about, tone, audience, title,
 *     metadata}`` (NOT ``{prompt, subject}``).
 *   - Draft entity:   ``{kind, title, body, tone, status,
 *     metadata}``. No ``subject`` field.
 *   - Draft tones:    ``formal | neutral | friendly | urgent``
 *     (NOT ``concise``).
 *   - Workflow GET envelope: ``{workflow, steps}``.
 *   - Workflow step:  ``{step_idx, description, tool, args,
 *     result, status, started_at, finished_at}``.
 *   - PendingAction has no ``rationale`` field — render from
 *     ``args`` + ``risk_level`` instead.
 *   - run_turn STILL returns JSON (NOT a stream). Streaming
 *     SSE remains v1.44.4.1 backend work
 *     (copilot_workflows.py:6-7).
 */

export type Severity = "info" | "warning" | "critical";

// ── Conversations ───────────────────────────────────────────────────


export interface Conversation {
  id:           string;
  title:        string | null;
  created_at:   string;
  updated_at:   string;
  message_count?: number;
}


export interface ConversationListResponse {
  conversations: Conversation[];
}


// ── Messages ────────────────────────────────────────────────────────


export type MessageRole = "user" | "assistant" | "system";


export interface Citation {
  source?:        string;
  fetched_at?:    string;
  snippet?:       string;
  href?:          string;
  [key: string]:  unknown;
}


export interface ToolCall {
  name?:         string;
  args?:         Record<string, unknown>;
  [key: string]: unknown;
}


export interface ToolResult {
  name?:         string;
  content?:      string;
  [key: string]: unknown;
}


/**
 * Real shape emitted by copilot_service.py inside
 * pending_actions: an invocation dict merged with
 * ``approval_key`` — fields include ``tool``, ``server``,
 * ``args``, ``risk_level``, ``requires_approval``,
 * ``approval_key``. No ``rationale``.
 */
export interface PendingAction {
  tool?:              string;
  server?:            string;
  args?:              Record<string, unknown>;
  risk_level?:        string;
  requires_approval?: boolean;
  approval_key?:      string;
  [key: string]:      unknown;
}


export interface Message {
  id:            string;
  role:          MessageRole;
  content:       string;
  tool_calls?:   ToolCall[]   | null;
  tool_results?: ToolResult[] | null;
  citations?:    Citation[]   | null;
  created_at?:   string;
}


export interface ConversationDetailResponse {
  conversation: Conversation;
  messages:     Message[];
}


// ── Send message response (run_turn) ────────────────────────────────


export interface SendMessageResponse {
  message_id:        string;
  reply:             string;
  tool_calls:        ToolCall[];
  tool_results:      ToolResult[];
  citations:         Citation[];
  pending_actions:   PendingAction[];
  requires_approval: boolean;
}


// ── Memory ──────────────────────────────────────────────────────────


export interface MemoryFact {
  id:           number;
  fact:         string;
  source?:      string | null;
  confidence?:  number | null;
  created_at?:  string;
}


export interface MemoryPreference {
  pref_key:    string;
  pref_value:  string;
  updated_at?: string;
}


export interface MemoryResponse {
  facts:        MemoryFact[];
  preferences:  MemoryPreference[];
}


/** Wrapper returned by POST /api/copilot/memory/fact. */
export interface CreateFactResponse {
  ok:    boolean;
  fact:  MemoryFact;
}


// ── Briefing ────────────────────────────────────────────────────────


export interface BriefingHighlight {
  id:            string;
  severity:      Severity;
  title:         string;
  body:          string;
  category:      string;
  cartridge:     string | null;
  action_label:  string | null;
  action_href:   string | null;
}


export interface BriefingResponse {
  highlights: BriefingHighlight[];
}


// ── Workflows ───────────────────────────────────────────────────────


/**
 * Run statuses produced by copilot_workflows.py. ``awaiting_approval``
 * is NOT a run status on the backend — destructive-action
 * approval is captured at the message layer (run_turn ->
 * pending_actions) and the workflow keeps its own state
 * orthogonally.
 */
export type WorkflowRunStatus =
  | "planning"
  | "running"
  | "completed"
  | "cancelled"
  | "failed";


export type WorkflowStepStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "skipped";


export interface Workflow {
  id:           string;
  intent?:      string;
  status:       WorkflowRunStatus;
  created_at?:  string;
  finished_at?: string | null;
}


export interface WorkflowStep {
  step_idx:     number;
  description:  string;
  tool?:        string;
  args?:        Record<string, unknown>;
  result?:      unknown;
  status:       WorkflowStepStatus;
  started_at?:  string | null;
  finished_at?: string | null;
}


export interface WorkflowDetailResponse {
  workflow: Workflow;
  steps:    WorkflowStep[];
}


export interface WorkflowListResponse {
  workflows: Workflow[];
}


// ── Drafts ──────────────────────────────────────────────────────────


export type DraftTone = "formal" | "neutral" | "friendly" | "urgent";


/**
 * Body accepted by POST /api/copilot/drafts/generate. ``kind``
 * is required ('email' | 'memo' | 'note' | 'report' — the
 * backend enforces the allowlist). ``about`` is the
 * free-form prompt; ``audience`` is who it's directed at;
 * ``title`` seeds the subject line if relevant.
 */
export interface DraftRequest {
  kind:      string;
  about:     string;
  tone?:     DraftTone;
  audience?: string;
  title?:    string;
  metadata?: Record<string, unknown>;
}


export interface Draft {
  id?:       string;
  kind?:     string;
  title?:    string;
  body:      string;
  tone?:     DraftTone;
  status?:   string;
  metadata?: Record<string, unknown>;
}


/** Wrapper returned by POST /api/copilot/drafts/generate. */
export interface GenerateDraftResponse {
  ok:    boolean;
  draft: Draft;
}
