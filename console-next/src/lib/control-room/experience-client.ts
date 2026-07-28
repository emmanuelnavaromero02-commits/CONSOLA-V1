import { api } from "@/lib/api";
import {
  controlRoomExperienceSchema,
  type ControlRoomExperience,
} from "@/lib/control-room/experience-contract";

export const CONTROL_ROOM_EXPERIENCE_ENDPOINT = "/api/control-room/experience";

export async function getControlRoomExperience(): Promise<ControlRoomExperience> {
  const response = await api.get<unknown>(CONTROL_ROOM_EXPERIENCE_ENDPOINT);
  return controlRoomExperienceSchema.parse(response.data);
}
