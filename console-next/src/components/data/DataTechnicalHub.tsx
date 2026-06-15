"use client";

import Link from "next/link";
import { useState } from "react";
import {
  Activity,
  ArrowRight,
  Database,
  Droplets,
  GitBranch,
  Layers3,
  Network,
  Search,
  Table2,
  Workflow,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

const CARTRIDGES = ["sap_successfactors", "replicon", "hubspot", "sap_hcm", "sap_s4hana"] as const;

interface TechnicalModule {
  title: string;
  description: string;
  icon: LucideIcon;
  href: (cartridge: string) => string;
}

const MODULES: TechnicalModule[] = [
  {
    title: "Inventario de datasets",
    description: "Inventario de datasets, columnas, terminos y relaciones.",
    icon: Database,
    href: () => "/data/inventory",
  },
  {
    title: "Ejecuciones y logs",
    description: "Historial operativo de extraccion, colas y enlaces a logs.",
    icon: Activity,
    href: () => "/monitor",
  },
  {
    title: "Pipeline",
    description: "Estado por entidad entre bronze, silver, gold y ultima corrida.",
    icon: Workflow,
    href: (cartridge) => `/viewer?type=pipeline&cartridge=${encodeURIComponent(cartridge)}`,
  },
  {
    title: "Watermarks",
    description: "Ultimas marcas de extraccion por entidad y cartucho.",
    icon: Droplets,
    href: (cartridge) => `/viewer?type=watermarks&cartridge=${encodeURIComponent(cartridge)}`,
  },
  {
    title: "Semantic",
    description: "Capa semantica, entidades, descripciones y campos publicados.",
    icon: Layers3,
    href: (cartridge) => `/viewer?type=semantic&cartridge=${encodeURIComponent(cartridge)}`,
  },
  {
    title: "Datasets",
    description: "Datasets disponibles, capas, columnas, staleness y preview.",
    icon: Table2,
    href: () => "/viewer?type=datasets",
  },
  {
    title: "Schema",
    description: "Particiones, columnas inferidas y preview seguro de fuentes.",
    icon: Search,
    href: () => "/viewer?type=schema",
  },
  {
    title: "Lineage",
    description: "Grafo tecnico de dependencias y relaciones de transformacion.",
    icon: GitBranch,
    href: (cartridge) => `/viewer?type=lineage&cartridge=${encodeURIComponent(cartridge)}`,
  },
  {
    title: "Consulta Bronze",
    description: "Consulta protegida contra capa cruda para diagnostico tecnico.",
    icon: Database,
    href: () => "/data/bronze",
  },
  {
    title: "Explorer",
    description: "Exploracion tecnica de fuentes, datasets y objetos disponibles.",
    icon: Network,
    href: () => "/explorer",
  },
];

export function DataTechnicalHub() {
  const [cartridge, setCartridge] = useState<string>("sap_successfactors");

  return (
    <main className="mx-auto max-w-7xl space-y-8 px-6 py-8">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-2">
          <h1 className="text-3xl font-semibold tracking-tight">Catalogo tecnico</h1>
          <p className="max-w-3xl text-sm text-muted-foreground">
            Centro unico para pipeline, schema, semantic, datasets, lineage, watermarks,
            bronze y exploracion tecnica.
          </p>
        </div>
        <label className="space-y-1 text-sm">
          <span className="text-xs font-medium uppercase text-muted-foreground">
            Cartucho para vistas tecnicas
          </span>
          <select
            value={cartridge}
            onChange={(event) => setCartridge(event.target.value)}
            className="min-h-[44px] w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:w-72"
          >
            {CARTRIDGES.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </label>
      </header>

      <section
        aria-label="Secciones del catalogo tecnico"
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3"
      >
        {MODULES.map(({ title, description, icon: Icon, href }) => (
          <Link
            key={title}
            href={href(cartridge)}
            prefetch={false}
            className="group flex min-h-44 flex-col gap-3 rounded-lg border bg-card p-5 shadow-sm transition-shadow hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
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
