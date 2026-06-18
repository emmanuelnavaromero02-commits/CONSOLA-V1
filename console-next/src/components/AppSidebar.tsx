"use client";

import Link from "next/link";
import { useMemo, useState, type Ref } from "react";
import {
  AppWindow,
  Bot,
  Coins,
  Database,
  GitBranch,
  Gauge,
  LayoutDashboard,
  Monitor,
  PanelLeftClose,
  PanelLeftOpen,
  Package,
  Search,
  Sparkles,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { WorkspaceSwitcher } from "@/components/WorkspaceSwitcher";
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
  capabilitiesAny?: string[];
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
      { href: "/copilot/tokens", label: "Tokens", icon: Coins, section: "Núcleo", capability: "can_view_tokens", keywords: "costos llm consumo metricas" },
    ],
  },
  {
    title: "Datos",
    items: [
      {
        href: "/data",
        label: "Catálogo técnico",
        icon: Database,
        section: "Datos",
        active: ["/data", "/data/catalog", "/data/inventory", "/data/lineage", "/viewer", "/lineage", "/linaje", "/explorer", "/data/bronze", "/copilot/knowledge", "/monitor"],
        capabilitiesAny: ["can_view_catalog", "can_view_lineage", "can_view_knowledge", "can_view_bronze", "can_view_explorer", "can_view_monitor"],
        keywords: "catalog datasets datos schema semantic lineage linaje watermarks explorer studio bronze conocimiento rag",
      },
      { href: "/studio", label: "Studio", icon: Sparkles, section: "Datos", capability: "can_view_studio", keywords: "studio cartuchos dag refinamiento capas semantica builder" },
    ],
  },
  {
    title: "Operación",
    items: [
      { href: "/control-room", label: "Control Room", icon: Monitor, section: "Operación", capability: "can_view_control_room", keywords: "control sala room operaciones" },
      { href: "/agents", label: "Agentes", icon: Sparkles, section: "Operación", capability: "can_view_agents", keywords: "automatizacion agentes tools monitores" },
      { href: "/decisions", label: "Decisiones", icon: GitBranch, section: "Operación", capability: "can_view_decisions", keywords: "decisiones approvals" },
    ],
  },
  {
    title: "Integraciones",
    items: [
      {
        href: "/marketplace",
        label: "Cartuchos",
        icon: Package,
        section: "Integraciones",
        capabilitiesAny: ["can_view_marketplace", "can_view_cartridges"],
        active: ["/marketplace", "/customer/cartridges", "/admin/installations", "/admin/licenses", "/cartridges", "/cartridges/viewer"],
        keywords: "marketplace cartuchos licencias instalaciones monitor tecnico conectores integraciones",
      },
      { href: "/apps-gallery", label: "Apps analíticas", icon: Sparkles, section: "Integraciones", capability: "can_view_apps", keywords: "aplicaciones galeria workspace analiticas" },
    ],
  },
  {
    title: "Administración",
    items: [
      {
        href: "/operations",
        label: "Centro de administración",
        icon: Gauge,
        section: "Administración",
        capabilitiesAny: ["can_manage_companies", "can_manage_workspace_users", "can_view_vault", "can_view_audit", "can_view_workflows", "can_view_metrics", "can_view_security", "can_view_settings"],
        active: ["/operations", "/operations/companies", "/operations/users", "/operations/vault", "/operations/audit", "/operations/workflows", "/operations/metrics", "/security", "/settings"],
        keywords: "operaciones empresas tenants usuarios vault auditoria workflows metricas administracion seguridad sesiones ajustes settings",
      },
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
  if (item.capabilitiesAny?.length && !item.capabilitiesAny.some((capability) => capabilities[capability] === true)) return false;
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

      <div className={cn("border-b py-3", showLabels ? "px-3" : "px-2")}>
        {showLabels && (access?.workspaces?.length ?? 0) > 0 ? (
          <WorkspaceSwitcher workspaces={access?.workspaces} className="mb-3" />
        ) : null}
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
