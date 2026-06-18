"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Boxes,
  CheckCircle2,
  CircleSlash2,
  Clock3,
  PauseCircle,
  RefreshCw,
  Search,
  UserRoundCog,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";

import { getMeAccess } from "@/lib/admin-surfaces";
import {
  getInstallationAccess,
  listAdminInstallations,
  listCustomerCartridges,
  listMarketplaceProducts,
  requestMarketplaceProduct,
  retryMarketplaceInstallation,
  runAdminInstallationAction,
  setInstallationUserAccess,
  type AdminInstallationAction,
  type CartridgeInstallation,
  type InstallationAccessMode,
  type InstallationAccessUser,
  type MarketplaceProduct,
  type MarketplaceStatus,
} from "@/lib/marketplace";
import { cn } from "@/lib/utils";

type MarketplaceMode = "catalog" | "customer" | "admin";

interface MarketplaceConsoleProps {
  mode: MarketplaceMode;
  title?: string;
}

interface StatusCopy {
  label: string;
  className: string;
  icon: typeof CheckCircle2;
}

const STATUS: Record<string, StatusCopy> = {
  available: {
    label: "Disponible",
    className: "border-border bg-muted/30 text-muted-foreground",
    icon: Boxes,
  },
  active: {
    label: "Activo",
    className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
    icon: CheckCircle2,
  },
  ready: {
    label: "Activo",
    className: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
    icon: CheckCircle2,
  },
  pending_approval: {
    label: "Pendiente aprobación",
    className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
    icon: Clock3,
  },
  requested: {
    label: "Solicitado",
    className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
    icon: Clock3,
  },
  pending_connection: {
    label: "Pendiente conexión",
    className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
    icon: Clock3,
  },
  waiting_credentials: {
    label: "Requiere credenciales",
    className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
    icon: Clock3,
  },
  failed: {
    label: "Falló",
    className: "border-destructive/35 bg-destructive/10 text-destructive",
    icon: XCircle,
  },
  paused: {
    label: "Pausado",
    className: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300",
    icon: PauseCircle,
  },
  revoked: {
    label: "Revocado",
    className: "border-destructive/35 bg-destructive/10 text-destructive",
    icon: CircleSlash2,
  },
  expired: {
    label: "Expirado",
    className: "border-destructive/35 bg-destructive/10 text-destructive",
    icon: CircleSlash2,
  },
  suspended: {
    label: "Suspendido",
    className: "border-destructive/35 bg-destructive/10 text-destructive",
    icon: CircleSlash2,
  },
};

function statusCopy(status: MarketplaceStatus | null | undefined): StatusCopy {
  return STATUS[String(status || "available")] ?? {
    label: String(status || "Disponible"),
    className: "border-border bg-muted/30 text-muted-foreground",
    icon: Boxes,
  };
}

function StatusBadge({ status }: { status: MarketplaceStatus | null | undefined }) {
  const copy = statusCopy(status);
  const Icon = copy.icon;
  return (
    <span className={cn("inline-flex min-h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs font-medium", copy.className)}>
      <Icon aria-hidden className="h-3.5 w-3.5" />
      {copy.label}
    </span>
  );
}

function shortDate(value: string | null | undefined): string {
  if (!value) return "sin actividad";
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value.slice(0, 19);
  return new Intl.DateTimeFormat("es", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(parsed));
}

function textSearch(values: Array<string | number | boolean | null | undefined>, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  return values.some((value) => String(value ?? "").toLowerCase().includes(q));
}

function Counter({ label, value }: { label: string; value: number }) {
  return (
    <article className="rounded-lg border bg-card p-4">
      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{label}</p>
      <p className="mt-1 text-2xl font-semibold tracking-tight">{value}</p>
    </article>
  );
}

