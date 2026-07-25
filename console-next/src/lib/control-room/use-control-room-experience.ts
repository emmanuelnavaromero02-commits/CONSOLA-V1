"use client";

import { useQuery } from "@tanstack/react-query";

import { readCookie } from "@/lib/api";
import { getControlRoomExperience } from "@/lib/control-room/experience-client";
import type { ControlRoomExperience } from "@/lib/control-room/experience-contract";
import { ACTIVE_WORKSPACE_COOKIE } from "@/lib/workspace-context";

type ExperienceFetcher = (
  expectedWorkspaceId: string | null,
) => Promise<ControlRoomExperience>;

export const controlRoomExperienceKey = (workspaceId: string | null) =>
  ["control-room", "experience", workspaceId ?? "unscoped"] as const;

export function controlRoomExperienceQueryOptions(
  workspaceId: string | null,
  fetcher: ExperienceFetcher = getControlRoomExperience,
) {
  return {
    queryKey: controlRoomExperienceKey(workspaceId),
    queryFn: () => fetcher(workspaceId),
    retry: false,
    refetchOnMount: false,
    refetchOnReconnect: false,
    refetchOnWindowFocus: false,
    refetchInterval: false,
    staleTime: Number.POSITIVE_INFINITY,
  } as const;
}

export function useControlRoomExperience() {
  const workspaceId = readCookie(ACTIVE_WORKSPACE_COOKIE);
  return useQuery(controlRoomExperienceQueryOptions(workspaceId));
}
