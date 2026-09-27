"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useRef } from "react";

import { getMeAccess } from "@/lib/admin-surfaces";
import { refreshControlRoomState } from "@/lib/control-room/experience-client";
import { invalidateControlRoomLive } from "@/lib/control-room/use-control-room-live";

export function canPersistControlRoom(permissions: readonly string[] | undefined): boolean {
  return (permissions ?? []).includes("control_room.write");
}

export function useControlRoomRefresh({
  workspaceId,
  refetchOnly,
}: {
  workspaceId: string | null;
  refetchOnly: () => void;
}) {
  const queryClient = useQueryClient();
  const access = useQuery({ queryKey: ["me", "access"], queryFn: getMeAccess, staleTime: 60_000 });
  const canPersist = canPersistControlRoom(access.data?.permissions);
  const inFlight = useRef(false);
  const mutation = useMutation({
    mutationFn: () => refreshControlRoomState(),
    onSuccess: () => invalidateControlRoomLive(queryClient, workspaceId),
    onSettled: () => {
      inFlight.current = false;
    },
  });
  const { isPending, mutate, reset } = mutation;

  const refresh = useCallback(() => {
    if (inFlight.current) return;
    if (!canPersist) {
      reset();
      refetchOnly();
      return;
    }
    inFlight.current = true;
    mutate();
  }, [canPersist, mutate, refetchOnly, reset]);

  return {
    refresh,
    canPersist,
    refreshing: isPending,
    failed: mutation.isError,
  } as const;
}
