"use client";

import { useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, MessageSquareText, Plus, RefreshCw, Save, Trash2, Wrench } from "lucide-react";
import { toast } from "sonner";

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
} from "@/lib/admin-surfaces";
import { KNOWN_CARTRIDGES } from "@/lib/cartridges";
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
  variables: Array<{ key: string; value: string }>;
  schedule: {
    cron: string;
    tz: string;
    prompt: string;
    enabled: boolean;
  };
  is_active: boolean;
}

const DEFAULT_MODEL = "claude-sonnet-4-6";
const RAG_KINDS = ["dataset", "schema", "metric", "policy", "runbook"];
const AGENT_LIST_FILTERS: Array<{ id: AgentListFilter; label: string }> = [
  { id: "all", label: "Todos" },
  { id: "monitor", label: "Monitores" },
  { id: "control_room", label: "Control Room" },
  { id: "cartridge", label: "Cartuchos" },
  { id: "platform_ops", label: "Plataforma" },
];
const AGENT_CATEGORIES = ["cartridge", "control_room", "platform_ops"];
const AGENT_SCOPES = ["workspace", "cartridge", "control_room"];
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
    temperature: 0.4,
    role: "",
    category: "cartridge",
    scope: "workspace",
    variables: [],
    schedule: { cron: "", tz: "UTC", prompt: "", enabled: true },
    is_active: true,
  };
}

function agentKey(agent: AgentRecord, index: number): string {
  return agent.id || agent.slug || `${agent.name}-${index}`;
}

function toolsLabel(agent: AgentRecord): string {
  const tools = agent.allowed_tools ?? [];
  if (tools.length === 0) return "Sin herramientas fijadas";
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
  const kinds = Array.isArray(ragFilter.kinds) ? ragFilter.kinds.filter((kind): kind is string => typeof kind === "string") : [];
  return {
    id: agent.id,
    cartridge_id: agent.cartridge_id || "replicon",
    slug: agent.slug || "",
    name: agent.name || "",
    description: agent.description || "",
    instructions: agent.instructions || "",
    personality: agent.personality || "",
    allowed_tools: [...(agent.allowed_tools ?? [])],
    rag_kinds: kinds,
    model: agent.model || DEFAULT_MODEL,
    max_tokens: Number(agent.max_tokens ?? 8192),
    temperature: Number(agent.temperature ?? 0.4),
    role: asString(extra.role),
    category: asString(extra.category) || "cartridge",
    scope: asString(extra.scope) || "workspace",
    variables: Object.entries(variables).map(([key, value]) => ({ key, value: String(value ?? "") })),
    schedule: {
      cron: asString(schedule.cron || schedule.cron_expression),
      tz: asString(schedule.tz) || "UTC",
      prompt: asString(schedule.prompt),
      enabled: schedule.enabled !== false,
    },
    is_active: agent.is_active !== false,
  };
}

