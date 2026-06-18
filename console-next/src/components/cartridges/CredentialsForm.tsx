"use client";

import { useMemo, useState } from "react";
import { ExternalLink, Plug, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import type { ConnectorSchema, TestConnectionResult as Result } from "@/lib/cartridges";
import { useTestConnection } from "@/lib/hooks/useCartridges";
import { useVaultConnections } from "@/lib/operations/hooks";
import type { VaultConnection } from "@/lib/operations/types";
import { TestConnectionResult } from "./TestConnectionResult";

interface Props {
  cartridgeId: string;
  schema: ConnectorSchema;
}

export function CredentialsForm({ cartridgeId, schema }: Props) {
  const testMut = useTestConnection(cartridgeId);
  const vaultConnections = useVaultConnections(cartridgeId);

  const [lastTest, setLastTest] = useState<Result | null>(null);
  const [selectedConnId, setSelectedConnId] = useState("");
  const hasConnectorSchema = (schema.fields ?? []).length > 0;
  const connectionOptions = useMemo(
    () => uniqueConnectionIds(vaultConnections.data?.connections ?? []),
    [vaultConnections.data?.connections],
  );
  const connIdForTest = selectedConnId || connectionOptions[0] || "";

  const onTest = async () => {
    try {
      const r = await testMut.mutateAsync(connIdForTest || undefined);
      setLastTest(r);
      if (r.ok) {
        toast.success("Conexion OK.");
      } else {
        const detail = r.message?.trim() || "Sin detalles";
        toast.error(`Error de conexion: ${detail}`);
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error desconocido";
      setLastTest({ ok: false, message, latency_ms: 0 });
      toast.error(`No se pudo probar: ${message}`);
    }
  };

  return (
    <div className="space-y-6">
      <section className="space-y-4 rounded-lg border bg-card p-6 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="space-y-1">
            <div className="flex items-center gap-2 text-sm font-medium">
              <ShieldCheck size={18} aria-hidden />
              Vault scoped
            </div>
            <p className="max-w-2xl text-sm text-muted-foreground">
              Las credenciales se administran en Vault; esta pantalla solo prueba conexiones guardadas.
            </p>
          </div>
          <a
            href="/operations/vault"
            className="inline-flex min-h-[44px] items-center justify-center gap-1.5 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Configurar en Vault
            <ExternalLink size={16} aria-hidden />
          </a>
        </div>

        {hasConnectorSchema ? null : (
          <p className="text-sm text-muted-foreground">
            Este cartucho se valida con las conexiones disponibles en Vault.
          </p>
        )}

        <div className="flex flex-wrap items-end gap-2 pt-2">
          {connectionOptions.length ? (
            <label className="flex flex-col gap-1 text-xs font-medium text-muted-foreground">
              Conexión a probar
              <select
                value={connIdForTest}
                onChange={(event) => setSelectedConnId(event.target.value)}
                className="min-h-[44px] rounded-md border bg-background px-3 text-sm text-foreground"
              >
                {connectionOptions.map((connId) => (
                  <option key={connId} value={connId}>
                    {connId}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <button
            type="button"
            onClick={onTest}
            disabled={testMut.isPending}
            className="inline-flex min-h-[44px] items-center justify-center gap-1.5 rounded-md border bg-background px-4 text-sm font-medium transition-colors hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
          >
            <Plug size={16} aria-hidden />
            {testMut.isPending ? "Probando..." : "Probar conexión"}
          </button>
        </div>
      </section>

      <TestConnectionResult result={lastTest} />
    </div>
  );
}

function uniqueConnectionIds(connections: VaultConnection[]): string[] {
  const ids = connections
    .map((conn) => String(conn.conn_id ?? conn.id ?? "").trim())
    .filter(Boolean);
  return Array.from(new Set(ids));
}
