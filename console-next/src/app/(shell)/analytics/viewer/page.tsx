"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

import { AppViewer } from "@/components/analytics/AppViewer";

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
