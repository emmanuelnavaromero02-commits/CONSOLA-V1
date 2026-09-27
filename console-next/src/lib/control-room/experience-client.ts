import { api } from "@/lib/api";
import {
  controlRoomExperienceV2Schema,
  controlRoomFreshnessSchema,
  decisionProposalResponseSchema,
  exceptionApprovalResponseSchema,
  exceptionReopenResponseSchema,
  experienceActionPreviewResponseSchema,
  studioTargetResponseSchema,
  type ControlRoomExperienceV2,
  type ControlRoomFreshness,
  type DecisionProposalResponse,
  type ExceptionApprovalResponse,
  type ExceptionReopenResponse,
  type ExperienceActionPreviewResponse,
  type StudioTargetResponse,
} from "@/lib/control-room/experience-contract";

export const CONTROL_ROOM_EXPERIENCE_ENDPOINT = "/api/control-room/experience/v2";
export const CONTROL_ROOM_FRESHNESS_ENDPOINT =
  "/api/control-room/experience/v2/freshness";
export const CONTROL_ROOM_ACTION_PREVIEW_ENDPOINT =
  "/api/control-room/actions/preview";
export const CONTROL_ROOM_EXCEPTION_ENDPOINT = "/api/control-room/actions/exception";
export const CONTROL_ROOM_EXCEPTION_REOPEN_ENDPOINT =
  "/api/control-room/actions/exception-reopen";
export const CONTROL_ROOM_DECISION_PROPOSAL_ENDPOINT =
  "/api/control-room/actions/decision-proposal";
export const CONTROL_ROOM_STUDIO_TARGET_ENDPOINT =
  "/api/control-room/actions/studio-target";

export async function getControlRoomExperience(): Promise<ControlRoomExperienceV2> {
  const response = await api.get<unknown>(CONTROL_ROOM_EXPERIENCE_ENDPOINT);
  return controlRoomExperienceV2Schema.parse(response.data);
}

export async function getControlRoomFreshness(): Promise<ControlRoomFreshness> {
  const response = await api.get<unknown>(CONTROL_ROOM_FRESHNESS_ENDPOINT);
  return controlRoomFreshnessSchema.parse(response.data);
}

function boundToHandle<T extends { action_handle: string }>(
  result: T,
  actionHandle: string,
): T {
  if (result.action_handle !== actionHandle) {
    throw new Error("Invalid action response");
  }
  return result;
}

export async function previewControlRoomExperienceAction(
  actionHandle: string,
): Promise<ExperienceActionPreviewResponse> {
  const response = await api.post<unknown>(CONTROL_ROOM_ACTION_PREVIEW_ENDPOINT, {
    action_handle: actionHandle,
  });
  return boundToHandle(
    experienceActionPreviewResponseSchema.parse(response.data),
    actionHandle,
  );
}

export async function approveControlRoomException(
  actionHandle: string,
  reason: string,
  idempotencyKey: string,
): Promise<ExceptionApprovalResponse> {
  const response = await api.post<unknown>(CONTROL_ROOM_EXCEPTION_ENDPOINT, {
    action_handle: actionHandle,
    reason,
    idempotency_key: idempotencyKey,
  });
  return boundToHandle(exceptionApprovalResponseSchema.parse(response.data), actionHandle);
}

export async function reopenControlRoomException(
  actionHandle: string,
  reason: string,
  idempotencyKey: string,
): Promise<ExceptionReopenResponse> {
  const response = await api.post<unknown>(CONTROL_ROOM_EXCEPTION_REOPEN_ENDPOINT, {
    action_handle: actionHandle,
    reason,
    idempotency_key: idempotencyKey,
  });
  return boundToHandle(exceptionReopenResponseSchema.parse(response.data), actionHandle);
}

export async function createControlRoomDecisionProposal(
  actionHandle: string,
  idempotencyKey: string,
): Promise<DecisionProposalResponse> {
  const response = await api.post<unknown>(CONTROL_ROOM_DECISION_PROPOSAL_ENDPOINT, {
    action_handle: actionHandle,
    idempotency_key: idempotencyKey,
  });
  return boundToHandle(decisionProposalResponseSchema.parse(response.data), actionHandle);
}

export async function resolveControlRoomStudioTarget(
  actionHandle: string,
): Promise<StudioTargetResponse> {
  const response = await api.post<unknown>(CONTROL_ROOM_STUDIO_TARGET_ENDPOINT, {
    action_handle: actionHandle,
  });
  return boundToHandle(studioTargetResponseSchema.parse(response.data), actionHandle);
}
