import { Award, Clock, Database, Network, Wand2, type LucideIcon } from "lucide-react";

import type { DatasetSummary } from "@/lib/monitor/types";

import { datasetsForLayer } from "./datasets";
import { uniqueGraph } from "./graph-layout";
import type { DagGraphPayload, StudioManifest, StudioManifestEntity } from "./types";
import { managedDagIds } from "./validation";

export type StudioSectionId = "grafo" | "dags" | "entidades" | "refinar" | "capas";

export interface StudioSection {
  id: StudioSectionId;
  label: string;
  step: number;
  icon: LucideIcon;
  countNoun: { one: string; other: string };
}

export const STUDIO_SECTIONS: readonly StudioSection[] = [
  { id: "grafo", label: "Mapa del Flujo", step: 1, icon: Network, countNoun: { one: "nodo", other: "nodos" } },
  {
    id: "dags",
    label: "Automatizaciones",
    step: 2,
    icon: Clock,
    countNoun: { one: "DAG declarado", other: "DAGs declarados" },
  },
  {
    id: "entidades",
    label: "Tablas de Origen (Bronce)",
    step: 3,
    icon: Database,
    countNoun: { one: "tabla de origen", other: "tablas de origen" },
  },
  {
    id: "refinar",
    label: "Modelado y Limpieza (Plata)",
    step: 4,
    icon: Wand2,
    countNoun: { one: "dataset Plata", other: "datasets Plata" },
  },
  {
    id: "capas",
    label: "Indicadores y KPIs (Oro)",
    step: 5,
    icon: Award,
    countNoun: { one: "dataset Oro", other: "datasets Oro" },
  },
];

export function isSectionId(value: string | null | undefined): value is StudioSectionId {
  return STUDIO_SECTIONS.some((section) => section.id === value);
}

export function sectionById(id: StudioSectionId): StudioSection {
  return STUDIO_SECTIONS.find((section) => section.id === id) ?? STUDIO_SECTIONS[0];
}

export function sectionForStep(step: number): StudioSection {
  return STUDIO_SECTIONS.find((section) => section.step === step) ?? STUDIO_SECTIONS[0];
}

export function sectionHint(id: StudioSectionId, { sourceName }: { sourceName?: string | null } = {}): string {
  switch (id) {
    case "grafo":
      return "Vista visual de cómo viaja la información desde el origen hasta los reportes finales";
    case "dags":
      return "Procesos programados que extraen y actualizan los datos automáticamente";
    case "entidades": {
      const name = sourceName?.trim();
      return `Datos crudos extraídos directamente de ${name || "los sistemas fuente"}`;
    }
    case "refinar":
      return "Transformaciones y reglas de negocio para estandarizar y validar los datos";
    case "capas":
      return "Tablas consolidadas listas para dashboards, análisis y decisiones directivas";
  }
}

export type SectionCounts = Record<StudioSectionId, number | null>;

export function sectionCounts({
  cartridge,
  graph,
  manifest,
  datasets,
}: {
  cartridge: string | null;
  graph: DagGraphPayload | null | undefined;
  manifest: StudioManifest | null | undefined;
  datasets: DatasetSummary[] | null | undefined;
}): SectionCounts {
  return {
    grafo: graph ? uniqueGraph(graph.nodes ?? [], graph.edges ?? []).nodes.length : null,
    dags: manifest ? managedDagIds(manifest).size : null,
    entidades: manifest ? (manifest.entities ?? []).length : null,
    refinar: datasets && cartridge ? datasetsForLayer(datasets, cartridge, "silver").length : null,
    capas: datasets && cartridge ? datasetsForLayer(datasets, cartridge, "gold").length : null,
  };
}

const CONCEPTS = {
  clientes: /\b(clientes?|clients?|customers?)\b/,
  facturas: /\b(facturas?|invoices?)\b/,
  notasCredito: /\b(notas? de credito|credit memos?|credit notes?)\b/,
  vendedores: /\b(vendedor(es)?|sales ?(employees?|reps?|persons?|people)|salespersons?)\b/,
};

type Concept = keyof typeof CONCEPTS;

function normalized(value: unknown): string {
  return typeof value === "string"
    ? value.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase()
    : "";
}

function entityText(entity: StudioManifestEntity): string {
  return [entity.entity, entity.name, entity.display_name, entity.business_name, entity.description]
    .map(normalized)
    .join(" ");
}

function entityLabel(entity: StudioManifestEntity): string | null {
  for (const value of [entity.display_name, entity.business_name, entity.entity, entity.name]) {
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

export function quickPromptsForStep(step: number, manifest?: StudioManifest | null): string[] {
  const entities = (manifest?.entities ?? []).filter((entity): entity is StudioManifestEntity => Boolean(entity));
  const texts = entities.map(entityText);
  const has = (concept: Concept) => texts.some((value) => CONCEPTS[concept].test(value));
  const labels = entities.map(entityLabel).filter((label): label is string => Boolean(label));
  const first = labels[0] ?? null;
  const second = labels[1] ?? first;
  switch (step) {
    case 1:
      return [
        "Resume cómo viajan los datos de este cartucho desde el origen hasta los indicadores",
        "¿Qué le falta a este cartucho para estar listo?",
      ];
    case 2:
      return ["¿Por qué falló la última extracción?", "¿Cómo cambio la frecuencia a diaria?"];
    case 3:
      return [
        has("clientes")
          ? "¿Qué campos incluye la tabla de Clientes?"
          : first
            ? `¿Qué campos incluye la tabla ${first}?`
            : "¿Qué campos incluyen las tablas de origen de este cartucho?",
        has("facturas")
          ? "¿Hay registros duplicados en facturas?"
          : second
            ? `¿Hay registros duplicados en ${second}?`
            : "¿Hay registros duplicados en las tablas de origen?",
      ];
    case 4:
      return [
        has("notasCredito")
          ? "Ayúdame a calcular el margen neto restando notas de crédito"
          : first
            ? `¿Qué reglas de limpieza conviene aplicar a ${first}?`
            : "¿Qué reglas de limpieza conviene aplicar a las tablas de origen?",
        has("vendedores")
          ? "¿Cómo agrupo las ventas por vendedor?"
          : second
            ? `¿Cómo resumo ${second} en un dataset Plata?`
            : "¿Cómo resumo las tablas de origen en un dataset Plata?",
      ];
    case 5:
      return [
        "¿Qué dashboards consumen este dataset?",
        has("clientes")
          ? "Muéstrame, sin publicar, una vista previa del ranking de clientes"
          : "Muéstrame, sin publicar, una vista previa de un indicador Oro de este cartucho",
      ];
    default:
      return [];
  }
}
