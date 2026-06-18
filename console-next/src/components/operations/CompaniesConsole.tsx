"use client";

import { useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  Building2,
  Copy,
  KeyRound,
  PackageCheck,
  RefreshCw,
  ShieldAlert,
  ShieldCheck,
  UserRoundCog,
} from "lucide-react";
import { toast } from "sonner";

import {
  listAdminInstallations,
  runAdminInstallationAction,
  type AdminInstallationAction,
  type CartridgeInstallation,
  type MarketplaceStatus,
} from "@/lib/marketplace";
import {
  useBootstrapTenantAdmin,
  useCreateTenant,
  useCreateTenantWorkspace,
  useIssueTenantAdminTemporaryPassword,
  useTenantWorkspaces,
  useTenants,
} from "@/lib/operations/hooks";
import type { BootstrapTenantAdminResponse, TenantSummary, WorkspaceSummary } from "@/lib/operations/types";
import { cn } from "@/lib/utils";


const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const EMPTY_WORKSPACES: WorkspaceSummary[] = [];

interface ProvisioningResult {
  tenant: TenantSummary;
  workspace: WorkspaceSummary;
  bootstrap: BootstrapTenantAdminResponse;
}

function copy(value: string, label: string) {
  const writer = navigator.clipboard?.writeText(value);
  if (!writer) {
    toast.error(`No se pudo copiar ${label.toLowerCase()}.`);
    return;
  }
  writer
    .then(() => toast.success(`${label} copiado.`))
    .catch(() => toast.error(`No se pudo copiar ${label.toLowerCase()}.`));
}

