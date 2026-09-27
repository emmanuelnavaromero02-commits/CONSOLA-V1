"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { autoProfileCatalog } from "@/lib/data/client";
import type { AutoProfileStatus } from "@/lib/data/types";

export const AUTO_CATALOG_DEBOUNCE_MS = 800;
export const AUTO_CATALOG_POLL_MS = 2000;
export const AUTO_CATALOG_MAX_POLL_MS = 15_000;
export const AUTO_CATALOG_MAX_WAIT_MS = 180_000;
export const AUTO_CATALOG_MAX_IDLE_POLLS = 6;

interface AutoCatalogOptions {
  cartridge?: string | null;
  includeSources?: boolean;
}

/**
 * Zero-click cataloguing: once per mount (and per filter) and only while the
 * tab is visible, ask the console to queue whatever is still undocumented,
 * then follow the background work with a backing-off poll until nothing is
 * pending (three minutes at most, or six polls without progress). Every new
 * annotation epoch refreshes the catalog query. Failures are silent on
 * purpose: the catalog stays usable without the Copilot.
 */
export function useAutoCatalog({ cartridge, includeSources = false }: AutoCatalogOptions = {}) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<AutoProfileStatus | null>(null);
  const fired = useRef<string | null>(null);

  useEffect(() => {
    const key = `${cartridge ?? ""}|${includeSources ? "1" : "0"}`;
    if (fired.current === key) return undefined;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let since: string | undefined;
    let lastEpoch: string | null | undefined;
    let lastPending: number | null = null;
    let lastProcessed = 0;
    let idlePolls = 0;
    let delay = AUTO_CATALOG_POLL_MS;
    let startedAt = 0;

    const run = async () => {
      try {
        const result = await autoProfileCatalog({
          cartridge: cartridge || undefined,
          include_sources: includeSources,
          since,
        });
        if (cancelled) return;
        setStatus(result);
        if (since === undefined) since = result.annotation_epoch || "start";
        const epoch = result.annotation_epoch ?? null;
        const epochChanged = lastEpoch !== undefined && epoch !== lastEpoch;
        if (epochChanged || result.processed > lastProcessed) {
          void queryClient.invalidateQueries({ queryKey: ["data", "catalog"] });
        }
        const progressed = epochChanged || (lastPending !== null && result.pending !== lastPending);
        idlePolls = progressed ? 0 : idlePolls + 1;
        lastEpoch = epoch;
        lastPending = result.pending;
        lastProcessed = Math.max(lastProcessed, result.processed);
        const elapsed = Date.now() - startedAt;
        if (
          result.pending > 0
          && elapsed < AUTO_CATALOG_MAX_WAIT_MS
          && idlePolls < AUTO_CATALOG_MAX_IDLE_POLLS
        ) {
          timer = setTimeout(() => void run(), delay);
          delay = Math.min(Math.round(delay * 1.5), AUTO_CATALOG_MAX_POLL_MS);
        }
      } catch {
        // The catalog renders without the Copilot; nothing to surface.
      }
    };

    let debounced = false;
    const fire = () => {
      if (cancelled || !debounced || fired.current === key) return;
      if (typeof document !== "undefined" && document.hidden) return;
      fired.current = key;
      startedAt = Date.now();
      void run();
    };

    const onVisible = () => fire();

    timer = setTimeout(() => {
      debounced = true;
      fire();
    }, AUTO_CATALOG_DEBOUNCE_MS);
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", onVisible);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onVisible);
    };
  }, [cartridge, includeSources, queryClient]);

  return status;
}
