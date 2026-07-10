"use client";

import { type FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Landmark, KeyRound, ShieldCheck } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import { getMeAccess } from "@/lib/admin-surfaces";
import { cn } from "@/lib/utils";

interface LlmKeyStatus {
  provider: "anthropic";
  configured: boolean;
  scope: "llm";
}

interface VaultConnectionStatus {
  connections?: { conn_id?: string; id?: string }[];
}

export function WorkspaceTokenKeys() {
  const queryClient = useQueryClient();
  const [anthropicKey, setAnthropicKey] = useState("");
  const [banxicoToken, setBanxicoToken] = useState("");
  const access = useQuery({
    queryKey: ["me", "access"],
    queryFn: getMeAccess,
    staleTime: 60_000,
  });
  const permissions = new Set(access.data?.permissions ?? []);
  const canManageLlmKey = permissions.has("llm.keys.write");
  const canReadLlmKey = permissions.has("llm.keys.read");
  const canManageVault = permissions.has("vault.connections.write");
  const canReadVault = permissions.has("vault.connections.read");
  const llmKey = useQuery({
    queryKey: ["copilot", "llm", "key"],
    queryFn: async () => {
      const { data } = await api.get<LlmKeyStatus>("/api/copilot/llm-key");
      return data;
    },
    enabled: canReadLlmKey,
    staleTime: 30_000,
  });
  const banxicoKey = useQuery({
    queryKey: ["operations", "vault", "banxico"],
    queryFn: async () => {
      const { data } = await api.get<VaultConnectionStatus>("/api/vault/connections/banxico");
      return data;
    },
    enabled: canReadVault,
    staleTime: 30_000,
  });
  const saveAnthropicKey = useMutation({
    mutationFn: async (value: string) => {
      await api.put("/api/copilot/llm-key", { value });
    },
    onSuccess: async () => {
      setAnthropicKey("");
      await queryClient.invalidateQueries({ queryKey: ["copilot", "llm", "key"] });
      toast.success("Clave Anthropic guardada para este workspace.");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "No se pudo guardar la clave Anthropic.");
    },
  });
  const saveBanxicoToken = useMutation({
    mutationFn: async (value: string) => {
      await api.post("/api/cartridges/banxico/credentials", {
        auth_method: "bmx_token",
        token: value,
      });
    },
    onSuccess: async () => {
      setBanxicoToken("");
      await queryClient.invalidateQueries({ queryKey: ["operations", "vault", "banxico"] });
      toast.success("Token Banxico guardado en Vault.");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "No se pudo guardar el token Banxico.");
    },
  });

  if (!canManageLlmKey && !canManageVault) return null;

  const banxicoConfigured = Boolean(banxicoKey.data?.connections?.length);
  return (
    <section className="grid gap-4 xl:grid-cols-2" aria-label="Claves del workspace">
      {canManageLlmKey ? (
        <SecretCard
          icon={KeyRound}
          title="Clave Anthropic del workspace"
          description="Esta clave se guarda aislada para tu tenant/workspace y solo se usa en tus llamadas del copiloto."
          configured={llmKey.isLoading ? undefined : llmKey.data?.configured === true}
          inputValue={anthropicKey}
          inputPlaceholder="sk-ant-..."
          saving={saveAnthropicKey.isPending}
          buttonLabel="Guardar clave"
          onInputChange={setAnthropicKey}
          onSubmit={(event) => {
            event.preventDefault();
            const value = anthropicKey.trim();
            if (!value) return toast.error("Pega una API key de Anthropic antes de guardar.");
            saveAnthropicKey.mutate(value);
          }}
        />
      ) : null}
      {canManageVault ? (
        <SecretCard
          icon={Landmark}
          title="Token Banxico SIE"
          description="Guarda el Bmx-Token en Vault para preflight oficial y extracción Bronze."
          configured={banxicoKey.isLoading ? undefined : banxicoConfigured}
          inputValue={banxicoToken}
          inputPlaceholder="Bmx-Token"
          saving={saveBanxicoToken.isPending}
          buttonLabel="Guardar token"
          onInputChange={setBanxicoToken}
          onSubmit={(event) => {
            event.preventDefault();
            const value = banxicoToken.trim();
            if (!value) return toast.error("Pega el Bmx-Token antes de guardar.");
            saveBanxicoToken.mutate(value);
          }}
        />
      ) : null}
    </section>
  );
}

function SecretCard({
  icon: Icon,
  title,
  description,
  configured,
  inputValue,
  inputPlaceholder,
  saving,
  buttonLabel,
  onInputChange,
  onSubmit,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  configured?: boolean;
  inputValue: string;
  inputPlaceholder: string;
  saving: boolean;
  buttonLabel: string;
  onInputChange: (value: string) => void;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}) {
  return (
    <section className="rounded-lg border bg-card p-4 shadow-sm" aria-label={title}>
      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(280px,420px)] lg:items-end">
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <span className="inline-flex h-9 w-9 items-center justify-center rounded-md bg-primary/10 text-primary">
              <Icon aria-hidden className="h-4 w-4" />
            </span>
            <div>
              <h2 className="text-base font-semibold">{title}</h2>
              <p className="text-sm text-muted-foreground">{description}</p>
            </div>
          </div>
          <span className={cn(
            "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium",
            configured
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
              : "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
          )}>
            <ShieldCheck aria-hidden className="h-3.5 w-3.5" />
            {configured === undefined ? "Revisando..." : configured ? "Configurada" : "Sin configurar"}
          </span>
        </div>
        <form onSubmit={onSubmit} className="flex flex-col gap-2 sm:flex-row">
          <input
            type="password"
            value={inputValue}
            onChange={(event) => onInputChange(event.target.value)}
            className={cn(
              "min-h-[44px] min-w-0 flex-1 rounded-md border bg-background px-3 text-sm",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            )}
            placeholder={inputPlaceholder}
            autoComplete="off"
          />
          <button
            type="submit"
            disabled={saving}
            className={cn(
              "inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4",
              "text-sm font-medium text-primary-foreground hover:bg-primary/90",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              "disabled:pointer-events-none disabled:opacity-60",
            )}
          >
            {saving ? "Guardando..." : buttonLabel}
          </button>
        </form>
      </div>
    </section>
  );
}
