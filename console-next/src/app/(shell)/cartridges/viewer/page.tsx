"use client";

import Link from "next/link";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

import { CredentialsForm } from "@/components/cartridges/CredentialsForm";
import { useConnectorSchema } from "@/lib/hooks/useCartridges";
import type { ConnectorSchema } from "@/lib/cartridges";

function fallbackSchema(id: string): ConnectorSchema {
  return {
    name: id,
    fields: [
      { name: "base_url", type: "url", label: "Base URL", required: true },
      { name: "token", type: "password", label: "Bearer token", required: true },
    ],
  };
}

function CartridgeViewerShell() {
  const params = useSearchParams();
  const id = params.get("id")?.trim() || "";
  const schemaQuery = useConnectorSchema(id || undefined);

  if (!id) {
    return (
      <main className="mx-auto max-w-3xl space-y-4 px-6 py-8">
        <Link href="/cartridges" className="text-sm text-muted-foreground underline-offset-2 hover:underline">
          Volver a cartuchos
        </Link>
        <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          Falta el parámetro de cartucho: usa <span className="font-mono">/cartridges/viewer?id=replicon</span>.
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-3xl space-y-6 px-6 py-8">
      <nav aria-label="breadcrumb" className="text-sm text-muted-foreground">
        <Link href="/cartridges" className="underline-offset-2 hover:underline">
          Cartuchos
        </Link>
        <span className="px-2" aria-hidden>›</span>
        <span className="text-foreground">{id}</span>
      </nav>

      <header className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{schemaQuery.data?.name ?? id}</h1>
        <p className="text-sm text-muted-foreground">
          {schemaQuery.data?.description ?? "Configura credenciales, prueba la conexión y deja el cartucho listo para extracción."}
        </p>
      </header>

      {schemaQuery.isError ? (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
          <p className="font-medium text-destructive">
            No se pudo cargar el esquema de configuración.
          </p>
          <button
            type="button"
            onClick={() => schemaQuery.refetch()}
            className="mt-2 inline-flex min-h-[44px] items-center justify-center rounded-md border border-destructive/40 px-3 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40"
          >
            Reintentar
          </button>
        </div>
      ) : (
        <CredentialsForm cartridgeId={id} schema={schemaQuery.data ?? fallbackSchema(id)} />
      )}
    </main>
  );
}

export default function CartridgeViewerPage() {
  return (
    <Suspense fallback={<main className="p-6 text-sm text-muted-foreground">Cargando cartucho...</main>}>
      <CartridgeViewerShell />
    </Suspense>
  );
}
