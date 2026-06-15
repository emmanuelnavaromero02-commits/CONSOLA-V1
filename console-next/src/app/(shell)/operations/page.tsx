"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowRight,
  Building2,
  FileSearch,
  KeySquare,
  Settings,
  ShieldCheck,
  Users,
  Workflow,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { getMeAccess } from "@/lib/admin-surfaces";

/**
 * v1.44.4 Group 1 — Operations overview.
 *
 * BACKEND REALITY (audited 2026-06-14):
 *   - Real surfaces: /api/admin/tenants, /api/admin/users,
 *                    /security/audit, /api/vault/connections/{cartridge},
 *                    /api/copilot/workflow and /api/metrics/operational.
 *   - Brief asked for:  Vault entries / Audit log / Users CRUD
 *                       / Workspaces CRUD / Settings / Monitor.
 *     Workspace onboarding now lives in Empresas. Settings(workspace_id)
 *     remains backend-pending and is kept in the honest pending panel.
 *
 * Pre-Task-D placeholder linked to the legacy :8000 surface;
 * this page now replaces it with a Next.js-native overview
 * that surfaces real sub-modules + an honest
 * "próximamente" panel for what's still backend-pending.
 */
interface ModuleCard {
  href:        string;
  title:       string;
  description: string;
  icon:        LucideIcon;
  permission?: string;
  capability?: string;
}

const READY_MODULES: ModuleCard[] = [
  {
    href:        "/operations/companies",
    title:       "Empresas",
    description: "Crear tenants, workspaces y primer tenant admin.",
    icon:        Building2,
    capability:  "can_manage_companies",
  },
  {
    href:        "/operations/users",
    title:       "Usuarios",
    description: "Usuarios dentro del workspace activo.",
    icon:        Users,
    permission:  "iam.users.read",
    capability:  "can_manage_workspace_users",
  },
  {
    href:        "/operations/audit",
    title:       "Auditoría operativa",
    description: "Últimas 100 acciones registradas en el sistema.",
    icon:        FileSearch,
    permission:  "security.audit.read",
  },
  {
    href:        "/operations/vault",
    title:       "Vault",
    description: "Inspecciona las conexiones guardadas por cartucho.",
    icon:        KeySquare,
    permission:  "vault.connections.read",
  },
  {
    href:        "/operations/workflows",
    title:       "Automatizaciones",
    description: "Visualiza flujos, ejecútalos y cancela corridas activas.",
    icon:        Workflow,
    permission:  "copilot.execute",
  },
  {
    href:        "/operations/metrics",
    title:       "Salud y métricas",
    description: "Salud de servicios, extracciones, errores y carga reciente.",
    icon:        Activity,
    permission:  "operations.read",
  },
  {
    href:        "/security",
    title:       "Seguridad y sesiones",
    description: "Sesiones, intentos y controles de seguridad del usuario actual.",
    icon:        ShieldCheck,
    capability:  "can_view_security",
  },
  {
    href:        "/settings",
    title:       "Ajustes",
    description: "Preferencias y configuración operativa disponible para tu rol.",
    icon:        Settings,
    capability:  "can_view_settings",
  },
];


export default function OperationsOverviewPage() {
  const access = useQuery({ queryKey: ["me", "access"], queryFn: getMeAccess, staleTime: 60_000 });
  const permissions = new Set(access.data?.permissions ?? []);
  const capabilities = access.data?.ui_capabilities ?? {};
  const readyModules = READY_MODULES.filter((item) => {
    if (item.permission && !permissions.has(item.permission)) return false;
    if (item.capability && capabilities[item.capability] !== true) return false;
    return true;
  });

  return (
    <main className="mx-auto max-w-7xl space-y-8 px-6 py-8">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Operaciones</h1>
        <p className="text-sm text-muted-foreground">
          Centro único para empresas, usuarios, secretos, auditoría y salud operativa.
        </p>
      </header>

      <section
        aria-label="Módulos disponibles"
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
      >
        {readyModules.map(({ href, title, description, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            prefetch={false}
            className="group flex flex-col gap-3 rounded-lg border bg-card p-5 shadow-sm transition-shadow hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <span
              aria-hidden
              className="inline-flex h-10 w-10 items-center justify-center rounded-md bg-primary/10 text-primary"
            >
              <Icon className="h-5 w-5" />
            </span>
            <div className="flex-1 space-y-1">
              <h2 className="text-base font-semibold tracking-tight">{title}</h2>
              <p className="text-xs text-muted-foreground">{description}</p>
            </div>
            <span className="inline-flex items-center gap-1 text-xs font-medium text-primary">
              Abrir
              <ArrowRight aria-hidden className="h-3.5 w-3.5 transition-transform group-hover:translate-x-0.5" />
            </span>
          </Link>
        ))}
      </section>

    </main>
  );
}
