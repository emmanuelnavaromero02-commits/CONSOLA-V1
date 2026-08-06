"use client";

import { useQuery } from "@tanstack/react-query";

import { listApps, type AnalyticsApp, type AppsResponse } from "@/lib/admin-surfaces";

const ROOT_KEY = "analytics-apps";

/**
 * Catalog feed for /analytics.
 *
 * ``includeUnready`` is on by design. The server keeps every scope filter in
 * place either way — permission, cartridge visibility and active scoped
 * connections are applied before readiness — so the flag only decides whether
 * an app whose Gold datasets are not materialised yet is *hidden* or *shown as
 * incomplete*. Hiding it produced a silent empty catalog with nothing to read;
 * showing it lets the surface name the missing datasets instead.
 */
export function useAnalyticsApps(options: { cartridge?: string } = {}) {
  const cartridge = options.cartridge?.trim() || undefined;
  return useQuery<AppsResponse>({
    queryKey: [ROOT_KEY, "list", cartridge ?? "all"],
    queryFn: () => listApps({ includeUnready: true, cartridge }),
    staleTime: 60_000,
  });
}

/**
 * Same shape the backend enforces for published app names (``DATASET_NAME_RE``).
 * Anything else never becomes a URL, which is what keeps ``javascript:``,
 * ``data:``, absolute URLs and ``../`` out of the viewer.
 */
const APP_NAME_RE = /^[a-zA-Z_][a-zA-Z0-9_]{0,127}$/;

export function isPublishedAppName(value: string | null | undefined): boolean {
  return APP_NAME_RE.test((value ?? "").trim());
}

export type AppDataState = "ready" | "partial" | "unknown";

/** Collapse the server's ``data_status`` into what the catalog renders. */
export function appDataState(app: AnalyticsApp): AppDataState {
  const status = (app.data_status ?? "").trim();
  if (status === "ready") return "ready";
  if (status === "unready" || status === "dataset_metadata_missing") return "partial";
  return "unknown";
}

export function appCartridgeId(app: AnalyticsApp): string {
  return (app.cartridge_id ?? app.cartridge ?? "").trim();
}

/** Cartridges present in the payload, for the filter chips. */
export function cartridgesFromApps(apps: AnalyticsApp[]): string[] {
  return Array.from(
    new Set(apps.map(appCartridgeId).filter((value) => value.length > 0)),
  ).sort();
}
