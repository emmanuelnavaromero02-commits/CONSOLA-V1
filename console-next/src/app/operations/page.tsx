"use client";

import Link from "next/link";
import {
  ArrowRight,
  FileSearch,
  KeySquare,
  Users,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { legacyConsoleUrl } from "@/lib/legacy-url";

/**
 * v1.44.4 Group 1 — Operations overview.
 *
 * BACKEND REALITY (audited 2026-05-17):
 *   - 3 real surfaces:  /api/admin/users, /security/audit,
 *                       /api/vault/connections/{cartridge}
 *   - Brief asked for:  Vault entries / Audit log / Users CRUD
 *                       / Workspaces CRUD / Settings / Monitor.
 *     Workspaces / Settings(workspace_id) / Monitor don't yet
 *     exist as backend endpoints — they're documented as
 *     coming soon below rather than shipped as UI theater.
 *
 * Pre-Task-D placeholder linked to the legacy :8000 surface;
 * this page now replaces it with a Next.js-native overview
 * that surfaces the three real sub-modules + an honest
 * "próximamente" panel for what's still backend-pending.
 */
interface ModuleCard {
  href:        string;
  title:       string;
  description: string;
  icon:        LucideIcon;
}

const READY_MODULES: ModuleCard[] = [
  {
    href:        "/operations/users",
    title:       "Usuarios",
    description: "Crear, editar, desactivar y mandar reset de contraseña.",
    icon:        Users,
  },
  {
    href:        "/operations/audit",
    title:       "Auditoría",
    description: "Últimas 100 acciones registradas en el sistema.",
    icon:        FileSearch,
  },
  {
    href:        "/operations/vault",
    title:       "Vault",
    description: "Inspecciona las conexiones guardadas por cartucho.",
    icon:        KeySquare,
  },
];


const PENDING_MODULES = [
  {
    title:       "Workspaces",
    description: "CRUD de workspaces y asignaciones de usuarios.",
    legacyHref:  legacyConsoleUrl("/iam"),
  },
  {
    title:       "Settings",
    description: "Preferencias por workspace (idioma, tono, branding).",
    legacyHref:  legacyConsoleUrl("/settings"),
  },
  {
    title:       "Monitor",
    description: "Estado de salud de los 18 servicios.",
    legacyHref:  legacyConsoleUrl("/monitor"),
  },
];


export default function OperationsOverviewPage() {
  return (
    <main className="mx-auto max-w-6xl space-y-8 px-6 py-8">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Operaciones</h1>
        <p className="text-sm text-muted-foreground">
          Administra usuarios, accesos y auditoría de la plataforma.
        </p>
      </header>

      <section
        aria-label="Módulos disponibles"
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
      >
        {READY_MODULES.map(({ href, title, description, icon: Icon }) => (
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

      <section
        aria-label="Próximamente"
        className="space-y-3 rounded-lg border bg-muted/30 p-5"
      >
        <header className="space-y-1">
          <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
            Próximamente
          </h2>
          <p className="text-xs text-muted-foreground">
            Estos módulos siguen disponibles en la consola clásica mientras
            terminamos de migrarlos a la nueva interfaz.
          </p>
        </header>
        <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {PENDING_MODULES.map((m) => (
            <li
              key={m.title}
              className="flex flex-col gap-2 rounded-md border bg-background p-4"
            >
              <h3 className="text-sm font-semibold tracking-tight">{m.title}</h3>
              <p className="text-xs text-muted-foreground">{m.description}</p>
              <a
                href={m.legacyHref}
                rel="noopener"
                className="mt-auto inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-3 text-xs font-medium transition-colors hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                Abrir en la consola clásica →
              </a>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}
