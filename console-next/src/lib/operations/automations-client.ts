import { z } from "zod";

import { api } from "@/lib/api";

export const AUTOMATIONS_ENDPOINT = "/api/pipelines/automations";

const dateTimeSchema = z
  .string()
  .datetime({ offset: true })
  .refine((value) => Number.isFinite(Date.parse(value)), "Invalid datetime");

const automationRunSchema = z
  .object({
    status: z.string().regex(/^[a-z_]{1,40}$/),
    started_at: dateTimeSchema.nullable(),
    finished_at: dateTimeSchema.nullable(),
  })
  .strict();

export const automationSchema = z
  .object({
    dag_id: z.string().regex(/^[A-Za-z0-9_.-]{1,250}$/),
    label: z.string().min(1).max(160),
    cartridge_id: z.string().regex(/^[a-z0-9_]{1,120}$/).nullable(),
    kind: z.enum(["manual", "scheduled"]),
    schedule_description: z.string().max(160).nullable(),
    state: z.enum(["active", "paused_by_operator", "paused_manual", "unavailable"]),
    state_note_es: z.string().min(1).max(240),
    active_runs: z.number().int().nonnegative().nullable(),
    active_runs_capped: z.boolean(),
    last_run: automationRunSchema.nullable(),
  })
  .strict();

export const automationsResponseSchema = z
  .object({
    schema_version: z.literal("pipeline-automations/v1"),
    checked_at: dateTimeSchema,
    airflow_available: z.boolean(),
    automations: z.array(automationSchema).max(200),
  })
  .strict();

export type Automation = z.infer<typeof automationSchema>;
export type AutomationsResponse = z.infer<typeof automationsResponseSchema>;

export async function listAutomations(): Promise<AutomationsResponse> {
  const response = await api.get<unknown>(AUTOMATIONS_ENDPOINT);
  return automationsResponseSchema.parse(response.data);
}
