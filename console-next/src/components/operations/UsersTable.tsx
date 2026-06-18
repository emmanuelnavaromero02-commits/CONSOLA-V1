"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { toast } from "sonner";
import { Check, Copy, KeyRound, ShieldOff } from "lucide-react";

import { formatDistanceToNow } from "date-fns";
import { es } from "date-fns/locale";

import type { AppUser } from "@/lib/operations/types";
import {
  useDeleteUser,
  useSendPasswordReset,
  useUpdateUser,
  useUsers,
} from "@/lib/operations/hooks";


/**
 * v1.44.4 Group 1 — Users table.
 *
 * Renders the real /api/admin/users response. Per-row actions:
 *   - Send password reset (POST /api/admin/users/{id}/send-reset)
 *   - Toggle is_active (PUT /api/admin/users/{id})
 *   - Delete account (DELETE /api/admin/users/{id} — gated by
 *     a confirm dialog because it's irreversible)
 *
 * Destructive actions show a confirm step so a wrong-row click
 * can't take a user out of the system. The confirm dialog
 * reuses the same a11y pattern as ApprovalGateDialog in
 * /workspace.
 */
function relativeTime(value: string | null): string {
  if (!value) return "—";
  const ts = Date.parse(value);
  if (!Number.isFinite(ts)) return "—";
  return formatDistanceToNow(new Date(ts), { addSuffix: true, locale: es });
}

function workspaceLabel(workspace: NonNullable<AppUser["workspaces"]>[number]): string {
  const name = workspace.workspace_name?.trim() || workspace.workspace_id || "Workspace";
  return workspace.tenant_name?.trim() ? `${workspace.tenant_name} / ${name}` : name;
}

function userWorkspaceSummary(user: AppUser): string {
  const workspaces = user.workspaces ?? [];
  if (!workspaces.length) return "—";
  return workspaces.map(workspaceLabel).join(", ");
}

interface ResetSecret {
  email: string;
  password: string;
  sent: boolean;
}


