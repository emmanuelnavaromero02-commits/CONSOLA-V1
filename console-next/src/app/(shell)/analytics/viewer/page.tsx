"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

import { AppViewer } from "@/components/analytics/AppViewer";

/**
 * Static route on purpose.
 *
 * console-next builds with ``output: "export"``, so a dynamic ``[app]`` segment
 * would need ``generateStaticParams`` to enumerate every app at build time.
 * Published apps are runtime data — they depend on the tenant, on which
 * cartridges are installed and on what the seed registered — so they cannot be
 * known when the bundle is built. Carrying the name in the query string keeps
 * the static export intact and keeps the URL shareable.
 */
function ViewerShell() {
  const params = useSearchParams();
  return <AppViewer appName={params.get("app")} />;
}

export default function AnalyticsViewerPage() {
  return (
    <Suspense
      fallback={
        <div className="p-6 text-sm text-muted-foreground">Cargando aplicación…</div>
      }
    >
      <ViewerShell />
    </Suspense>
  );
}