function payloadFromDraft(draft: AgentDraft): AgentPayload {
  const variables: Record<string, string> = {};
  draft.variables.forEach((row) => {
    const key = row.key.trim();
    if (key) variables[key] = row.value;
  });
  const extra: Record<string, unknown> = {};
  if (draft.role.trim()) extra.role = draft.role.trim();
  if (draft.category.trim()) extra.category = draft.category.trim();
  if (draft.scope.trim()) extra.scope = draft.scope.trim();
  if (Object.keys(variables).length) extra.variables = variables;
  if (draft.schedule.cron.trim() || draft.schedule.prompt.trim() || draft.schedule.tz.trim() || !draft.schedule.enabled) {
    extra.schedule = {
      cron: draft.schedule.cron.trim(),
      tz: draft.schedule.tz.trim() || "UTC",
      prompt: draft.schedule.prompt.trim(),
      enabled: draft.schedule.enabled,
    };
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

  const save = useMutation({
    mutationFn: async (nextDraft: AgentDraft) => {
      const payload = payloadFromDraft(nextDraft);
      if (!payload.slug || !payload.name || !payload.instructions) {
        throw new Error("Slug, nombre e instrucciones son obligatorios.");
      }
      return nextDraft.id ? updateAgent(nextDraft.id, payload) : createAgent(payload);
    },
    onSuccess: (saved) => {
      toast.success("Agente guardado.");
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      setSelectedId(saved.id);
      setDraft(draftFromAgent(saved));
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo guardar el agente."),
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
    mutationFn: ({ id, message }: { id: string; message: string }) => invokeAgent(id, message),
    onSuccess: (result) => {
      setTestOutput(result.output_text || result.text || JSON.stringify(result, null, 2));
      queryClient.invalidateQueries({ queryKey: ["agents", selectedId, "runs"] });
    },
    onError: (error) => setTestOutput(error instanceof Error ? error.message : "No se pudo invocar el agente."),
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
        agent.description,
        agent.instructions,
        agent.model,
        role,
        category,
        ...(agent.allowed_tools ?? []),
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
              placeholder="nombre, cartucho, herramienta..."
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
                  <span className="rounded-md bg-muted/30 p-2">Cartucho: <strong>{agent.cartridge_id || "-"}</strong></span>
                  <span className="rounded-md bg-muted/30 p-2">Tipo: <strong>{role === "monitor" ? "Monitor" : category || "general"}</strong></span>
                  <span className="rounded-md bg-muted/30 p-2">Tools: <strong>{toolsLabel(agent)}</strong></span>
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
            onSave={() => save.mutate(draft)}
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
              Aquí puedes configurar instrucciones, herramientas MCP, RAG, variables, schedule, ejecuciones y prueba manual.
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
  toolCatalog: Record<string, Array<{ name: string; description?: string }>>;
  toolCatalogLoading: boolean;
  runs: AgentRunRecord[];
  runsLoading: boolean;
  selectedRun?: AgentRunRecord;
  selectedRunLoading: boolean;
  selectedRunId: number | string | null;
  setSelectedRunId: (runId: number | string | null) => void;
  saving: boolean;
  onSave: () => void;
  onCancel: () => void;
  onStatusToggle: () => void;
  onDelete: () => void;
  canDelete: boolean;
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
    onSave,
    onCancel,
    onStatusToggle,
    onDelete,
    canDelete,
    testMessage,
    setTestMessage,
    testOutput,
    invoking,
    onInvoke,
  } = props;

  const tabs: Array<{ id: AgentTab; label: string }> = [
    { id: "config", label: "Configuración" },
    { id: "tools", label: "Tools" },
    { id: "rag", label: "RAG" },
    { id: "schedule", label: "Tareas" },
    { id: "runs", label: "Ejecuciones" },
    { id: "test", label: "Probar" },
  ];

  return (
    <div className="min-w-0">
      <header className="flex flex-col gap-3 border-b p-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{draft.id ? "Editar agente" : "Nuevo agente"}</p>
          <h2 className="truncate text-xl font-semibold">{draft.name || "Sin nombre"}</h2>
          <p className="font-mono text-xs text-muted-foreground">{draft.slug || "slug pendiente"}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={onStatusToggle} className="inline-flex min-h-[40px] items-center rounded-md border px-3 text-xs font-medium">
            {draft.is_active ? "Desactivar" : "Activar"}
          </button>
          <button type="button" onClick={onSave} disabled={saving} className="inline-flex min-h-[40px] items-center gap-2 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground disabled:opacity-60">
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
          <ConfigTab draft={draft} setDraft={setDraft} cartridgeOptions={cartridgeOptions} />
        ) : tab === "tools" ? (
          <ToolsTab draft={draft} setDraft={setDraft} catalog={toolCatalog} loading={toolCatalogLoading} />
        ) : tab === "rag" ? (
          <RagTab draft={draft} setDraft={setDraft} />
        ) : tab === "schedule" ? (
          <ScheduleTab draft={draft} setDraft={setDraft} />
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

function ConfigTab({ draft, setDraft, cartridgeOptions }: { draft: AgentDraft; setDraft: (draft: AgentDraft) => void; cartridgeOptions: string[] }) {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Field label="Cartucho">
        <select value={draft.cartridge_id} onChange={(event) => setDraft({ ...draft, cartridge_id: event.target.value })} className={INPUT_CLASS}>
          {cartridgeOptions.map((id) => <option key={id} value={id}>{id}</option>)}
        </select>
      </Field>
      <Field label="Slug">
        <input value={draft.slug} onChange={(event) => setDraft({ ...draft, slug: event.target.value })} className={cn(INPUT_CLASS, "font-mono")} />
      </Field>
      <Field label="Nombre">
        <input value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} className={INPUT_CLASS} />
      </Field>
      <Field label="Modelo">
        <input value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} className={cn(INPUT_CLASS, "font-mono")} />
      </Field>
      <Field label="Max tokens">
        <input type="number" value={draft.max_tokens} onChange={(event) => setDraft({ ...draft, max_tokens: Number(event.target.value) })} className={INPUT_CLASS} />
      </Field>
      <Field label="Temperatura">
        <input type="number" min={0} max={2} step={0.1} value={draft.temperature} onChange={(event) => setDraft({ ...draft, temperature: Number(event.target.value) })} className={INPUT_CLASS} />
      </Field>
      <Field label="Rol operativo">
        <select value={draft.role} onChange={(event) => setDraft({ ...draft, role: event.target.value })} className={INPUT_CLASS}>
          <option value="">General</option>
          <option value="monitor">Monitor</option>
        </select>
      </Field>
      <Field label="Categoría">
        <select value={draft.category} onChange={(event) => setDraft({ ...draft, category: event.target.value })} className={INPUT_CLASS}>
          {AGENT_CATEGORIES.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
      </Field>
      <Field label="Scope">
        <select value={draft.scope} onChange={(event) => setDraft({ ...draft, scope: event.target.value })} className={INPUT_CLASS}>
          {AGENT_SCOPES.map((item) => <option key={item} value={item}>{item}</option>)}
        </select>
      </Field>
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

function ToolsTab({ draft, setDraft, catalog, loading }: { draft: AgentDraft; setDraft: (draft: AgentDraft) => void; catalog: Record<string, Array<{ name: string; description?: string }>>; loading: boolean }) {
  const selected = new Set(draft.allowed_tools);
  function toggle(tool: string) {
    const next = new Set(selected);
    if (next.has(tool)) next.delete(tool);
    else next.add(tool);
    setDraft({ ...draft, allowed_tools: Array.from(next).sort() });
  }
  if (loading) return <SkeletonRows rows={6} />;
  const servers = Object.keys(catalog).sort();
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Wrench aria-hidden className="h-4 w-4" />
        {draft.allowed_tools.length} tool(s) asignados
      </div>
      {servers.length ? servers.map((server) => (
        <section key={server} className="rounded-lg border bg-background p-4">
          <h3 className="font-mono text-sm font-semibold">{server}</h3>
          <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2">
            {(catalog[server] ?? []).map((tool) => {
              const id = `${server}__${tool.name}`;
              return (
                <label key={id} className="flex min-h-[72px] gap-3 rounded-md border bg-card p-3 text-sm">
                  <input type="checkbox" checked={selected.has(id)} onChange={() => toggle(id)} className="mt-1 h-4 w-4" />
                  <span>
                    <span className="block font-medium">{tool.name}</span>
                    <span className="line-clamp-2 text-xs text-muted-foreground">{tool.description || "Sin descripción."}</span>
                  </span>
                </label>
              );
            })}
          </div>
        </section>
      )) : (
        <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">Sin tools disponibles.</p>
      )}
    </div>
  );
}

function RagTab({ draft, setDraft }: { draft: AgentDraft; setDraft: (draft: AgentDraft) => void }) {
  const selected = new Set(draft.rag_kinds);
  return (
    <div className="space-y-4">
      <section className="rounded-lg border bg-background p-4">
        <h3 className="text-sm font-semibold">Filtro RAG</h3>
        <div className="mt-3 flex flex-wrap gap-2">
          {RAG_KINDS.map((kind) => (
            <label key={kind} className="inline-flex min-h-[40px] items-center gap-2 rounded-md border bg-card px-3 text-sm">
              <input
                type="checkbox"
                checked={selected.has(kind)}
                onChange={() => {
                  const next = new Set(selected);
                  if (next.has(kind)) next.delete(kind);
                  else next.add(kind);
                  setDraft({ ...draft, rag_kinds: Array.from(next).sort() });
                }}
              />
              {kind}
            </label>
          ))}
        </div>
      </section>
      <VariablesEditor draft={draft} setDraft={setDraft} />
    </div>
  );
}

function VariablesEditor({ draft, setDraft }: { draft: AgentDraft; setDraft: (draft: AgentDraft) => void }) {
  return (
    <section className="rounded-lg border bg-background p-4">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-semibold">Variables</h3>
        <button type="button" onClick={() => setDraft({ ...draft, variables: [...draft.variables, { key: "", value: "" }] })} className="min-h-[36px] rounded-md border px-3 text-xs">
          Agregar
        </button>
      </div>
      <div className="mt-3 space-y-2">
        {draft.variables.length ? draft.variables.map((row, index) => (
          <div key={index} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_40px] gap-2">
            <input value={row.key} onChange={(event) => updateVariable(draft, setDraft, index, "key", event.target.value)} placeholder="clave" className={cn(INPUT_CLASS, "font-mono")} />
            <input value={row.value} onChange={(event) => updateVariable(draft, setDraft, index, "value", event.target.value)} placeholder="valor" className={cn(INPUT_CLASS, "font-mono")} />
            <button type="button" onClick={() => setDraft({ ...draft, variables: draft.variables.filter((_, i) => i !== index) })} className="min-h-[44px] rounded-md border text-sm">x</button>
          </div>
        )) : (
          <p className="rounded-md border bg-muted/30 p-3 text-sm text-muted-foreground">Sin variables.</p>
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
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Field label="Cron">
        <input value={draft.schedule.cron} onChange={(event) => setDraft({ ...draft, schedule: { ...draft.schedule, cron: event.target.value } })} placeholder="0 8 * * 1-5" className={cn(INPUT_CLASS, "font-mono")} />
      </Field>
      <Field label="Zona horaria">
        <input value={draft.schedule.tz} onChange={(event) => setDraft({ ...draft, schedule: { ...draft.schedule, tz: event.target.value } })} className={cn(INPUT_CLASS, "font-mono")} />
      </Field>
      <Field label="Tarea programada" wide>
        <textarea value={draft.schedule.prompt} onChange={(event) => setDraft({ ...draft, schedule: { ...draft.schedule, prompt: event.target.value } })} className={cn(TEXTAREA_CLASS, "min-h-36")} />
      </Field>
      <label className="inline-flex min-h-[44px] items-center gap-2 rounded-md border bg-background px-3 text-sm">
        <input type="checkbox" checked={draft.schedule.enabled} onChange={(event) => setDraft({ ...draft, schedule: { ...draft.schedule, enabled: event.target.checked } })} />
        Tarea activa
      </label>
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
              <th className="px-3 py-2">Run</th>
              <th className="px-3 py-2">Estado</th>
              <th className="px-3 py-2">Inicio</th>
              <th className="px-3 py-2">Tools</th>
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
          <p className="mt-3 text-sm text-muted-foreground">Selecciona un run.</p>
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
