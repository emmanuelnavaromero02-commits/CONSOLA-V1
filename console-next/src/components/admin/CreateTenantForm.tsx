"use client";

import { useState } from "react";

import { toast } from "sonner";

import { useCreateTenant } from "@/lib/admin/tenants";

const MAX_NAME = 100;
const MIN_NAME = 2;
const MAX_EMAIL = 254;
const MIN_PASS = 12;
const MAX_PASS = 256;

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

/**
 * Create tenant form — Configuración/Admin → Tenants.
 *
 * POSTs to /api/admin/tenants. Optionally provisions the first
 * workspace and its first tenant_admin in the same call. The admin
 * fields stay disabled until a workspace name is given, because a
 * user must land in a workspace to have any access.
 */
export function CreateTenantForm() {
  const createMutation = useCreateTenant();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [workspaceName, setWorkspaceName] = useState("");
  const [adminEmail, setAdminEmail] = useState("");
  const [adminName, setAdminName] = useState("");
  const [adminPassword, setAdminPassword] = useState("");

  const hasWorkspace = workspaceName.trim().length > 0;
  const wantsAdmin = adminEmail.trim().length > 0 || adminPassword.length > 0;

  function reset() {
    setName("");
    setWorkspaceName("");
    setAdminEmail("");
    setAdminName("");
    setAdminPassword("");
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedName = name.trim();
    if (trimmedName.length < MIN_NAME) {
      toast.error(`El nombre del tenant debe tener al menos ${MIN_NAME} caracteres.`);
      return;
    }
    if (wantsAdmin) {
      if (!hasWorkspace) {
        toast.error("Para crear un admin inicial primero define un workspace.");
        return;
      }
      const email = adminEmail.trim().toLowerCase();
      if (!EMAIL_RE.test(email)) {
        toast.error("El email del admin no tiene un formato válido.");
        return;
      }
      if (adminPassword.length < MIN_PASS) {
        toast.error(`La contraseña temporal debe tener al menos ${MIN_PASS} caracteres.`);
        return;
      }
    }
    try {
      const res = await createMutation.mutateAsync({
        name: trimmedName,
        workspace_name: hasWorkspace ? workspaceName.trim() : undefined,
        admin_email: wantsAdmin ? adminEmail.trim().toLowerCase() : undefined,
        admin_password: wantsAdmin ? adminPassword : undefined,
        admin_name: wantsAdmin && adminName.trim() ? adminName.trim() : undefined,
      });
      toast.success(`Tenant ${res.name} creado.`);
      reset();
      setOpen(false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Error desconocido.";
      toast.error(`No se pudo crear: ${msg}`);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex min-h-[44px] items-center justify-center gap-1.5 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span aria-hidden>＋</span>
        Nuevo tenant
      </button>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-lg border bg-card p-5 shadow-sm"
      aria-label="Nuevo tenant"
    >
      <header className="mb-3 flex items-baseline justify-between gap-2">
        <h2 className="text-base font-semibold tracking-tight">Nuevo tenant</h2>
        <button
          type="button"
          onClick={() => { reset(); setOpen(false); }}
          className="text-xs text-muted-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:underline"
        >
          Cancelar
        </button>
      </header>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="space-y-1.5 text-sm">
          <span className="font-medium">Nombre del tenant</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
            minLength={MIN_NAME}
            maxLength={MAX_NAME}
            placeholder="acme"
            className="min-h-[44px] w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </label>
        <label className="space-y-1.5 text-sm">
          <span className="font-medium">Workspace inicial (opcional)</span>
          <input
            type="text"
            value={workspaceName}
            onChange={(e) => setWorkspaceName(e.target.value)}
            maxLength={MAX_NAME}
            placeholder="acme-prod"
            className="min-h-[44px] w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </label>
      </div>

      <fieldset className="mt-4 rounded-md border bg-muted/20 p-4" disabled={!hasWorkspace}>
        <legend className="px-1 text-xs font-medium text-muted-foreground">
          Admin inicial (opcional{hasWorkspace ? "" : " — requiere workspace"})
        </legend>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="space-y-1.5 text-sm">
            <span className="font-medium">Email</span>
            <input
              type="email"
              value={adminEmail}
              onChange={(e) => setAdminEmail(e.target.value)}
              maxLength={MAX_EMAIL}
              autoComplete="off"
              className="min-h-[44px] w-full rounded-md border border-input bg-background px-3 text-sm disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </label>
          <label className="space-y-1.5 text-sm">
            <span className="font-medium">Nombre (opcional)</span>
            <input
              type="text"
              value={adminName}
              onChange={(e) => setAdminName(e.target.value)}
              maxLength={MAX_NAME}
              autoComplete="off"
              className="min-h-[44px] w-full rounded-md border border-input bg-background px-3 text-sm disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </label>
          <label className="space-y-1.5 text-sm sm:col-span-2">
            <span className="font-medium">Contraseña temporal</span>
            <input
              type="password"
              value={adminPassword}
              onChange={(e) => setAdminPassword(e.target.value)}
              minLength={MIN_PASS}
              maxLength={MAX_PASS}
              autoComplete="new-password"
              className="min-h-[44px] w-full rounded-md border border-input bg-background px-3 text-sm disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <span className="block text-[11px] text-muted-foreground">
              Mínimo {MIN_PASS} caracteres. Rol tenant_admin. Deberá cambiarla en su primer login.
            </span>
          </label>
        </div>
      </fieldset>

      <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <button
          type="submit"
          disabled={createMutation.isPending}
          className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
        >
          {createMutation.isPending ? "Creando…" : "Crear tenant"}
        </button>
      </div>
    </form>
  );
}
