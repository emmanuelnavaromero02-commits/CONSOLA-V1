"use client";

import { useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Bot, CheckCircle2, Clock, MessageSquareText, Play, Plus, RefreshCw, Save, Trash2, Wrench } from "lucide-react";
import { toast } from "sonner";

import { FrequencyPicker } from "@/components/schedule/FrequencyPicker";
import {
  createAgent,
  deleteAgent,
  getAgentRun,
  invokeAgent,
  listAgentRuns,
  listAgents,
  listAgentToolCatalog,
  updateAgent,
  updateAgentStatus,
  type AgentPayload,
  type AgentRecord,
  type AgentRunRecord,
  type AgentToolCatalogItem,
} from "@/lib/admin-surfaces";
import {
  CURRENT_STYLE_LABEL,
  PRECISION,
  RESPONSE_STYLES,
  responseStyleFor,
  temperatureFor,
} from "@/lib/agents/presets";
import { agentSlug } from "@/lib/agents/slug";
import { AGENT_TEMPLATES, templateTools, type AgentTemplate, type RagKind } from "@/lib/agents/templates";
import { splitToolId, toolLabel, toolServerLabel, toolTooltip } from "@/lib/agents/tool-labels";
import { isApiError } from "@/lib/api";
import { KNOWN_CARTRIDGES } from "@/lib/cartridges";
import { dataSourceName, GLOSSARY } from "@/lib/glossary";
import { customScheduleError, defaultTimeZone, describeSchedule } from "@/lib/schedule/frequency";
import { cn } from "@/lib/utils";

type AgentTab = "config" | "tools" | "rag" | "schedule" | "runs" | "test";
type AgentListFilter = "all" | "monitor" | "control_room" | "cartridge" | "platform_ops";

interface AgentDraft {
  id: string | null;
  cartridge_id: string;
  slug: string;
  name: string;
  description: string;
  instructions: string;
  personality: string;
  allowed_tools: string[];
  rag_kinds: string[];
  model: string;
  max_tokens: number;
  temperature: number;
  role: string;
  category: string;
  scope: string;
  extra_passthrough: Record<string, unknown>;
  variables: Array<{ key: string; value: string }>;
  schedule: {
    cron: string;
    tz: string;
    prompt: string;
    enabled: boolean;
  };
  is_active: boolean;
  rag_adjusted: number;
  template_missing: string[];
}

type ToolCatalog = Record<string, AgentToolCatalogItem[]>;

const DEFAULT_MODEL = "claude-sonnet-4-6";
const RAG_KIND_OPTIONS: Array<{ id: RagKind; label: string; hint: string }> = [
  { id: "document", label: "Documentos y políticas", hint: "Reportes, políticas y documentos cargados." },
  { id: "schema", label: "Estructura de datos", hint: "Descripción de tablas, columnas y su significado." },
];
const LEGACY_RAG_KINDS: Record<string, RagKind> = {
  policy: "document",
  runbook: "document",
  dataset: "schema",
  metric: "schema",
};
const AGENT_LIST_FILTERS: Array<{ id: AgentListFilter; label: string }> = [
  { id: "all", label: "Todos" },
  { id: "monitor", label: "Monitores" },
  { id: "control_room", label: "Control Room" },
  { id: "cartridge", label: GLOSSARY.cartridge.other },
  { id: "platform_ops", label: "Plataforma" },
];
const AGENT_CATEGORIES: Array<{ id: string; label: string }> = [
  { id: "cartridge", label: GLOSSARY.cartridge.one },
  { id: "control_room", label: "Control Room" },
  { id: "platform_ops", label: "Operación de la plataforma" },
];
const AGENT_SCOPES: Array<{ id: string; label: string }> = [
  { id: "workspace", label: "Espacio de trabajo" },
  { id: "cartridge", label: GLOSSARY.cartridge.one },
  { id: "control_room", label: "Control Room" },
];
const INPUT_CLASS = "min-h-[44px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const TEXTAREA_CLASS = "rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

function emptyDraft(cartridge = "replicon"): AgentDraft {
  return {
    id: null,
    cartridge_id: cartridge,
    slug: "",
    name: "",
    description: "",
    instructions: "",
    personality: "",
    allowed_tools: [],
    rag_kinds: [],
    model: DEFAULT_MODEL,
    max_tokens: 8192,
    temperature: PRECISION,
    role: "",
    category: "cartridge",
    scope: "workspace",
    extra_passthrough: {},
    variables: [],
    schedule: { cron: "", tz: defaultTimeZone(), prompt: "", enabled: true },
    is_active: true,
    rag_adjusted: 0,
    template_missing: [],
  };
}

function normalizeRagKinds(kinds: string[]): { kinds: RagKind[]; adjusted: number } {
  const next = new Set<RagKind>();
  let adjusted = 0;
  for (const kind of kinds) {
    if (kind === "document" || kind === "schema") {
      next.add(kind);
      continue;
    }
    adjusted += 1;
    const mapped = LEGACY_RAG_KINDS[kind];
    if (mapped) next.add(mapped);
  }
  return { kinds: Array.from(next).sort(), adjusted };
}

function optionLabel(options: Array<{ id: string; label: string }>, id: string): string {
  return options.find((option) => option.id === id)?.label ?? id;
}

function agentKey(agent: AgentRecord, index: number): string {
  return agent.id || agent.slug || `${agent.name}-${index}`;
}

