"use client";

import Link from "next/link";
import { Suspense, useEffect } from "react";
import { useSearchParams } from "next/navigation";

import { decisionsRedirectTarget } from "./redirect-target";

function DecisionsRedirect() {
  const params = useSearchParams();
  const target = decisionsRedirectTarget(params);

  useEffect(() => {
    window.location.replace(target);
  }, [target]);

  return (
    <main className="mx-auto flex min-h-[60vh] max-w-3xl flex-col items-center justify-center gap-4 px-6 py-12 text-center">
      <p className="text-xs font-semibold uppercase text-primary">Control Room</p>
      <h1 className="text-2xl font-semibold tracking-tight">Las decisiones ahora viven en el Control Room</h1>
      <p className="text-sm text-muted-foreground">
        Redirigiendo a su fase del ciclo operativo.
      </p>
      <Link
        href={target}
        className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-semibold text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        Abrir Control Room
      </Link>
    </main>
  );
}

export default function DecisionsRedirectPage() {
  return (
    <Suspense fallback={null}>
      <DecisionsRedirect />
    </Suspense>
  );
}
