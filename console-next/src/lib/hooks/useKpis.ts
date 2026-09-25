"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

export interface KpiPayload {
  cartridges:    { total: number; connected: number; disconnected: number };
  extractions:   { today: number; week: number };
  data_freshness: Record<
    string,
    { age_hours: number | null; status: "fresh" | "stale" | "very_stale" | "never" }
  >;
  users:         { active_today: number; total: number };
  copilot:       { conversations_today: number; tools_invoked_today: number };
  audit:         { events_today: number; destructive_actions_today: number };
}

const POLL_MS = 30_000;

export function useKpis() {
  return useQuery<KpiPayload>({
    queryKey: ["dashboard", "kpis"],
    queryFn: async () => {
      const { data } = await api.get<KpiPayload>("/api/dashboard/kpis");
      return data;
    },
    refetchInterval: POLL_MS,
    refetchOnWindowFocus: true,
  });
}
