"use client";

import { useSyncExternalStore } from "react";

export const DESKTOP_QUERY = "(min-width: 768px)";
export const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

function mediaList(query: string): MediaQueryList | null {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(query) : null;
}

export function mediaMatches(query: string, fallback: boolean): boolean {
  return mediaList(query)?.matches ?? fallback;
}

export function useMediaQuery(query: string, fallback: boolean): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const list = mediaList(query);
      if (!list) return () => undefined;
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    () => mediaMatches(query, fallback),
    () => fallback,
  );
}
