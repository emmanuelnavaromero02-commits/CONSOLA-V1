export type SupervisedActionState =
  | "prepared"
  | "validated"
  | "requires_approval"
  | "approved"
  | "executed"
  | "cancelled"
  | "validation_failed"
  | "failed"
  | string;

export interface SupervisedAction {
  id: string;
  action_id?: string;
  source_type?: string;
  source_id?: string;
  action_type?: string;
  status?: SupervisedActionState;
  state?: SupervisedActionState;
  title?: string;
  label?: string;
  created_at?: string;
  updated_at?: string;
  expires_at?: string | null;
  payload?: Record<string, unknown> | null;
  dry_run_payload?: Record<string, unknown> | null;
  last_error?: string | null;
  metadata?: Record<string, unknown> | null;
  [key: string]: unknown;
}

export interface SupervisedActionList {
  actions: SupervisedAction[];
  total?: number;
  [key: string]: unknown;
}

export interface ActionMutationRequest {
  idempotency_key?: string;
}

export interface DryRunActionRequest extends ActionMutationRequest {
  dry_run_payload?: Record<string, unknown>;
}
