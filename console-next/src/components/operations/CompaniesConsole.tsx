"use client";

import { useState, type FormEvent } from "react";

import { Building2, CheckCircle2, Copy, RefreshCw, ShieldCheck } from "lucide-react";
import { toast } from "sonner";

import {
  useBootstrapTenantAdmin,
  useCreateTenant,
  useCreateTenantWorkspace,
  useTenantWorkspaces,
  useTenants,
} from "@/lib/operations/hooks";
import type { BootstrapTenantAdminResponse, TenantSummary, WorkspaceSummary } from "@/lib/operations/types";


const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

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

export function CompaniesConsole() {
  const tenants = useTenants();
  const createTenant = useCreateTenant();
  const createWorkspace = useCreateTenantWorkspace();
  const bootstrapAdmin = useBootstrapTenantAdmin();

  const [companyName, setCompanyName] = useState("");
  const [companySlug, setCompanySlug] = useState("");
  const [workspaceName, setWorkspaceName] = useState("");
  const [adminEmail, setAdminEmail] = useState("");
  const [adminName, setAdminName] = useState("");
  const [selectedTenantId, setSelectedTenantId] = useState<string | null>(null);
  const [result, setResult] = useState<ProvisioningResult | null>(null);

  const workspaces = useTenantWorkspaces(selectedTenantId);
  const rows = tenants.data?.tenants ?? [];
  const selectedTenant = rows.find((tenant) => tenant.id === selectedTenantId) ?? null;
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

      <section className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,0.95fr)_minmax(420px,1.05fr)]">
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

        <section className="rounded-lg border bg-card shadow-sm">
          <header className="border-b px-4 py-3">
            <h2 className="text-base font-semibold">Empresas existentes</h2>
          </header>
          {tenants.isLoading ? (
            <div className="space-y-2 p-4" aria-busy="true">
              {Array.from({ length: 4 }).map((_, idx) => (
                <span key={idx} className="block h-12 animate-pulse rounded bg-muted" />
              ))}
            </div>
          ) : tenants.isError ? (
            <div role="alert" className="p-4 text-sm text-destructive">
              No se pudieron cargar las empresas.
            </div>
          ) : rows.length === 0 ? (
            <p className="p-4 text-sm text-muted-foreground">No hay empresas registradas.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-muted/40 text-left text-xs uppercase text-muted-foreground">
                  <tr>
                    <th className="px-4 py-2 font-medium">Empresa</th>
                    <th className="px-4 py-2 font-medium">Workspaces</th>
                    <th className="px-4 py-2 font-medium">Usuarios</th>
                    <th className="px-4 py-2 font-medium">Estado</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((tenant) => (
                    <tr
                      key={tenant.id}
                      className={`cursor-pointer border-t hover:bg-muted/30 ${selectedTenantId === tenant.id ? "bg-primary/5" : ""}`}
                      onClick={() => setSelectedTenantId(tenant.id)}
                    >
                      <td className="px-4 py-3">
                        <div className="font-medium">{tenant.name}</div>
                        <div className="font-mono text-xs text-muted-foreground">{tenant.slug}</div>
                      </td>
                      <td className="px-4 py-3">{tenant.workspace_count}</td>
                      <td className="px-4 py-3">{tenant.user_count}</td>
                      <td className="px-4 py-3">{tenant.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </section>

      {result ? (
        <section
          aria-live="polite"
          className="rounded-lg border border-emerald-500/30 bg-emerald-500/5 p-5"
        >
          <div className="flex items-start gap-3">
            <CheckCircle2 aria-hidden className="mt-0.5 h-5 w-5 text-emerald-600" />
            <div className="min-w-0 flex-1 space-y-3">
              <div>
                <h2 className="text-base font-semibold">Empresa creada</h2>
                <p className="text-sm text-muted-foreground">
                  El admin solo tiene acceso a este workspace.
                </p>
              </div>
              <dl className="grid grid-cols-1 gap-3 text-sm md:grid-cols-2 xl:grid-cols-4">
                <div>
                  <dt className="text-xs uppercase text-muted-foreground">Tenant</dt>
                  <dd className="break-all font-mono">{result.tenant.id}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase text-muted-foreground">Workspace</dt>
                  <dd className="break-all font-mono">{result.workspace.id}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase text-muted-foreground">Admin</dt>
                  <dd>{result.bootstrap.user.email}</dd>
                </div>
                <div>
                  <dt className="text-xs uppercase text-muted-foreground">Rol</dt>
                  <dd className="font-mono">{result.bootstrap.workspace_role}</dd>
                </div>
              </dl>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-[1fr_auto_auto] md:items-end">
                {result.bootstrap.temporary_password ? (
                  <>
                    <label className="space-y-1.5 text-sm">
                      <span className="font-medium">Contraseña temporal</span>
                      <input
                        readOnly
                        value={result.bootstrap.temporary_password}
                        className="min-h-[44px] w-full rounded-md border bg-background px-3 font-mono text-sm"
                      />
                    </label>
                    <button
                      type="button"
                      onClick={() => copy(result.bootstrap.temporary_password ?? "", "Contraseña temporal")}
                      className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <Copy aria-hidden className="h-4 w-4" />
                      Copiar password
                    </button>
                  </>
                ) : (
                  <div className="rounded-md border bg-background p-3 text-sm text-muted-foreground md:col-span-2">
                    El usuario ya existía en este tenant; no se generó una contraseña nueva.
                  </div>
                )}
                <a
                  href={result.bootstrap.login_url}
                  className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  Abrir login
                </a>
              </div>
            </div>
          </div>
        </section>
      ) : null}

      {selectedTenant ? (
        <section className="rounded-lg border bg-card shadow-sm">
          <header className="border-b px-4 py-3">
            <h2 className="text-base font-semibold">Workspaces de {selectedTenant.name}</h2>
            <p className="text-xs text-muted-foreground">Creada {fmtDate(selectedTenant.created_at)}</p>
          </header>
          {workspaces.isLoading ? (
            <div className="p-4 text-sm text-muted-foreground">Cargando workspaces...</div>
          ) : workspaces.isError ? (
            <div role="alert" className="p-4 text-sm text-destructive">No se pudieron cargar los workspaces.</div>
          ) : (
            <div className="divide-y">
              {(workspaces.data?.workspaces ?? []).map((workspace) => (
                <div key={workspace.id} className="grid grid-cols-1 gap-2 px-4 py-3 text-sm md:grid-cols-[1fr_auto_auto] md:items-center">
                  <div>
                    <div className="font-medium">{workspace.name}</div>
                    <div className="break-all font-mono text-xs text-muted-foreground">{workspace.id}</div>
                  </div>
                  <span className="text-muted-foreground">{workspace.user_count} usuarios</span>
                  <span className="text-xs text-muted-foreground">{fmtDate(workspace.created_at)}</span>
                </div>
              ))}
            </div>
          )}
        </section>
      ) : null}
    </main>
  );
}
