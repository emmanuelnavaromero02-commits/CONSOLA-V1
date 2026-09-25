"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import type { LucideIcon } from "lucide-react";
import { Activity, Building2, FileSearch, KeySquare, ShieldCheck, Users, Workflow } from "lucide-react";

import { getMeAccess } from "@/lib/admin-surfaces";
import { cn } from "@/lib/utils";


interface SubNavItem {
  href:  string;
  label: string;
  icon:  LucideIcon;
  permission?: string;
  capability?: string;
}

const ITEMS: SubNavItem[] = [
  { href: "/operations",        label: "Resumen",  icon: ShieldCheck },
  { href: "/operations/companies", label: "Empresas", icon: Building2, capability: "can_manage_companies" },
  { href: "/operations/users",  label: "Usuarios", icon: Users, capability: "can_manage_workspace_users" },
  { href: "/operations/audit",  label: "Auditoría operativa", icon: FileSearch, capability: "can_view_audit" },
  { href: "/operations/vault",  label: "Vault",    icon: KeySquare, capability: "can_view_vault" },
  { href: "/operations/workflows", label: "Automatizaciones", icon: Workflow, capability: "can_view_workflows" },
  { href: "/operations/metrics",   label: "Salud y métricas",  icon: Activity, capability: "can_view_metrics" },
];


function isActive(pathname: string, href: string): boolean {
  if (href === "/operations") return pathname === href;
  return pathname === href || pathname.startsWith(href + "/");
}


export function OperationsSubNav() {
  const pathname = usePathname() ?? "";
  const access = useQuery({ queryKey: ["me", "access"], queryFn: getMeAccess, staleTime: 60_000 });
  if (pathname === "/operations") return null;

  const permissions = new Set(access.data?.permissions ?? []);
  const capabilities = access.data?.ui_capabilities ?? {};
  const visibleItems = ITEMS.filter((item) => {
    if (item.permission && !permissions.has(item.permission)) return false;
    if (item.capability && capabilities[item.capability] !== true) return false;
    return true;
  });
  return (
    <nav
      aria-label="Secciones de Operaciones"
      className="overflow-x-auto border-b"
    >
      <ul className="mx-auto flex max-w-7xl items-center gap-1 px-6">
        {visibleItems.map((item) => {
          const Icon   = item.icon;
          const active = isActive(pathname, item.href);
          return (
            <li key={item.href}>
              <Link
                prefetch={false}
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "inline-flex min-h-[44px] items-center gap-1.5 border-b-2 px-3 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  active
                    ? "border-primary text-foreground"
                    : "border-transparent text-muted-foreground hover:border-border hover:text-foreground",
                )}
              >
                <Icon aria-hidden className="h-4 w-4" />
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
