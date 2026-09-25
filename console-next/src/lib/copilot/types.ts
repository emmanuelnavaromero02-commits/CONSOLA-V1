export type Severity = "info" | "warning" | "critical";


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


export interface SendMessageResponse {
  message_id:        string;
  reply:             string;
  tool_calls:        ToolCall[];
  tool_results:      ToolResult[];
  citations:         Citation[];
  pending_actions:   PendingAction[];
  requires_approval: boolean;
}


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


export interface CreateFactResponse {
  ok:    boolean;
  fact:  MemoryFact;
}


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


export type DraftTone = "formal" | "neutral" | "friendly" | "urgent";


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


export interface GenerateDraftResponse {
  ok:    boolean;
  draft: Draft;
}


export type CopilotGoalStatus =
  | "planning"
  | "running"
  | "awaiting_approval"
  | "completed"
  | "failed"
  | "cancelled";


export interface CopilotGoal {
  id:               string;
  goal_text:        string;
  plan_summary?:    string | null;
  status:           CopilotGoalStatus;
  impact_estimate?: Record<string, unknown> | null;
  outcome_summary?: string | null;
  workflow_ids?:    string[] | null;
  created_at?:      string;
  finished_at?:     string | null;
}


export interface CopilotSubgoal {
  description?:         string;
  expected_cartridges?: string[];
  success_criteria?:    string;
  risk_level?:          string;
  watchdog_hint?:       string | null;
  [key: string]:        unknown;
}


export interface CopilotGoalDiagnosis {
  classification?:   string;
  plan_summary?:     string;
  intent_keywords?:  string[];
  subgoals?:         CopilotSubgoal[];
  impact_estimate?:  Record<string, unknown>;
  already_planned?:  boolean;
  [key: string]:     unknown;
}


export interface CopilotWatchdog {
  id?:              string;
  cartridge_id:    string;
  slug:            string;
  name:            string;
  description?:    string | null;
  intent_keywords?: string[];
  agent_slug?:     string | null;
  tools?:          unknown[];
  risk_level?:     string | null;
  enabled?:        boolean;
  metadata?:       Record<string, unknown> | null;
}


export interface CopilotWatchdogMatch {
  subgoal?:  CopilotSubgoal;
  watchdog?: CopilotWatchdog;
}


export interface CopilotGoalDiagnosisResponse {
  diagnosis: CopilotGoalDiagnosis;
  watchdogs: CopilotWatchdogMatch[];
}


export interface CopilotNextAction {
  kind:       string;
  label:      string;
  href?:      string;
  cartridge?: string;
  [key: string]: unknown;
}


export interface BriefingV2Highlight extends BriefingHighlight {
  priority_score?: number;
  next_action?:    CopilotNextAction | null;
  watchdogs?:      Pick<CopilotWatchdog, "cartridge_id" | "slug" | "name">[];
}


export interface CopilotLesson {
  id:               string;
  trigger_pattern?: string;
  lesson_text?:     string;
  confidence?:      number;
  source_kind?:     string;
  enabled?:         boolean;
  created_at?:      string;
  last_used_at?:    string | null;
  [key: string]:    unknown;
}


export interface AskWithContextResponse {
  answer:       string;
  context_used: Record<string, unknown>;
}


export interface CopilotContextSource {
  id?:          string;
  key?:         string;
  label?:       string;
  status?:      string;
  item_count?:  number;
  last_read_at?: string | null;
  [key: string]: unknown;
}


export interface CopilotContextSnapshot {
  id?:              string;
  status?:          string;
  generated_at?:    string;
  materialized_at?: string;
  summary?:         Record<string, unknown> | null;
  sources?:         CopilotContextSource[];
  recommendations?: CopilotRecommendation[];
  [key: string]:    unknown;
}


export interface CopilotRecommendation {
  id:               string;
  title?:           string;
  body?:            string;
  category?:        string;
  priority?:        number;
  priority_score?:  number;
  status?:          string;
  dismissed_at?:    string | null;
  created_at?:      string;
  action_label?:    string | null;
  action_href?:     string | null;
  [key: string]:    unknown;
}