function toolsLabel(agent: AgentRecord): string {
  const tools = (agent.allowed_tools ?? []).map(toolLabel);
  if (tools.length === 0) return "Sin herramientas asignadas";
  if (tools.length <= 3) return tools.join(", ");
  return `${tools.slice(0, 3).join(", ")} +${tools.length - 3}`;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function asString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function draftFromAgent(agent: AgentRecord): AgentDraft {
  const extra = asRecord(agent.extra);
  const variables = asRecord(extra.variables);
  const schedule = asRecord(extra.schedule);
  const ragFilter = asRecord(agent.rag_filter);
  const rawKinds = Array.isArray(ragFilter.kinds) ? ragFilter.kinds.filter((kind): kind is string => typeof kind === "string") : [];
  const rag = normalizeRagKinds(rawKinds);
  const cron = asString(schedule.cron || schedule.cron_expression);
  return {
    id: agent.id,
    cartridge_id: agent.cartridge_id || "replicon",
    slug: agent.slug || "",
    name: agent.name || "",
    description: agent.description || "",
    instructions: agent.instructions || "",
    personality: agent.personality || "",
    allowed_tools: [...(agent.allowed_tools ?? [])],
    rag_kinds: rag.kinds,
    model: agent.model || DEFAULT_MODEL,
    max_tokens: Number(agent.max_tokens ?? 8192),
    temperature: Number(agent.temperature ?? 0.4),
    role: asString(extra.role),
    category: asString(extra.category) || "cartridge",
    scope: asString(extra.scope) || "workspace",
    extra_passthrough: extra,
    variables: Object.entries(variables).map(([key, value]) => ({ key, value: String(value ?? "") })),
    schedule: {
      cron,
      tz: asString(schedule.tz) || (Object.keys(schedule).length ? "UTC" : defaultTimeZone()),
      prompt: asString(schedule.prompt),
      enabled: schedule.enabled !== false,
    },
    is_active: agent.is_active !== false,
    rag_adjusted: rag.adjusted,
    template_missing: [],
  };
}

function payloadFromDraft(draft: AgentDraft): AgentPayload {
  const variables: Record<string, string> = {};
  draft.variables.forEach((row) => {
    const key = row.key.trim();
    if (key) variables[key] = row.value;
  });
  const extra: Record<string, unknown> = { ...draft.extra_passthrough };
  if (draft.role.trim()) extra.role = draft.role.trim();
  else delete extra.role;
  if (draft.category.trim()) extra.category = draft.category.trim();
  else delete extra.category;
  if (draft.scope.trim()) extra.scope = draft.scope.trim();
  else delete extra.scope;
  if (Object.keys(variables).length) extra.variables = variables;
  else delete extra.variables;
  const cron = draft.schedule.cron.trim();
  const prompt = draft.schedule.prompt.trim();
  const existingSchedule = asRecord(draft.extra_passthrough.schedule);
  const hasScheduleIntent = Boolean(cron || prompt || Object.keys(existingSchedule).length);
  if (hasScheduleIntent) {
    extra.schedule = {
      ...existingSchedule,
      cron,
      tz: draft.schedule.tz.trim() || "UTC",
      prompt,
      enabled: draft.schedule.enabled,
    };
  } else {
    delete extra.schedule;
  }
  return {
    cartridge_id: draft.cartridge_id,
    slug: draft.slug.trim(),
    name: draft.name.trim(),
    description: draft.description.trim(),
    instructions: draft.instructions.trim(),
    personality: draft.personality.trim(),
    allowed_tools: draft.allowed_tools,
    rag_filter: draft.rag_kinds.length ? { kinds: draft.rag_kinds } : {},
    model: draft.model.trim() || DEFAULT_MODEL,
    max_tokens: Number.isFinite(draft.max_tokens) ? draft.max_tokens : 8192,
    temperature: Number.isFinite(draft.temperature) ? draft.temperature : 0.4,
    extra,
    is_active: draft.is_active,
  };
}

function resultText(result: Record<string, unknown>): string {
  const direct = result.output_text || result.text || result.reply;
  if (typeof direct === "string" && direct.trim()) return direct;
  return JSON.stringify(result, null, 2);
}

function operationalMessage(draft: AgentDraft): string {
  const scheduledPrompt = draft.schedule.prompt.trim();
  if (scheduledPrompt) return scheduledPrompt;
  if (draft.role === "monitor") {
    return "Ejecuta una revision operativa ahora usando tu contrato de monitor. Registra evidencia agregada, respeta recommendation_only y no hagas write-back externo.";
  }
  return "Ejecuta una revision operativa ahora con las herramientas permitidas y devuelve hallazgos, evidencia y siguientes acciones.";
}

function hasMonitorContract(draft: AgentDraft): boolean {
  return Object.keys(asRecord(draft.extra_passthrough.monitor)).length > 0;
}

function agentWarnings(draft: AgentDraft): string[] {
  const warnings: string[] = [];
  const hasCron = Boolean(draft.schedule.cron.trim());
  const hasPrompt = Boolean(draft.schedule.prompt.trim());
  if (!draft.id) warnings.push("Guarda el agente antes de ejecutarlo o usa «Guardar y ejecutar».");
  if (!draft.is_active) warnings.push("El agente está inactivo; no ejecutará tareas automáticas.");
  if (draft.allowed_tools.length === 0) warnings.push("No tiene herramientas asignadas: podrá conversar, pero no consultar datos ni reunir evidencia.");
  if (draft.role === "monitor" && !hasMonitorContract(draft)) warnings.push("Está marcado como Monitor, pero no tiene contrato de monitoreo; sus ejecuciones programadas no lo tratarán como monitor.");
  if (hasPrompt && draft.schedule.enabled && !hasCron) warnings.push("La tarea programada no tiene frecuencia; elige una en la pestaña Tareas para que se ejecute.");
  if (hasCron && customScheduleError(draft.schedule.cron) === null && draft.role !== "monitor") warnings.push("Tiene una frecuencia programada, pero solo los agentes con rol Monitor se ejecutan automáticamente.");
  if (hasCron && customScheduleError(draft.schedule.cron)) warnings.push("La programación personalizada no es válida; revísala en la pestaña Tareas.");
  if (draft.template_missing.length) warnings.push(`El perfil incluye herramientas que no están disponibles en este entorno: ${draft.template_missing.map(toolLabel).join(", ")}.`);
  return warnings;
}

export function AgentsConsole() {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [listFilter, setListFilter] = useState<AgentListFilter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<AgentDraft | null>(null);
  const [tab, setTab] = useState<AgentTab>("config");
  const [testMessage, setTestMessage] = useState("");
  const [testOutput, setTestOutput] = useState("");
  const [selectedRunId, setSelectedRunId] = useState<number | string | null>(null);

  const refreshRunsSoon = (agentId: string) => {
    queryClient.invalidateQueries({ queryKey: ["agents", agentId, "runs"] });
    if (typeof window === "undefined") return;
    window.setTimeout(() => {
      queryClient.invalidateQueries({ queryKey: ["agents", agentId, "runs"] });
    }, 1_500);
    window.setTimeout(() => {
      queryClient.invalidateQueries({ queryKey: ["agents", agentId, "runs"] });
    }, 6_000);
  };

  const agents = useQuery({
    queryKey: ["agents"],
    queryFn: listAgents,
    staleTime: 30_000,
  });

  const tools = useQuery({
    queryKey: ["agents", "tool-catalog"],
    queryFn: listAgentToolCatalog,
    staleTime: 60_000,
  });

  const runs = useQuery({
    queryKey: ["agents", selectedId, "runs"],
    queryFn: () => listAgentRuns(selectedId as string, 30),
    enabled: Boolean(selectedId && selectedId !== "_new_"),
    staleTime: 15_000,
  });

  const runDetail = useQuery({
    queryKey: ["agent-run", selectedRunId],
    queryFn: () => getAgentRun(selectedRunId as string),
    enabled: selectedRunId !== null,
    staleTime: 30_000,
  });

  const withSlug = (nextDraft: AgentDraft): AgentDraft =>
    nextDraft.id ? nextDraft : { ...nextDraft, slug: agentSlug(nextDraft.name, (agents.data ?? []).map((agent) => agent.slug)) };

  const save = useMutation({
    mutationFn: async (nextDraft: AgentDraft) => {
      const payload = payloadFromDraft(withSlug(nextDraft));
      if (!payload.slug || !payload.name || !payload.instructions) {
        throw new Error("El nombre y las instrucciones son obligatorios.");
      }
      return nextDraft.id ? updateAgent(nextDraft.id, payload) : createAgent(payload);
    },
    onSuccess: (saved) => {
      toast.success("Agente guardado. Aún no se ha ejecutado.");
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      setSelectedId(saved.id);
      setDraft(draftFromAgent(saved));
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo guardar el agente."),
  });

  const saveAndRun = useMutation({
    mutationFn: async (nextDraft: AgentDraft) => {
      const payload = payloadFromDraft(withSlug(nextDraft));
      if (!payload.slug || !payload.name || !payload.instructions) {
        throw new Error("El nombre y las instrucciones son obligatorios.");
      }
      const saved = nextDraft.id ? await updateAgent(nextDraft.id, payload) : await createAgent(payload);
      const savedDraft = draftFromAgent(saved);
      const result = await invokeAgent(saved.id, operationalMessage(savedDraft), [], { background: true });
      return { saved, result };
    },
    onSuccess: ({ saved, result }) => {
      toast.success("Agente guardado. Ejecucion iniciada.");
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      refreshRunsSoon(saved.id);
      setSelectedId(saved.id);
      setDraft(draftFromAgent(saved));
      setTab("runs");
      setTestOutput(resultText(result));
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo guardar y ejecutar."),
  });

  const status = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => updateAgentStatus(id, active),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agents"] }),
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo cambiar el estado."),
  });

  const remove = useMutation({
    mutationFn: deleteAgent,
    onSuccess: () => {
      toast.success("Agente eliminado.");
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      setSelectedId(null);
      setDraft(null);
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo eliminar."),
  });

  const invoke = useMutation({
    mutationFn: async ({ id, message, showRuns = false }: { id: string; message: string; showRuns?: boolean }) => ({
      result: await invokeAgent(id, message, [], showRuns ? { background: true } : {}),
      agentId: id,
      showRuns,
    }),
    onSuccess: ({ result, agentId, showRuns }) => {
      setTestOutput(resultText(result));
      queryClient.invalidateQueries({ queryKey: ["agents", agentId, "runs"] });
      if (showRuns) {
        refreshRunsSoon(agentId);
        setTab("runs");
      }
    },
    onError: (error) => {
      const requestId = isApiError(error) ? error.requestId : undefined;
      setTestOutput(requestId ? `No se pudo invocar el agente. Ref: ${requestId}` : "No se pudo invocar el agente.");
    },
  });

  const cartridgeIds = new Set<string>(KNOWN_CARTRIDGES);
  (agents.data ?? []).forEach((agent) => {
    if (agent.cartridge_id) cartridgeIds.add(agent.cartridge_id);
  });
  if (draft?.cartridge_id) cartridgeIds.add(draft.cartridge_id);
  const cartridgeOptions = Array.from(cartridgeIds).sort();

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (agents.data ?? []).filter((agent) => {
      const extra = asRecord(agent.extra);
      const role = asString(extra.role);
      const category = asString(extra.category);
      const filterMatch = (
        listFilter === "all"
        || (listFilter === "monitor" && role === "monitor")
        || (listFilter !== "monitor" && category === listFilter)
      );
      if (!filterMatch) return false;
      if (!needle) return true;
      return [
        agent.name,
        agent.slug,
        agent.cartridge_id,
        dataSourceName(agent.cartridge_id),
        agent.description,
        agent.instructions,
        agent.model,
        role,
        category,
        ...(agent.allowed_tools ?? []),
        ...(agent.allowed_tools ?? []).map(toolLabel),
      ]
        .filter((value): value is string => typeof value === "string")
        .some((value) => value.toLowerCase().includes(needle));
    });
  }, [agents.data, listFilter, query]);

  function selectAgent(agent: AgentRecord) {
    setSelectedId(agent.id);
    setDraft(draftFromAgent(agent));
    setTab("config");
    setTestOutput("");
    setSelectedRunId(null);
  }

  function startNew() {
    setSelectedId("_new_");
    setDraft(emptyDraft(cartridgeOptions[0] || "replicon"));
    setTab("config");
    setTestOutput("");
    setSelectedRunId(null);
  }

  if (agents.isLoading) {
    return (
      <div aria-busy="true" className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <span key={i} className="block h-44 animate-pulse rounded-lg bg-muted" aria-hidden />
        ))}
      </div>
    );
  }

  if (agents.isError) {
    return (
      <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
        <p className="font-medium text-destructive">No se pudieron cargar los agentes.</p>
        <button
          type="button"
          onClick={() => agents.refetch()}
          className="mt-2 inline-flex min-h-[44px] items-center gap-2 rounded-md border px-3 text-xs font-medium"
        >
          <RefreshCw aria-hidden className="h-4 w-4" />
          Reintentar
        </button>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">
      <aside className="space-y-4">
        <div className="rounded-lg border bg-card p-4">
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium">Buscar</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="nombre, fuente de datos, herramienta..."
              className="min-h-[44px] rounded-md border bg-background px-3 text-sm"
            />
          </label>
          <div className="mt-3 flex gap-2">
            <button
              type="button"
              onClick={startNew}
              className="inline-flex min-h-[44px] flex-1 items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground"
            >
              <Plus aria-hidden className="h-4 w-4" />
              Nuevo
            </button>
            <button
              type="button"
              onClick={() => {
                agents.refetch();
                tools.refetch();
              }}
              className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md border bg-background"
              aria-label="Refrescar agentes"
            >
              <RefreshCw aria-hidden className="h-4 w-4" />
            </button>
          </div>
        </div>

        <section aria-label="Agentes" className="space-y-2">
          <div className="flex flex-wrap gap-2">
            {AGENT_LIST_FILTERS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setListFilter(item.id)}
                className={cn(
                  "inline-flex min-h-[34px] items-center rounded-md border px-2.5 text-xs font-medium",
                  listFilter === item.id ? "border-primary bg-primary/10 text-primary" : "bg-background text-muted-foreground",
                )}
              >
                {item.label}
              </button>
            ))}
          </div>
          {filtered.length === 0 ? (
            <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">No hay agentes que coincidan.</p>
          ) : filtered.map((agent, index) => {
            const active = agent.is_active !== false;
            const extra = asRecord(agent.extra);
            const role = asString(extra.role);
            const category = asString(extra.category);
            return (
              <button
                key={agentKey(agent, index)}
                type="button"
                onClick={() => selectAgent(agent)}
                className={cn(
                  "w-full rounded-lg border bg-card p-4 text-left transition-colors hover:bg-accent/5",
                  selectedId === agent.id ? "border-primary" : "",
                )}
              >
                <div className="flex items-start gap-3">
                  <span className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
                    <Bot aria-hidden className="h-5 w-5" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold">{agent.name}</span>
                    <span className="block truncate font-mono text-xs text-muted-foreground">{agent.slug || agent.id}</span>
                  </span>
                  <span className={cn(
                    "rounded-md px-2 py-1 text-xs font-semibold",
                    active ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "bg-muted text-muted-foreground",
                  )}>
                    {active ? "Activo" : "Inactivo"}
                  </span>
                </div>
                <p className="mt-3 line-clamp-2 text-sm text-muted-foreground">{agent.description || "Sin descripción."}</p>
                <div className="mt-3 grid grid-cols-1 gap-2 text-xs">
                  <span className="rounded-md bg-muted/30 p-2">{GLOSSARY.cartridge.one}: <strong>{dataSourceName(agent.cartridge_id)}</strong></span>
                  <span className="rounded-md bg-muted/30 p-2">Tipo: <strong>{role === "monitor" ? "Monitor" : category ? optionLabel(AGENT_CATEGORIES, category) : "General"}</strong></span>
                  <span className="rounded-md bg-muted/30 p-2">{GLOSSARY.tools.other}: <strong>{toolsLabel(agent)}</strong></span>
                </div>
              </button>
            );
          })}
        </section>
      </aside>

      <section className="min-w-0 rounded-lg border bg-card">
        {draft ? (
          <AgentEditor
            draft={draft}
            setDraft={setDraft}
            tab={tab}
            setTab={setTab}
            cartridgeOptions={cartridgeOptions}
            toolCatalog={tools.data ?? {}}
            toolCatalogLoading={tools.isLoading}
            runs={runs.data ?? []}
            runsLoading={runs.isLoading}
            selectedRun={runDetail.data}
            selectedRunLoading={runDetail.isLoading}
            selectedRunId={selectedRunId}
            setSelectedRunId={setSelectedRunId}
            saving={save.isPending}
            savingAndRunning={saveAndRun.isPending}
            executing={invoke.isPending}
            onSave={() => save.mutate(draft)}
            onSaveAndRun={() => saveAndRun.mutate(draft)}
            onCancel={() => {
              setSelectedId(null);
              setDraft(null);
            }}
            onStatusToggle={() => {
              if (draft.id) status.mutate({ id: draft.id, active: !draft.is_active });
              setDraft({ ...draft, is_active: !draft.is_active });
            }}
            onDelete={() => {
              if (draft.id) remove.mutate(draft.id);
            }}
            canDelete={Boolean(draft.id)}
            onExecuteNow={() => {
              if (!draft.id) return;
              setTestOutput("");
              invoke.mutate({ id: draft.id, message: operationalMessage(draft), showRuns: true });
            }}
            testMessage={testMessage}
            setTestMessage={setTestMessage}
            testOutput={testOutput}
            invoking={invoke.isPending}
            onInvoke={() => {
              if (!draft.id || !testMessage.trim()) return;
              setTestOutput("");
              invoke.mutate({ id: draft.id, message: testMessage.trim() });
            }}
          />
        ) : (
          <div className="flex min-h-[560px] flex-col items-center justify-center gap-3 p-8 text-center">
            <Bot aria-hidden className="h-10 w-10 text-muted-foreground" />
            <h2 className="text-lg font-semibold">Selecciona o crea un agente</h2>
            <p className="max-w-md text-sm text-muted-foreground">
              Configura instrucciones, herramientas, conocimiento, datos de contexto, frecuencia, ejecuciones y pruebas.
            </p>
            <button
              type="button"
              onClick={startNew}
              className="inline-flex min-h-[44px] items-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground"
            >
              <Plus aria-hidden className="h-4 w-4" />
              Nuevo agente
            </button>
          </div>
        )}
      </section>
    </div>
  );
}