function fmtDate(value: string | null | undefined): string {
  if (!value) return "-";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "-";
  return new Intl.DateTimeFormat("es-MX", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function loginUrl(path: string): string {
  if (!path.startsWith("/")) return path;
  if (typeof window === "undefined") return path;
  return `${window.location.origin}${path}`;
}

function accessBundle(result: ProvisioningResult): string {
  const login = loginUrl(result.bootstrap.login_url);
  return [
    `Login: ${login}`,
    `Email: ${result.bootstrap.user.email}`,
    result.bootstrap.temporary_password ? `Password temporal: ${result.bootstrap.temporary_password}` : null,
    `Tenant: ${result.tenant.name} (${result.tenant.id})`,
    `Workspace: ${result.workspace.name} (${result.workspace.id})`,
    `Rol: ${result.bootstrap.workspace_role}`,
  ].filter(Boolean).join("\n");
}

function adminActionsFor(status: MarketplaceStatus | null | undefined): AdminInstallationAction[] {
  const value = String(status || "");
  const actions: AdminInstallationAction[] = [];
  if (["requested", "pending_connection", "waiting_credentials", "failed"].includes(value)) actions.push("approve");
  if (["ready", "pending_connection", "failed"].includes(value)) actions.push("pause");
  if (["paused", "revoked", "expired", "suspended"].includes(value)) actions.push("reactivate");
  if (["requested", "pending_connection", "waiting_credentials", "ready", "failed", "paused", "expired", "suspended"].includes(value)) actions.push("revoke");
  return actions;
}

function actionLabel(action: AdminInstallationAction): string {
  if (action === "approve") return "Aprobar";
  if (action === "pause") return "Pausar";
  if (action === "reactivate") return "Reactivar";
  return "Revocar";
}

function statusLabel(status: MarketplaceStatus | null | undefined): string {
  const value = String(status || "available");
  const labels: Record<string, string> = {
    available: "Disponible",
    active: "Activo",
    ready: "Activo",
    pending_approval: "Pendiente",
    requested: "Solicitado",
    pending_connection: "Pendiente conexión",
    waiting_credentials: "Requiere credenciales",
    failed: "Falló",
    paused: "Pausado",
    revoked: "Revocado",
    expired: "Expirado",
    suspended: "Suspendido",
  };
  return labels[value] ?? value;
}

function StatusPill({ status }: { status: MarketplaceStatus | null | undefined }) {
  const value = String(status || "available");
  const good = value === "ready" || value === "active";
  const warning = ["requested", "pending_approval", "pending_connection", "waiting_credentials", "paused"].includes(value);
  return (
    <span
      className={cn(
        "inline-flex min-h-7 items-center rounded-full border px-2.5 text-xs font-medium",
        good
          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
          : warning
            ? "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300"
            : "border-destructive/35 bg-destructive/10 text-destructive",
      )}
    >
      {statusLabel(status)}
    </span>
  );
}

export function CompaniesConsole() {
  const queryClient = useQueryClient();
  const tenants = useTenants();
  const createTenant = useCreateTenant();
  const createWorkspace = useCreateTenantWorkspace();
  const bootstrapAdmin = useBootstrapTenantAdmin();
  const issueTemporaryPassword = useIssueTenantAdminTemporaryPassword();

  const [companyName, setCompanyName] = useState("");
  const [companySlug, setCompanySlug] = useState("");
  const [workspaceName, setWorkspaceName] = useState("");
  const [adminEmail, setAdminEmail] = useState("");
  const [adminName, setAdminName] = useState("");
  const [workspaceCreateName, setWorkspaceCreateName] = useState("");
  const [selectedTenantId, setSelectedTenantId] = useState<string | null>(null);
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(null);
  const [result, setResult] = useState<ProvisioningResult | null>(null);

  const workspaces = useTenantWorkspaces(selectedTenantId);
  const rows = tenants.data?.tenants ?? [];
  const selectedTenant = rows.find((tenant) => tenant.id === selectedTenantId) ?? null;
  const workspaceRows = useMemo(
    () => (workspaces.data?.workspaces ?? EMPTY_WORKSPACES).filter((workspace) => workspace.tenant_id === selectedTenantId),
    [selectedTenantId, workspaces.data?.workspaces],
  );
  const selectedWorkspace = workspaceRows.find((workspace) => workspace.id === selectedWorkspaceId) ?? workspaceRows[0] ?? null;
  const selectedWorkspaceFilterId = selectedWorkspace?.id ?? null;
  const selectedMarketplaceScope = useMemo(
    () => ({
      tenantId: selectedTenantId,
      workspaceId: selectedWorkspace?.id ?? null,
    }),
    [selectedTenantId, selectedWorkspace?.id],
  );
  const installations = useQuery({
    queryKey: ["marketplace", "admin", "installations", selectedTenantId, selectedWorkspace?.id ?? null],
    queryFn: () => listAdminInstallations(selectedMarketplaceScope),
    enabled: Boolean(selectedTenantId && selectedWorkspace?.id),
    staleTime: 30_000,
  });
  const installationAction = useMutation({
    mutationFn: ({ id, action }: { id: string; action: AdminInstallationAction }) =>
      runAdminInstallationAction(id, action, selectedMarketplaceScope),
    onSuccess: () => {
      toast.success("Instalación actualizada.");
      queryClient.invalidateQueries({ queryKey: ["marketplace"] });
      queryClient.invalidateQueries({ queryKey: ["operations", "tenants"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo actualizar la instalación."),
  });
  const tenantWorkspaceIds = useMemo(() => new Set(workspaceRows.map((workspace) => workspace.id)), [workspaceRows]);
  const tenantInstallations = useMemo(
    () => (installations.data?.installations ?? []).filter((row) => {
      if (row.tenant_id) return row.tenant_id === selectedTenantId;
      if (row.workspace_id) return tenantWorkspaceIds.has(row.workspace_id);
      return false;
    }),
    [installations.data?.installations, selectedTenantId, tenantWorkspaceIds],
  );
  const workspaceInstallations = useMemo(
    () => tenantInstallations.filter((row) => !selectedWorkspaceFilterId || row.workspace_id === selectedWorkspaceFilterId),
    [tenantInstallations, selectedWorkspaceFilterId],
  );
  const pendingInstallations = tenantInstallations.filter((row) =>
    ["requested", "pending_approval", "pending_connection", "waiting_credentials"].includes(String(row.status || row.access_status || "")),
  ).length;
  const activeInstallations = tenantInstallations.filter((row) =>
    ["ready", "active"].includes(String(row.status || row.access_status || "")),
  ).length;
  const blockedInstallations = tenantInstallations.filter((row) =>
    ["paused", "revoked", "expired", "suspended", "failed"].includes(String(row.status || row.access_status || "")),
  ).length;
  const busy = createTenant.isPending || createWorkspace.isPending || bootstrapAdmin.isPending;

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const name = companyName.trim();
    const workspace = workspaceName.trim();
    const email = adminEmail.trim().toLowerCase();
    if (!name || !workspace || !email) {
      toast.error("Empresa, workspace y email del admin son obligatorios.");
      return;
    }
    if (!EMAIL_RE.test(email)) {
      toast.error("El email del admin no tiene un formato válido.");
      return;
    }
    try {
      const tenantResp = await createTenant.mutateAsync({
        name,
        slug: companySlug.trim() || undefined,
      });
      const workspaceResp = await createWorkspace.mutateAsync({
        tenantId: tenantResp.tenant.id,
        payload: { name: workspace },
      });
      const bootstrapResp = await bootstrapAdmin.mutateAsync({
        tenantId: tenantResp.tenant.id,
        payload: {
          workspace_id: workspaceResp.workspace.id,
          email,
          name: adminName.trim() || undefined,
        },
      });
      setResult({
        tenant: tenantResp.tenant,
        workspace: workspaceResp.workspace,
        bootstrap: bootstrapResp,
      });
      setSelectedTenantId(tenantResp.tenant.id);
      setSelectedWorkspaceId(workspaceResp.workspace.id);
      setCompanyName("");
      setCompanySlug("");
      setWorkspaceName("");
      setAdminEmail("");
      setAdminName("");
      toast.success("Empresa creada con admin aislado.");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Error desconocido.";
      toast.error(`No se pudo crear la empresa: ${message}`);
    }
  }

  async function handleIssueTemporaryPassword(workspace: WorkspaceSummary, userId: number) {
    if (!selectedTenant) return;
    try {
      const bootstrapResp = await issueTemporaryPassword.mutateAsync({
        tenantId: selectedTenant.id,
        userId,
        payload: { workspace_id: workspace.id },
      });
      setResult({
        tenant: selectedTenant,
        workspace,
        bootstrap: bootstrapResp,
      });
      toast.success("Contraseña temporal generada. Cópiala ahora.");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "No se pudo generar la contraseña temporal.");
    }
  }

  async function handleCreateWorkspaceForSelectedTenant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedTenant) return;
    const name = workspaceCreateName.trim();
    if (!name) {
      toast.error("El nombre del workspace es obligatorio.");
      return;
    }
    try {
      const response = await createWorkspace.mutateAsync({
        tenantId: selectedTenant.id,
        payload: { name },
      });
      setSelectedWorkspaceId(response.workspace.id);
      setWorkspaceCreateName("");
      toast.success(response.created ? "Workspace creado." : "Workspace existente seleccionado.");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "No se pudo crear el workspace.");
    }
  }

  function handleInstallationAction(installation: CartridgeInstallation, action: AdminInstallationAction) {
    const destructive = action === "revoke" || action === "pause";
    if (destructive) {
      const ok = window.confirm(
        `${actionLabel(action)} ${installation.product_name || installation.cartridge_id} para ${installation.workspace_name || "este workspace"}?`,
      );
      if (!ok) return;
    }
    installationAction.mutate({ id: installation.id, action });
  }

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-6">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Empresas</h1>
          <p className="max-w-3xl text-sm text-muted-foreground">
            Provisiona una empresa con workspace inicial y primer tenant admin aislado.
          </p>
        </div>
        <button
          type="button"
          onClick={() => tenants.refetch()}
          className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RefreshCw aria-hidden className={`h-4 w-4 ${tenants.isFetching ? "animate-spin" : ""}`} />
          Refrescar
        </button>
      </header>

      {result ? (
        <section
          aria-live="polite"
          className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-5 shadow-sm"
        >
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0 space-y-3">
              <div className="flex items-start gap-3">
                <KeyRound aria-hidden className="mt-0.5 h-5 w-5 text-amber-700 dark:text-amber-300" />
                <div>
                  <h2 className="text-base font-semibold">Acceso one-time listo</h2>
                  <p className="text-sm text-muted-foreground">
                    Entrega estos datos al tenant admin ahora. La contraseña temporal no se puede recuperar después.
                  </p>
                </div>
              </div>
              <dl className="grid grid-cols-1 gap-3 text-sm md:grid-cols-2 xl:grid-cols-4">
                <div>
                  <dt className="text-xs uppercase text-muted-foreground">Empresa</dt>
                  <dd className="font-medium">{result.tenant.name}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase text-muted-foreground">Workspace</dt>
                  <dd className="font-medium">{result.workspace.name}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase text-muted-foreground">Email</dt>
                  <dd className="break-all">{result.bootstrap.user.email}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase text-muted-foreground">Rol</dt>
                  <dd className="font-mono">{result.bootstrap.workspace_role}</dd>
                </div>
              </dl>
              {result.bootstrap.temporary_password ? (
                <label className="block space-y-1.5 text-sm">
                  <span className="font-medium">Contraseña temporal generada por backend</span>
                  <input
                    readOnly
                    value={result.bootstrap.temporary_password}
                    className="min-h-[44px] w-full rounded-md border bg-background px-3 font-mono text-sm"
                  />
                </label>
              ) : (
                <div className="rounded-md border bg-background p-3 text-sm text-muted-foreground">
                  El usuario ya existía y no se generó contraseña nueva. Selecciona la empresa y usa “Reset temporal”.
                </div>
              )}
            </div>
            <div className="flex shrink-0 flex-col gap-2 sm:flex-row lg:flex-col">
              <button
                type="button"
                onClick={() => copy(accessBundle(result), "Paquete de acceso")}
                className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <Copy aria-hidden className="h-4 w-4" />
                Copiar acceso
              </button>
              <a
                href={result.bootstrap.login_url}
                className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                Abrir login
              </a>
              <button
                type="button"
                onClick={() => setResult(null)}
                className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                Ya lo copié
              </button>
            </div>
          </div>
        </section>
      ) : null}

      <section className="rounded-lg border bg-card p-4 shadow-sm" aria-label="Empresa activa">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(280px,0.8fr)_1fr] lg:items-end">
          <label className="space-y-1.5 text-sm">
            <span className="text-xs font-medium uppercase text-muted-foreground">Empresa activa</span>
            <select
              value={selectedTenantId ?? ""}
              onChange={(event) => {
                setSelectedTenantId(event.target.value || null);
                setSelectedWorkspaceId(null);
              }}
              className="min-h-[44px] w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="Seleccionar empresa para gestionar"
            >
              <option value="">Selecciona una empresa</option>
              {rows.map((tenant) => (
                <option key={tenant.id} value={tenant.id}>
                  {tenant.name}
                </option>
              ))}
            </select>
          </label>
          <div className="grid grid-cols-3 gap-3 text-sm">
            <div className="rounded-md border bg-background p-3">
              <p className="text-xs uppercase text-muted-foreground">Workspaces</p>
              <p className="text-lg font-semibold">{selectedTenant?.workspace_count ?? 0}</p>
            </div>
            <div className="rounded-md border bg-background p-3">
              <p className="text-xs uppercase text-muted-foreground">Usuarios</p>
              <p className="text-lg font-semibold">{selectedTenant?.user_count ?? 0}</p>
            </div>
            <div className="rounded-md border bg-background p-3">
              <p className="text-xs uppercase text-muted-foreground">Estado</p>
              <p className="truncate text-lg font-semibold">{selectedTenant?.status ?? "-"}</p>
            </div>
          </div>
        </div>
      </section>

      {!selectedTenant ? (
        <section className="rounded-lg border border-dashed bg-muted/20 p-5">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start">
            <Building2 aria-hidden className="h-5 w-5 text-muted-foreground" />
            <div className="space-y-1">
              <h2 className="text-base font-semibold">Selecciona una empresa</h2>
              <p className="max-w-3xl text-sm text-muted-foreground">
                El panel operativo aparece aquí con workspaces, tenant admins, cartuchos instalados,
                peticiones pendientes y acciones contextuales de la empresa.
              </p>
            </div>
          </div>
        </section>
      ) : null}

      {selectedTenant ? (
        <section className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(420px,1.05fr)]">
          <section className="rounded-lg border bg-card shadow-sm">
            <div key={selectedTenant.id}>
            <header className="border-b px-4 py-3">
              <h2 className="text-base font-semibold">Panel operativo de empresa</h2>
              <p className="text-xs text-muted-foreground">
                {selectedTenant.name} · {selectedTenant.slug} · creada {fmtDate(selectedTenant.created_at)}
              </p>
              <p className="mt-1 break-all font-mono text-[11px] text-muted-foreground">
                tenant_id={selectedTenant.id}
              </p>
            </header>
            <div className="grid grid-cols-3 gap-3 border-b p-4 text-sm">
              <div>
                <p className="text-xs uppercase text-muted-foreground">Workspaces</p>
                <p className="text-xl font-semibold">{selectedTenant.workspace_count}</p>
              </div>
              <div>
                <p className="text-xs uppercase text-muted-foreground">Usuarios</p>
                <p className="text-xl font-semibold">{selectedTenant.user_count}</p>
              </div>
              <div>
                <p className="text-xs uppercase text-muted-foreground">Cartuchos</p>
                <p className="text-xl font-semibold">{tenantInstallations.length}</p>
              </div>
            </div>
            <form
              onSubmit={handleCreateWorkspaceForSelectedTenant}
              className="grid grid-cols-1 gap-3 border-b p-4 sm:grid-cols-[1fr_auto]"
              aria-label="Crear workspace para empresa seleccionada"
            >
              <label className="space-y-1.5 text-sm">
                <span className="font-medium">Nuevo workspace para esta empresa</span>
                <input
                  value={workspaceCreateName}
                  onChange={(event) => setWorkspaceCreateName(event.target.value)}
                  maxLength={160}
                  className="min-h-[44px] w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  placeholder="Ej. Finanzas, Operaciones, RH"
                />
              </label>
              <button
                type="submit"
                disabled={createWorkspace.isPending}
                className="inline-flex min-h-[44px] items-center justify-center self-end rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 disabled:pointer-events-none disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                Crear workspace
              </button>
            </form>
            {workspaces.isLoading ? (
              <div className="p-4 text-sm text-muted-foreground">Cargando workspaces...</div>
            ) : workspaces.isError ? (
              <div role="alert" className="p-4 text-sm text-destructive">No se pudieron cargar los workspaces.</div>
            ) : (
              <div className="divide-y">
                {workspaceRows.map((workspace) => (
                  <div
                    key={workspace.id}
                    className={cn(
                      "space-y-3 px-4 py-3 text-sm",
                      selectedWorkspace?.id === workspace.id ? "bg-primary/5" : "",
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => setSelectedWorkspaceId(workspace.id)}
                      className="grid w-full grid-cols-1 gap-2 text-left md:grid-cols-[1fr_auto_auto] md:items-center"
                    >
                      <div>
                        <div className="font-medium">{workspace.name}</div>
                        <div className="break-all font-mono text-xs text-muted-foreground">{workspace.id}</div>
                      </div>
                      <span className="text-muted-foreground">{workspace.user_count} usuarios</span>
                      <span className="text-xs text-muted-foreground">{fmtDate(workspace.created_at)}</span>
                    </button>
                    {workspace.tenant_admins?.length ? (
                      <div className="space-y-2 rounded-md border bg-background p-3">
                        <div className="flex items-center gap-2 text-xs font-semibold uppercase text-muted-foreground">
                          <UserRoundCog aria-hidden className="h-4 w-4" />
                          Tenant admins
                        </div>
                        {workspace.tenant_admins.map((admin) => (
                          <div key={admin.id} className="grid grid-cols-1 gap-2 md:grid-cols-[1fr_auto] md:items-center">
                            <div className="min-w-0">
                              <p className="truncate font-medium">{admin.email}</p>
                              <p className="text-xs text-muted-foreground">
                                {admin.is_active ? "activo" : "inactivo"} · {admin.must_change_password ? "debe cambiar password" : "password vigente"}
                              </p>
                            </div>
                            <button
                              type="button"
                              disabled={issueTemporaryPassword.isPending}
                              onClick={() => handleIssueTemporaryPassword(workspace, admin.id)}
                              className="inline-flex min-h-[40px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 disabled:pointer-events-none disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            >
                              <KeyRound aria-hidden className="h-4 w-4" />
                              Reset temporal
                            </button>
                          </div>
                        ))}
                      </div>
                    ) : (
                      <div className="rounded-md border bg-background p-3 text-sm text-muted-foreground">
                        Sin tenant admin registrado en este workspace.
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
            </div>
          </section>

          <aside key={`${selectedTenant.id}:${selectedWorkspaceFilterId ?? "none"}`} className="space-y-4">
            <section className="rounded-lg border bg-card p-4 shadow-sm">
              <div className="flex items-start gap-2">
                <PackageCheck aria-hidden className="mt-0.5 h-5 w-5 text-primary" />
                <div>
                  <h2 className="text-base font-semibold">Acciones de empresa</h2>
                  <p className="text-xs text-muted-foreground">
                    Operaciones inline sobre la empresa seleccionada, sin redirigir a tu workspace activo.
                  </p>
                </div>
              </div>
              <div className="mt-4 grid grid-cols-3 gap-2 text-sm">
                <div className="rounded-md border bg-background p-3">
                  <p className="text-xs uppercase text-muted-foreground">Activos</p>
                  <p className="text-lg font-semibold">{activeInstallations}</p>
                </div>
                <div className="rounded-md border bg-background p-3">
                  <p className="text-xs uppercase text-muted-foreground">Peticiones</p>
                  <p className="text-lg font-semibold">{pendingInstallations}</p>
                </div>
                <div className="rounded-md border bg-background p-3">
                  <p className="text-xs uppercase text-muted-foreground">Bloqueados</p>
                  <p className="text-lg font-semibold">{blockedInstallations}</p>
                </div>
              </div>
              <div className="mt-4 space-y-2 rounded-md border bg-background p-3 text-xs text-muted-foreground">
                <p>
                  Usa el panel izquierdo para crear workspaces o resetear el acceso temporal del tenant admin.
                </p>
                <p>
                  Usa “Cartuchos y peticiones” para pausar, reactivar o revocar licencias del workspace seleccionado.
                </p>
              </div>
            </section>

            <section className="rounded-lg border bg-card shadow-sm">
              <header className="border-b px-4 py-3">
                <h2 className="text-base font-semibold">Cartuchos y peticiones</h2>
                <p className="text-xs text-muted-foreground">
                  {selectedWorkspace
                    ? `${selectedTenant.name} / ${selectedWorkspace.name}`
                    : selectedTenant.name}
                </p>
                <p className="mt-1 break-all font-mono text-[11px] text-muted-foreground">
                  tenant_id={selectedTenant.id}
                  {selectedWorkspace ? ` · workspace_id=${selectedWorkspace.id}` : ""}
                </p>
              </header>
              {installations.isLoading ? (
                <div className="space-y-2 p-4" aria-busy="true">
                  {Array.from({ length: 3 }).map((_, idx) => (
                    <span key={idx} className="block h-20 animate-pulse rounded bg-muted" />
                  ))}
                </div>
              ) : installations.isError ? (
                <div role="alert" className="p-4 text-sm text-destructive">No se pudieron cargar instalaciones.</div>
              ) : workspaceInstallations.length ? (
                <div className="divide-y">
                  {workspaceInstallations.map((installation) => {
                    const status = installation.status || installation.access_status;
                    const actions = adminActionsFor(status);
                    return (
                      <div key={installation.id} className="space-y-3 p-4 text-sm">
                        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                          <div className="min-w-0">
                            <p className="font-medium">{installation.product_name || installation.cartridge_id}</p>
                            <p className="break-all font-mono text-xs text-muted-foreground">{installation.cartridge_id}</p>
                            <p className="text-xs text-muted-foreground">
                              {installation.current_step || "sin paso"} · {fmtDate(installation.updated_at)}
                            </p>
                          </div>
                          <StatusPill status={status} />
                        </div>
                        {installation.error_message ? (
                          <div className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive">
                            {installation.error_message}
                          </div>
                        ) : null}
                        <div className="flex flex-wrap gap-2">
                          {actions.length ? actions.map((action) => (
                            <button
                              key={action}
                              type="button"
                              disabled={installationAction.isPending}
                              onClick={() => handleInstallationAction(installation, action)}
                              className={cn(
                                "inline-flex min-h-[40px] items-center justify-center rounded-md border px-3 text-xs font-medium disabled:pointer-events-none disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                                action === "revoke"
                                  ? "border-destructive/40 bg-background text-destructive hover:bg-destructive/10"
                                  : "bg-background hover:bg-accent/5",
                              )}
                            >
                              {actionLabel(action)}
                            </button>
                          )) : (
                            <span className="text-xs text-muted-foreground">Sin acciones disponibles para este estado.</span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="p-4 text-sm text-muted-foreground">
                  No hay cartuchos ni peticiones para este workspace.
                </div>
              )}
            </section>

            {blockedInstallations > 0 ? (
              <div className="flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
                <ShieldAlert aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                <p>Hay cartuchos bloqueados o fallidos para esta empresa. Revisa credenciales, Vault y logs antes de reactivar.</p>
              </div>
            ) : null}
          </aside>
        </section>
      ) : null}

      {!selectedTenant ? (
      <section className="max-w-3xl">
        <form
          onSubmit={handleSubmit}
          className="space-y-4 rounded-lg border bg-card p-5 shadow-sm"
          aria-label="Nueva empresa"
        >
          <header className="space-y-1">
            <div className="flex items-center gap-2">
              <Building2 aria-hidden className="h-5 w-5 text-primary" />
              <h2 className="text-base font-semibold">Nueva empresa</h2>
            </div>
            <p className="text-xs text-muted-foreground">
              Este flujo crea tenant, workspace y primer admin con rol global user.
            </p>
          </header>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="space-y-1.5 text-sm">
              <span className="font-medium">Empresa</span>
              <input
                value={companyName}
                onChange={(event) => setCompanyName(event.target.value)}
                maxLength={160}
                required
                className="min-h-[44px] w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <label className="space-y-1.5 text-sm">
              <span className="font-medium">Slug opcional</span>
              <input
                value={companySlug}
                onChange={(event) => setCompanySlug(event.target.value)}
                maxLength={64}
                placeholder="cliente-demo"
                className="min-h-[44px] w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <label className="space-y-1.5 text-sm">
              <span className="font-medium">Workspace inicial</span>
              <input
                value={workspaceName}
                onChange={(event) => setWorkspaceName(event.target.value)}
                maxLength={160}
                required
                className="min-h-[44px] w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <label className="space-y-1.5 text-sm">
              <span className="font-medium">Email tenant admin</span>
              <input
                type="email"
                value={adminEmail}
                onChange={(event) => setAdminEmail(event.target.value)}
                maxLength={254}
                required
                className="min-h-[44px] w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
            <label className="space-y-1.5 text-sm sm:col-span-2">
              <span className="font-medium">Nombre admin opcional</span>
              <input
                value={adminName}
                onChange={(event) => setAdminName(event.target.value)}
                maxLength={160}
                className="min-h-[44px] w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </label>
          </div>
          <button
            type="submit"
            disabled={busy}
            className="inline-flex min-h-[44px] w-full items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <ShieldCheck aria-hidden className="h-4 w-4" />
            {busy ? "Creando..." : "Crear empresa aislada"}
          </button>
        </form>
      </section>
      ) : null}
    </main>
  );
}
