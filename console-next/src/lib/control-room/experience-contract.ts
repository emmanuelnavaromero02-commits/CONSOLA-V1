import { z } from "zod";

const dateTimeSchema = z
  .string()
  .datetime({ offset: true })
  .refine((value) => Number.isFinite(Date.parse(value)), "Invalid datetime");

const scopeSchema = z
  .object({
    tenant_id: z.string(),
    workspace_id: z.string(),
  })
  .strict();

const metricSchema = z
  .object({
    name: z.string(),
    kind: z.enum([
      "count",
      "rate",
      "percentage",
      "average",
      "division",
      "amount",
      "scalar",
    ]),
    value: z.number().finite(),
    unit: z.string().optional(),
  })
  .strict();

const decisionSchema = z
  .object({
    reference: z.number().int(),
    status: z.enum(["decision_created", "approved", "resolved"]),
  })
  .strict();

const factSchema = z
  .object({
    kind: z.enum(["anomaly", "signal", "alert", "kpi"]),
    title: z.string(),
    severity: z.enum(["critical", "high", "medium", "low"]),
    observed_at: dateTimeSchema,
    stale: z.boolean().default(false),
    entity_label: z.string().optional(),
    metric: metricSchema.optional(),
    decision: decisionSchema.optional(),
  })
  .strict();

const sectionSchema = z
  .object({
    id: z.string(),
    cartridge_id: z.string(),
    module_id: z.string(),
    title: z.string(),
    domain: z.string(),
    facts: z.array(factSchema),
  })
  .strict();

export const controlRoomExperienceSchema = z
  .object({
    schema_version: z.literal("control-room-experience/v1"),
    generated_at: dateTimeSchema,
    scope: scopeSchema,
    sections: z.array(sectionSchema).default([]),
  })
  .strict();

export type ControlRoomExperience = z.infer<typeof controlRoomExperienceSchema>;
export type ExperienceSection = z.infer<typeof sectionSchema>;
export type ExperienceFact = z.infer<typeof factSchema>;
export type ExperienceMetric = z.infer<typeof metricSchema>;
export type ExperienceDecision = z.infer<typeof decisionSchema>;
