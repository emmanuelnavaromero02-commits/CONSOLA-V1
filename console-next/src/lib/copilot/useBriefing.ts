"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { api } from "@/lib/api";
import type { BriefingHighlight, BriefingResponse } from "./types";

/**
 * v1.44.4 Task B — proactive briefing hook.
 *
 * Used by /dashboard (BriefingSection above the KPI grid).
 *
 * The dismiss flow on the backend is a one-shot
 * POST /api/copilot/briefing/{id}/dismiss — there is no
 * undismiss endpoint. To implement the brief's "undo" UX
 * SAFELY against a backend that lacks rollback support, this
 * hook holds the dismiss for ``UNDO_GRACE_MS`` before actually
 * firing the network call AND filters in-flight pending IDs
 * out of the polling refetch result so a 60 s refetch landing
 * inside the grace window can't resurrect the optimistically-
 * removed card.
 *
 * Round 1 review fixes embedded here:
 *   - P0-1: unmount no longer fires a stealth dismiss POST.
 *     Cleanup just cancels pending timers — the user's intent
 *     (which they could still undo!) is NOT committed silently.
 *   - P0-2: pendingIds state + ``select`` filter prevents the
 *     polling refetch from resurrecting cards mid-grace.
 *   - P0-3: toast duration > timer duration so the "Deshacer"
 *     affordance always outlives the cancel window.
 */
const POLL_MS         = 60_000;
const UNDO_GRACE_MS   = 3_000;
// Toast must outlive the timer so the user always has an
// affordance to undo for the full grace window. 500 ms buffer.
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

  // ``pendingIds`` is REACTIVE so the query's ``select`` filter
  // re-runs when the set changes. Using state (not ref) means
  // an optimistic dismiss survives any background refetch that
  // lands before the grace timer fires.
  const [pendingIds, setPendingIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );

  // ``pending`` tracks the timer + saved highlight for each
  // grace window so we can cancel + restore on undo. Held in a
  // ref because the timer reference itself isn't render-state.
  const pending = useRef<Map<string, PendingDismiss>>(new Map());

  const briefingQuery = useQuery<BriefingHighlight[], Error, BriefingHighlight[]>({
    queryKey: ["copilot", "briefing"],
    queryFn:  listBriefing,
    refetchInterval: POLL_MS,
    staleTime: 30_000,
    // The select filter strips highlights currently in the
    // 3 s grace window so a background refetch can't resurrect
    // the card while the user still has an undo affordance.
    select: (highlights) =>
      pendingIds.size === 0
        ? highlights
        : highlights.filter((h) => !pendingIds.has(h.id)),
  });

  /**
   * Direct dismiss mutation — used internally by the undo timer
   * once the grace window elapses. Exposed for tests + advanced
   * callers that don't want the toast affordance.
   */
  const dismissMutation = useMutation<unknown, Error, string>({
    mutationFn: (id) => dismissBriefing(id),
    onSettled: () => {
      qc.invalidateQueries({ queryKey: ["copilot", "briefing"] });
    },
  });

  // Schedule the eventual real POST after the grace window
  // elapses. Held as a callback so the timer body stays stable
  // across renders.
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
          // Backend rejected — the refetch from onSettled will
          // bring the card back. Surface a toast so the user
          // sees the dismissal didn't stick.
          toast.error("No se pudo descartar el aviso. Vuelve a intentarlo.");
        },
      });
    },
    [dismissMutation],
  );

  /**
   * Optimistically mark the highlight as pending-dismiss; show
   * a toast with a "Deshacer" action; if the user does nothing
   * for ``UNDO_GRACE_MS`` the real POST fires.
   */
  const dismissWithUndo = useCallback((highlight: BriefingHighlight) => {
    // Cancel any earlier pending dismiss for the same id.
    const existing = pending.current.get(highlight.id);
    if (existing) clearTimeout(existing.timer);

    // Add to the pending set — this hides the card via the
    // query's select filter, surviving any refetch in the
    // grace window.
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

    // Toast copy is present-continuous on purpose: the dismiss
    // is HELD for UNDO_GRACE_MS before it actually hits the
    // backend (Gmail "Message sent. Undo" pattern). Past-tense
    // "Aviso descartado." would lie during the 3 s window.
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
          // Force the cache to re-include the highlight via
          // an invalidate — the select filter will re-run
          // against the (unchanged) server data and the card
          // returns in its original severity-sorted position.
          qc.invalidateQueries({ queryKey: ["copilot", "briefing"] });
        },
      },
      duration: UNDO_TOAST_MS,
    });
  }, [qc, scheduleRealDismiss]);

  // Cleanup on unmount: just cancel pending timers. We
  // INTENTIONALLY do NOT fire-and-forget the dismiss POST on
  // unmount — the user could still have changed their mind
  // and silently committing destructive UI state is surprising.
  // The card stays dismissed in their session via the
  // pendingIds set, but reappears on next page load — that's
  // the correct fail-safe behaviour for a no-undismiss backend.
  useEffect(() => {
    const pendingMap = pending.current;
    return () => {
      pendingMap.forEach(({ timer }) => clearTimeout(timer));
      pendingMap.clear();
    };
  }, []);

  return { briefingQuery, dismissMutation, dismissWithUndo };
}
