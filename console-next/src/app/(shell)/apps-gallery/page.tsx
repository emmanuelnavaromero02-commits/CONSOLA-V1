"use client";

import { AppsGallery } from "@/components/apps/AppsGallery";

export default function AppsGalleryPage() {
  return (
    <main className="mx-auto max-w-[1600px] space-y-6 px-6 py-8">
      <header>
        <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">
          Analitica operativa
        </p>
        <h1 className="mt-1 text-2xl font-semibold tracking-tight">
          Galería de aplicaciones
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Aplicaciones publicadas para este workspace: creadas aquí o
          provenientes de fuentes de datos conectadas.
        </p>
      </header>
      <AppsGallery />
    </main>
  );
}
