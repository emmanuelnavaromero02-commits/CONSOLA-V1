"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type { BriefingHighlight, BriefingResponse } from "./types";

const POLL_MS         = 60_000;
const UNDO_GRACE_MS   = 3_000;
const UNDO_TOAST_MS   = UNDO_GRACE_MS + 500;


async function listBriefing(): Promise<BriefingHighlight[]> {
  const { data } = await api.get<BriefingResponse>("/api/copilot/briefing");
  return data.highlights ?? [];
}


async function dismissBriefing(id: string): Promise<void> {
  await api.post(
    `/api/copilot/briefing/${encodeURIComponent(id)}/dismiss`,
    {},
  );
}


interface PendingDismiss {
  highlight: BriefingHighlight;
  timer:     ReturnType<typeof setTimeout>;
}


export function useBriefing() {
  const qc = useQueryClient();

  const [pendingIds, setPendingIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );

  const pending = useRef<Map<string, PendingDismiss>>(new Map());

  const briefingQuery = useQuery<BriefingHighlight[], Error, BriefingHighlight[]>({
    queryKey: ["copilot", "briefing"],
    queryFn:  listBriefing,
    refetchInterval: POLL_MS,
    staleTime: 30_000,
    select: (highlights) =>
      pendingIds.size === 0
        ? highlights
        : highlights.filter((h) => !pendingIds.has(h.id)),
  });

  const dismissMutation = useMutation<unknown, Error, string>({
    mutationFn: (id) => dismissBriefing(id),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["copilot", "briefing"] });
    },
  });

  const scheduleRealDismiss = useCallback(
    (id: string) => {
      pending.current.delete(id);
      setPendingIds((prev) => {
        if (!prev.has(id)) return prev;
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      dismissMutation.mutate(id, {
        onError: () => {
          toast.error("No se pudo descartar el aviso. Vuelve a intentarlo.");
        },
      });
    },
    [dismissMutation],
  );

  const dismissWithUndo = useCallback((highlight: BriefingHighlight) => {
    const existing = pending.current.get(highlight.id);
    if (existing) clearTimeout(existing.timer);

    setPendingIds((prev) => {
      if (prev.has(highlight.id)) return prev;
      const next = new Set(prev);
      next.add(highlight.id);
      return next;
    });

    const timer = setTimeout(() => {
      scheduleRealDismiss(highlight.id);
    }, UNDO_GRACE_MS);

    pending.current.set(highlight.id, { highlight, timer });

    toast("Descartando aviso…", {
      id: `briefing-dismiss-${highlight.id}`,  // dedup repeats
      action: {
        label: "Deshacer",
        onClick: () => {
          const entry = pending.current.get(highlight.id);
          if (!entry) return;
          clearTimeout(entry.timer);
          pending.current.delete(highlight.id);
          setPendingIds((prev) => {
            if (!prev.has(highlight.id)) return prev;
            const next = new Set(prev);
            next.delete(highlight.id);
            return next;
          });
          qc.invalidateQueries({ queryKey: ["copilot", "briefing"] });
        },
      },
      duration: UNDO_TOAST_MS,
    });
  }, [qc, scheduleRealDismiss]);

  useEffect(() => {
    const pendingMap = pending.current;
    return () => {
      pendingMap.forEach(({ timer }) => clearTimeout(timer));
      pendingMap.clear();
    };
  }, []);

  return { briefingQuery, dismissMutation, dismissWithUndo };
}
