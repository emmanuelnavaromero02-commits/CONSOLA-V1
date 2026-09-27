import { z } from "zod";

import { api } from "@/lib/api";
import { experienceNarrativeSchema } from "@/lib/control-room/experience-contract";

export const COUNCIL_ENDPOINT = "/api/control-room/council";
export const NO_ESTIMATE_LABEL = "Sin estimación";

const handleSchema = z.string().regex(/^[a-f0-9]{64}$/);
const dateTimeSchema = z
  .string()
  .datetime({ offset: true })
  .refine((value) => Number.isFinite(Date.parse(value)), "Invalid datetime");
const dateSchema = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const copySchema = z
  .string()
  .min(1)
  .max(240)
  .refine((value) => value.trim().length > 0, "Empty copy");

export const councilDisabledReasons = [
  "Requiere la aprobación de otra persona del equipo.",
  "La propuesta venció; su autor puede renovarla.",
  "La tarea de seguimiento no está preparada; su autor puede renovarla.",
  "Los datos de origen cambiaron desde que se preparó la propuesta.",
  "Las tareas de seguimiento están desactivadas en este espacio de trabajo.",
  "Ajustaste un umbral que originó esta sugerencia; requiere la aprobación de otra persona del equipo.",
  "La propuesta no tiene un autor vigente; ciérrala desde el Registro.",
] as const;

export const councilStates = [
  "pending_approval",
  "needs_other_approver",
  "expired",
  "no_followup",
  "source_changed",
  "completed",
] as const;

const impactSchema = z
  .object({
    kind: z.enum(["money", "time", "none"]),
    value: z.number().finite().optional(),
    currency: z.string().regex(/^[A-Z]{3}$/).optional(),
    unit: z.literal("horas").optional(),
    basis: z.enum(["persisted", "rule", "observed"]).optional(),
    formula: copySchema.optional(),
    label: copySchema,
  })
  .strict()
  .superRefine((impact, context) => {
    const hasFigures =
      impact.value !== undefined ||
      impact.currency !== undefined ||
      impact.unit !== undefined ||
      impact.basis !== undefined;
    if (impact.kind === "none") {
      if (hasFigures || impact.label !== NO_ESTIMATE_LABEL) {
        context.addIssue({ code: "custom", message: "Impact without estimate has figures" });
      }
      return;
    }
    if (impact.value === undefined || impact.basis === undefined) {
      context.addIssue({ code: "custom", message: "Impact estimate without value or basis" });
    }
    if ((impact.basis === "persisted") === (impact.formula !== undefined)) {
      context.addIssue({ code: "custom", message: "Rule impacts show their formula" });
    }
    if ((impact.kind === "money") !== (impact.currency !== undefined)) {
      context.addIssue({ code: "custom", message: "Only money carries currency" });
    }
    if ((impact.kind === "time") !== (impact.unit !== undefined)) {
      context.addIssue({ code: "custom", message: "Only time carries unit" });
    }
  });

const evidenceSchema = z
  .object({
    label: z.enum(["Entidad", "Indicador", "Fecha del dato", "Severidad"]),
    value: copySchema,
  })
  .strict();

export const councilProposalSchema = z
  .object({
    proposal_id: handleSchema,
    origin: z.enum(["system", "person"]),
    authored_by_you: z.boolean(),
    decision_id: z.number().int().positive().optional(),
    title: copySchema,
    section_title: copySchema.optional(),
    severity: z.enum(["critical", "high", "medium", "low"]),
    observed_at: dateTimeSchema.optional(),
    created_at: dateTimeSchema.optional(),
    commitment_date: dateSchema.optional(),
    state: z.enum(councilStates),
    impact: impactSchema,
    evidence: z.array(evidenceSchema).max(8),
    narrative: experienceNarrativeSchema.optional(),
    can_approve: z.boolean(),
    can_discard: z.boolean(),
    can_renew: z.boolean(),
    disabled_reason: z.enum(councilDisabledReasons).optional(),
  })
  .strict()
  .superRefine((proposal, context) => {
    if (proposal.can_approve && proposal.disabled_reason !== undefined) {
      context.addIssue({ code: "custom", message: "Approvable proposal has a disabled reason" });
    }
    if (proposal.origin === "person" && proposal.decision_id === undefined) {
      context.addIssue({ code: "custom", message: "Person proposal without decision" });
    }
    if (
      proposal.origin === "system" &&
      ((proposal.decision_id !== undefined && proposal.state !== "completed") ||
        proposal.can_renew ||
        proposal.authored_by_you)
    ) {
      context.addIssue({ code: "custom", message: "System suggestion with author data" });
    }
    if (
      proposal.state === "completed" &&
      (proposal.can_approve || proposal.can_discard || proposal.can_renew)
    ) {
      context.addIssue({ code: "custom", message: "Completed proposal is actionable" });
    }
  });

export const actionCouncilSchema = z
  .object({
    schema_version: z.literal("control-room-council/v1"),
    generated_at: dateTimeSchema,
    proposals: z.array(councilProposalSchema).max(50),
  })
  .strict();

export const councilApproveResponseSchema = z
  .object({
    status: z.literal("approved_with_followup"),
    decision_id: z.number().int().positive(),
    followup_created: z.literal(true),
    message: copySchema,
  })
  .strict();

export const councilDiscardResponseSchema = z
  .object({
    status: z.literal("discarded"),
    decision_id: z.number().int().positive().nullish(),
    message: copySchema,
  })
  .strict();

export const councilRenewResponseSchema = z
  .object({
    status: z.literal("renewed"),
    decision_id: z.number().int().positive(),
    message: copySchema,
  })
  .strict();

export type ActionCouncil = z.infer<typeof actionCouncilSchema>;
export type CouncilProposal = z.infer<typeof councilProposalSchema>;
export type CouncilImpact = z.infer<typeof impactSchema>;
export type CouncilApproveResponse = z.infer<typeof councilApproveResponseSchema>;
export type CouncilDiscardResponse = z.infer<typeof councilDiscardResponseSchema>;
export type CouncilRenewResponse = z.infer<typeof councilRenewResponseSchema>;

function proposalPath(proposalId: string, command: "approve" | "discard" | "renew"): string {
  return `${COUNCIL_ENDPOINT}/${handleSchema.parse(proposalId)}/${command}`;
}

export async function getActionCouncil(): Promise<ActionCouncil> {
  const response = await api.get<unknown>(COUNCIL_ENDPOINT);
  return actionCouncilSchema.parse(response.data);
}

export async function approveCouncilProposal(
  proposalId: string,
  idempotencyKey: string,
): Promise<CouncilApproveResponse> {
  const response = await api.post<unknown>(proposalPath(proposalId, "approve"), {
    idempotency_key: idempotencyKey,
    confirm: true,
  });
  return councilApproveResponseSchema.parse(response.data);
}

export async function discardCouncilProposal(
  proposalId: string,
  reason: string,
  idempotencyKey: string,
): Promise<CouncilDiscardResponse> {
  const response = await api.post<unknown>(proposalPath(proposalId, "discard"), {
    reason,
    idempotency_key: idempotencyKey,
  });
  return councilDiscardResponseSchema.parse(response.data);
}

export async function renewCouncilProposal(
  proposalId: string,
): Promise<CouncilRenewResponse> {
  const response = await api.post<unknown>(proposalPath(proposalId, "renew"), {
    confirm: true,
  });
  return councilRenewResponseSchema.parse(response.data);
}