function MarketplaceTabs({ mode, canAdmin }: { mode: MarketplaceMode; canAdmin: boolean }) {
  const items = [
    { href: "/marketplace", label: "Catálogo", mode: "catalog" },
    { href: "/customer/cartridges", label: "Instalados", mode: "customer" },
    ...(canAdmin ? [{ href: "/admin/installations", label: "Licencias", mode: "admin" }] : []),
  ] as const;
  return (
    <nav aria-label="Secciones de cartuchos" className="overflow-x-auto border-b">
      <ul className="mx-auto flex max-w-6xl items-center gap-1 px-6">
        {items.map((item) => (
          <li key={item.href}>
            <Link
              href={item.href}
              prefetch={false}
              aria-current={mode === item.mode ? "page" : undefined}
              className={cn(
                "inline-flex min-h-[44px] items-center border-b-2 px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                mode === item.mode
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:border-border hover:text-foreground",
              )}
            >
              {item.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}

function ErrorPanel({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
      <p className="font-medium text-destructive">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-2 inline-flex min-h-[44px] items-center gap-2 rounded-md border border-destructive/40 px-3 text-xs font-medium text-destructive hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40"
      >
        <RefreshCw aria-hidden className="h-4 w-4" />
        Reintentar
      </button>
    </div>
  );
}

function ProductCard({
  product,
  busy,
  canAdmin,
  onRequest,
}: {
  product: MarketplaceProduct;
  busy: boolean;
  canAdmin: boolean;
  onRequest: (cartridgeId: string) => void;
}) {
  const profile = product.commercial ?? {};
  const domains = profile.data_domains ?? [];
  const status = product.access_status ?? "available";
  const isActive = status === "active" || status === "ready";
  const canRequest = product.can_request === true;

  return (
    <article className="flex min-h-72 flex-col rounded-lg border bg-card p-5 shadow-sm">
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {profile.plan || product.category || "Enterprise"}
          </p>
          <h2 className="text-lg font-semibold tracking-tight">{product.name || product.cartridge_id}</h2>
          <p className="font-mono text-xs text-muted-foreground">{product.cartridge_id}</p>
        </div>
        <StatusBadge status={status} />
      </header>

      <p className="mt-4 text-sm text-muted-foreground">
        {profile.headline || product.description || "Cartucho empresarial disponible para este workspace."}
      </p>

      <div className="mt-4 grid grid-cols-3 gap-2 text-xs">
        <Counter label="Entidades" value={Number(product.entity_count ?? 0)} />
        <Counter label="Datasets" value={Number(product.dataset_count ?? 0)} />
        <Counter label="Apps" value={Number(product.app_count ?? 0)} />
      </div>

      {domains.length ? (
        <div className="mt-4 flex flex-wrap gap-1.5">
          {domains.slice(0, 6).map((domain) => (
            <span key={domain} className="rounded-full bg-muted px-2 py-1 text-xs text-muted-foreground">
              {domain}
            </span>
          ))}
        </div>
      ) : null}

      <div className="mt-auto flex flex-wrap items-center gap-2 pt-5">
        {!isActive && canRequest ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => onRequest(product.cartridge_id)}
            className="inline-flex min-h-[44px] items-center gap-1.5 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
          >
            {busy ? "Enviando..." : "Solicitar activación"}
          </button>
        ) : !isActive ? (
          <a
            href={`mailto:support@omega.local?subject=Acceso%20Marketplace%20${encodeURIComponent(product.cartridge_id)}`}
            className="inline-flex min-h-[44px] items-center rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Contactar soporte
          </a>
        ) : null}
        {isActive && !canAdmin ? (
          <span className="inline-flex min-h-[44px] items-center rounded-md border bg-success/5 px-3 text-sm font-medium text-success">
            Activo en workspace
          </span>
        ) : null}
        {canAdmin ? (
          <Link
            href={`/cartridges/viewer?id=${encodeURIComponent(product.cartridge_id)}`}
            prefetch={false}
            className="inline-flex min-h-[44px] items-center rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Configurar
          </Link>
        ) : null}
      </div>
    </article>
  );
}

function InstallationRow({
  installation,
  busy,
  onRetry,
}: {
  installation: CartridgeInstallation;
  busy: boolean;
  onRetry: (installationId: string) => void;
}) {
  const status = installation.access_status || installation.status || "available";
  return (
    <article className="flex flex-col gap-4 rounded-lg border bg-card p-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0 space-y-1">
        <h2 className="text-base font-semibold tracking-tight">
          {installation.product_name || installation.cartridge_id}
        </h2>
        <p className="font-mono text-xs text-muted-foreground">{installation.cartridge_id}</p>
        <p className="text-sm text-muted-foreground">
          {installation.current_step || "sin paso"} · {shortDate(installation.updated_at)}
        </p>
        {installation.error_message ? (
          <p className="text-sm text-destructive">{installation.error_message}</p>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={status} />
        {installation.can_retry ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => onRetry(installation.id)}
            className="inline-flex min-h-[44px] items-center gap-1.5 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
          >
            <RefreshCw aria-hidden className="h-4 w-4" />
            Reintentar
          </button>
        ) : null}
      </div>
    </article>
  );
}

function CatalogAndCustomer({ mode, title }: { mode: Exclude<MarketplaceMode, "admin">; title?: string }) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const access = useQuery({ queryKey: ["me", "access"], queryFn: getMeAccess, staleTime: 60_000 });
  const products = useQuery({ queryKey: ["marketplace", "products"], queryFn: listMarketplaceProducts });
  const installations = useQuery({ queryKey: ["marketplace", "customer"], queryFn: listCustomerCartridges });
  const canAdmin = access.data?.ui_capabilities?.can_admin_marketplace === true;

  const requestMutation = useMutation({
    mutationFn: requestMarketplaceProduct,
    onMutate: (cartridgeId) => setBusyId(cartridgeId),
    onSuccess: (_data, cartridgeId) => {
      toast.success(`Solicitud enviada para ${cartridgeId}.`);
      queryClient.invalidateQueries({ queryKey: ["marketplace"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo solicitar el cartucho."),
    onSettled: () => setBusyId(null),
  });

  const retryMutation = useMutation({
    mutationFn: retryMarketplaceInstallation,
    onMutate: (installationId) => setBusyId(installationId),
    onSuccess: () => {
      toast.success("Instalación reintentada.");
      queryClient.invalidateQueries({ queryKey: ["marketplace"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo reintentar."),
    onSettled: () => setBusyId(null),
  });

  const filteredProducts = useMemo(
    () => (products.data?.products ?? []).filter((product) => textSearch([
      product.name,
      product.cartridge_id,
      product.description,
      product.category,
      ...(product.commercial?.data_domains ?? []),
    ], query)),
    [products.data?.products, query],
  );

  const filteredInstallations = useMemo(
    () => (installations.data?.installations ?? []).filter((row) => textSearch([
      row.product_name,
      row.cartridge_id,
      row.status,
      row.access_status,
      row.current_step,
    ], query)),
    [installations.data?.installations, query],
  );

  const activeCount = (products.data?.products ?? []).filter((p) => p.access_status === "active").length;
  const pendingCount = (products.data?.products ?? []).filter((p) => p.access_status === "pending_approval").length;

  return (
    <>
      <MarketplaceTabs mode={mode} canAdmin={canAdmin} />
      <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
        <header className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">Cartuchos</p>
            <h1 className="text-3xl font-semibold tracking-tight">
              {title || (mode === "customer" ? "Cartuchos instalados" : "Catálogo de cartuchos")}
            </h1>
            <p className="max-w-3xl text-sm text-muted-foreground">
              {mode === "customer"
                ? "Estado real de los cartuchos solicitados o activos para tu workspace."
                : "Catálogo conectado a permisos reales: solicitud, aprobación, conexión y visibilidad por workspace."}
            </p>
          </div>
          <label className="relative block w-full md:w-80">
            <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <span className="sr-only">Buscar</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Buscar cartucho, estado o dominio"
              className="min-h-[44px] w-full rounded-md border bg-background pl-9 pr-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </label>
        </header>

        <section className="grid grid-cols-2 gap-3 md:grid-cols-4" aria-label="Métricas marketplace">
          <Counter label="Productos" value={products.data?.products.length ?? 0} />
          <Counter label="Instalados" value={installations.data?.installations.length ?? 0} />
          <Counter label="Activos" value={activeCount} />
          <Counter label="Pendientes" value={pendingCount} />
        </section>

        {products.isError ? (
          <ErrorPanel message="No se pudo cargar el catálogo." onRetry={() => products.refetch()} />
        ) : null}
        {installations.isError ? (
          <ErrorPanel message="No se pudieron cargar tus cartuchos." onRetry={() => installations.refetch()} />
        ) : null}

        {mode === "catalog" ? (
          <section className="grid grid-cols-1 gap-4 lg:grid-cols-2" aria-label="Catálogo">
            {products.isLoading
              ? Array.from({ length: 4 }).map((_, index) => (
                  <div key={index} className="h-72 animate-pulse rounded-lg border bg-card" aria-hidden />
                ))
              : filteredProducts.map((product) => (
                  <ProductCard
                    key={product.cartridge_id}
                    product={product}
                    canAdmin={canAdmin}
                    busy={busyId === product.cartridge_id && requestMutation.isPending}
                    onRequest={(id) => requestMutation.mutate(id)}
                  />
                ))}
          </section>
        ) : (
          <section className="space-y-3" aria-label="Mis cartuchos">
            {installations.isLoading ? (
              Array.from({ length: 3 }).map((_, index) => (
                <div key={index} className="h-24 animate-pulse rounded-lg border bg-card" aria-hidden />
              ))
            ) : filteredInstallations.length ? (
              filteredInstallations.map((installation) => (
                <InstallationRow
                  key={installation.id}
                  installation={installation}
                  busy={busyId === installation.id && retryMutation.isPending}
                  onRetry={(id) => retryMutation.mutate(id)}
                />
              ))
            ) : (
              <p className="rounded-lg border bg-card p-5 text-sm text-muted-foreground">
                No hay cartuchos para este filtro.
              </p>
            )}
          </section>
        )}
      </main>
    </>
  );
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

function AccessDrawer({ installationId }: { installationId: string }) {
  const queryClient = useQueryClient();
  const access = useQuery({
    queryKey: ["marketplace", "admin", "access", installationId],
    queryFn: () => getInstallationAccess(installationId),
  });
  const mutation = useMutation({
    mutationFn: ({ userId, mode }: { userId: number; mode: InstallationAccessMode }) =>
      setInstallationUserAccess(installationId, userId, mode),
    onSuccess: (data) => {
      queryClient.setQueryData(["marketplace", "admin", "access", installationId], data);
      queryClient.invalidateQueries({ queryKey: ["me", "access"] });
      toast.success("Acceso de usuario actualizado.");
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo actualizar el acceso."),
  });

  if (access.isLoading) {
    return <div className="mt-4 h-24 animate-pulse rounded-md border bg-muted/30" aria-hidden />;
  }
  if (access.isError) {
    return <ErrorPanel message="No se pudo cargar el acceso de usuarios." onRetry={() => access.refetch()} />;
  }

  const users = access.data?.users ?? [];
  if (!users.length) {
    return <p className="mt-4 rounded-md border bg-muted/30 p-3 text-sm text-muted-foreground">Sin usuarios asignados a este workspace.</p>;
  }

  return (
    <div className="mt-4 rounded-md border bg-muted/20 p-3">
      <header className="mb-3 flex items-center gap-2">
        <UserRoundCog aria-hidden className="h-4 w-4 text-primary" />
        <h3 className="text-sm font-semibold">Acceso por usuario</h3>
      </header>
      <div className="space-y-2">
        {users.map((user: InstallationAccessUser) => {
          const mode = user.mode === "deny" ? "deny" : "inherit";
          return (
            <div key={user.id} className="grid grid-cols-1 gap-3 rounded-md bg-background p-3 md:grid-cols-[1fr_auto_auto] md:items-center">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">{user.email}</p>
                <p className="text-xs text-muted-foreground">
                  {user.workspace_role || user.global_role || "usuario"} · {user.is_active ? "activo" : "inactivo"}
                </p>
              </div>
              <span className={cn(
                "inline-flex min-h-7 items-center rounded-full px-2 text-xs font-medium",
                user.effective_access
                  ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                  : "bg-destructive/10 text-destructive",
              )}
              >
                {user.effective_access ? "Con acceso" : "Sin acceso"}
              </span>
              <select
                value={mode}
                disabled={mutation.isPending}
                onChange={(event) => mutation.mutate({
                  userId: user.id,
                  mode: event.target.value === "deny" ? "deny" : "inherit",
                })}
                className="min-h-[44px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <option value="inherit">Heredar workspace</option>
                <option value="deny">Bloquear</option>
              </select>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function AdminInstallationRow({
  row,
  open,
  busyAction,
  onToggleAccess,
  onAction,
}: {
  row: CartridgeInstallation;
  open: boolean;
  busyAction: string | null;
  onToggleAccess: (id: string) => void;
  onAction: (id: string, action: AdminInstallationAction) => void;
}) {
  const status = row.access_status || row.status || "available";
  const actions = adminActionsFor(row.status);
  return (
    <article className="rounded-lg border bg-card p-4">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0 space-y-1">
          <h2 className="text-base font-semibold tracking-tight">{row.product_name || row.cartridge_id}</h2>
          <p className="text-sm text-muted-foreground">
            {row.tenant_name || row.tenant_id || "tenant"} · {row.workspace_name || row.workspace_id || "workspace"}
          </p>
          <p className="font-mono text-xs text-muted-foreground">{row.cartridge_id}</p>
          <p className="text-sm text-muted-foreground">
            {row.current_step || "sin paso"} · {shortDate(row.updated_at)} · {row.created_by_email || "sin usuario"}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={status} />
          <button
            type="button"
            onClick={() => onToggleAccess(row.id)}
            className="inline-flex min-h-[44px] items-center gap-1.5 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <UserRoundCog aria-hidden className="h-4 w-4" />
            Usuarios
          </button>
          {actions.map((action) => (
            <button
              key={action}
              type="button"
              disabled={busyAction === `${row.id}:${action}`}
              onClick={() => onAction(row.id, action)}
              className={cn(
                "inline-flex min-h-[44px] items-center rounded-md border px-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60",
                action === "revoke"
                  ? "border-destructive/40 bg-background text-destructive hover:bg-destructive/10"
                  : action === "approve" || action === "reactivate"
                    ? "border-primary bg-primary text-primary-foreground hover:bg-primary/90"
                    : "bg-background hover:bg-accent/5",
              )}
            >
              {busyAction === `${row.id}:${action}` ? "Aplicando..." : actionLabel(action)}
            </button>
          ))}
        </div>
      </div>
      {open ? <AccessDrawer installationId={row.id} /> : null}
    </article>
  );
}

function AdminMarketplace({ title }: { title?: string }) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [openAccessId, setOpenAccessId] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const access = useQuery({ queryKey: ["me", "access"], queryFn: getMeAccess, staleTime: 60_000 });
  const canAdmin = access.data?.ui_capabilities?.can_admin_marketplace === true;
  const installations = useQuery({
    queryKey: ["marketplace", "admin", "installations"],
    queryFn: () => listAdminInstallations(),
    enabled: canAdmin,
  });
  const actionMutation = useMutation({
    mutationFn: ({ id, action }: { id: string; action: AdminInstallationAction }) =>
      runAdminInstallationAction(id, action),
    onMutate: ({ id, action }) => setBusyAction(`${id}:${action}`),
    onSuccess: () => {
      toast.success("Instalación actualizada.");
      queryClient.invalidateQueries({ queryKey: ["marketplace"] });
      queryClient.invalidateQueries({ queryKey: ["me", "access"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo actualizar la instalación."),
    onSettled: () => setBusyAction(null),
  });

  const rows = useMemo(
    () => (installations.data?.installations ?? []).filter((row) => textSearch([
      row.product_name,
      row.cartridge_id,
      row.tenant_name,
      row.workspace_name,
      row.status,
      row.current_step,
      row.created_by_email,
    ], query)),
    [installations.data?.installations, query],
  );
  const ready = rows.filter((row) => row.status === "ready").length;
  const requested = rows.filter((row) => row.status === "requested").length;
  const blocked = rows.filter((row) => ["paused", "revoked", "expired", "suspended"].includes(String(row.status))).length;

  return (
    <>
      <MarketplaceTabs mode="admin" canAdmin={canAdmin} />
      <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
        <header className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <div className="space-y-2">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">Cartuchos</p>
            <h1 className="text-3xl font-semibold tracking-tight">{title || "Licencias y solicitudes"}</h1>
            <p className="max-w-3xl text-sm text-muted-foreground">
              Aprueba, pausa, revoca y controla el acceso por usuario. Los cambios impactan Workspace, Copilot y MCP desde backend.
            </p>
          </div>
          <label className="relative block w-full md:w-96">
            <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <span className="sr-only">Buscar instalaciones</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Buscar tenant, workspace, cartucho o estado"
              className="min-h-[44px] w-full rounded-md border bg-background pl-9 pr-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </label>
        </header>

        <section className="grid grid-cols-2 gap-3 md:grid-cols-4" aria-label="Métricas de instalaciones">
          <Counter label="Instalaciones" value={installations.data?.installations.length ?? 0} />
          <Counter label="Pendientes" value={requested} />
          <Counter label="Activas" value={ready} />
          <Counter label="Bloqueadas" value={blocked} />
        </section>

        {!canAdmin && !access.isLoading ? (
          <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
            No tienes permiso para administrar marketplace.
          </div>
        ) : null}
        {installations.isError ? (
          <ErrorPanel message="No se pudieron cargar las instalaciones." onRetry={() => installations.refetch()} />
        ) : null}

        <section className="space-y-3" aria-label="Instalaciones">
          {installations.isLoading || access.isLoading ? (
            Array.from({ length: 4 }).map((_, index) => (
              <div key={index} className="h-32 animate-pulse rounded-lg border bg-card" aria-hidden />
            ))
          ) : rows.length ? (
            rows.map((row) => (
              <AdminInstallationRow
                key={row.id}
                row={row}
                open={openAccessId === row.id}
                busyAction={busyAction}
                onToggleAccess={(id) => setOpenAccessId((current) => (current === id ? null : id))}
                onAction={(id, action) => actionMutation.mutate({ id, action })}
              />
            ))
          ) : (
            <p className="rounded-lg border bg-card p-5 text-sm text-muted-foreground">
              Sin instalaciones para este filtro.
            </p>
          )}
        </section>
      </main>
    </>
  );
}

export function MarketplaceConsole({ mode, title }: MarketplaceConsoleProps) {
  if (mode === "admin") return <AdminMarketplace title={title} />;
  return <CatalogAndCustomer mode={mode} title={title} />;
}
