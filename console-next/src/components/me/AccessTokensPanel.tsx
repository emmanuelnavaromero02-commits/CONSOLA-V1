"use client";

import { useState, useSyncExternalStore } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Copy, KeyRound, RefreshCw } from "lucide-react";

import {
  type AccessToken,
  type AccessTokenScope,
  canRevoke,
  createAccessToken,
  formatDate,
  gatewayUrls,
  listAccessTokens,
  revokeAccessToken,
  scopeLabel,
  statusLabel,
} from "@/lib/access-tokens";
import { copyText } from "@/lib/clipboard";

const QUERY_KEY = ["me", "access-tokens"] as const;
const MAX_NAME_LENGTH = 80;
const SCOPE_OPTIONS: Array<{ value: AccessTokenScope; label: string }> = [
  { value: "lectura", label: "Solo lectura" },
  { value: "acciones", label: "Lectura y acciones" },
];
const REVOKE_CONFIRMATION =
  "¿Revocar este token? Los asistentes que lo usen perderán acceso de inmediato.";

function subscribeToNothing(): () => void {
  return () => undefined;
}

function readOrigin(): string {
  return window.location.origin;
}

function serverOrigin(): string {
  return "";
}

function errorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === "object" && "message" in error) {
    const message = String((error as { message?: unknown }).message ?? "");
    if (message) return message;
  }
  return fallback;
}

