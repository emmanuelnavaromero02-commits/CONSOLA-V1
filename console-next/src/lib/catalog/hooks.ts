"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { autoProfileCatalog } from "@/lib/data/client";
import type { AutoProfileStatus } from "@/lib/data/types";

export const AUTO_CATALOG_DEBOUNCE_MS = 800;
export const AUTO_CATALOG_POLL_MS = 2000;
export const AUTO_CATALOG_MAX_POLLS = 5;

interface AutoCatalogOptions {
  cartridge?: string | null;
  includeSources?: boolean;
}

/**
 * Zero-click cataloguing: once per mount (and per filter) and only while the
 * tab is visible, ask the console to profile whatever is still undocumented.
 * Failures are silent on purpose: the catalog stays usable without the
 * Copilot, and nothing here blocks rendering.
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
    let polls = 0;
    let lastPending: number | null = null;

    const run = async () => {
      try {
        const result = await autoProfileCatalog({
          cartridge: cartridge || undefined,
          include_sources: includeSources,
        });
        if (cancelled) return;
        setStatus(result);
        const progressed = result.processed > 0 || (lastPending !== null && result.pending < lastPending);
        if (progressed) {
          void queryClient.invalidateQueries({ queryKey: ["data", "catalog"] });
        }
        lastPending = result.pending;
        if (result.pending > 0 && polls < AUTO_CATALOG_MAX_POLLS) {
          polls += 1;
          timer = setTimeout(() => void run(), AUTO_CATALOG_POLL_MS);
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
