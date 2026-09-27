"use client";

import { useEffect, useState } from "react";

export function scheduleTicks(
  onTick: (now: number) => void,
  intervalMs: number,
  clock: () => number = Date.now,
): () => void {
  let cancelled = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const tick = () => {
    if (cancelled) return;
    onTick(clock());
    timer = setTimeout(tick, intervalMs);
  };
  timer = setTimeout(tick, intervalMs);
  return () => {
    cancelled = true;
    if (timer !== undefined) clearTimeout(timer);
  };
}

export function useNow(intervalMs = 1_000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => scheduleTicks(setNow, intervalMs), [intervalMs]);
  return now;
}
