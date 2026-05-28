"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, RefreshCw, ShieldCheck } from "lucide-react";

import { changeOwnPassword, getMeAccess, getMeProfile } from "@/lib/admin-surfaces";

const MIN_PASSWORD_LENGTH = 12;

function boolLabel(value?: boolean): string {
  return value ? "Sí" : "No";
}

export function MeAccessPanel() {
  const queryClient = useQueryClient();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [formMessage, setFormMessage] = useState("");

  const access = useQuery({
    queryKey: ["me", "access"],
    queryFn: getMeAccess,
    staleTime: 60_000,
  });
  const profile = useQuery({
    queryKey: ["me", "profile"],
    queryFn: getMeProfile,
    staleTime: 60_000,
  });
  const changePassword = useMutation({
    mutationFn: changeOwnPassword,
    onSuccess: async () => {
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      setFormMessage("Password actualizado correctamente. Ya puedes continuar.");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["me", "access"] }),
        queryClient.invalidateQueries({ queryKey: ["me", "profile"] }),
      ]);
    },
    onError: () => setFormMessage("No se pudo actualizar el password."),
  });

  function submitPassword() {
    setFormMessage("");
    if (newPassword !== confirmPassword) {
      setFormMessage("Los passwords nuevos no coinciden.");
      return;
    }
    if (newPassword.length < MIN_PASSWORD_LENGTH) {
      setFormMessage(`El nuevo password debe tener al menos ${MIN_PASSWORD_LENGTH} caracteres.`);
      return;
    }
    changePassword.mutate({
      current_password: currentPassword,
      new_password: newPassword,
    });
  }

  if (access.isLoading) {
    return (
      <div aria-busy="true" className="space-y-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <span key={i} className="block h-14 animate-pulse rounded bg-muted" aria-hidden />
        ))}
      </div>
    );
  }

  if (access.isError) {
    return (
      <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
        <p className="font-medium text-destructive">No se pudo cargar tu perfil de acceso.</p>
        <button
          type="button"
          onClick={() => access.refetch()}
          className="mt-2 inline-flex min-h-[44px] items-center gap-2 rounded-md border px-3 text-xs font-medium"
        >
          <RefreshCw aria-hidden className="h-4 w-4" />
          Reintentar
        </button>
      </div>
    );
  }

  const data = access.data;
  const allowed = data?.cartridges?.allowed ?? [];
  const denied = data?.cartridges?.denied ?? [];
  const capabilities = Object.entries(data?.ui_capabilities ?? {}).sort(([a], [b]) => a.localeCompare(b));
  const permissions = data?.permissions ?? [];

  return (
    <div className="space-y-6">
      <section className="grid grid-cols-1 gap-4 md:grid-cols-3" aria-label="Identidad">
        <article className="rounded-lg border bg-card p-5">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Usuario</p>
          <h2 className="mt-2 text-lg font-semibold">{profile.data?.name || data?.user?.name || data?.user?.email || "—"}</h2>
          <p className="text-sm text-muted-foreground">{profile.data?.email || data?.user?.email || "—"}</p>
        </article>
        <article className="rounded-lg border bg-card p-5">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Rol global</p>
          <h2 className="mt-2 text-lg font-semibold">{profile.data?.role || data?.role?.global || "—"}</h2>
          <p className="text-sm text-muted-foreground">Admin plataforma: {boolLabel(data?.role?.is_platform_admin)}</p>
        </article>
        <article className="rounded-lg border bg-card p-5">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Workspace</p>
          <h2 className="mt-2 text-lg font-semibold">{data?.workspace?.workspace_role || "sin rol"}</h2>
          <p className="font-mono text-xs text-muted-foreground">{data?.workspace?.workspace_id || "—"}</p>
        </article>
      </section>

      {profile.data?.must_change_password ? (
        <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          Debes cambiar tu password antes de continuar usando la aplicación. Usa la contraseña temporal como password actual.
        </p>
      ) : null}

      <section className="space-y-4 rounded-lg border bg-card p-5" aria-label="Cambiar password">
        <div className="flex items-center gap-2">
          <KeyRound aria-hidden className="h-5 w-5 text-primary" />
          <h2 className="text-base font-semibold">Cambiar password</h2>
        </div>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium">Password actual</span>
            <input
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(event) => setCurrentPassword(event.target.value)}
              className="min-h-[44px] rounded-md border bg-background px-3 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium">Nuevo password</span>
            <input
              type="password"
              autoComplete="new-password"
              value={newPassword}
              onChange={(event) => setNewPassword(event.target.value)}
              className="min-h-[44px] rounded-md border bg-background px-3 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium">Confirmar</span>
            <input
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              className="min-h-[44px] rounded-md border bg-background px-3 text-sm"
            />
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={submitPassword}
            disabled={changePassword.isPending || !currentPassword || !newPassword || !confirmPassword}
            className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            Guardar password
          </button>
          {formMessage ? (
            <div role="status" className="flex flex-wrap items-center gap-3">
              <p
                className={changePassword.isError ? "text-sm text-destructive" : "text-sm text-emerald-700 dark:text-emerald-300"}
              >
                {formMessage}
              </p>
              {changePassword.isSuccess ? (
                <Link
                  href="/dashboard"
                  className="inline-flex min-h-[36px] items-center rounded-md border px-3 text-xs font-medium hover:bg-accent/5"
                >
                  Ir al panel
                </Link>
              ) : null}
            </div>
          ) : null}
        </div>
      </section>

      <section className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <article className="space-y-3 rounded-lg border bg-card p-5">
          <div className="flex items-center gap-2">
            <ShieldCheck aria-hidden className="h-5 w-5 text-primary" />
            <h2 className="text-base font-semibold">Capacidades UI</h2>
          </div>
          {capabilities.length === 0 ? (
            <p className="text-sm text-muted-foreground">Sin capacidades visibles.</p>
          ) : (
            <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {capabilities.map(([key, value]) => (
                <div key={key} className="rounded-md bg-muted/30 p-3">
                  <dt className="font-mono text-xs text-muted-foreground">{key}</dt>
                  <dd className={value ? "font-semibold text-emerald-700 dark:text-emerald-300" : "font-semibold text-muted-foreground"}>
                    {boolLabel(value)}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </article>

        <article className="space-y-3 rounded-lg border bg-card p-5">
          <h2 className="text-base font-semibold">Cartuchos</h2>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Permitidos</p>
              {allowed.length === 0 ? (
                <p className="text-sm text-muted-foreground">Sin cartuchos visibles.</p>
              ) : (
                <ul className="space-y-2">
                  {allowed.map((item) => (
                    <li key={item.cartridge_id} className="rounded-md bg-muted/30 p-3 text-sm">
                      <span className="font-medium">{item.product_name || item.cartridge_id}</span>
                      <span className="ml-2 text-xs text-muted-foreground">{item.status || "—"}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div>
              <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">Denegados</p>
              {denied.length === 0 ? (
                <p className="text-sm text-muted-foreground">Sin denegaciones.</p>
              ) : (
                <ul className="space-y-2">
                  {denied.map((item) => (
                    <li key={item.cartridge_id} className="rounded-md bg-destructive/5 p-3 text-sm text-destructive">
                      {item.product_name || item.cartridge_id}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </article>
      </section>

      <section className="space-y-3 rounded-lg border bg-card p-5" aria-label="Permisos efectivos">
        <h2 className="text-base font-semibold">Permisos efectivos</h2>
        {permissions.length === 0 ? (
          <p className="text-sm text-muted-foreground">Sin permisos efectivos.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {permissions.map((permission) => (
              <span key={permission} className="rounded-md border bg-background px-2.5 py-1.5 font-mono text-xs">
                {permission}
              </span>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
