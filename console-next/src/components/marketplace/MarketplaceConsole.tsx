"use client";

import Link from "next/link";
import { Suspense, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
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
import { useActivateCartridge, useCartridgeList } from "@/lib/hooks/useCartridges";
import { useKpis } from "@/lib/hooks/useKpis";
import {
  CONNECTION_LABELS,
  StatusBadge as ConnectionStatusBadge,
  type ConnectionStatus,
} from "@/components/cartridges/StatusBadge";
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

export type MarketplaceTab = "conectadas" | "catalogo" | "licencias";
type MarketplaceMode = "catalog" | "customer" | "admin";

const MODE_BY_TAB: Record<MarketplaceTab, MarketplaceMode> = {
  conectadas: "customer",
  catalogo: "catalog",
  licencias: "admin",
};

const TAB_LABELS: Record<MarketplaceTab, string> = {
  conectadas: "Conectadas",
  catalogo: "Catálogo disponible",
  licencias: "Licencias",
};

export interface TabAccess {
  canConnected: boolean;
  canCatalog: boolean;
  canAdmin: boolean;
  hasConnected: boolean;
}

export function deriveTab(param: string | null | undefined, access: TabAccess): MarketplaceTab {
  const value = String(param || "").trim().toLowerCase();
  const requested: MarketplaceTab | null =
    value === "conectadas" || value === "catalogo" || value === "licencias" ? value : null;
  const allowed = (tab: MarketplaceTab): boolean =>
    tab === "conectadas" ? access.canConnected : tab === "catalogo" ? access.canCatalog : access.canAdmin;
  if (requested && allowed(requested)) return requested;
  if (access.canConnected && (access.hasConnected || !access.canCatalog)) return "conectadas";
  if (access.canCatalog) return "catalogo";
  if (access.canConnected) return "conectadas";
  if (access.canAdmin) return "licencias";
  return "catalogo";
}

export const STEP_LABELS: Record<string, string> = {
  activated: "Activada",
  seeded_from_existing_dataset: "Sembrada desde datos existentes",
  pending_admin_approval: "Pendiente de aprobación",
  approved_ready: "Aprobada y lista",
  reactivated_ready: "Reactivada y lista",
  paused_by_admin: "Pausada por administración",
  revoked_by_admin: "Revocada por administración",
  retry_requested: "Reintento solicitado",
};

export function stepLabel(step: string | null | undefined): string {
  return STEP_LABELS[String(step || "").trim()] ?? "Sin información";
}

export interface FreshnessEntry {
  age_hours: number | null;
  status: "fresh" | "stale" | "very_stale" | "never";
}

export function connectionStatusFor(info: FreshnessEntry | undefined): ConnectionStatus | null {
  if (!info) return null;
  if (info.status === "never") return "unconfigured";
  if (info.status === "very_stale") return "very_stale";
  if (info.status === "stale") return "stale";
  return "connected";
}

const CARTRIDGE_META: Record<string, { name: string; description: string }> = {
  replicon: {
    name: "Replicon",
    description: "Time tracking + project hours. Empleados, proyectos, time entries.",
  },
  "hubspot": {
    name: "HubSpot CRM",
    description: "CRM comercial. Deals, empresas, contactos, pipeline y forecast.",
  },
  banxico: {
    name: "Banxico SIE",
    description: "Series macro oficiales. Bronze, provenance y manifest.",
  },
  inegi: {
    name: "INEGI",
    description: "Indicadores oficiales. Silver/Gold gobernado para contexto macro.",
  },
  sap_hcm: {
    name: "SAP HCM",
    description: "Recursos humanos. Empleados, puestos, organización.",
  },
  sap_s4hana: {
    name: "SAP S/4HANA",
    description: "Financiero + logística. Cuentas, asientos, materiales.",
  },
  sap_successfactors: {
    name: "SAP SuccessFactors",
    description: "Talento + performance. Goals, reviews, learning.",
  },
  sap_b1: {
    name: "SAP Business One",
    description: "ERP PyME por compañía. Socios de negocio, ventas, compras, inventario y asientos.",
  },
};

export interface ConnectedSource {
  cartridgeId: string;
  name: string;
  description: string | null;
  installation: CartridgeInstallation | null;
  product: MarketplaceProduct | null;
  connection: ConnectionStatus | null;
  ageHours: number | null;
}

export function mergeConnectedSources(input: {
  cartridgeIds: string[];
  installations: CartridgeInstallation[];
  products: MarketplaceProduct[];
  freshness: Record<string, FreshnessEntry> | undefined;
}): ConnectedSource[] {
  const byId = new Map<string, ConnectedSource>();
  const ensure = (cartridgeId: string): ConnectedSource => {
    const existing = byId.get(cartridgeId);
    if (existing) return existing;
    const created: ConnectedSource = {
      cartridgeId,
      name: cartridgeId,
      description: null,
      installation: null,
      product: null,
      connection: null,
      ageHours: null,
    };
    byId.set(cartridgeId, created);
    return created;
  };
  for (const cartridgeId of input.cartridgeIds) ensure(cartridgeId);
  for (const installation of input.installations) {
    if (!installation.cartridge_id) continue;
    ensure(installation.cartridge_id).installation = installation;
  }
  const productById = new Map(
    input.products.filter((product) => product.cartridge_id).map((product) => [product.cartridge_id, product]),
  );
  for (const row of byId.values()) {
    row.product = productById.get(row.cartridgeId) ?? null;
    row.name =
      row.installation?.product_name ||
      row.product?.name ||
      CARTRIDGE_META[row.cartridgeId]?.name ||
      row.cartridgeId;
    row.description =
      row.product?.commercial?.headline ||
      row.product?.description ||
      CARTRIDGE_META[row.cartridgeId]?.description ||
      null;
    const info = input.freshness?.[row.cartridgeId];
    row.connection = connectionStatusFor(info);
    row.ageHours = info?.age_hours ?? null;
  }
  return [...byId.values()].sort((a, b) => a.name.localeCompare(b.name, "es"));
}

export function connectedLifecycle(source: ConnectedSource): MarketplaceStatus | null {
  if (source.installation) {
    return source.installation.access_status || source.installation.status || "available";
  }
  return source.product?.access_status ?? null;
}

export function connectedSearchValues(source: ConnectedSource): Array<string | null | undefined> {
  const lifecycle = connectedLifecycle(source);
  return [
    source.name,
    source.cartridgeId,
    source.description,
    source.connection,
    source.connection ? CONNECTION_LABELS[source.connection] : null,
    source.installation?.status,
    source.installation?.access_status,
    source.installation?.current_step,
    source.installation ? stepLabel(source.installation.current_step) : null,
    lifecycle,
    lifecycle ? statusCopy(lifecycle).label : null,
  ];
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
    label: "Sin información",
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

export function Counter({ label, value, href }: { label: string; value: number | null; href?: string }) {
  const body = (
    <>
      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{label}</p>
      {value === null ? (
        <p className="mt-2 text-sm font-medium text-muted-foreground">Sin información</p>
      ) : (
        <p className="mt-1 text-2xl font-semibold tracking-tight">{value}</p>
      )}
    </>
  );
  if (href && value !== null && value > 0) {
    return (
      <Link
        href={href}
        className="rounded-lg border bg-card p-4 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {body}
      </Link>
    );
  }
  return <article className="rounded-lg border bg-card p-4">{body}</article>;
}

function MarketplaceTabs({ tab, access }: { tab: MarketplaceTab; access: TabAccess }) {
  const items: MarketplaceTab[] = [
    ...(access.canConnected ? (["conectadas"] as const) : []),
    ...(access.canCatalog ? (["catalogo"] as const) : []),
    ...(access.canAdmin ? (["licencias"] as const) : []),
  ];
  return (
    <nav aria-label="Secciones de fuentes de datos" className="overflow-x-auto border-b">
      <ul className="mx-auto flex max-w-6xl items-center gap-1 px-6">
        {items.map((item) => (
          <li key={item}>
            <Link
              href={`/marketplace?tab=${item}`}
              prefetch={false}
              aria-current={tab === item ? "page" : undefined}
              className={cn(
                "inline-flex min-h-[44px] items-center border-b-2 px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                tab === item
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:border-border hover:text-foreground",
              )}
            >
              {TAB_LABELS[item]}
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

function SearchField({
  value,
  onChange,
  placeholder,
  label,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  label: string;
}) {
  return (
    <label className="relative block w-full md:w-80">
      <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
      <span className="sr-only">{label}</span>
      <input
        type="search"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="min-h-[44px] w-full rounded-md border bg-background pl-9 pr-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />
    </label>
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
        {profile.headline || product.description || "Fuente de datos empresarial disponible para este workspace."}
      </p>

      <div className="mt-4 grid grid-cols-3 gap-2 text-xs">
        <Counter label="Entidades" value={Number(product.entity_count ?? 0)} />
        <Counter label="Datasets" value={Number(product.dataset_count ?? 0)} />
        <Counter
          label="Apps"
          value={Number(product.app_count ?? 0)}
          href={`/analytics?cartridge=${encodeURIComponent(product.cartridge_id ?? "")}`}
        />
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

export function ConnectedSourceCard({
  source,
  canAdmin,
  canConfigure,
  busyRetry,
  busyActivate,
  onRetry,
  onActivate,
}: {
  source: ConnectedSource;
  canAdmin: boolean;
  canConfigure: boolean;
  busyRetry: boolean;
  busyActivate: boolean;
  onRetry: (installationId: string) => void;
  onActivate: (cartridgeId: string) => void;
}) {
  const installation = source.installation;
  const lifecycle = connectedLifecycle(source);
  const isActive = lifecycle === "active" || lifecycle === "ready";
  return (
    <article className="flex flex-col gap-4 rounded-lg border bg-card p-4 lg:flex-row lg:items-center lg:justify-between">
      <div className="min-w-0 space-y-1">
        <h2 className="text-base font-semibold tracking-tight">{source.name}</h2>
        <p className="font-mono text-xs text-muted-foreground">{source.cartridgeId}</p>
        {installation ? (
          <p className="text-sm text-muted-foreground">
            {stepLabel(installation.current_step)} · {shortDate(installation.updated_at)}
          </p>
        ) : source.description ? (
          <p className="text-sm text-muted-foreground">{source.description}</p>
        ) : null}
        {installation?.error_message ? (
          <p className="text-sm text-destructive">{installation.error_message}</p>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        {source.connection ? (
          <ConnectionStatusBadge status={source.connection} ageHours={source.ageHours} />
        ) : (
          <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
            Sin información
          </span>
        )}
        {lifecycle ? <StatusBadge status={lifecycle} /> : null}
        {installation && installation.can_retry ? (
          <button
            type="button"
            disabled={busyRetry}
            onClick={() => onRetry(installation.id)}
            className="inline-flex min-h-[44px] items-center gap-1.5 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
          >
            <RefreshCw aria-hidden className="h-4 w-4" />
            Reintentar
          </button>
        ) : null}
        {canAdmin && !isActive ? (
          <button
            type="button"
            disabled={busyActivate}
            onClick={() => onActivate(source.cartridgeId)}
            className="inline-flex min-h-[44px] items-center rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
          >
            {busyActivate ? "Activando..." : "Activar"}
          </button>
        ) : null}
        {canConfigure ? (
          <Link
            href={`/cartridges/viewer?id=${encodeURIComponent(source.cartridgeId)}`}
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

function ConnectedView({
  canAdmin,
  canCatalog,
  canConfigure,
}: {
  canAdmin: boolean;
  canCatalog: boolean;
  canConfigure: boolean;
}) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const installations = useQuery({
    queryKey: ["marketplace", "customer"],
    queryFn: listCustomerCartridges,
    enabled: canCatalog,
  });
  const products = useQuery({
    queryKey: ["marketplace", "products"],
    queryFn: listMarketplaceProducts,
    enabled: canCatalog,
  });
  const cartridgeList = useCartridgeList({ enabled: canConfigure });
  const kpis = useKpis();
  const activate = useActivateCartridge();

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

  const activateOne = async (cartridgeId: string) => {
    setBusyId(cartridgeId);
    try {
      const result = await activate.mutateAsync(cartridgeId);
      const estado = statusCopy(result.installation?.access_status || result.installation?.status || "requested").label;
      toast.success(`${CARTRIDGE_META[cartridgeId]?.name ?? cartridgeId}: activación enviada (${estado}).`);
      queryClient.invalidateQueries({ queryKey: ["marketplace"] });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "No se pudo activar la fuente de datos.");
    } finally {
      setBusyId(null);
    }
  };

  const merged = useMemo(
    () =>
      mergeConnectedSources({
        cartridgeIds: cartridgeList.data?.cartridges ?? [],
        installations: installations.data?.installations ?? [],
        products: products.data?.products ?? [],
        freshness: kpis.data?.data_freshness,
      }),
    [cartridgeList.data?.cartridges, installations.data?.installations, products.data?.products, kpis.data?.data_freshness],
  );

  const filtered = useMemo(
    () => merged.filter((source) => textSearch(connectedSearchValues(source), query)),
    [merged, query],
  );

  const connectedCount = kpis.data
    ? merged.filter((source) => source.connection === "connected").length
    : null;
  const pendingCount = installations.data
    ? merged.filter((source) => {
        const lifecycle = source.installation?.access_status || source.installation?.status;
        return ["requested", "pending_approval", "pending_connection", "waiting_credentials"].includes(String(lifecycle || ""));
      }).length
    : null;
  const loading = cartridgeList.isLoading || installations.isLoading || kpis.isLoading;

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
      <header className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">Fuentes de datos</p>
          <h1 className="text-3xl font-semibold tracking-tight">Fuentes de datos conectadas</h1>
          <p className="max-w-3xl text-sm text-muted-foreground">
            Estado real de conexión, frescura de datos y licencia de cada fuente de tu workspace.
          </p>
        </div>
        <SearchField
          value={query}
          onChange={setQuery}
          label="Buscar"
          placeholder="Buscar fuente de datos, estado o dominio"
        />
      </header>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4" aria-label="Métricas de fuentes conectadas">
        <Counter label="Fuentes" value={merged.length} />
        <Counter label="Conectadas" value={connectedCount} />
        <Counter label="Instaladas" value={installations.data ? installations.data.installations.length : null} />
        <Counter label="Pendientes" value={pendingCount} />
      </section>

      {cartridgeList.isError ? (
        <ErrorPanel message="No se pudieron cargar las fuentes de datos." onRetry={() => cartridgeList.refetch()} />
      ) : null}
      {installations.isError ? (
        <ErrorPanel message="No se pudieron cargar tus fuentes de datos." onRetry={() => installations.refetch()} />
      ) : null}

      <section className="space-y-3" aria-label="Fuentes de datos conectadas" aria-busy={loading}>
        {loading ? (
          Array.from({ length: 4 }).map((_, index) => (
            <div key={index} className="h-24 animate-pulse rounded-lg border bg-card" aria-hidden />
          ))
        ) : filtered.length ? (
          filtered.map((source) => (
            <ConnectedSourceCard
              key={source.cartridgeId}
              source={source}
              canAdmin={canAdmin}
              canConfigure={canConfigure}
              busyRetry={busyId === source.installation?.id && retryMutation.isPending}
              busyActivate={busyId === source.cartridgeId && activate.isPending}
              onRetry={(id) => retryMutation.mutate(id)}
              onActivate={activateOne}
            />
          ))
        ) : (
          <p className="rounded-lg border bg-card p-5 text-sm text-muted-foreground">
            No hay fuentes de datos para este filtro.
          </p>
        )}
      </section>
    </main>
  );
}

function CatalogView({ canAdmin }: { canAdmin: boolean }) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const products = useQuery({ queryKey: ["marketplace", "products"], queryFn: listMarketplaceProducts });
  const installations = useQuery({ queryKey: ["marketplace", "customer"], queryFn: listCustomerCartridges });

  const requestMutation = useMutation({
    mutationFn: requestMarketplaceProduct,
    onMutate: (cartridgeId) => setBusyId(cartridgeId),
    onSuccess: (_data, cartridgeId) => {
      toast.success(`Solicitud enviada para ${cartridgeId}.`);
      queryClient.invalidateQueries({ queryKey: ["marketplace"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo solicitar la fuente de datos."),
    onSettled: () => setBusyId(null),
  });

  const filteredProducts = useMemo(
    () => (products.data?.products ?? []).filter((product) => textSearch([
      product.name,
      product.cartridge_id,
      product.description,
      product.category,
      product.access_status,
      statusCopy(product.access_status ?? "available").label,
      ...(product.commercial?.data_domains ?? []),
    ], query)),
    [products.data?.products, query],
  );

  const activeCount = products.data
    ? products.data.products.filter((p) => p.access_status === "active").length
    : null;
  const pendingCount = products.data
    ? products.data.products.filter((p) => p.access_status === "pending_approval").length
    : null;

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
      <header className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">Fuentes de datos</p>
          <h1 className="text-3xl font-semibold tracking-tight">Catálogo de fuentes de datos</h1>
          <p className="max-w-3xl text-sm text-muted-foreground">
            Catálogo conectado a permisos reales: solicitud, aprobación, conexión y visibilidad por workspace.
          </p>
        </div>
        <SearchField
          value={query}
          onChange={setQuery}
          label="Buscar"
          placeholder="Buscar fuente de datos, estado o dominio"
        />
      </header>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4" aria-label="Métricas marketplace">
        <Counter label="Productos" value={products.data ? products.data.products.length : null} />
        <Counter label="Instalados" value={installations.data ? installations.data.installations.length : null} />
        <Counter label="Activos" value={activeCount} />
        <Counter label="Pendientes" value={pendingCount} />
      </section>

      {products.isError ? (
        <ErrorPanel message="No se pudo cargar el catálogo." onRetry={() => products.refetch()} />
      ) : null}

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
    </main>
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
            {row.tenant_name || row.tenant_id || "Sin información"} · {row.workspace_name || row.workspace_id || "Sin información"}
          </p>
          <p className="font-mono text-xs text-muted-foreground">{row.cartridge_id}</p>
          <p className="text-sm text-muted-foreground">
            {stepLabel(row.current_step)} · {shortDate(row.updated_at)} · {row.created_by_email || "sin usuario"}
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

function AdminView({ canAdmin }: { canAdmin: boolean }) {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [openAccessId, setOpenAccessId] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
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
      statusCopy(row.access_status || row.status).label,
      row.current_step,
      stepLabel(row.current_step),
      row.created_by_email,
    ], query)),
    [installations.data?.installations, query],
  );
  const ready = installations.data ? rows.filter((row) => row.status === "ready").length : null;
  const requested = installations.data ? rows.filter((row) => row.status === "requested").length : null;
  const blocked = installations.data
    ? rows.filter((row) => ["paused", "revoked", "expired", "suspended"].includes(String(row.status))).length
    : null;

  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-8">
      <header className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-primary">Fuentes de datos</p>
          <h1 className="text-3xl font-semibold tracking-tight">Licencias y solicitudes</h1>
          <p className="max-w-3xl text-sm text-muted-foreground">
            Aprueba, pausa, revoca y controla el acceso por usuario. Los cambios impactan Workspace, Copilot y MCP desde backend.
          </p>
        </div>
        <SearchField
          value={query}
          onChange={setQuery}
          label="Buscar instalaciones"
          placeholder="Buscar tenant, workspace, fuente de datos o estado"
        />
      </header>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-4" aria-label="Métricas de instalaciones">
        <Counter label="Instalaciones" value={installations.data ? installations.data.installations.length : null} />
        <Counter label="Pendientes" value={requested} />
        <Counter label="Activas" value={ready} />
        <Counter label="Bloqueadas" value={blocked} />
      </section>

      {!canAdmin ? (
        <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
          No tienes permiso para administrar marketplace.
        </div>
      ) : null}
      {installations.isError ? (
        <ErrorPanel message="No se pudieron cargar las instalaciones." onRetry={() => installations.refetch()} />
      ) : null}

      <section className="space-y-3" aria-label="Instalaciones">
        {installations.isLoading ? (
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
  );
}

function MarketplaceSkeleton() {
  return (
    <main className="mx-auto max-w-6xl space-y-6 px-6 py-8" aria-busy>
      <div className="h-10 w-72 animate-pulse rounded-md border bg-card" aria-hidden />
      {Array.from({ length: 4 }).map((_, index) => (
        <div key={index} className="h-24 animate-pulse rounded-lg border bg-card" aria-hidden />
      ))}
    </main>
  );
}

function MarketplaceShell() {
  const params = useSearchParams();
  const tabParam = params.get("tab");
  const access = useQuery({ queryKey: ["me", "access"], queryFn: getMeAccess, staleTime: 60_000 });
  const capabilities = access.data?.ui_capabilities;
  const canAdmin = capabilities?.can_admin_marketplace === true;
  const canCatalog = capabilities?.can_view_marketplace === true;
  const canConfigure = capabilities?.can_view_cartridges === true;
  const canConnected = canConfigure || canCatalog;
  const installations = useQuery({
    queryKey: ["marketplace", "customer"],
    queryFn: listCustomerCartridges,
    enabled: access.isSuccess && canCatalog,
  });
  const hasConnected = (installations.data?.installations.length ?? 0) > 0;
  const tabAccess: TabAccess = { canConnected, canCatalog, canAdmin, hasConnected };

  const normalized = String(tabParam || "").trim().toLowerCase();
  const requestedTab: MarketplaceTab | null =
    normalized === "conectadas" || normalized === "catalogo" || normalized === "licencias" ? normalized : null;
  const requestedAllowed =
    requestedTab !== null &&
    (requestedTab === "conectadas" ? canConnected : requestedTab === "catalogo" ? canCatalog : canAdmin);
  const waitingForDefault = !requestedAllowed && canCatalog && canConnected && installations.isLoading;
  if (access.isLoading || waitingForDefault) {
    return <MarketplaceSkeleton />;
  }
  if (access.isError) {
    return (
      <main className="mx-auto max-w-6xl px-6 py-8">
        <ErrorPanel message="No se pudo cargar tu acceso." onRetry={() => access.refetch()} />
      </main>
    );
  }

  const tab = deriveTab(tabParam, tabAccess);
  const mode = MODE_BY_TAB[tab];
  return (
    <>
      <MarketplaceTabs tab={tab} access={tabAccess} />
      {mode === "admin" ? (
        <AdminView canAdmin={canAdmin} />
      ) : mode === "catalog" ? (
        <CatalogView canAdmin={canAdmin} />
      ) : (
        <ConnectedView canAdmin={canAdmin} canCatalog={canCatalog} canConfigure={canConfigure} />
      )}
    </>
  );
}

export function MarketplaceConsole() {
  return (
    <Suspense fallback={<MarketplaceSkeleton />}>
      <MarketplaceShell />
    </Suspense>
  );
}
