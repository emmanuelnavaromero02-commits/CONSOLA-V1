"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import type { LucideIcon } from "lucide-react";
import { Activity, FileSearch, KeySquare, ShieldCheck, Users, Workflow } from "lucide-react";

import { getMeAccess } from "@/lib/admin-surfaces";
import { cn } from "@/lib/utils";


interface SubNavItem {
  href:  string;
  label: string;
  icon:  LucideIcon;
  permission?: string;
  capability?: string;
}

/**
 * v1.44.4 Group 1 — Operations sub-navigation.
 *
 * Lists ONLY the sub-pages where the real backend exists today:
 *   - Users (full CRUD against /api/admin/users)
 *   - Audit (read against /security/audit)
 *   - Vault (read connections against /api/vault/connections)
 *   - Workflows (read/execute/cancel against /api/copilot/workflow)
 *   - Metrics (read against /api/metrics and /api/operations/health)
 *
 * Workspaces / Monitor / Settings sub-pages are intentionally
 * absent — the backend endpoints don't exist yet (see audit
 * comment in lib/operations/types.ts).
 */
const ITEMS: SubNavItem[] = [
  { href: "/operations",        label: "Resumen",  icon: ShieldCheck },
  { href: "/operations/users",  label: "Usuarios", icon: Users, permission: "iam.users.read", capability: "can_manage_workspace_users" },
  { href: "/operations/audit",  label: "Auditoría", icon: FileSearch, permission: "security.audit.read" },
  { href: "/operations/vault",  label: "Vault",    icon: KeySquare, permission: "vault.connections.read" },
  { href: "/operations/workflows", label: "Workflows", icon: Workflow, permission: "copilot.execute" },
  { href: "/operations/metrics",   label: "Métricas",  icon: Activity, permission: "operations.read" },
];


function isActive(pathname: string, href: string): boolean {
  if (href === "/operations") return pathname === href;
  return pathname === href || pathname.startsWith(href + "/");
}


export function OperationsSubNav() {
  const pathname = usePathname() ?? "";
  const access = useQuery({ queryKey: ["me", "access"], queryFn: getMeAccess, staleTime: 60_000 });
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
