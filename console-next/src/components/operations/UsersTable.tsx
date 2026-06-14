"use client";

import { useEffect, useRef, useState } from "react";

import { toast } from "sonner";
import { KeyRound, ShieldOff } from "lucide-react";

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


export function UsersTable() {
  const { data: users, isLoading, isError, refetch } = useUsers();
  const updateMutation = useUpdateUser();
  const deleteMutation = useDeleteUser();
  const resetMutation  = useSendPasswordReset();

  const [confirmDelete, setConfirmDelete] = useState<AppUser | null>(null);
  const [workspaceFilter, setWorkspaceFilter] = useState<string>("");

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

  const allRows = users ?? [];
  if (allRows.length === 0) {
    return (
      <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">
        No hay usuarios registrados todavía.
      </p>
    );
  }

  // Unique tenant/workspace options across all listed users, for the
  // segmentation filter. Grouped by tenant in the dropdown.
  const workspaceIndex = new Map<string, { label: string; tenant: string }>();
  for (const u of allRows) {
    for (const w of u.workspaces ?? []) {
      if (!workspaceIndex.has(w.workspace_id)) {
        workspaceIndex.set(w.workspace_id, { label: w.workspace_name, tenant: w.tenant_name });
      }
    }
  }
  const workspaceTenants = Array.from(
    Array.from(workspaceIndex.entries()).reduce((acc, [id, info]) => {
      const list = acc.get(info.tenant) ?? [];
      list.push({ id, label: info.label });
      acc.set(info.tenant, list);
      return acc;
    }, new Map<string, Array<{ id: string; label: string }>>()),
  );

  const rows = workspaceFilter
    ? allRows.filter((u) => (u.workspaces ?? []).some((w) => w.workspace_id === workspaceFilter))
    : allRows;

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
      await resetMutation.mutateAsync(u.id);
      toast.success(`Email de restablecimiento enviado a ${u.email}.`);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Error desconocido.";
      toast.error(`No se pudo enviar el reset: ${msg}`);
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
      {workspaceIndex.size > 0 ? (
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <label className="text-xs font-medium uppercase text-muted-foreground" htmlFor="ws-filter">
            Segmentar por
          </label>
          <select
            id="ws-filter"
            value={workspaceFilter}
            onChange={(e) => setWorkspaceFilter(e.target.value)}
            className="min-h-[40px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <option value="">Todos los workspaces</option>
            {workspaceTenants.map(([tenant, items]) => (
              <optgroup key={tenant} label={tenant}>
                {items.map((w) => (
                  <option key={w.id} value={w.id}>
                    {w.label}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
          <span className="text-xs text-muted-foreground">
            {rows.length} de {allRows.length}
          </span>
        </div>
      ) : null}

      <div className="overflow-x-auto rounded-lg border bg-card">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="px-4 py-2 font-medium">Email</th>
              <th className="px-4 py-2 font-medium">Nombre</th>
              <th className="px-4 py-2 font-medium">Tenant / Workspace</th>
              <th className="px-4 py-2 font-medium">Rol</th>
              <th className="px-4 py-2 font-medium">Estado</th>
              <th className="px-4 py-2 font-medium">Último login</th>
              <th className="px-4 py-2 text-right font-medium">Acciones</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((u) => (
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
                <td className="px-4 py-3">
                  {(u.workspaces ?? []).length === 0 ? (
                    <span className="text-xs text-muted-foreground">—</span>
                  ) : (
                    <div className="flex flex-wrap gap-1">
                      {(u.workspaces ?? []).map((w) => (
                        <span
                          key={w.workspace_id}
                          className="inline-flex items-center rounded-full border bg-muted/40 px-2 py-0.5 text-[11px]"
                          title={`${w.tenant_name} / ${w.workspace_name} · ${w.workspace_role}`}
                        >
                          <span className="text-muted-foreground">{w.tenant_name}</span>
                          <span className="mx-1 text-muted-foreground/60">/</span>
                          <span className="font-medium">{w.workspace_name}</span>
                        </span>
                      ))}
                    </div>
                  )}
                </td>
                <td className="px-4 py-3 font-mono text-xs">{u.role}</td>
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
