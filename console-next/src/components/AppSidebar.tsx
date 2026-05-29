"use client";

import Link from "next/link";
import { useMemo, useState, type Ref } from "react";
import {
  Activity,
  AppWindow,
  Bot,
  Boxes,
  Coins,
  Database,
  GitBranch,
  Gauge,
  KeyRound,
  LayoutDashboard,
  Layers3,
  Monitor,
  Package,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  Table2,
  UserCircle,
  Users,
  Workflow,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { LogoutButton } from "@/components/auth/LogoutButton";
import type { MeAccessResponse } from "@/lib/admin-surfaces";
import { cn } from "@/lib/utils";

interface NavItem {
  href:        string;
  label:       string;
  icon:        LucideIcon;
  section:     string;
  keywords?:   string;
  active?:     string[];
  permission?: string;
  capability?: string;
  adminOnly?:  boolean;
  matchNested?: boolean;
}

interface NavSection {
  title: string;
  items: NavItem[];
}

interface AppSidebarProps {
  pathname: string;
  access?: MeAccessResponse;
  email: string;
  dark: boolean;
  onToggleDark: () => void;
  onNavigate?: () => void;
  closeButtonRef?: Ref<HTMLButtonElement>;
  className?: string;
}

const NAV_SECTIONS: NavSection[] = [
  {
    title: "Principal",
    items: [
      { href: "/dashboard", label: "Panel", icon: LayoutDashboard, section: "Principal", keywords: "dashboard inicio kpis" },
      { href: "/workspace", label: "Workspace", icon: AppWindow, section: "Principal", permission: "workspace.access", keywords: "trabajo chat contexto" },
      { href: "/control-room", label: "Control Room", icon: Monitor, section: "Principal", permission: "workspace.access", keywords: "control sala room operaciones" },
      { href: "/marketplace", label: "Marketplace", icon: Package, section: "Principal", permission: "marketplace.read", active: ["/marketplace", "/customer/cartridges", "/admin/installations", "/admin/licenses"], keywords: "market cartuchos licencias instalaciones" },
      { href: "/apps-gallery", label: "Apps", icon: Boxes, section: "Principal", permission: "apps.read", keywords: "aplicaciones galeria" },
      { href: "/monitor", label: "Monitor", icon: Activity, section: "Principal", permission: "monitor.read", keywords: "jobs pipeline salud" },
    ],
  },
  {
    title: "Copiloto",
    items: [
      { href: "/copilot", label: "Copiloto", icon: Bot, section: "Copiloto", permission: "copilot.use", matchNested: false, keywords: "chat agente ia streaming" },
      { href: "/copilot/knowledge", label: "Conocimiento", icon: Layers3, section: "Copiloto", permission: "copilot.use", keywords: "rag conocimiento fuentes vectorial" },
      { href: "/copilot/tokens", label: "Tokens", icon: Coins, section: "Copiloto", permission: "copilot.write", keywords: "costos llm consumo metricas" },
      { href: "/agents", label: "Agentes", icon: Sparkles, section: "Copiloto", adminOnly: true, keywords: "automatizacion agentes tools" },
    ],
  },
  {
    title: "Datos",
    items: [
      { href: "/data/catalog", label: "Catálogo", icon: Database, section: "Datos", active: ["/data", "/data/catalog"], permission: "datasets.read", keywords: "catalog datasets datos" },
      { href: "/data/lineage", label: "Linaje", icon: GitBranch, section: "Datos", active: ["/data/lineage", "/viewer", "/lineage", "/linaje"], permission: "datasets.read", keywords: "lineage linaje grafo dependencias" },
      { href: "/data/bronze", label: "Bronze", icon: Table2, section: "Datos", permission: "datasets.write", keywords: "raw bronze query consultas" },
      { href: "/explorer", label: "Explorer", icon: Search, section: "Datos", permission: "pipelines.read", keywords: "explorar esquema datasets" },
      { href: "/studio", label: "Studio", icon: Sparkles, section: "Datos", permission: "studio.read", keywords: "studio semantic dag datasets" },
    ],
  },
  {
    title: "Operación",
    items: [
      { href: "/operations", label: "Operaciones", icon: Gauge, section: "Operación", permission: "operations.read", adminOnly: true, matchNested: false, keywords: "operaciones admin sistema" },
      { href: "/operations/workflows", label: "Workflows", icon: Workflow, section: "Operación", permission: "operations.read", adminOnly: true, keywords: "flujos workflow ejecutar cancelar" },
      { href: "/operations/metrics", label: "Métricas", icon: Activity, section: "Operación", permission: "operations.read", adminOnly: true, keywords: "metricas salud carga" },
      { href: "/operations/vault", label: "Vault", icon: KeyRound, section: "Operación", permission: "vault.connections.read", adminOnly: true, keywords: "secretos conexiones vault" },
      { href: "/cartridges", label: "Cartuchos", icon: Boxes, section: "Operación", permission: "cartridges.read", adminOnly: true, keywords: "plugins integraciones cartuchos" },
    ],
  },
  {
    title: "Gobierno",
    items: [
      { href: "/operations/users", label: "Usuarios", icon: Users, section: "Gobierno", permission: "iam.users.read", adminOnly: true, keywords: "iam usuarios roles" },
      { href: "/operations/audit", label: "Auditoría", icon: ShieldCheck, section: "Gobierno", permission: "security.audit.read", adminOnly: true, keywords: "logs auditoria seguridad" },
      { href: "/security", label: "Seguridad", icon: ShieldCheck, section: "Gobierno", permission: "security.audit.read", keywords: "seguridad sesiones intentos" },
      { href: "/decisions", label: "Decisiones", icon: GitBranch, section: "Gobierno", adminOnly: true, keywords: "decisiones approvals" },
      { href: "/settings", label: "Settings", icon: Settings, section: "Gobierno", permission: "settings.read", adminOnly: true, keywords: "configuracion ajustes" },
      { href: "/my-access", label: "Mi acceso", icon: UserCircle, section: "Gobierno", keywords: "perfil acceso permisos" },
    ],
  },
];

function userLabel(email: string): string {
  const trimmed = email.trim();
  if (!trimmed) return "Usuario";
  return trimmed.split("@")[0] || trimmed;
}

function navPath(href: string): string {
  return href.split("?")[0] || href;
}

function isActive(pathname: string, item: NavItem): boolean {
  if (item.active?.some((path) => pathname === path)) return true;
  const href = navPath(item.href);
  if (pathname === href) return true;
  if (item.matchNested === false) return false;
  return pathname.startsWith(href + "/");
}

function canShowNavItem(item: NavItem, access: MeAccessResponse | undefined): boolean {
  if (!item.permission && !item.capability && !item.adminOnly) return true;
  if (!access) return false;
  const permissions = new Set(access.permissions ?? []);
  const capabilities = access.ui_capabilities ?? {};
  const isPlatformAdmin = access.role?.is_platform_admin === true;
  if (item.adminOnly && !isPlatformAdmin) return false;
  if (item.permission && !permissions.has(item.permission)) return false;
  if (item.capability && capabilities[item.capability] !== true) return false;
  return true;
}

function matchesQuery(item: NavItem, section: string, query: string): boolean {
  if (!query) return true;
  const haystack = `${item.label} ${item.href} ${section} ${item.keywords ?? ""}`.toLowerCase();
  return haystack.includes(query);
}

export function AppSidebar({
  pathname,
  access,
  email,
  dark,
  onToggleDark,
  onNavigate,
  closeButtonRef,
  className,
}: AppSidebarProps) {
  const [query, setQuery] = useState("");
  const normalizedQuery = query.trim().toLowerCase();
  const sections = useMemo(() => (
    NAV_SECTIONS
      .map((section) => ({
        ...section,
        items: section.items.filter((item) => (
          canShowNavItem(item, access) && matchesQuery(item, section.title, normalizedQuery)
        )),
      }))
      .filter((section) => section.items.length > 0)
  ), [access, normalizedQuery]);

  const itemCount = sections.reduce((total, section) => total + section.items.length, 0);

  return (
    <aside className={cn("flex h-full flex-col border-r bg-card text-card-foreground", className)}>
      <header className="flex items-center justify-between border-b px-4 py-3">
        <Link
          prefetch={false}
          href="/dashboard"
          onClick={onNavigate}
          className="flex min-h-[44px] items-center gap-2 font-semibold tracking-tight text-foreground"
        >
          <span
            aria-hidden
            className="inline-flex h-8 w-8 items-center justify-center rounded-md bg-primary text-primary-foreground"
          >
            Ω
          </span>
          <span>OMEGA</span>
        </Link>
        {onNavigate ? (
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onNavigate}
            aria-label="Cerrar navegación"
            className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:hidden"
          >
            <X aria-hidden className="h-5 w-5" />
          </button>
        ) : null}
      </header>

      <div className="border-b px-3 py-3">
        <label className="space-y-1 text-sm">
          <span className="text-xs font-medium uppercase text-muted-foreground">Servicios</span>
          <div className="relative">
            <Search aria-hidden className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              className="min-h-[44px] w-full rounded-md border bg-background pl-9 pr-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              placeholder="Buscar servicio"
            />
          </div>
        </label>
      </div>

      <nav aria-label="Servicios de la plataforma" className="min-h-0 flex-1 overflow-y-auto px-2 py-3">
        {itemCount === 0 ? (
          <div className="rounded-md border border-dashed px-3 py-8 text-center text-sm text-muted-foreground">
            Sin servicios visibles.
          </div>
        ) : (
          <div className="space-y-5">
            {sections.map((section) => (
              <section key={section.title} aria-label={section.title} className="space-y-1">
                <h2 className="px-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {section.title}
                </h2>
                <ul className="space-y-1">
                  {section.items.map((item) => {
                    const active = isActive(pathname, item);
                    const Icon = item.icon;
                    return (
                      <li key={item.href}>
                        <Link
                          prefetch={false}
                          href={item.href}
                          onClick={onNavigate}
                          aria-current={active ? "page" : undefined}
                          className={cn(
                            "flex min-h-[42px] items-center gap-2 rounded-md px-2.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                            active
                              ? "bg-primary/10 text-primary ring-1 ring-primary/20"
                              : "text-muted-foreground hover:bg-accent/10 hover:text-foreground",
                          )}
                        >
                          <Icon aria-hidden className="h-4 w-4 shrink-0" />
                          <span className="truncate">{item.label}</span>
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
          </div>
        )}
      </nav>

      <footer className="space-y-3 border-t p-3">
        <div className="flex items-center gap-2 rounded-md bg-background px-3 py-2 text-sm">
          <UserCircle aria-hidden className="h-4 w-4 text-muted-foreground" />
          <span className="min-w-0 truncate text-muted-foreground">{userLabel(email)}</span>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <button
            type="button"
            aria-label={dark ? "Cambiar a modo claro" : "Cambiar a modo oscuro"}
            onClick={onToggleDark}
            className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-3 text-xs font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {dark ? "Claro" : "Oscuro"}
          </button>
          <LogoutButton className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-3 text-xs font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50" />
        </div>
      </footer>
    </aside>
  );
}
