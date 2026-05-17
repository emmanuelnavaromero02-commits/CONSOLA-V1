"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { BriefingHighlight, BriefingResponse } from "./types";

/**
 * v1.44.4 Task B — proactive briefing hook.
 *
 * Used by both:
 *   - /dashboard (BriefingCards above the KPI grid),
 *   - /workspace (sidebar surfaces unread highlights as
 *     suggested prompts when the chat is empty).
 *
 * ``dismissMutation`` optimistically removes the highlight from
 * the cached list so the UI feels instant; the server enforces
 * persistent dismissal via /api/copilot/briefing/{id}/dismiss.
 */
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


export function useBriefing() {
  const qc = useQueryClient();

  const briefingQuery = useQuery<BriefingHighlight[]>({
    queryKey: ["copilot", "briefing"],
    queryFn:  listBriefing,
    // The briefing analyzers are deliberately cheap (a few short
    // SQL queries) — poll every 60 s so a new alert surfaces
    // without a full refresh.
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  const dismissMutation = useMutation<unknown, Error, string>({
    mutationFn: (id) => dismissBriefing(id),
    onMutate: async (id) => {
      await qc.cancelQueries({ queryKey: ["copilot", "briefing"] });
      const previous = qc.getQueryData<BriefingHighlight[]>([
        "copilot", "briefing",
      ]) ?? [];
      qc.setQueryData<BriefingHighlight[]>(
        ["copilot", "briefing"],
        previous.filter((h) => h.id !== id),
      );
      return { previous };
    },
    onError: (_err, _id, ctx) => {
      const previous = (ctx as { previous?: BriefingHighlight[] } | undefined)?.previous;
      if (previous) {
        qc.setQueryData(["copilot", "briefing"], previous);
      }
    },
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["copilot", "briefing"] });
    },
  });

  return { briefingQuery, dismissMutation };
}
