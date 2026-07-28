"use client";

import { useQuery } from "@tanstack/react-query";

import { readCookie } from "@/lib/api";
import { getControlRoomExperience } from "@/lib/control-room/experience-client";
import type { ControlRoomExperienceV2 } from "@/lib/control-room/experience-contract";
import { ACTIVE_WORKSPACE_COOKIE } from "@/lib/workspace-context";

type ExperienceFetcher = () => Promise<ControlRoomExperienceV2>;

export const controlRoomExperienceKey = (workspaceId: string | null) =>
  ["control-room", "experience", "v2", workspaceId ?? "unscoped"] as const;

export function controlRoomExperienceQueryOptions(
  workspaceId: string | null,
  fetcher: ExperienceFetcher = getControlRoomExperience,
) {
  return {
    queryKey: controlRoomExperienceKey(workspaceId),
    queryFn: () => fetcher(),
    retry: false,
    refetchOnMount: true,
    refetchOnReconnect: false,
    refetchOnWindowFocus: false,
    refetchInterval: false,
    staleTime: 15_000,
  } as const;
}

export function useControlRoomExperience() {
  const workspaceId = readCookie(ACTIVE_WORKSPACE_COOKIE);
  const query = useQuery(controlRoomExperienceQueryOptions(workspaceId));
  return { ...query, workspaceId };
}
