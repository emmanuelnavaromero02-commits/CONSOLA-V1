import { api } from "@/lib/api";
import {
  controlRoomExperienceSchema,
  type ControlRoomExperience,
} from "@/lib/control-room/experience-contract";

export const CONTROL_ROOM_EXPERIENCE_ENDPOINT = "/api/control-room/experience";

export async function getControlRoomExperience(
  expectedWorkspaceId: string | null = null,
): Promise<ControlRoomExperience> {
  const response = await api.get<unknown>(CONTROL_ROOM_EXPERIENCE_ENDPOINT);
  const experience = controlRoomExperienceSchema.parse(response.data);
  if (
    expectedWorkspaceId !== null &&
    experience.scope.workspace_id !== expectedWorkspaceId
  ) {
    throw new Error("Control Room experience scope mismatch");
  }
  return experience;
}
