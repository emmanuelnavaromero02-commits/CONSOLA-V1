"use client";

import { useQuery } from "@tanstack/react-query";

import { listApps, type AnalyticsApp, type AppsResponse } from "@/lib/admin-surfaces";

const ROOT_KEY = "analytics-apps";

export function useAnalyticsApps(options: { cartridge?: string } = {}) {
  const cartridge = options.cartridge?.trim() || undefined;
  return useQuery<AppsResponse>({
    queryKey: [ROOT_KEY, "list", cartridge ?? "all"],
    queryFn: () => listApps({ includeUnready: true, cartridge }),
    staleTime: 60_000,
  });
}

const APP_NAME_RE = /^[a-zA-Z_][a-zA-Z0-9_]{0,127}$/;

export function isPublishedAppName(value: string | null | undefined): boolean {
  return APP_NAME_RE.test((value ?? "").trim());
}

export type AppDataState = "ready" | "partial" | "unknown";

export function appDataState(app: AnalyticsApp): AppDataState {
  const status = (app.data_status ?? "").trim();
  if (status === "ready") return "ready";
  if (status === "unready" || status === "dataset_metadata_missing") return "partial";
  return "unknown";
}

export function appCartridgeId(app: AnalyticsApp): string {
  return (app.cartridge_id ?? app.cartridge ?? "").trim();
}

export function cartridgesFromApps(apps: AnalyticsApp[]): string[] {
  return Array.from(
    new Set(apps.map(appCartridgeId).filter((value) => value.length > 0)),
  ).sort();
}
