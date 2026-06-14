"use client";

import Link from "next/link";
import { useMemo, useState, type Ref } from "react";
import {
  Activity,
  AppWindow,
  Bot,
  Boxes,
  Building2,
  Coins,
  Database,
  GitBranch,
  Gauge,
  LayoutDashboard,
  Layers3,
  Monitor,
  PanelLeftClose,
  PanelLeftOpen,
  Package,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  Table2,
  Users,
  Workflow,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type { MeAccessResponse } from "@/lib/admin-surfaces";
import { cn } from "@/lib/utils";
import { WorkspaceSwitcher } from "@/components/WorkspaceSwitcher";

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
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
  onNavigate?: () => void;
  closeButtonRef?: Ref<HTMLButtonElement>;
  className?: string;
}

const NAV_SECTIONS: NavSection[] = [
  {
    title: "Núcleo",
    items: [
      { href: "/dashboard", label: "Panel", icon: LayoutDashboard, section: "Núcleo", keywords: "dashboard inicio kpis" },
      { href: "/workspace", label: "Espacio de Trabajo", icon: AppWindow, section: "Núcleo", capability: "can_view_workspace", keywords: "workspace trabajo chat contexto" },
      { href: "/copilot", label: "Copiloto", icon: Bot, section: "Núcleo", capability: "can_view_copilot", matchNested: false, keywords: "chat agente ia streaming" },
      { href: "/copilot/knowledge", label: "Conocimiento", icon: Layers3, section: "Núcleo", capability: "can_view_knowledge", keywords: "rag conocimiento fuentes vectorial" },
      { href: "/copilot/tokens", label: "Tokens", icon: Coins, section: "Núcleo", capability: "can_view_tokens", keywords: "costos llm consumo metricas" },
      { href: "/marketplace", label: "Marketplace", icon: Package, section: "Núcleo", capability: "can_view_marketplace", active: ["/marketplace", "/customer/cartridges", "/admin/installations", "/admin/licenses"], keywords: "market cartuchos licencias instalaciones" },
      { href: "/apps-gallery", label: "Apps", icon: Boxes, section: "Núcleo", capability: "can_view_apps", keywords: "aplicaciones galeria" },
    ],
  },
  {
    title: "Datos",
    items: [
      { href: "/data/catalog", label: "Catálogo", icon: Database, section: "Datos", active: ["/data", "/data/catalog"], capability: "can_view_catalog", keywords: "catalog datasets datos" },
      { href: "/data/lineage", label: "Linaje", icon: GitBranch, section: "Datos", active: ["/data/lineage", "/viewer", "/lineage", "/linaje"], capability: "can_view_lineage", keywords: "lineage linaje grafo dependencias" },
      { href: "/data/bronze", label: "Consulta Bronce", icon: Table2, section: "Datos", capability: "can_view_bronze", keywords: "raw bronze query consultas" },
      { href: "/explorer", label: "Explorer", icon: Search, section: "Datos", capability: "can_view_explorer", keywords: "explorar esquema datasets" },
      { href: "/studio", label: "Studio", icon: Sparkles, section: "Datos", capability: "can_view_studio", keywords: "studio semantic dag datasets" },
    ],
  },
  {
    title: "Operaciones",
    items: [
      { href: "/control-room", label: "Control Room", icon: Monitor, section: "Operaciones", capability: "can_view_control_room", keywords: "control sala room operaciones" },
      { href: "/monitor", label: "Monitor", icon: Activity, section: "Operaciones", capability: "can_view_monitor", keywords: "jobs pipeline salud" },
      { href: "/operations/workflows", label: "Flujos de trabajo", icon: Workflow, section: "Operaciones", capability: "can_view_workflows", keywords: "workflows flujos ejecutar cancelar" },
      { href: "/operations/metrics", label: "Métricas", icon: Gauge, section: "Operaciones", capability: "can_view_metrics", keywords: "metricas salud carga" },
      { href: "/agents", label: "Agentes", icon: Sparkles, section: "Operaciones", capability: "can_view_agents", keywords: "automatizacion agentes tools" },
      { href: "/operations/vault", label: "Vault", icon: ShieldCheck, section: "Operaciones", capability: "can_view_vault", keywords: "secretos conexiones vault" },
      { href: "/cartridges", label: "Cartuchos", icon: Boxes, section: "Operaciones", capability: "can_view_cartridges", keywords: "plugins integraciones cartuchos" },
    ],
  },
  {
    title: "Configuración/Admin",
    items: [
      { href: "/admin/tenants", label: "Tenants", icon: Building2, section: "Configuración/Admin", capability: "can_manage_tenants", keywords: "tenants clientes provisionar workspace organizacion" },
      { href: "/admin/workspaces", label: "Workspaces", icon: Boxes, section: "Configuración/Admin", capability: "can_manage_workspaces", keywords: "workspaces espacios provisionar tenant" },
      { href: "/operations/audit", label: "Auditoría", icon: ShieldCheck, section: "Configuración/Admin", capability: "can_view_audit", keywords: "logs auditoria seguridad" },
      { href: "/operations/users", label: "Usuarios", icon: Users, section: "Configuración/Admin", permission: "iam.users.read", capability: "can_manage_workspace_users", keywords: "iam usuarios roles" },
      { href: "/settings", label: "Ajustes", icon: Settings, section: "Configuración/Admin", capability: "can_view_settings", keywords: "configuracion settings ajustes" },
      { href: "/security", label: "Seguridad", icon: ShieldCheck, section: "Configuración/Admin", capability: "can_view_security", keywords: "seguridad sesiones intentos" },
      { href: "/decisions", label: "Decisiones", icon: GitBranch, section: "Configuración/Admin", capability: "can_view_decisions", keywords: "decisiones approvals" },
    ],
  },
];

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
  collapsed = false,
  onToggleCollapsed,
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
  const showLabels = !collapsed || Boolean(onNavigate);

  return (
    <aside className={cn("flex h-full flex-col border-r bg-card text-card-foreground transition-[width] duration-200", className)}>
      <header className={cn("flex items-center border-b py-3", showLabels ? "justify-between px-4" : "justify-center px-2")}>
        <Link
          prefetch={false}
          href="/dashboard"
          onClick={onNavigate}
          className={cn(
            "flex min-h-[44px] items-center gap-2 font-semibold tracking-tight text-foreground",
            !showLabels && "justify-center",
          )}
          aria-label="OMEGA"
        >
          <span
            aria-hidden
            className="inline-flex h-8 w-8 items-center justify-center rounded-md bg-primary text-primary-foreground"
          >
            Ω
          </span>
          {showLabels ? <span>OMEGA</span> : null}
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
        ) : (
          <button
            type="button"
            onClick={onToggleCollapsed}
            aria-label={collapsed ? "Expandir navegación" : "Colapsar navegación"}
            className="hidden min-h-[44px] min-w-[44px] items-center justify-center rounded-md hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:inline-flex"
          >
            {collapsed ? <PanelLeftOpen aria-hidden className="h-5 w-5" /> : <PanelLeftClose aria-hidden className="h-5 w-5" />}
          </button>
        )}
      </header>

      {showLabels && (access?.workspaces?.length ?? 0) > 1 ? (
        <div className="border-b px-3 py-3">
          <WorkspaceSwitcher access={access} showLabels={showLabels} />
        </div>
      ) : null}

      <div className={cn("border-b py-3", showLabels ? "px-3" : "px-2")}>
        {showLabels ? (
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
        ) : (
          <button
            type="button"
            onClick={onToggleCollapsed}
            aria-label="Expandir para buscar servicios"
            className="inline-flex min-h-[44px] w-full items-center justify-center rounded-md border bg-background text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Search aria-hidden className="h-4 w-4" />
          </button>
        )}
      </div>

      <nav aria-label="Navegación principal" className={cn("min-h-0 flex-1 overflow-y-auto overflow-x-hidden py-3", showLabels ? "px-2" : "px-1.5")}>
        {itemCount === 0 ? (
          <div className="rounded-md border border-dashed px-3 py-8 text-center text-sm text-muted-foreground">
            Sin servicios visibles.
          </div>
        ) : (
          <div className={cn(showLabels ? "space-y-5" : "space-y-2")}>
            {sections.map((section) => (
              <section key={section.title} aria-label={section.title} className="space-y-1">
                {showLabels ? (
                  <h2 className="px-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    {section.title}
                  </h2>
                ) : null}
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
                          title={showLabels ? undefined : item.label}
                          className={cn(
                            "flex min-h-[42px] items-center rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                            showLabels ? "gap-2 px-2.5" : "justify-center px-2",
                            active
                              ? "bg-primary/10 text-primary ring-1 ring-primary/20"
                              : "text-muted-foreground hover:bg-accent/10 hover:text-foreground",
                          )}
                        >
                          <Icon aria-hidden className="h-4 w-4 shrink-0" />
                          {showLabels ? <span className="truncate">{item.label}</span> : null}
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
    </aside>
  );
}
