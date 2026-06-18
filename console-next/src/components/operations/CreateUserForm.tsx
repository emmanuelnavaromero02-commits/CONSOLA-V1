"use client";

import { useMemo, useState } from "react";

import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";

import { getMeAccess } from "@/lib/admin-surfaces";
import { useCreateUser } from "@/lib/operations/hooks";
import type { WorkspaceAccessItem } from "@/lib/workspace-context";


const PLATFORM_ROLES: { value: string; label: string }[] = [
  { value: "viewer",          label: "Viewer" },
  { value: "analyst",         label: "Analyst" },
  { value: "tenant_admin",    label: "Tenant admin" },
  { value: "workspace_admin", label: "Workspace admin" },
  { value: "auditor",         label: "Auditor" },
  { value: "security_admin",  label: "Security admin" },
  { value: "admin",           label: "Admin" },
  { value: "super_admin",     label: "Super admin" },
];

const TENANT_ROLES: { value: string; label: string }[] = [
  { value: "viewer",       label: "Viewer" },
  { value: "analyst",      label: "Analyst" },
  { value: "workspace_user", label: "Workspace user" },
  { value: "tenant_admin", label: "Tenant admin" },
];

const MAX_EMAIL  = 254;
const MAX_NAME   = 120;
const MIN_PASS   = 12;
const MAX_PASS   = 256;

// Round 1 P1 — client-side email format guard so we don't
// round-trip a malformed value to the backend just to get a
// generic 400.  Same shape RFC 5322 accepts for simple
// mailboxes (no comment/quoted-string forms, which we don't
// need for an enterprise console).
const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

function workspaceLabel(workspace: WorkspaceAccessItem): string {
  const tenant = workspace.tenant_name?.trim();
  const name = workspace.workspace_name?.trim() || workspace.workspace_id || "Workspace";
  return tenant ? `${tenant} / ${name}` : name;
}


/**
 * v1.44.4 Group 1 — Create user form.
 *
 * POSTs to /api/admin/users (real backend). Returns the new user
 * dict; React-Query invalidates the list so the table updates
 * immediately.
 *
 * Backend enforces ``must_change_password=TRUE`` whenever an
 * admin sets a password, so this form's "password" is a
 * temporary credential the operator gives the user — they'll
 * be forced to change it on first login.
 */
export function CreateUserForm() {
  const createMutation = useCreateUser();
  const access = useQuery({
    queryKey: ["me", "access"],
    queryFn: getMeAccess,
    staleTime: 60_000,
  });
  const [email,    setEmail]    = useState("");
  const [name,     setName]     = useState("");
  const [password, setPassword] = useState("");
  const [role,     setRole]     = useState<string>("viewer");
  const [open,     setOpen]     = useState(false);
  const [manualWorkspaceId, setManualWorkspaceId] = useState("");
  const workspaces = useMemo(() => (
    (access.data?.workspaces ?? []).filter((workspace) => workspace.workspace_id?.trim())
  ), [access.data?.workspaces]);
  const activeWorkspaceId = access.data?.workspace?.workspace_id?.trim() ?? "";
  const activeWorkspace = workspaces.find((workspace) => workspace.active && workspace.workspace_id);
  const workspaceId = manualWorkspaceId || activeWorkspace?.workspace_id || activeWorkspaceId || workspaces[0]?.workspace_id || "";
  const roles = access.data?.role?.is_platform_admin ? PLATFORM_ROLES : TENANT_ROLES;

  function reset() {
    setEmail("");
    setName("");
    setPassword("");
    setRole("viewer");
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedEmail = email.trim().toLowerCase();
    if (!trimmedEmail || !password) {
      toast.error("El email y la contraseña temporal son obligatorios.");
      return;
    }
    if (!EMAIL_RE.test(trimmedEmail)) {
      toast.error("El email no tiene un formato válido.");
      return;
    }
    if (password.length < MIN_PASS) {
      toast.error(`La contraseña temporal debe tener al menos ${MIN_PASS} caracteres.`);
      return;
    }
    if (!workspaceId) {
      toast.error("No hay workspace activo para asignar el usuario.");
      return;
    }
    try {
      await createMutation.mutateAsync({
        email:    trimmedEmail,
        password,
        name:     name.trim() || undefined,
        role,
        workspace_id: workspaceId,
      });
      toast.success(`Usuario ${trimmedEmail} creado.`);
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
        Nuevo usuario
      </button>
    );
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-lg border bg-card p-5 shadow-sm"
      aria-label="Nuevo usuario"
    >
      <header className="mb-3 flex items-baseline justify-between gap-2">
        <h2 className="text-base font-semibold tracking-tight">Nuevo usuario</h2>
        <button
          type="button"
          onClick={() => { reset(); setOpen(false); }}
          className="text-xs text-muted-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:underline"
        >
          Cancelar
        </button>
      </header>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {workspaces.length > 0 ? (
          <label className="space-y-1.5 text-sm sm:col-span-2">
            <span className="font-medium">Workspace destino</span>
            <select
              value={workspaceId}
              onChange={(event) => setManualWorkspaceId(event.target.value)}
              className="min-h-[44px] w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {workspaces.map((workspace) => (
                <option key={workspace.workspace_id || workspaceLabel(workspace)} value={workspace.workspace_id || ""}>
                  {workspaceLabel(workspace)}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <label className="space-y-1.5 text-sm">
          <span className="font-medium">Email</span>
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            maxLength={MAX_EMAIL}
            autoComplete="email"
            className="min-h-[44px] w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </label>
        <label className="space-y-1.5 text-sm">
          <span className="font-medium">Nombre (opcional)</span>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={MAX_NAME}
            autoComplete="name"
            className="min-h-[44px] w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </label>
        <label className="space-y-1.5 text-sm">
          <span className="font-medium">Contraseña temporal</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            minLength={MIN_PASS}
            maxLength={MAX_PASS}
            autoComplete="new-password"
            className="min-h-[44px] w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
          <span className="block text-[11px] text-muted-foreground">
            Mínimo {MIN_PASS} caracteres. El usuario tendrá que cambiarla en su primer login.
          </span>
        </label>
        <label className="space-y-1.5 text-sm">
          <span className="font-medium">Rol</span>
          <select
            value={role}
            onChange={(e) => setRole(e.target.value)}
            className="min-h-[44px] w-full rounded-md border border-input bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {roles.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="mt-4 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <button
          type="submit"
          disabled={createMutation.isPending || access.isLoading || !workspaceId}
          className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
        >
          {createMutation.isPending ? "Creando…" : "Crear usuario"}
        </button>
      </div>
    </form>
  );
}