export function AccessTokensPanel() {
  const queryClient = useQueryClient();
  const [nombre, setNombre] = useState("");
  const [alcance, setAlcance] = useState<AccessTokenScope>("lectura");
  const [dias, setDias] = useState<number | null>(null);
  const [secret, setSecret] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const origin = useSyncExternalStore(subscribeToNothing, readOrigin, serverOrigin);

  const tokens = useQuery({ queryKey: QUERY_KEY, queryFn: listAccessTokens, staleTime: 30_000 });

  const create = useMutation({
    mutationFn: createAccessToken,
    onSuccess: async (created) => {
      setSecret(created.token);
      setNombre("");
      setNotice("");
      await queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    },
    onError: (error) => setNotice(errorMessage(error, "No se pudo crear el token.")),
  });

  const revoke = useMutation({
    mutationFn: revokeAccessToken,
    onSuccess: async () => {
      setNotice("Token revocado.");
      await queryClient.invalidateQueries({ queryKey: QUERY_KEY });
    },
    onError: (error) => setNotice(errorMessage(error, "No se pudo revocar el token.")),
  });

  const data = tokens.data;
  const allowedScopes = data?.alcances_permitidos ?? ["lectura"];
  const dayOptions = data?.dias_permitidos ?? [7, 30, 90];
  const selectedDays = dias ?? data?.dias_predeterminados ?? 30;
  const urls = origin ? gatewayUrls(origin) : null;

  function submit() {
    const clean = nombre.trim();
    if (!clean) return;
    create.mutate({
      nombre: clean,
      alcance: allowedScopes.includes(alcance) ? alcance : "lectura",
      dias: selectedDays,
    });
  }

  function confirmRevoke(token: AccessToken) {
    if (!window.confirm(REVOKE_CONFIRMATION)) return;
    revoke.mutate(token.id);
  }

  async function copySecret() {
    if (!secret) return;
    try {
      await copyText(secret);
      setNotice("Token copiado al portapapeles.");
    } catch (error) {
      setNotice(errorMessage(error, "No se pudo copiar el token."));
    }
  }

  return (
    <section className="space-y-5 rounded-lg border bg-card p-5" aria-label="Conectores de IA">
      <header className="space-y-1">
        <div className="flex items-center gap-2">
          <Bot aria-hidden className="h-5 w-5 text-primary" />
          <h2 className="text-base font-semibold">Conectores de IA</h2>
        </div>
        <p className="text-sm text-muted-foreground">
          Crea tokens personales para que un asistente de IA consulte este espacio de trabajo en tu nombre.
          Cada token queda ligado al espacio de trabajo activo y solo funciona en la pasarela de IA.
        </p>
      </header>

      {secret ? (
        <div role="alert" className="space-y-3 rounded-md border border-amber-500/40 bg-amber-500/10 p-4">
          <p className="text-sm font-semibold">Copia este token ahora. Por seguridad no volverá a mostrarse.</p>
          <code data-testid="access-token-secret" className="block break-all rounded bg-background px-3 py-2 font-mono text-xs">
            {secret}
          </code>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={copySecret}
              className="inline-flex min-h-[40px] items-center gap-2 rounded-md border px-3 text-xs font-medium"
            >
              <Copy aria-hidden className="h-4 w-4" />
              Copiar
            </button>
            <button
              type="button"
              onClick={() => setSecret(null)}
              className="inline-flex min-h-[40px] items-center rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground"
            >
              Ya lo guardé
            </button>
          </div>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-3 md:grid-cols-4">
        <label className="flex flex-col gap-1.5 text-sm md:col-span-2">
          <span className="font-medium">Nombre</span>
          <input
            value={nombre}
            maxLength={MAX_NAME_LENGTH}
            onChange={(event) => setNombre(event.target.value)}
            placeholder="Por ejemplo: Asistente de finanzas"
            className="min-h-[44px] rounded-md border bg-background px-3 text-sm"
          />
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium">Alcance</span>
          <select
            value={alcance}
            onChange={(event) => setAlcance(event.target.value as AccessTokenScope)}
            className="min-h-[44px] rounded-md border bg-background px-3 text-sm"
          >
            {SCOPE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value} disabled={!allowedScopes.includes(option.value)}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1.5 text-sm">
          <span className="font-medium">Vigencia</span>
          <select
            value={selectedDays}
            onChange={(event) => setDias(Number(event.target.value))}
            className="min-h-[44px] rounded-md border bg-background px-3 text-sm"
          >
            {dayOptions.map((option) => (
              <option key={option} value={option}>
                {option} días
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={submit}
          disabled={create.isPending || !nombre.trim() || !data?.puede_crear}
          className="inline-flex min-h-[44px] items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
        >
          <KeyRound aria-hidden className="h-4 w-4" />
          Crear token
        </button>
        {data && !data.puede_crear ? (
          <p className="text-xs text-muted-foreground">
            Alcanzaste el máximo de {data.limite_activos} tokens activos; revoca uno para crear otro.
          </p>
        ) : null}
        {!allowedScopes.includes("acciones") ? (
          <p className="text-xs text-muted-foreground">
            Tu usuario solo puede crear tokens de solo lectura.
          </p>
        ) : null}
        {notice ? (
          <p role="status" className="text-sm">
            {notice}
          </p>
        ) : null}
      </div>

      {tokens.isLoading ? (
        <div aria-busy="true" className="space-y-2">
          {Array.from({ length: 3 }).map((_, index) => (
            <span key={index} className="block h-10 animate-pulse rounded bg-muted" aria-hidden />
          ))}
        </div>
      ) : tokens.isError ? (
        <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
          <p className="font-medium text-destructive">No se pudieron cargar tus tokens.</p>
          <button
            type="button"
            onClick={() => tokens.refetch()}
            className="mt-2 inline-flex min-h-[40px] items-center gap-2 rounded-md border px-3 text-xs font-medium"
          >
            <RefreshCw aria-hidden className="h-4 w-4" />
            Reintentar
          </button>
        </div>
      ) : (data?.tokens ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">Todavía no has creado tokens.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] text-left text-sm">
            <thead className="text-xs uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="py-2 pr-3">Nombre</th>
                <th className="py-2 pr-3">Espacio</th>
                <th className="py-2 pr-3">Alcance</th>
                <th className="py-2 pr-3">Creado</th>
                <th className="py-2 pr-3">Vence</th>
                <th className="py-2 pr-3">Último uso</th>
                <th className="py-2 pr-3">Estado</th>
                <th className="py-2" aria-label="Acciones" />
              </tr>
            </thead>
            <tbody>
              {(data?.tokens ?? []).map((token) => (
                <tr key={token.id} className="border-t" data-testid="access-token-row">
                  <td className="py-2 pr-3">
                    <span className="font-medium">{token.nombre}</span>
                    <span className="ml-2 font-mono text-xs text-muted-foreground">{token.token_prefix}…</span>
                  </td>
                  <td className="py-2 pr-3">{token.espacio_de_trabajo?.nombre || "Sin información"}</td>
                  <td className="py-2 pr-3">{scopeLabel(token.alcances)}</td>
                  <td className="py-2 pr-3">{formatDate(token.creado_en)}</td>
                  <td className="py-2 pr-3">{formatDate(token.vence_en)}</td>
                  <td className="py-2 pr-3">{formatDate(token.ultimo_uso_en, "Sin uso registrado")}</td>
                  <td className="py-2 pr-3">{statusLabel(token)}</td>
                  <td className="py-2 text-right">
                    {canRevoke(token) ? (
                      <button
                        type="button"
                        onClick={() => confirmRevoke(token)}
                        disabled={revoke.isPending}
                        className="inline-flex min-h-[36px] items-center rounded-md border border-destructive/40 px-3 text-xs font-medium text-destructive disabled:opacity-50"
                      >
                        Revocar
                      </button>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="space-y-2 rounded-md bg-muted/30 p-4 text-sm" aria-label="Cómo conectar tu asistente">
        <h3 className="font-semibold">Cómo conectar tu asistente</h3>
        {urls ? (
          <ul className="space-y-2">
            <li>
              <span className="font-medium">ChatGPT (acciones personalizadas):</span> importa el esquema desde{" "}
              <code className="break-all font-mono text-xs">{urls.esquema}</code> y elige autenticación con clave de API
              de tipo Bearer.
            </li>
            <li>
              <span className="font-medium">Claude y asistentes con conector remoto:</span> usa la dirección{" "}
              <code className="break-all font-mono text-xs">{urls.conectorRemoto}</code> y envía el token en el encabezado
              Authorization como Bearer.
            </li>
            <li>
              <span className="font-medium">Asistentes de escritorio:</span> usa el puente local con{" "}
              <code className="font-mono text-xs">OMEGA_BASE_URL={urls.base}</code> y{" "}
              <code className="font-mono text-xs">OMEGA_API_KEY_FILE</code> apuntando a un archivo con permisos 600.
            </li>
          </ul>
        ) : (
          <p className="text-muted-foreground">Sin información</p>
        )}
      </div>
    </section>
  );
}
