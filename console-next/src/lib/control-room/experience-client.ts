import { api } from "@/lib/api";
import {
  controlRoomExperienceV2Schema,
  controlRoomFreshnessSchema,
  experienceActionPreviewResponseSchema,
  type ControlRoomExperienceV2,
  type ControlRoomFreshness,
  type ExperienceActionPreviewResponse,
} from "@/lib/control-room/experience-contract";

export const CONTROL_ROOM_EXPERIENCE_ENDPOINT = "/api/control-room/experience/v2";
export const CONTROL_ROOM_FRESHNESS_ENDPOINT =
  "/api/control-room/experience/v2/freshness";
export const CONTROL_ROOM_ACTION_PREVIEW_ENDPOINT =
  "/api/control-room/actions/preview";

export async function getControlRoomExperience(): Promise<ControlRoomExperienceV2> {
  const response = await api.get<unknown>(CONTROL_ROOM_EXPERIENCE_ENDPOINT);
  return controlRoomExperienceV2Schema.parse(response.data);
}

export async function getControlRoomFreshness(): Promise<ControlRoomFreshness> {
  const response = await api.get<unknown>(CONTROL_ROOM_FRESHNESS_ENDPOINT);
  return controlRoomFreshnessSchema.parse(response.data);
}

export async function previewControlRoomExperienceAction(
  actionHandle: string,
): Promise<ExperienceActionPreviewResponse> {
  const response = await api.post<unknown>(CONTROL_ROOM_ACTION_PREVIEW_ENDPOINT, {
    action_handle: actionHandle,
  });
  const preview = experienceActionPreviewResponseSchema.parse(response.data);
  if (preview.action_handle !== actionHandle) {
    throw new Error("Invalid preview response");
  }
  return preview;
}
