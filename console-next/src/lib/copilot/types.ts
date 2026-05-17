/**
 * v1.44.4 Task A — Copilot TypeScript types.
 *
 * Mirrors the real shapes returned by console/app/routers/copilot*.py
 * + console/app/services/copilot_service.py.  Confirmed against
 * the source on 2026-05-17 — keep these in lockstep with any
 * backend changes; drift here means silent UI breakage.
 *
 * Notable shape facts:
 *   - run_turn → /api/copilot/conversations/{id}/messages returns
 *     a SINGLE JSON object (NOT a stream).  Streaming is documented
 *     as next-session work in copilot_workflows.py:6-7. The
 *     ``useChat`` hook awaits the full response; a future v1.44.4.1
 *     can swap the implementation to EventSource once the backend
 *     ships ``StreamingResponse``.
 *   - Severity enum on briefing highlights is
 *     ``info | warning | critical`` (NOT the brief's
 *     ``info | warn | alert`` shorthand).
 *   - Briefing text field is ``body`` (NOT ``description``).
 */

export type Severity = "info" | "warning" | "critical";

// ── Conversations ───────────────────────────────────────────────────


export interface Conversation {
  id:           string;
  title:        string | null;
  created_at:   string;
  updated_at:   string;
  /** Number of persisted messages — populated by list_conversations. */
  message_count?: number;
}


export interface ConversationListResponse {
  conversations: Conversation[];
}


// ── Messages ────────────────────────────────────────────────────────


export type MessageRole = "user" | "assistant" | "system";


export interface Citation {
  /** Source label, e.g. "Replicon · time_entries". */
  source?:        string;
  /** Optional ISO timestamp of the underlying data. */
  fetched_at?:    string;
  /** Free-form snippet body. */
  snippet?:       string;
  /** Optional URL to drill into. */
  href?:          string;
  /** Any extra keys the backend emits — kept loose for UI display. */
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


export interface PendingAction {
  /** Stable key — the backend's approve endpoint expects the
   *  message_id; this is mostly a UI hint for what would run. */
  tool?:         string;
  args?:         Record<string, unknown>;
  rationale?:    string;
  [key: string]: unknown;
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
  key:          string;
  value:        string;
  created_at?:  string;
  updated_at?:  string;
}


export interface MemoryPreference {
  key:    string;
  value:  string;
}


export interface MemoryResponse {
  facts:        MemoryFact[];
  preferences:  MemoryPreference[];
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


export type WorkflowStatus =
  | "planning"
  | "running"
  | "awaiting_approval"
  | "completed"
  | "cancelled"
  | "failed";


export interface WorkflowStep {
  id:          string;
  title:       string;
  status:      WorkflowStatus;
  detail?:     string;
}


export interface Workflow {
  id:           string;
  status:       WorkflowStatus;
  title?:       string;
  steps:        WorkflowStep[];
  created_at?:  string;
}


export interface WorkflowListResponse {
  workflows: Workflow[];
}


// ── Drafts ──────────────────────────────────────────────────────────


export type DraftTone = "formal" | "friendly" | "concise";


export interface DraftRequest {
  prompt:    string;
  subject?:  string;
  tone?:     DraftTone;
}


export interface Draft {
  id?:       string;
  subject?:  string;
  body:      string;
  tone?:     DraftTone;
}