function AgentEditor(props: {
  draft: AgentDraft;
  setDraft: (draft: AgentDraft) => void;
  tab: AgentTab;
  setTab: (tab: AgentTab) => void;
  cartridgeOptions: string[];
  toolCatalog: ToolCatalog;
  toolCatalogLoading: boolean;
  runs: AgentRunRecord[];
  runsLoading: boolean;
  selectedRun?: AgentRunRecord;
  selectedRunLoading: boolean;
  selectedRunId: number | string | null;
  setSelectedRunId: (runId: number | string | null) => void;
  saving: boolean;
  savingAndRunning: boolean;
  executing: boolean;
  onSave: () => void;
  onSaveAndRun: () => void;
  onCancel: () => void;
  onStatusToggle: () => void;
  onDelete: () => void;
  canDelete: boolean;
  onExecuteNow: () => void;
  testMessage: string;
  setTestMessage: (value: string) => void;
  testOutput: string;
  invoking: boolean;
  onInvoke: () => void;
}) {
  const {
    draft,
    setDraft,
    tab,
    setTab,
    cartridgeOptions,
    toolCatalog,
    toolCatalogLoading,
    runs,
    runsLoading,
    selectedRun,
    selectedRunLoading,
    selectedRunId,
    setSelectedRunId,
    saving,
    savingAndRunning,
    executing,
    onSave,
    onSaveAndRun,
    onCancel,
    onStatusToggle,
    onDelete,
    canDelete,
    onExecuteNow,
    testMessage,
    setTestMessage,
    testOutput,
    invoking,
    onInvoke,
  } = props;

  const tabs: Array<{ id: AgentTab; label: string }> = [
    { id: "config", label: "Configuración" },
    { id: "tools", label: "Herramientas" },
    { id: "rag", label: "Conocimiento" },
    { id: "schedule", label: "Tareas" },
    { id: "runs", label: "Ejecuciones" },
    { id: "test", label: "Probar" },
  ];
  const warnings = agentWarnings(draft);
  const canExecuteSaved = Boolean(draft.id && draft.is_active);
  const busy = saving || savingAndRunning || executing || invoking;

  return (
    <div className="min-w-0">
      <header className="flex flex-col gap-3 border-b p-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{draft.id ? "Editar agente" : "Nuevo agente"}</p>
          <h2 className="truncate text-xl font-semibold">{draft.name || "Sin nombre"}</h2>
          <p className="text-xs text-muted-foreground">{GLOSSARY.cartridge.one}: {dataSourceName(draft.cartridge_id)}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={onStatusToggle} className="inline-flex min-h-[40px] items-center rounded-md border px-3 text-xs font-medium">
            {draft.is_active ? "Desactivar" : "Activar"}
          </button>
          <button type="button" onClick={onExecuteNow} disabled={!canExecuteSaved || busy} className="inline-flex min-h-[40px] items-center gap-2 rounded-md border px-3 text-xs font-medium disabled:opacity-60">
            <Play aria-hidden className="h-4 w-4" />
            {executing ? "Ejecutando..." : "Ejecutar ahora"}
          </button>
          <button type="button" onClick={onSaveAndRun} disabled={busy} className="inline-flex min-h-[40px] items-center gap-2 rounded-md border border-primary/60 px-3 text-xs font-medium text-primary disabled:opacity-60">
            <Play aria-hidden className="h-4 w-4" />
            {savingAndRunning ? "Guardando y ejecutando..." : "Guardar y ejecutar"}
          </button>
          <button type="button" onClick={onSave} disabled={busy} className="inline-flex min-h-[40px] items-center gap-2 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground disabled:opacity-60">
            <Save aria-hidden className="h-4 w-4" />
            {saving ? "Guardando..." : "Guardar"}
          </button>
          {canDelete ? (
            <button type="button" onClick={onDelete} className="inline-flex min-h-[40px] min-w-[40px] items-center justify-center rounded-md border border-destructive/40 text-destructive" aria-label="Eliminar agente">
              <Trash2 aria-hidden className="h-4 w-4" />
            </button>
          ) : null}
          <button type="button" onClick={onCancel} className="inline-flex min-h-[40px] items-center rounded-md border px-3 text-xs font-medium">
            Cerrar
          </button>
        </div>
      </header>
      <OperationalStatus draft={draft} warnings={warnings} />

      <nav className="overflow-x-auto border-b" aria-label="Editor de agente">
        <div className="flex min-w-max gap-1 px-4">
          {tabs.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setTab(item.id)}
              className={cn(
                "min-h-[44px] border-b-2 px-3 text-sm font-medium",
                tab === item.id ? "border-primary text-foreground" : "border-transparent text-muted-foreground",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      </nav>

      <div className="p-4">
        {tab === "config" ? (
          <ConfigTab
            draft={draft}
            setDraft={setDraft}
            cartridgeOptions={cartridgeOptions}
            catalog={toolCatalog}
            catalogLoading={toolCatalogLoading}
          />
        ) : tab === "tools" ? (
          <ToolsTab draft={draft} setDraft={setDraft} catalog={toolCatalog} loading={toolCatalogLoading} />
        ) : tab === "rag" ? (
          <RagTab draft={draft} setDraft={setDraft} />
        ) : tab === "schedule" ? (
          <ScheduleTab key={draft.id ?? "new"} draft={draft} setDraft={setDraft} />
        ) : tab === "runs" ? (
          <RunsTab
            runs={runs}
            loading={runsLoading}
            selectedRun={selectedRun}
            selectedRunLoading={selectedRunLoading}
            selectedRunId={selectedRunId}
            setSelectedRunId={setSelectedRunId}
          />
        ) : (
          <TestTab
            canInvoke={Boolean(draft.id)}
            message={testMessage}
            setMessage={setTestMessage}
            output={testOutput}
            invoking={invoking}
            onInvoke={onInvoke}
          />
        )}
      </div>
    </div>
  );
}

function OperationalStatus({ draft, warnings }: { draft: AgentDraft; warnings: string[] }) {
  const monitorContract = hasMonitorContract(draft);
  const hasSchedule = Boolean(draft.schedule.cron.trim());
  const latestState = draft.id ? "Guardado" : "Sin guardar";
  const executionState = draft.id && draft.is_active ? "Listo para ejecución manual" : "Aún no ejecutable";
  const scheduleState = hasSchedule && draft.schedule.enabled
    ? describeSchedule(draft.schedule.cron, draft.schedule.tz)
    : hasSchedule ? "Tarea pausada" : "Sin tarea";
  const monitorState = draft.role === "monitor"
    ? monitorContract ? "Monitor operativo" : "Monitor incompleto"
    : "Agente general";

  return (
    <section className="grid gap-3 border-b bg-muted/10 p-4 lg:grid-cols-[minmax(0,1fr)_minmax(260px,0.8fr)]">
      <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
        <StatusTile icon={<Save className="h-4 w-4" />} label="Configuración" value={latestState} tone={draft.id ? "ok" : "warn"} />
        <StatusTile icon={<Play className="h-4 w-4" />} label="Ejecución" value={executionState} tone={draft.id && draft.is_active ? "ok" : "warn"} />
        <StatusTile icon={<Clock className="h-4 w-4" />} label="Tarea" value={scheduleState} tone={hasSchedule && draft.schedule.enabled ? "ok" : "neutral"} />
        <StatusTile icon={<Bot className="h-4 w-4" />} label="Modo" value={monitorState} tone={draft.role === "monitor" && !monitorContract ? "warn" : "ok"} />
      </div>
      <div className={cn(
        "rounded-md border p-3 text-sm",
        warnings.length ? "border-amber-500/40 bg-amber-500/10 text-amber-700 dark:text-amber-100" : "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-100",
      )}>
        <div className="mb-2 flex items-center gap-2 font-medium">
          {warnings.length ? <AlertTriangle className="h-4 w-4" /> : <CheckCircle2 className="h-4 w-4" />}
          {warnings.length ? "Antes de operar" : "Listo"}
        </div>
        {warnings.length ? (
          <ul className="space-y-1 text-xs">
            {warnings.map((warning) => <li key={warning}>- {warning}</li>)}
          </ul>
        ) : (
          <p className="text-xs">Guardar solo conserva la configuración; usa «Ejecutar ahora» o «Guardar y ejecutar» para iniciar una ejecución.</p>
        )}
      </div>
    </section>
  );
}

function StatusTile({ icon, label, value, tone }: { icon: ReactNode; label: string; value: string; tone: "ok" | "warn" | "neutral" }) {
  return (
    <div className={cn(
      "rounded-md border bg-background p-3",
      tone === "ok" && "border-emerald-500/30",
      tone === "warn" && "border-amber-500/40",
    )}>
      <div className="flex items-center gap-2 text-xs uppercase tracking-wide text-muted-foreground">
        {icon}
        {label}
      </div>
      <p className="mt-2 text-sm font-medium">{value}</p>
    </div>
  );
}

function applyTemplate(draft: AgentDraft, template: AgentTemplate, catalog: ToolCatalog): AgentDraft {
  const tools = templateTools(template, catalog);
  return {
    ...draft,
    name: template.name,
    description: template.description,
    instructions: template.instructions,
    personality: template.personality,
    temperature: temperatureFor(template.style),
    allowed_tools: [...tools.available].sort(),
    rag_kinds: [...template.ragKinds].sort(),
    template_missing: tools.missing,
  };
}

function TemplatePicker({ draft, setDraft, catalog, loading }: { draft: AgentDraft; setDraft: (draft: AgentDraft) => void; catalog: ToolCatalog; loading: boolean }) {
  return (
    <section className="rounded-lg border bg-background p-4 lg:col-span-2" aria-label="Perfiles sugeridos">
      <h3 className="text-sm font-semibold">Empieza con un perfil</h3>
      <p className="mt-1 text-xs text-muted-foreground">Rellena instrucciones, herramientas y conocimiento; puedes ajustar todo antes de guardar.</p>
      <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-3">
        {AGENT_TEMPLATES.map((template) => (
          <button
            key={template.id}
            type="button"
            disabled={loading}
            data-agent-template={template.id}
            onClick={() => setDraft(applyTemplate(draft, template, catalog))}
            className="flex min-h-[96px] flex-col items-start gap-1 rounded-md border bg-card p-3 text-left text-sm hover:bg-accent/5 disabled:opacity-60"
          >
            <span className="font-medium">
              <span aria-hidden>{template.icon}</span> {template.name}
            </span>
            <span className="text-xs text-muted-foreground">{template.description}</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function ResponseStylePicker({ draft, setDraft }: { draft: AgentDraft; setDraft: (draft: AgentDraft) => void }) {
  const current = responseStyleFor(draft.temperature);
  return (
    <fieldset className="flex flex-col gap-2 text-sm lg:col-span-2">
      <legend className="mb-1.5 font-medium">Estilo de respuesta</legend>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        {RESPONSE_STYLES.map((style) => (
          <label key={style.id} className={cn("flex min-h-[64px] cursor-pointer gap-3 rounded-md border bg-background p-3", current === style.id && "border-primary bg-primary/5")}>
            <input
              type="radio"
              name="agent_response_style"
              value={style.id}
              checked={current === style.id}
              onChange={() => setDraft({ ...draft, temperature: style.temperature })}
              className="mt-1 h-4 w-4"
            />
            <span>
              <span className="block font-medium">{style.label}</span>
              <span className="block text-xs text-muted-foreground">{style.hint}</span>
            </span>
          </label>
        ))}
        {current === "current" ? (
          <label className="flex min-h-[64px] gap-3 rounded-md border border-primary bg-primary/5 p-3">
            <input type="radio" name="agent_response_style" value="current" checked readOnly className="mt-1 h-4 w-4" />
            <span>
              <span className="block font-medium">{CURRENT_STYLE_LABEL}</span>
              <span className="block text-xs text-muted-foreground">Se conserva hasta que elijas otro estilo.</span>
            </span>
          </label>
        ) : null}
      </div>
    </fieldset>
  );
}

function withCurrent(options: Array<{ id: string; label: string }>, value: string): Array<{ id: string; label: string }> {
  if (!value || options.some((option) => option.id === value)) return options;
  return [...options, { id: value, label: `${value} (valor actual)` }];
}

function ConfigTab({
  draft,
  setDraft,
  cartridgeOptions,
  catalog,
  catalogLoading,
}: {
  draft: AgentDraft;
  setDraft: (draft: AgentDraft) => void;
  cartridgeOptions: string[];
  catalog: ToolCatalog;
  catalogLoading: boolean;
}) {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {draft.id ? null : <TemplatePicker draft={draft} setDraft={setDraft} catalog={catalog} loading={catalogLoading} />}
      <Field label="Nombre">
        <input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} className={INPUT_CLASS} />
      </Field>
      <Field label={GLOSSARY.cartridge.one}>
        <select value={draft.cartridge_id} onChange={(event) => setDraft({ ...draft, cartridge_id: event.target.value })} className={INPUT_CLASS}>
          {cartridgeOptions.map((id) => <option key={id} value={id}>{dataSourceName(id)}</option>)}
        </select>
      </Field>
      <Field label="Rol operativo">
        <select value={draft.role} onChange={(event) => setDraft({ ...draft, role: event.target.value })} className={INPUT_CLASS}>
          <option value="">General</option>
          <option value="monitor">Monitor</option>
        </select>
      </Field>
      <Field label="Categoría">
        <select value={draft.category} onChange={(event) => setDraft({ ...draft, category: event.target.value })} className={INPUT_CLASS}>
          {withCurrent(AGENT_CATEGORIES, draft.category).map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select>
      </Field>
      <Field label={GLOSSARY.scope.one}>
        <select value={draft.scope} onChange={(event) => setDraft({ ...draft, scope: event.target.value })} className={INPUT_CLASS}>
          {withCurrent(AGENT_SCOPES, draft.scope).map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
        </select>
      </Field>
      <ResponseStylePicker draft={draft} setDraft={setDraft} />
      <Field label="Descripción" wide>
        <textarea value={draft.description} onChange={(event) => setDraft({ ...draft, description: event.target.value })} className={cn(TEXTAREA_CLASS, "min-h-24")} />
      </Field>
      <Field label="Instrucciones" wide>
        <textarea value={draft.instructions} onChange={(event) => setDraft({ ...draft, instructions: event.target.value })} className={cn(TEXTAREA_CLASS, "min-h-40")} />
      </Field>
      <Field label="Personalidad" wide>
        <textarea value={draft.personality} onChange={(event) => setDraft({ ...draft, personality: event.target.value })} className={cn(TEXTAREA_CLASS, "min-h-28")} />
      </Field>
    </div>
  );
}

function ToolsTab({ draft, setDraft, catalog, loading }: { draft: AgentDraft; setDraft: (draft: AgentDraft) => void; catalog: ToolCatalog; loading: boolean }) {
  const selected = new Set(draft.allowed_tools);
  function toggle(tool: string) {
    const next = new Set(selected);
    if (next.has(tool)) next.delete(tool);
    else next.add(tool);
    setDraft({ ...draft, allowed_tools: Array.from(next).sort(), template_missing: [] });
  }
  if (loading) return <SkeletonRows rows={6} />;
  const servers = Object.keys(catalog)
    .filter((server) => server !== "infra" || draft.allowed_tools.some((tool) => splitToolId(tool).server === "infra"))
    .sort();
  const offered = new Set(servers.flatMap((server) => (catalog[server] ?? []).map((tool) => `${server}__${tool.name}`)));
  const unavailable = draft.allowed_tools.filter((tool) => !offered.has(tool));
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Wrench aria-hidden className="h-4 w-4" />
        {draft.allowed_tools.length === 1 ? "1 herramienta asignada" : `${draft.allowed_tools.length} herramientas asignadas`}
      </div>
      {unavailable.length ? (
        <section className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm">
          <h3 className="font-medium">Asignadas, pero no disponibles en este entorno</h3>
          <ul className="mt-2 space-y-1">
            {unavailable.map((tool) => (
              <li key={tool} className="flex items-center justify-between gap-2">
                <span title={toolTooltip(tool)}>{toolLabel(tool)}</span>
                <button type="button" onClick={() => toggle(tool)} className="min-h-[36px] rounded-md border px-3 text-xs">Quitar</button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {servers.length ? servers.map((server) => (
        <section key={server} className="rounded-lg border bg-background p-4">
          <h3 className="text-sm font-semibold">{toolServerLabel(server)}</h3>
          <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2">
            {(catalog[server] ?? []).map((tool) => {
              const id = `${server}__${tool.name}`;
              const readOnly = tool.risk_level === "read";
              return (
                <label key={id} title={toolTooltip(id, tool.description)} className="flex min-h-[56px] gap-3 rounded-md border bg-card p-3 text-sm">
                  <input type="checkbox" checked={selected.has(id)} onChange={() => toggle(id)} className="mt-1 h-4 w-4" />
                  <span>
                    <span className="block font-medium">{toolLabel(id)}</span>
                    <span className="text-xs text-muted-foreground">
                      {readOnly ? "Solo lectura" : tool.requires_approval ? "Requiere aprobación" : "Puede registrar cambios"}
                    </span>
                  </span>
                </label>
              );
            })}
          </div>
        </section>
      )) : (
        <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">No hay herramientas disponibles.</p>
      )}
    </div>
  );
}

function RagTab({ draft, setDraft }: { draft: AgentDraft; setDraft: (draft: AgentDraft) => void }) {
  const selected = new Set<string>(draft.rag_kinds);
  return (
    <div className="space-y-4">
      <section className="rounded-lg border bg-background p-4">
        <h3 className="text-sm font-semibold">Qué conocimiento puede consultar</h3>
        {draft.rag_adjusted ? (
          <p role="status" className="mt-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-xs">
            Se ajustaron tipos de conocimiento anteriores a las categorías vigentes. Guarda el agente para conservar el ajuste.
          </p>
        ) : null}
        <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2">
          {RAG_KIND_OPTIONS.map((kind) => (
            <label key={kind.id} className="flex min-h-[56px] gap-3 rounded-md border bg-card p-3 text-sm">
              <input
                type="checkbox"
                checked={selected.has(kind.id)}
                onChange={() => {
                  const next = new Set(selected);
                  if (next.has(kind.id)) next.delete(kind.id);
                  else next.add(kind.id);
                  setDraft({ ...draft, rag_kinds: Array.from(next).sort() });
                }}
                className="mt-1 h-4 w-4"
              />
              <span>
                <span className="block font-medium">{kind.label}</span>
                <span className="text-xs text-muted-foreground">{kind.hint}</span>
              </span>
            </label>
          ))}
        </div>
        {selected.size === 0 ? <p className="mt-2 text-xs text-muted-foreground">Sin selección: consulta todo el conocimiento disponible.</p> : null}
      </section>
      <VariablesEditor draft={draft} setDraft={setDraft} />
    </div>
  );
}

function VariablesEditor({ draft, setDraft }: { draft: AgentDraft; setDraft: (draft: AgentDraft) => void }) {
  return (
    <section className="rounded-lg border bg-background p-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">{GLOSSARY.contextData.one}</h3>
        <button type="button" onClick={() => setDraft({ ...draft, variables: [...draft.variables, { key: "", value: "" }] })} className="min-h-[36px] rounded-md border px-3 text-xs">
          Agregar
        </button>
      </div>
      <div className="mt-3 space-y-2">
        {draft.variables.length ? draft.variables.map((row, index) => (
          <div key={index} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_40px] gap-2">
            <input value={row.key} onChange={(event) => updateVariable(draft, setDraft, index, "key", event.target.value)} placeholder="dato" aria-label="Nombre del dato" className={cn(INPUT_CLASS, "font-mono")} />
            <input value={row.value} onChange={(event) => updateVariable(draft, setDraft, index, "value", event.target.value)} placeholder="valor" aria-label="Valor del dato" className={cn(INPUT_CLASS, "font-mono")} />
            <button type="button" onClick={() => setDraft({ ...draft, variables: draft.variables.filter((_, i) => i !== index) })} className="min-h-[44px] rounded-md border text-sm" aria-label="Quitar dato">x</button>
          </div>
        )) : (
          <p className="rounded-md border bg-muted/30 p-3 text-sm text-muted-foreground">Sin datos de contexto.</p>
        )}
      </div>
    </section>
  );
}

function updateVariable(draft: AgentDraft, setDraft: (draft: AgentDraft) => void, index: number, field: "key" | "value", value: string) {
  setDraft({
    ...draft,
    variables: draft.variables.map((row, i) => i === index ? { ...row, [field]: value } : row),
  });
}

function ScheduleTab({ draft, setDraft }: { draft: AgentDraft; setDraft: (draft: AgentDraft) => void }) {
  const hasCron = Boolean(draft.schedule.cron.trim());
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <FrequencyPicker
        name="agent_frequency"
        className="lg:col-span-2"
        value={{ cron: draft.schedule.cron, timeZone: draft.schedule.tz }}
        onChange={(next) =>
          setDraft({
            ...draft,
            schedule: {
              ...draft.schedule,
              cron: next.cron,
              tz: next.timeZone,
              enabled: next.cron.trim() ? (draft.schedule.cron.trim() ? draft.schedule.enabled : true) : false,
            },
          })
        }
      />
      <Field label="Instrucción de la tarea programada" wide>
        <textarea value={draft.schedule.prompt} onChange={(event) => setDraft({ ...draft, schedule: { ...draft.schedule, prompt: event.target.value } })} className={cn(TEXTAREA_CLASS, "min-h-36")} />
      </Field>
      {hasCron ? (
        <label className="inline-flex min-h-[44px] items-center gap-2 rounded-md border bg-background px-3 text-sm">
          <input type="checkbox" checked={draft.schedule.enabled} onChange={(event) => setDraft({ ...draft, schedule: { ...draft.schedule, enabled: event.target.checked } })} />
          Tarea activa
        </label>
      ) : null}
    </div>
  );
}

function RunsTab(props: {
  runs: AgentRunRecord[];
  loading: boolean;
  selectedRun?: AgentRunRecord;
  selectedRunLoading: boolean;
  selectedRunId: number | string | null;
  setSelectedRunId: (runId: number | string | null) => void;
}) {
  const { runs, loading, selectedRun, selectedRunLoading, selectedRunId, setSelectedRunId } = props;
  if (loading) return <SkeletonRows rows={6} />;
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[0.9fr_1.1fr]">
      <div className="overflow-x-auto rounded-lg border bg-background">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
            <tr>
              <th className="px-3 py-2">Ejecución</th>
              <th className="px-3 py-2">Estado</th>
              <th className="px-3 py-2">Inicio</th>
              <th className="px-3 py-2">{GLOSSARY.tools.other}</th>
            </tr>
          </thead>
          <tbody>
            {runs.length ? runs.map((run) => (
              <tr key={String(run.id)} className={cn("border-t", selectedRunId === run.id ? "bg-primary/5" : "")}>
                <td className="px-3 py-2">
                  <button type="button" onClick={() => setSelectedRunId(run.id)} className="font-mono text-xs underline-offset-2 hover:underline">#{run.id}</button>
                </td>
                <td className="px-3 py-2">{run.status || "-"}</td>
                <td className="px-3 py-2 text-xs text-muted-foreground">{formatDate(run.started_at)}</td>
                <td className="px-3 py-2 text-xs">{run.n_tool_calls ?? 0}</td>
              </tr>
            )) : (
              <tr><td colSpan={4} className="px-3 py-8 text-center text-sm text-muted-foreground">Sin ejecuciones registradas.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="rounded-lg border bg-background p-4">
        <h3 className="text-sm font-semibold">Detalle</h3>
        {selectedRunLoading ? <SkeletonRows rows={4} /> : selectedRun ? (
          <pre className="mt-3 max-h-[460px] overflow-auto rounded-md bg-muted/40 p-3 text-xs">{JSON.stringify(selectedRun, null, 2)}</pre>
        ) : (
          <p className="mt-3 text-sm text-muted-foreground">Selecciona una ejecución.</p>
        )}
      </div>
    </div>
  );
}

function TestTab(props: {
  canInvoke: boolean;
  message: string;
  setMessage: (value: string) => void;
  output: string;
  invoking: boolean;
  onInvoke: () => void;
}) {
  const { canInvoke, message, setMessage, output, invoking, onInvoke } = props;
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[0.8fr_1.2fr]">
      <section className="rounded-lg border bg-background p-4">
        <div className="flex items-center gap-2">
          <MessageSquareText aria-hidden className="h-4 w-4 text-primary" />
          <h3 className="text-sm font-semibold">Mensaje de prueba</h3>
        </div>
        <textarea value={message} onChange={(event) => setMessage(event.target.value)} className={cn(TEXTAREA_CLASS, "mt-3 min-h-36 w-full")} />
        <button type="button" disabled={!canInvoke || !message.trim() || invoking} onClick={onInvoke} className="mt-3 inline-flex min-h-[44px] items-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-60">
          {invoking ? "Ejecutando..." : "Ejecutar prueba"}
        </button>
      </section>
      <section className="rounded-lg border bg-background p-4">
        <h3 className="text-sm font-semibold">Respuesta</h3>
        <pre className="mt-3 min-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-muted/40 p-3 text-xs">{output || "Sin respuesta todavía."}</pre>
      </section>
    </div>
  );
}

function Field({ label, children, wide }: { label: string; children: ReactNode; wide?: boolean }) {
  return (
    <label className={cn("flex flex-col gap-1.5 text-sm", wide ? "lg:col-span-2" : "")}>
      <span className="font-medium">{label}</span>
      {children}
    </label>
  );
}

function SkeletonRows({ rows = 4 }: { rows?: number }) {
  return (
    <div aria-busy="true" className="space-y-2">
      {Array.from({ length: rows }).map((_, index) => (
        <span key={index} className="block h-12 animate-pulse rounded bg-muted" aria-hidden />
      ))}
    </div>
  );
}

function formatDate(value: string | null | undefined): string {
  if (!value) return "-";
  const parsed = Date.parse(value);
  if (!Number.isFinite(parsed)) return value;
  return new Intl.DateTimeFormat("es", { dateStyle: "short", timeStyle: "short" }).format(new Date(parsed));
}
