"use client";

import { useState } from "react";

import { useQuery } from "@tanstack/react-query";
import { toast } from "sonner";

import { getMeAccess } from "@/lib/admin-surfaces";
import { useCreateUser } from "@/lib/operations/hooks";


const ROLES: { value: string; label: string }[] = [
  { value: "viewer",          label: "Viewer" },
  { value: "analyst",         label: "Analyst" },
  { value: "workspace_admin", label: "Workspace admin" },
  { value: "admin",           label: "Admin" },
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
  const workspaceId = access.data?.workspace?.workspace_id?.trim() ?? "";

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
      <p className="mb-3 rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
        Workspace destino: <span className="font-mono">{workspaceId || "sin workspace activo"}</span>
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
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
            {ROLES.map((r) => (
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