export function UsersTable() {
  const { data: users, isLoading, isError, refetch } = useUsers();
  const updateMutation = useUpdateUser();
  const deleteMutation = useDeleteUser();
  const resetMutation  = useSendPasswordReset();

  const [confirmDelete, setConfirmDelete] = useState<AppUser | null>(null);
  const [workspaceFilter, setWorkspaceFilter] = useState("");
  const [resetSecret, setResetSecret] = useState<ResetSecret | null>(null);
  const [copiedReset, setCopiedReset] = useState(false);
  const rows = useMemo(() => users ?? [], [users]);
  const workspaceOptions = useMemo(() => {
    const byId = new Map<string, string>();
    for (const user of rows) {
      for (const workspace of user.workspaces ?? []) {
        const id = workspace.workspace_id?.trim();
        if (id) byId.set(id, workspaceLabel(workspace));
      }
    }
    return Array.from(byId.entries())
      .map(([id, label]) => ({ id, label }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [rows]);
  const visibleRows = workspaceFilter
    ? rows.filter((user) => (user.workspaces ?? []).some((workspace) => workspace.workspace_id === workspaceFilter))
    : rows;

  // Round 1 P0: focus management on the delete confirm dialog.
  // The dialog auto-focuses Cancel (defensive default for a
  // destructive prompt), traps Escape, locks body scroll,
  // and restores focus to the trigger on close.
  const dialogCancelRef = useRef<HTMLButtonElement | null>(null);
  const previousFocus   = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!confirmDelete) return;

    previousFocus.current = (document.activeElement instanceof HTMLElement)
      ? document.activeElement
      : null;

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const focusTimer = window.setTimeout(() => {
      dialogCancelRef.current?.focus();
    }, 0);

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setConfirmDelete(null);
    }
    document.addEventListener("keydown", onKey);

    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = previousOverflow;
      previousFocus.current?.focus();
    };
  }, [confirmDelete]);

  if (isLoading) {
    return (
      <div
        aria-busy="true"
        className="space-y-2"
      >
        {Array.from({ length: 4 }).map((_, i) => (
          <span
            key={i}
            className="block h-14 animate-pulse rounded bg-muted"
            aria-hidden
          />
        ))}
      </div>
    );
  }

  if (isError) {
    return (
      <div
        role="alert"
        className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm"
      >
        <p className="font-medium text-destructive">
          No se pudieron cargar los usuarios.
        </p>
        <button
          type="button"
          onClick={() => refetch()}
          className="mt-2 inline-flex min-h-[44px] items-center justify-center rounded-md border border-destructive/40 px-3 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40"
        >
          Reintentar
        </button>
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">
        No hay usuarios registrados todavía.
      </p>
    );
  }

  async function handleToggleActive(u: AppUser) {
    try {
      await updateMutation.mutateAsync({
        userId: u.id,
        patch:  { is_active: !u.is_active },
      });
      toast.success(u.is_active ? "Usuario desactivado." : "Usuario reactivado.");
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Error desconocido.";
      toast.error(`No se pudo cambiar el estado: ${msg}`);
    }
  }

  async function handleSendReset(u: AppUser) {
    try {
      const result = await resetMutation.mutateAsync(u.id);
      if (result.temporary_password) {
        setResetSecret({
          email: u.email,
          password: result.temporary_password,
          sent: Boolean(result.sent),
        });
        setCopiedReset(false);
        toast.success("Contraseña temporal generada.");
      } else {
        setResetSecret(null);
        toast.success(`Email de restablecimiento enviado a ${u.email}.`);
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Error desconocido.";
      toast.error(`No se pudo enviar el reset: ${msg}`);
    }
  }

  async function handleCopyResetPassword() {
    if (!resetSecret) return;
    try {
      await navigator.clipboard.writeText(resetSecret.password);
      setCopiedReset(true);
      toast.success("Contraseña temporal copiada.");
    } catch {
      toast.error("No se pudo copiar automáticamente.");
    }
  }

  async function handleConfirmDelete() {
    if (!confirmDelete) return;
    try {
      await deleteMutation.mutateAsync(confirmDelete.id);
      toast.success(`Usuario ${confirmDelete.email} eliminado.`);
      setConfirmDelete(null);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Error desconocido.";
      toast.error(`No se pudo eliminar: ${msg}`);
    }
  }

  return (
    <>
      {resetSecret ? (
        <div className="mb-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="min-w-0">
              <p className="font-semibold text-amber-800 dark:text-amber-200">
                Contraseña temporal generada
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                {resetSecret.email} · {resetSecret.sent ? "correo enviado" : "correo no confirmado"}
              </p>
              <code className="mt-2 block overflow-x-auto rounded-md border bg-background px-3 py-2 font-mono text-xs text-foreground">
                {resetSecret.password}
              </code>
            </div>
            <div className="flex shrink-0 gap-2">
              <button
                type="button"
                onClick={() => void handleCopyResetPassword()}
                className="inline-flex min-h-[40px] items-center gap-2 rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {copiedReset ? (
                  <Check aria-hidden className="h-3.5 w-3.5" />
                ) : (
                  <Copy aria-hidden className="h-3.5 w-3.5" />
                )}
                {copiedReset ? "Copiada" : "Copiar"}
              </button>
              <button
                type="button"
                onClick={() => setResetSecret(null)}
                className="inline-flex min-h-[40px] items-center rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                Ocultar
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {workspaceOptions.length > 1 ? (
        <div className="mb-3 flex flex-col gap-2 rounded-lg border bg-card p-3 sm:flex-row sm:items-center sm:justify-between">
          <label className="space-y-1 text-sm">
            <span className="font-medium">Filtrar por workspace</span>
            <select
              value={workspaceFilter}
              onChange={(event) => setWorkspaceFilter(event.target.value)}
              className="min-h-[40px] min-w-72 rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <option value="">Todos los workspaces visibles</option>
              {workspaceOptions.map((workspace) => (
                <option key={workspace.id} value={workspace.id}>
                  {workspace.label}
                </option>
              ))}
            </select>
          </label>
          <span className="text-xs text-muted-foreground">
            {visibleRows.length} de {rows.length} usuarios visibles
          </span>
        </div>
      ) : null}
      <div className="overflow-x-auto rounded-lg border bg-card">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="px-4 py-2 font-medium">Email</th>
              <th className="px-4 py-2 font-medium">Nombre</th>
              <th className="px-4 py-2 font-medium">Rol</th>
              <th className="px-4 py-2 font-medium">Workspace</th>
              <th className="px-4 py-2 font-medium">Estado</th>
              <th className="px-4 py-2 font-medium">Último login</th>
              <th className="px-4 py-2 text-right font-medium">Acciones</th>
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((u) => (
              <tr key={u.id} className="border-t">
                <td className="px-4 py-3">
                  <span className="font-medium">{u.email}</span>
                  {u.must_change_password ? (
                    <span className="ml-2 inline-flex items-center rounded-full bg-amber-500/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-amber-700 dark:text-amber-300">
                      Cambio pendiente
                    </span>
                  ) : null}
                </td>
                <td className="px-4 py-3 text-muted-foreground">
                  {u.name || "—"}
                </td>
                <td className="px-4 py-3 font-mono text-xs">{u.role}</td>
                <td className="max-w-xs px-4 py-3 text-xs text-muted-foreground">
                  {userWorkspaceSummary(u)}
                </td>
                <td className="px-4 py-3">
                  <span
                    className={
                      u.is_active
                        ? "inline-flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400"
                        : "inline-flex items-center gap-1.5 text-muted-foreground"
                    }
                  >
                    <span
                      aria-hidden
                      className={
                        "h-1.5 w-1.5 rounded-full " +
                        (u.is_active ? "bg-emerald-500" : "bg-muted-foreground")
                      }
                    />
                    {u.is_active ? "Activo" : "Inactivo"}
                  </span>
                </td>
                <td className="px-4 py-3 text-xs text-muted-foreground">
                  {relativeTime(u.last_login)}
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="inline-flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => void handleSendReset(u)}
                      aria-label={`Enviar reset de contraseña a ${u.email}`}
                      disabled={resetMutation.isPending}
                      className="inline-flex min-h-[44px] items-center gap-1 rounded-md border bg-background px-2.5 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
                    >
                      <KeyRound aria-hidden className="h-3.5 w-3.5" />
                      Reset
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleToggleActive(u)}
                      aria-label={u.is_active
                        ? `Desactivar a ${u.email}`
                        : `Reactivar a ${u.email}`}
                      disabled={updateMutation.isPending}
                      className="inline-flex min-h-[44px] items-center gap-1 rounded-md border bg-background px-2.5 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
                    >
                      <ShieldOff aria-hidden className="h-3.5 w-3.5" />
                      {u.is_active ? "Desactivar" : "Reactivar"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmDelete(u)}
                      aria-label={`Eliminar a ${u.email}`}
                      className="inline-flex min-h-[44px] items-center rounded-md border border-destructive/40 bg-background px-2.5 text-xs font-medium text-destructive transition-colors hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40"
                    >
                      Borrar
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {confirmDelete ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="delete-user-title"
          className="fixed inset-0 z-50 flex items-center justify-center p-4"
        >
          <div
            className="absolute inset-0 bg-black/50"
            onClick={() => setConfirmDelete(null)}
            aria-hidden
          />
          <div className="relative w-full max-w-md rounded-lg border bg-card p-6 shadow-lg">
            <h3
              id="delete-user-title"
              className="text-lg font-semibold tracking-tight"
            >
              ⚠️ Eliminar usuario
            </h3>
            <p className="mt-2 text-sm text-muted-foreground">
              Esta acción NO se puede deshacer.{" "}
              <span className="font-medium text-foreground">
                {confirmDelete.email}
              </span>{" "}
              perderá acceso a la plataforma inmediatamente.
            </p>
            <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
              <button
                ref={dialogCancelRef}
                type="button"
                onClick={() => setConfirmDelete(null)}
                disabled={deleteMutation.isPending}
                className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-4 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
              >
                Cancelar
              </button>
              <button
                type="button"
                onClick={() => void handleConfirmDelete()}
                disabled={deleteMutation.isPending}
                className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-destructive px-4 text-sm font-medium text-destructive-foreground hover:bg-destructive/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40 disabled:pointer-events-none disabled:opacity-60"
              >
                {deleteMutation.isPending ? "Eliminando…" : "Eliminar"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
