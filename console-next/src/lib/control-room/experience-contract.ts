import { z } from "zod";

const dateTimeSchema = z
  .string()
  .datetime({ offset: true })
  .refine((value) => Number.isFinite(Date.parse(value)), "Invalid datetime");

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
    status: z.enum(["decision_created", "approved", "resolved"]),
  })
  .strict();

const factSchema = z
  .object({
    kind: z.enum(["anomaly", "signal", "alert", "kpi"]),
    title: z.string(),
    severity: z.enum(["critical", "high", "medium", "low"]),
    observed_at: dateTimeSchema,
    stale: z.boolean().nullish().transform((value) => value ?? null),
    entity_label: z.string().optional(),
    metric: metricSchema.optional(),
    decision: decisionSchema.optional(),
  })
  .strict();

const sectionSchema = z
  .object({
    title: z.string(),
    domain: z.string(),
    facts: z.array(factSchema),
  })
  .strict();

export const controlRoomExperienceSchema = z
  .object({
    schema_version: z.literal("control-room-experience/v1"),
    generated_at: dateTimeSchema,
    sections: z.array(sectionSchema).default([]),
  })
  .strict();

export type ControlRoomExperience = z.infer<typeof controlRoomExperienceSchema>;
export type ExperienceSection = z.infer<typeof sectionSchema>;
export type ExperienceFact = z.infer<typeof factSchema>;
export type ExperienceMetric = z.infer<typeof metricSchema>;
export type ExperienceDecision = z.infer<typeof decisionSchema>;

const experienceActionHandleSchema = z.string().regex(/^[a-f0-9]{64}$/);
const disabledReasonSchema = z.enum([
  "Actualiza los datos antes de continuar.",
  "Completa los datos requeridos antes de continuar.",
]);

const experienceActionSchema = z
  .object({
    action_handle: experienceActionHandleSchema,
    label: z
      .string()
      .min(1)
      .max(120)
      .refine((value) => value.trim().length > 0, "Empty action label"),
    enabled: z.boolean(),
    requires_approval: z.boolean(),
    disabled_reason: disabledReasonSchema.optional(),
  })
  .strict()
  .superRefine((action, context) => {
    if (action.enabled === (action.disabled_reason !== undefined)) {
      context.addIssue({
        code: "custom",
        message: "Action enabled state does not match disabled reason",
      });
    }
  });

const experienceFactV2Schema = z
  .object({
    kind: z.enum(["anomaly", "signal", "alert", "kpi"]),
    title: z.string(),
    severity: z.enum(["critical", "high", "medium", "low"]),
    observed_at: dateTimeSchema,
    stale: z.boolean().nullish().transform((value) => value ?? null),
    entity_label: z.string().optional(),
    metric: metricSchema.optional(),
    decision: decisionSchema.optional(),
    actions: z.array(experienceActionSchema).max(8),
  })
  .strict()
  .superRefine((fact, context) => {
    const handles = fact.actions.map((action) => action.action_handle);
    if (new Set(handles).size !== handles.length) {
      context.addIssue({ code: "custom", message: "Duplicate action handles" });
    }
  });

const experienceSectionV2Schema = z
  .object({
    title: z.string(),
    facts: z.array(experienceFactV2Schema),
  })
  .strict();

export const controlRoomExperienceV2Schema = z
  .object({
    schema_version: z.literal("control-room-experience/v2"),
    generated_at: dateTimeSchema,
    sections: z.array(experienceSectionV2Schema),
  })
  .strict();

export const experienceActionPreviewResponseSchema = z
  .object({
    action_handle: experienceActionHandleSchema,
    operation: z.literal("preview"),
    status: z.literal("generated"),
    message: z.literal("Preview generado; no se ejecuto ningun cambio externo."),
  })
  .strict();

export type ControlRoomExperienceV2 = z.infer<typeof controlRoomExperienceV2Schema>;
export type ExperienceSectionV2 = z.infer<typeof experienceSectionV2Schema>;
export type ExperienceFactV2 = z.infer<typeof experienceFactV2Schema>;
export type ExperienceAction = z.infer<typeof experienceActionSchema>;
export type ExperienceActionPreviewResponse = z.infer<
  typeof experienceActionPreviewResponseSchema
>;
