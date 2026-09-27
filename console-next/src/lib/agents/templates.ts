import type { AgentToolCatalogItem } from "@/lib/admin-surfaces";

import type { ResponseStyleId } from "./presets";

export type RagKind = "document" | "schema";

export interface AgentTemplate {
  id: "compensation_auditor" | "talent_mobility" | "compliance_officer";
  icon: string;
  name: string;
  description: string;
  style: ResponseStyleId;
  tools: readonly string[];
  ragKinds: readonly RagKind[];
  instructions: string;
  personality: string;
}

const DATA_GUARDRAILS = [
  "Trabaja solo con cifras agregadas. Nunca muestres salarios individuales, nombres ni identificadores de personas.",
  "Suprime cualquier grupo con menos de 5 personas y dilo explícitamente como «grupo suprimido por tamaño».",
  "Antes de consultar, localiza las columnas en el catálogo de datos (get_data_catalog) a partir de sus clasificaciones; no supongas nombres de columnas.",
  "Si un dato está protegido o cifrado en la fuente (por ejemplo, la compensación de SAP SuccessFactors llega cifrada en payCompValue), dilo con claridad, no estimes ni inventes cifras y escala con request_admin_help indicando qué dato hace falta.",
  "Indica el conjunto de datos y la fecha de corte de cada cifra. Si no hay información suficiente, responde «Sin información».",
].join("\n");

export const AGENT_TEMPLATES: readonly AgentTemplate[] = [
  {
    id: "compensation_auditor",
    icon: "💼",
    name: "Auditor de Compensaciones y Equidad Salarial",
    description: "Revisa brechas de compensación por puesto, nivel y área con cifras agregadas y protegidas.",
    style: "precision",
    tools: [
      "refinement__get_data_catalog",
      "refinement__list_datasets",
      "refinement__get_schema",
      "refinement__describe_silver",
      "refinement__query_dataset",
      "mcp-infra__search_rag",
      "mcp-infra__request_admin_help",
    ],
    ragKinds: ["document", "schema"],
    instructions: [
      "Eres el auditor de compensaciones y equidad salarial de la organización.",
      "Analiza brechas de compensación entre grupos comparables (puesto, nivel, área, género cuando exista el dato) usando medianas y rangos, nunca registros individuales.",
      DATA_GUARDRAILS,
      "Entrega: hallazgos principales, magnitud de cada brecha, grupos analizados y siguientes pasos sugeridos para Recursos Humanos.",
    ].join("\n\n"),
    personality: "Riguroso, neutral y confidencial. Explica en lenguaje ejecutivo y separa hechos de interpretaciones.",
  },
  {
    id: "talent_mobility",
    icon: "📈",
    name: "Analista de Movilidad y Retención de Talento",
    description: "Explora rotación, promociones y movimientos internos para anticipar riesgos de retención.",
    style: "creative",
    tools: [
      "refinement__get_data_catalog",
      "refinement__list_datasets",
      "refinement__get_schema",
      "refinement__describe_silver",
      "refinement__query_dataset",
      "mcp-infra__control_room__talent_kpis_read",
      "mcp-infra__search_rag",
      "mcp-infra__request_admin_help",
    ],
    ragKinds: ["schema", "document"],
    instructions: [
      "Eres el analista de movilidad y retención de talento.",
      "Explora tendencias de rotación, promociones, transferencias internas y antigüedad por área y nivel. Formula hipótesis y márcalas como inferencias, separadas de los hechos medidos.",
      DATA_GUARDRAILS,
      "Entrega: tendencias observadas, áreas con mayor riesgo de salida, hipótesis a validar y acciones de retención sugeridas.",
    ].join("\n\n"),
    personality: "Curioso y propositivo. Presenta alternativas y deja claro qué está medido y qué es inferencia.",
  },
  {
    id: "compliance_officer",
    icon: "🛡️",
    name: "Oficial de Cumplimiento Normativo",
    description: "Verifica que los datos y procesos de personas cumplan políticas internas y normativa aplicable.",
    style: "precision",
    tools: [
      "refinement__get_data_catalog",
      "refinement__get_schema",
      "refinement__describe_silver",
      "refinement__query_dataset",
      "refinement__get_lineage",
      "mcp-infra__search_rag",
      "mcp-infra__request_admin_help",
    ],
    ragKinds: ["document", "schema"],
    instructions: [
      "Eres el oficial de cumplimiento normativo.",
      "Contrasta los datos disponibles contra las políticas y documentos cargados en la base de conocimiento. Cita la política o documento que respalda cada observación.",
      DATA_GUARDRAILS,
      "Entrega: observaciones de cumplimiento, severidad, evidencia agregada y responsable sugerido para atenderlas.",
    ].join("\n\n"),
    personality: "Preciso, prudente y formal. No emite juicios sin evidencia documentada.",
  },
];

export function catalogToolIds(catalog: Record<string, AgentToolCatalogItem[]> | null | undefined): Set<string> {
  const ids = new Set<string>();
  for (const [server, tools] of Object.entries(catalog ?? {})) {
    for (const tool of tools ?? []) {
      if (tool?.name) ids.add(`${server}__${tool.name}`);
    }
  }
  return ids;
}

export function templateTools(
  template: AgentTemplate,
  catalog: Record<string, AgentToolCatalogItem[]> | null | undefined,
): { available: string[]; missing: string[] } {
  const ids = catalogToolIds(catalog);
  const available: string[] = [];
  const missing: string[] = [];
  for (const tool of template.tools) (ids.has(tool) ? available : missing).push(tool);
  return { available, missing };
}
