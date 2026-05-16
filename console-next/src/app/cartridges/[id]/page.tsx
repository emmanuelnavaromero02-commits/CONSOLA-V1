"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useConnectorSchema } from "@/lib/hooks/useCartridges";
import { CredentialsForm } from "@/components/cartridges/CredentialsForm";

/**
 * /cartridges/[id] detail page.
 *
 * Loads the connector_schema for the cartridge and renders a
 * dynamic CredentialsForm. The form handles save / test / delete
 * via the shared TanStack mutations.
 */
export default function CartridgeDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params?.id;
  const schemaQuery = useConnectorSchema(id);

  if (!id) return null;

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
        <h1 className="text-2xl font-semibold tracking-tight">{id}</h1>
        {schemaQuery.data?.description ? (
          <p className="text-sm text-muted-foreground">{schemaQuery.data.description}</p>
        ) : null}
      </header>

      {schemaQuery.isLoading ? (
        <div className="space-y-3">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="h-12 animate-pulse rounded-md bg-muted" aria-hidden />
          ))}
        </div>
      ) : schemaQuery.isError ? (
        <div className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
          <p className="font-medium text-destructive">
            No se pudo cargar el esquema de configuración.
          </p>
          <button
            type="button"
            onClick={() => schemaQuery.refetch()}
            className="mt-2 text-xs font-medium text-destructive underline-offset-2 hover:underline"
          >
            Reintentar
          </button>
        </div>
      ) : (
        <CredentialsForm cartridgeId={id} schema={schemaQuery.data ?? { fields: [] }} />
      )}
    </main>
  );
}
