"use client";

import Link from "next/link";
import { ArrowRight } from "lucide-react";

export function AppsGallery() {
  return (
    <section className="rounded-xl border bg-card p-6 text-center shadow-sm">
      <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">Analitica operativa</p>
      <h2 className="mt-2 text-xl font-semibold tracking-tight">Analitica del workspace</h2>
      <p className="mx-auto mt-2 max-w-2xl text-sm text-muted-foreground">
        Revisa indicadores, agentes y decisiones desde la Sala de Control.
      </p>
      <Link
        href="/control-room#apps"
        className="mt-4 inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        Abrir modulos
        <ArrowRight aria-hidden className="h-4 w-4" />
      </Link>
    </section>
  );
}
