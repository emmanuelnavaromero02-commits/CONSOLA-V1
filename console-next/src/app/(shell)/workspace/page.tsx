"use client";

import Link from "next/link";
import { Suspense, useEffect } from "react";
import { useSearchParams } from "next/navigation";

function copilotHref(prompt: string | null): string {
  if (!prompt) return "/copilot";
  return `/copilot?${new URLSearchParams({ prompt }).toString()}`;
}

function WorkspaceRedirect() {
  const params = useSearchParams();
  const href = copilotHref(params.get("prompt"));

  useEffect(() => {
    window.location.replace(href);
  }, [href]);

  return (
    <main className="mx-auto flex min-h-[60vh] max-w-3xl flex-col items-center justify-center gap-4 px-6 py-12 text-center">
      <p className="text-xs font-semibold uppercase text-cyan-700 dark:text-cyan-300/80">Copiloto</p>
      <h1 className="text-2xl font-semibold tracking-tight">El workspace ahora vive en el copiloto</h1>
      <p className="text-sm text-muted-foreground">
        Redirigiendo al copiloto.
      </p>
      <Link
        href={href}
        className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        Abrir copiloto
      </Link>
    </main>
  );
}

export default function WorkspacePage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Redirigiendo al copiloto…</div>}>
      <WorkspaceRedirect />
    </Suspense>
  );
}
