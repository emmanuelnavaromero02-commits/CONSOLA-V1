"use client";

import {
  useQuery,
  type QueryClient,
} from "@tanstack/react-query";
import { useCallback, useEffect, useLayoutEffect, useRef } from "react";

import { getControlRoomFreshness } from "@/lib/control-room/experience-client";
import type { ControlRoomFreshness } from "@/lib/control-room/experience-contract";
import { controlRoomExperienceKey } from "@/lib/control-room/use-control-room-experience";

export const MAX_EXPERIENCE_AGE_MS = 10 * 60_000;

type FreshnessFetcher = () => Promise<ControlRoomFreshness>;

export const controlRoomFreshnessKey = (workspaceId: string | null) =>
  ["control-room", "freshness", "v1", workspaceId ?? "unscoped"] as const;

export function controlRoomFreshnessQueryOptions(
  workspaceId: string | null,
  fetcher: FreshnessFetcher = getControlRoomFreshness,
) {
  return {
    queryKey: controlRoomFreshnessKey(workspaceId),
    queryFn: () => fetcher(),
    retry: false,
    refetchOnMount: true,
    refetchOnReconnect: true,
    refetchOnWindowFocus: true,
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    staleTime: 0,
  } as const;
}

export interface LiveRefreshState {
  baseline: string | null;
  pending: boolean;
}

export interface LiveRefreshInput {
  state: LiveRefreshState;
  fingerprint: string | null;
  experienceUpdatedAt: number;
  experienceFetching: boolean;
  paused: boolean;
  now: number;
}

export function decideExperienceRefresh({
  state,
  fingerprint,
  experienceUpdatedAt,
  experienceFetching,
  paused,
  now,
}: LiveRefreshInput): { state: LiveRefreshState; refetch: boolean } {
  const changed =
    fingerprint !== null && state.baseline !== null && fingerprint !== state.baseline;
  const baseline = fingerprint ?? state.baseline;
  const aged =
    experienceUpdatedAt > 0 && now - experienceUpdatedAt >= MAX_EXPERIENCE_AGE_MS;
  if (experienceFetching) {
    return { state: { baseline, pending: false }, refetch: false };
  }
  if (paused) {
    return { state: { baseline, pending: state.pending || changed }, refetch: false };
  }
  return {
    state: { baseline, pending: false },
    refetch: changed || state.pending || aged,
  };
}

export async function invalidateControlRoomLive(
  queryClient: QueryClient,
  workspaceId: string | null,
): Promise<void> {
  await Promise.all([
    queryClient.invalidateQueries({
      queryKey: controlRoomExperienceKey(workspaceId),
      exact: true,
      refetchType: "active",
    }),
    queryClient.invalidateQueries({
      queryKey: controlRoomFreshnessKey(workspaceId),
      exact: true,
      refetchType: "active",
    }),
  ]);
}

export interface LiveExperienceHandle {
  dataUpdatedAt: number;
  isFetching: boolean;
  refetch: () => Promise<unknown>;
}

export function useControlRoomLive({
  workspaceId,
  experience,
  paused,
  fetcher,
}: {
  workspaceId: string | null;
  experience: LiveExperienceHandle;
  paused: boolean;
  fetcher?: FreshnessFetcher;
}) {
  const freshness = useQuery(controlRoomFreshnessQueryOptions(workspaceId, fetcher));
  const experienceRef = useRef(experience);
  const stateRef = useRef<LiveRefreshState & { workspaceId: string | null }>({
    workspaceId,
    baseline: null,
    pending: false,
  });

  useLayoutEffect(() => {
    experienceRef.current = experience;
  });

  const fingerprint = freshness.data?.fingerprint ?? null;
  const freshnessUpdatedAt = freshness.dataUpdatedAt;

  useEffect(() => {
    const previous = stateRef.current;
    const state =
      previous.workspaceId === workspaceId
        ? previous
        : { workspaceId, baseline: null, pending: false };
    const current = experienceRef.current;
    const decision = decideExperienceRefresh({
      state,
      fingerprint,
      experienceUpdatedAt: current.dataUpdatedAt,
      experienceFetching: current.isFetching,
      paused,
      now: Date.now(),
    });
    stateRef.current = { workspaceId, ...decision.state };
    if (decision.refetch) void current.refetch().catch(() => undefined);
  }, [fingerprint, freshnessUpdatedAt, paused, workspaceId]);

  const refetchFreshness = freshness.refetch;
  const refreshAll = useCallback(() => {
    void experienceRef.current.refetch().catch(() => undefined);
    void refetchFreshness().catch(() => undefined);
  }, [refetchFreshness]);

  return {
    checkedAt: freshness.dataUpdatedAt > 0 ? freshness.dataUpdatedAt : null,
    dataRefreshedAt: freshness.data?.data_refreshed_at ?? null,
    offline: freshness.isError || freshness.fetchStatus === "paused",
    refreshing: freshness.isFetching,
    refreshAll,
  } as const;
}
