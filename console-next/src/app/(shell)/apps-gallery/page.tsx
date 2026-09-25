"use client";

import Link from "next/link";
import { useEffect } from "react";

export default function AppsGalleryPage() {
  useEffect(() => {
    window.location.replace("/analytics");
  }, []);

  return (
    <main className="mx-auto flex min-h-[60vh] max-w-3xl flex-col items-center justify-center gap-4 px-6 py-12 text-center">
      <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">Analitica operativa</p>
      <h1 className="text-2xl font-semibold tracking-tight">Las aplicaciones se abren desde Analytics</h1>
      <p className="text-sm text-muted-foreground">
        Redirigiendo al catálogo.
      </p>
      <Link
        href="/analytics"
        className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        Abrir Analytics
      </Link>
    </main>
  );
}
