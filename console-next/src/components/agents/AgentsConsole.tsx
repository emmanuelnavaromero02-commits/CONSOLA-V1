"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  CheckCircle2,
  MessageSquareText,
  Plus,
  RefreshCw,
  Save,
  Send,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";

import {
  createAgent,
  deleteAgent,
  getAgentRunDetail,
  invokeAgentStream,
  listAgentRuns,
  listAgents,
  listAgentToolCatalog,
  updateAgent,
  updateAgentStatus,
  type AgentChatMessage,
  type AgentExtra,
  type AgentRecord,
  type AgentRagFilter,
  type AgentRun,
  type AgentRunDetail,
  type AgentStreamEvent,
  type AgentTool,
  type AgentUpsertPayload,
} from "@/lib/admin-surfaces";
import { cn } from "@/lib/utils";

const KNOWN_CARTRIDGES = ["replicon", "sap_hcm", "sap_s4hana", "sap_successfactors"] as const;
const MODEL_OPTIONS = [
  "claude-sonnet-4-6",
  "claude-opus-4-1",
  "gpt-5.4",
  "gpt-5.4-mini",
  "gpt-4.1",
] as const;
const RAG_KINDS = ["document", "schema", "metric", "decision"] as const;

type AgentTab = "identity" | "instructions" | "tools" | "model" | "context" | "extra" | "runs";
type SelectionId = string | "_new_" | null;

interface AgentDraft {
  id?: string;
  cartridge_id: string;
  slug: string;
  name: string;
  description: string;
  instructions: string;
  personality: string;
  allowed_tools: string[];
  rag_filter: AgentRagFilter;
  model: string;
  max_tokens: number;
  temperature: number;
  extra: AgentExtra;
  is_active: boolean;
}

interface TestLine {
  id: string;
  role: "user" | "assistant" | "tool" | "error";
  content: string;
}

function createEmptyDraft(cartridge = "replicon"): AgentDraft {
  return {
    cartridge_id: cartridge,
    slug: "",
    name: "",
    description: "",
    instructions: "",
    personality: "",
    allowed_tools: [],
    rag_filter: {},
    model: "claude-sonnet-4-6",
    max_tokens: 8192,
    temperature: 0.4,
    extra: { variables: {}, schedule: { enabled: true } },
    is_active: true,
  };
}

function agentToDraft(agent: AgentRecord): AgentDraft {
  return {
    id: agent.id,
    cartridge_id: agent.cartridge_id || "replicon",
    slug: agent.slug || "",
    name: agent.name || "",
    description: agent.description || "",
    instructions: agent.instructions || "",
    personality: agent.personality || "",
    allowed_tools: agent.allowed_tools ?? [],
    rag_filter: agent.rag_filter ?? {},
    model: agent.model || "claude-sonnet-4-6",
    max_tokens: agent.max_tokens ?? 8192,
    temperature: agent.temperature ?? 0.4,
    extra: normaliseExtra(agent.extra),
    is_active: agent.is_active !== false,
  };
}

function normaliseExtra(extra: AgentExtra | null | undefined): AgentExtra {
  const variables = extra?.variables && typeof extra.variables === "object" ? extra.variables : {};
  const schedule = extra?.schedule && typeof extra.schedule === "object" ? extra.schedule : { enabled: true };
  return { ...(extra ?? {}), variables, schedule };
}

function buildPayload(draft: AgentDraft): AgentUpsertPayload {
  const variables = Object.fromEntries(
    Object.entries(draft.extra.variables ?? {})
      .map(([key, value]) => [key.trim(), String(value)])
      .filter(([key]) => key),
  );
  const schedule = draft.extra.schedule ?? {};
  const extra: AgentExtra = {
    ...draft.extra,
    variables,
    schedule: Object.fromEntries(
      Object.entries(schedule).filter(([, value]) => value !== "" && value !== undefined && value !== null),
    ),
  };

  return {
    cartridge_id: draft.cartridge_id.trim(),
    slug: draft.slug.trim(),
    name: draft.name.trim(),
    description: draft.description.trim(),
    instructions: draft.instructions,
    personality: draft.personality,
    allowed_tools: [...new Set(draft.allowed_tools)].sort(),
    rag_filter: draft.rag_filter,
    model: draft.model,
    max_tokens: Math.max(1, Math.floor(draft.max_tokens || 8192)),
    temperature: Number.isFinite(draft.temperature) ? draft.temperature : 0.4,
    extra,
    is_active: draft.is_active,
  };
}

function agentKey(agent: AgentRecord, index: number): string {
  return agent.id || agent.slug || `${agent.name}-${index}`;
}

function agentToolsLabel(agent: AgentRecord): string {
  const tools = agent.allowed_tools ?? [];
  if (!tools.length) return "Sin tools";
  if (tools.length <= 2) return tools.join(", ");
  return `${tools.slice(0, 2).join(", ")} +${tools.length - 2}`;
}

export function AgentsConsole() {
  const queryClient = useQueryClient();
  const abortRef = useRef<AbortController | null>(null);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<SelectionId>("_new_");
  const [activeTab, setActiveTab] = useState<AgentTab>("identity");
  const [draft, setDraft] = useState<AgentDraft>(() => createEmptyDraft());
  const [deleteArmed, setDeleteArmed] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [testInput, setTestInput] = useState("");
  const [testLines, setTestLines] = useState<TestLine[]>([]);
  const [testHistory, setTestHistory] = useState<AgentChatMessage[]>([]);
  const [testing, setTesting] = useState(false);

  const agents = useQuery({
    queryKey: ["agents"],
    queryFn: listAgents,
    staleTime: 30_000,
  });
  const toolCatalog = useQuery({
    queryKey: ["agents", "tool-catalog"],
    queryFn: listAgentToolCatalog,
    staleTime: 60_000,
  });

  const activeAgentId = selectedId && selectedId !== "_new_" ? selectedId : null;
  const runs = useQuery({
    queryKey: ["agents", activeAgentId, "runs"],
    queryFn: () => activeAgentId ? listAgentRuns(activeAgentId, 30) : Promise.resolve([]),
    enabled: Boolean(activeAgentId) && activeTab === "runs",
    refetchInterval: activeTab === "runs" ? 20_000 : false,
  });
  const runDetail = useQuery({
    queryKey: ["agent-runs", selectedRunId],
    queryFn: () => selectedRunId ? getAgentRunDetail(selectedRunId) : Promise.resolve(null),
    enabled: selectedRunId !== null,
  });

  const cartridges = useMemo(() => {
    const ids = new Set<string>(KNOWN_CARTRIDGES);
    for (const agent of agents.data ?? []) {
      if (agent.cartridge_id) ids.add(agent.cartridge_id);
    }
    if (draft.cartridge_id) ids.add(draft.cartridge_id);
    return [...ids].sort();
  }, [agents.data, draft.cartridge_id]);

  const filteredAgents = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return agents.data ?? [];
    return (agents.data ?? []).filter((agent) => (
      [
        agent.name,
        agent.slug,
        agent.cartridge_id,
        agent.description,
        agent.model,
        ...(agent.allowed_tools ?? []),
      ]
        .filter((value): value is string => typeof value === "string")
        .some((value) => value.toLowerCase().includes(needle))
    ));
  }, [agents.data, query]);

  const groupedAgents = useMemo(() => {
    const groups = new Map<string, AgentRecord[]>();
    for (const agent of filteredAgents) {
      const key = agent.cartridge_id || "sin_cartucho";
      groups.set(key, [...(groups.get(key) ?? []), agent]);
    }
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [filteredAgents]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const save = useMutation({
    mutationFn: (payload: AgentUpsertPayload) => (
      activeAgentId ? updateAgent(activeAgentId, payload) : createAgent(payload)
    ),
    onSuccess: async (agent) => {
      setSelectedId(agent.id);
      setDraft(agentToDraft(agent));
      setDeleteArmed(false);
      await queryClient.invalidateQueries({ queryKey: ["agents"] });
      toast.success("Agente guardado.");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "No se pudo guardar el agente.");
    },
  });

  const status = useMutation({
    mutationFn: ({ id, active }: { id: string; active: boolean }) => updateAgentStatus(id, active),
    onSuccess: async (agent) => {
      if (agent.id === activeAgentId) setDraft(agentToDraft(agent));
      await queryClient.invalidateQueries({ queryKey: ["agents"] });
    },
    onError: () => toast.error("No se pudo cambiar el estado del agente."),
  });

  const remove = useMutation({
    mutationFn: deleteAgent,
    onSuccess: async () => {
      setSelectedId("_new_");
      setDraft(createEmptyDraft(cartridges[0]));
      setDeleteArmed(false);
      setSelectedRunId(null);
      await queryClient.invalidateQueries({ queryKey: ["agents"] });
      toast.success("Agente eliminado.");
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo eliminar."),
  });

  function selectAgent(agent: AgentRecord) {
    abortRef.current?.abort();
    setSelectedId(agent.id);
    setDraft(agentToDraft(agent));
    setDeleteArmed(false);
    setSelectedRunId(null);
    setTestLines([]);
    setTestHistory([]);
  }

  function newAgent() {
    abortRef.current?.abort();
    setSelectedId("_new_");
    setDraft(createEmptyDraft(cartridges[0] ?? "replicon"));
    setActiveTab("identity");
    setDeleteArmed(false);
    setSelectedRunId(null);
    setTestLines([]);
    setTestHistory([]);
  }

  function patchDraft(patch: Partial<AgentDraft>) {
    setDraft((current) => ({ ...current, ...patch }));
  }

  function updateSchedule(key: string, value: string | boolean) {
    setDraft((current) => ({
      ...current,
      extra: {
        ...current.extra,
        schedule: {
          ...(current.extra.schedule ?? {}),
          [key]: value,
        },
      },
    }));
  }

  function updateVariable(oldKey: string, nextKey: string, value: string) {
    setDraft((current) => {
      const entries = Object.entries(current.extra.variables ?? {});
      const variables: Record<string, string> = {};
      for (const [key, currentValue] of entries) {
        variables[key === oldKey ? nextKey : key] = key === oldKey ? value : currentValue;
      }
      return { ...current, extra: { ...current.extra, variables } };
    });
  }

  function addVariable() {
    setDraft((current) => {
      const variables = { ...(current.extra.variables ?? {}) };
      let key = "nueva_var";
      let i = 1;
      while (key in variables) key = `nueva_var_${i++}`;
      variables[key] = "";
      return { ...current, extra: { ...current.extra, variables } };
    });
  }

  function removeVariable(key: string) {
    setDraft((current) => {
      const variables = { ...(current.extra.variables ?? {}) };
      delete variables[key];
      return { ...current, extra: { ...current.extra, variables } };
    });
  }

  function toggleTool(toolId: string) {
    const current = new Set(draft.allowed_tools);
    if (current.has(toolId)) current.delete(toolId);
    else current.add(toolId);
    patchDraft({ allowed_tools: [...current] });
  }

  function toggleRagKind(kind: string) {
    const current = new Set(draft.rag_filter.kinds ?? []);
    if (current.has(kind)) current.delete(kind);
    else current.add(kind);
    patchDraft({ rag_filter: { ...draft.rag_filter, kinds: [...current] } });
  }

  function handleSave() {
    const payload = buildPayload(draft);
    if (!payload.cartridge_id || !payload.slug || !payload.name || !payload.instructions.trim()) {
      toast.error("Cartucho, slug, nombre e instrucciones son obligatorios.");
      return;
    }
    save.mutate(payload);
  }

  async function sendTest() {
    const message = testInput.trim();
    if (!message || !activeAgentId) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const userId = `user_${Date.now()}`;
    const assistantId = `assistant_${Date.now()}`;
    setTesting(true);
    setTestInput("");
    setTestLines((current) => [
      ...current,
      { id: userId, role: "user", content: message },
      { id: assistantId, role: "assistant", content: "(pensando...)" },
    ]);

    let reply = "";
    try {
      const response = await invokeAgentStream(activeAgentId, { message, history: testHistory }, controller.signal);
      if (!response.body) throw new Error("stream no disponible");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let done = false;
      while (!done) {
        const chunk = await reader.read();
        done = chunk.done;
        buffer += decoder.decode(chunk.value, { stream: !done });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() ?? "";
        for (const block of parts) {
          const event = parseStreamBlock(block);
          if (!event) continue;
          if (event.type === "tool_use") {
            setTestLines((current) => insertBeforeAssistant(current, assistantId, {
              id: `tool_${Date.now()}_${Math.random().toString(36).slice(2)}`,
              role: "tool",
              content: `→ ${event.server || "mcp"} / ${event.tool || "tool"}`,
            }));
          } else if (event.type === "tool_result") {
            setTestLines((current) => insertBeforeAssistant(current, assistantId, {
              id: `tool_${Date.now()}_${Math.random().toString(36).slice(2)}`,
              role: "tool",
              content: `← ${event.summary || "ok"}`,
            }));
          } else if (event.type === "text") {
            reply = event.text || reply;
            setTestLines((current) => updateLine(current, assistantId, reply || "(sin respuesta)"));
          } else if (event.type === "error") {
            throw new Error(event.message || "Error del agente.");
          }
        }
      }
      setTestHistory((current) => [
        ...current,
        { role: "user", content: message },
        { role: "assistant", content: reply || "(sin respuesta)" },
      ]);
      if (activeTab === "runs") void runs.refetch();
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      const messageText = error instanceof Error ? error.message : "No se pudo probar el agente.";
      setTestLines((current) => updateLine(current, assistantId, messageText, "error"));
    } finally {
      setTesting(false);
      abortRef.current = null;
    }
  }

  if (agents.isLoading) return <SkeletonGrid />;

  if (agents.isError) {
    return (
      <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
        <p className="font-medium text-destructive">No se pudieron cargar los agentes.</p>
        <p className="mt-1 text-muted-foreground">Si ves 403, esta sección requiere rol admin global.</p>
        <button
          type="button"
          onClick={() => agents.refetch()}
          className="mt-3 inline-flex min-h-[44px] items-center gap-2 rounded-md border px-3 text-xs font-medium"
        >
          <RefreshCw aria-hidden className="h-4 w-4" />
          Reintentar
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <section className="grid grid-cols-1 gap-3 md:grid-cols-4" aria-label="Resumen de agentes">
        <MetricCard label="Agentes" value={(agents.data ?? []).length} />
        <MetricCard label="Activos" value={(agents.data ?? []).filter((agent) => agent.is_active !== false).length} />
        <MetricCard label="Cartuchos" value={cartridges.length} />
        <MetricCard label="Tools catálogo" value={Object.values(toolCatalog.data?.servers ?? {}).reduce((sum, tools) => sum + tools.length, 0)} />
      </section>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">
        <aside className="space-y-3 rounded-lg border bg-card p-4" aria-label="Catálogo de agentes">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold">Catálogo</h2>
              <p className="text-xs text-muted-foreground">Agrupado por cartucho.</p>
            </div>
            <button
              type="button"
              onClick={newAgent}
              className="inline-flex min-h-[40px] items-center gap-2 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground"
            >
              <Plus aria-hidden className="h-4 w-4" />
              Nuevo
            </button>
          </div>
          <div className="flex gap-2">
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="buscar agente, tool, cartucho..."
              className="min-h-[44px] min-w-0 flex-1 rounded-md border bg-background px-3 text-sm"
            />
            <button
              type="button"
              onClick={() => agents.refetch()}
              aria-label="Refrescar agentes"
              className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md border bg-background"
            >
              <RefreshCw aria-hidden className="h-4 w-4" />
            </button>
          </div>
          <div className="max-h-[680px] overflow-auto rounded-md border bg-background">
            {groupedAgents.length ? groupedAgents.map(([cartridge, rows]) => (
              <div key={cartridge}>
                <div className="sticky top-0 z-10 border-b bg-muted/70 px-3 py-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {cartridge}
                </div>
                {rows.map((agent, index) => (
                  <button
                    key={agentKey(agent, index)}
                    type="button"
                    onClick={() => selectAgent(agent)}
                    className={cn(
                      "block w-full border-b px-3 py-3 text-left last:border-b-0 hover:bg-accent/5",
                      selectedId === agent.id && "bg-primary/10",
                    )}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <span className="min-w-0">
                        <span className="block truncate text-sm font-semibold">{agent.name}</span>
                        <span className="block truncate font-mono text-xs text-muted-foreground">{agent.slug || agent.id}</span>
                      </span>
                      <span className={cn(
                        "rounded-md px-2 py-1 text-[10px] font-semibold",
                        agent.is_active === false ? "bg-muted text-muted-foreground" : "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
                      )}>
                        {agent.is_active === false ? "Inactivo" : "Activo"}
                      </span>
                    </div>
                    <p className="mt-2 line-clamp-2 text-xs text-muted-foreground">{agent.description || "Sin descripción."}</p>
                    <p className="mt-2 truncate text-[11px] text-muted-foreground">{agentToolsLabel(agent)}</p>
                  </button>
                ))}
              </div>
            )) : (
              <p className="p-6 text-center text-sm text-muted-foreground">Sin agentes en este filtro.</p>
            )}
          </div>
        </aside>

        <section className="rounded-lg border bg-card" aria-label="Editor de agente">
          <header className="flex flex-col gap-3 border-b p-4 lg:flex-row lg:items-center lg:justify-between">
            <div className="min-w-0">
              <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                {selectedId === "_new_" ? "Nuevo agente" : "Editor"}
              </p>
              <h2 className="truncate text-xl font-semibold tracking-tight">{draft.name || "Sin nombre"}</h2>
              <p className="truncate font-mono text-xs text-muted-foreground">{draft.slug || "slug pendiente"}</p>
            </div>
            <div className="flex flex-wrap gap-2">
              {activeAgentId ? (
                <button
                  type="button"
                  onClick={() => status.mutate({ id: activeAgentId, active: !draft.is_active })}
                  disabled={status.isPending}
                  className="inline-flex min-h-[40px] items-center gap-2 rounded-md border bg-background px-3 text-xs font-medium"
                >
                  <CheckCircle2 aria-hidden className="h-4 w-4" />
                  {draft.is_active ? "Desactivar" : "Activar"}
                </button>
              ) : null}
              <button
                type="button"
                onClick={handleSave}
                disabled={save.isPending}
                className="inline-flex min-h-[40px] items-center gap-2 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground disabled:opacity-60"
              >
                <Save aria-hidden className="h-4 w-4" />
                {save.isPending ? "Guardando" : "Guardar"}
              </button>
            </div>
          </header>

          <nav className="flex gap-1 overflow-x-auto border-b px-4 py-2" aria-label="Secciones del agente">
            {[
              ["identity", "Identidad"],
              ["instructions", "Instrucciones"],
              ["tools", "Tools"],
              ["model", "Modelo"],
              ["context", "RAG"],
              ["extra", "Variables"],
              ["runs", "Ejecuciones"],
            ].map(([id, label]) => (
              <button
                key={id}
                type="button"
                onClick={() => setActiveTab(id as AgentTab)}
                className={cn(
                  "min-h-[38px] rounded-md px-3 text-xs font-semibold",
                  activeTab === id ? "bg-primary text-primary-foreground" : "hover:bg-accent/10",
                )}
              >
                {label}
              </button>
            ))}
          </nav>

          <div className="p-4">
            {activeTab === "identity" ? (
              <IdentityPanel draft={draft} cartridges={cartridges} patchDraft={patchDraft} />
            ) : null}
            {activeTab === "instructions" ? (
              <InstructionsPanel draft={draft} patchDraft={patchDraft} />
            ) : null}
            {activeTab === "tools" ? (
              <ToolsPanel
                servers={toolCatalog.data?.servers ?? {}}
                loading={toolCatalog.isLoading}
                error={toolCatalog.isError}
                checked={draft.allowed_tools}
                onToggle={toggleTool}
                onRetry={() => toolCatalog.refetch()}
              />
            ) : null}
            {activeTab === "model" ? (
              <ModelPanel draft={draft} patchDraft={patchDraft} />
            ) : null}
            {activeTab === "context" ? (
              <ContextPanel draft={draft} patchDraft={patchDraft} toggleRagKind={toggleRagKind} />
            ) : null}
            {activeTab === "extra" ? (
              <ExtraPanel
                draft={draft}
                addVariable={addVariable}
                removeVariable={removeVariable}
                updateVariable={updateVariable}
                updateSchedule={updateSchedule}
              />
            ) : null}
            {activeTab === "runs" ? (
              <RunsPanel
                activeAgentId={activeAgentId}
                runs={runs.data ?? []}
                runsLoading={runs.isFetching}
                selectedRunId={selectedRunId}
                selectedRun={runDetail.data}
                selectedRunLoading={runDetail.isFetching}
                onSelectRun={setSelectedRunId}
                onRefresh={() => runs.refetch()}
                testInput={testInput}
                setTestInput={setTestInput}
                sendTest={sendTest}
                testing={testing}
                testLines={testLines}
                clearTest={() => { setTestLines([]); setTestHistory([]); }}
              />
            ) : null}
          </div>

          <footer className="flex flex-wrap items-center gap-2 border-t p-4">
            {activeAgentId ? (
              deleteArmed ? (
                <div className="flex flex-wrap items-center gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-2 text-sm">
                  <span className="text-destructive">Confirmar eliminación</span>
                  <button
                    type="button"
                    onClick={() => remove.mutate(activeAgentId)}
                    disabled={remove.isPending}
                    className="inline-flex min-h-[36px] items-center rounded-md bg-destructive px-3 text-xs font-medium text-destructive-foreground"
                  >
                    Eliminar
                  </button>
                  <button
                    type="button"
                    onClick={() => setDeleteArmed(false)}
                    className="inline-flex min-h-[36px] items-center rounded-md border bg-background px-3 text-xs font-medium"
                  >
                    Cancelar
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => setDeleteArmed(true)}
                  className="inline-flex min-h-[40px] items-center gap-2 rounded-md border border-destructive/40 px-3 text-xs font-medium text-destructive hover:bg-destructive/10"
                >
                  <Trash2 aria-hidden className="h-4 w-4" />
                  Eliminar agente
                </button>
              )
            ) : null}
            <span className="ml-auto text-xs text-muted-foreground">
              {activeAgentId ? "Los cambios se auditan vía X-Request-ID y CSRF." : "Guarda para habilitar prueba y ejecuciones."}
            </span>
          </footer>
        </section>
      </div>
    </div>
  );
}

function IdentityPanel({
  draft,
  cartridges,
  patchDraft,
}: {
  draft: AgentDraft;
  cartridges: string[];
  patchDraft: (patch: Partial<AgentDraft>) => void;
}) {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Field label="Cartucho">
        <select
          value={draft.cartridge_id}
          onChange={(event) => patchDraft({ cartridge_id: event.target.value })}
          className="min-h-[44px] rounded-md border bg-background px-3 text-sm"
        >
          {cartridges.map((id) => <option key={id} value={id}>{id}</option>)}
        </select>
      </Field>
      <Field label="Activo">
        <label className="flex min-h-[44px] items-center gap-2 rounded-md border bg-background px-3 text-sm">
          <input
            type="checkbox"
            checked={draft.is_active}
            onChange={(event) => patchDraft({ is_active: event.target.checked })}
          />
          Disponible para ejecución
        </label>
      </Field>
      <Field label="Slug">
        <input value={draft.slug} onChange={(event) => patchDraft({ slug: event.target.value })} className="min-h-[44px] rounded-md border bg-background px-3 text-sm" />
      </Field>
      <Field label="Nombre">
        <input value={draft.name} onChange={(event) => patchDraft({ name: event.target.value })} className="min-h-[44px] rounded-md border bg-background px-3 text-sm" />
      </Field>
      <Field label="Descripción">
        <textarea value={draft.description} onChange={(event) => patchDraft({ description: event.target.value })} className="min-h-28 rounded-md border bg-background px-3 py-2 text-sm lg:col-span-2" />
      </Field>
    </div>
  );
}

function InstructionsPanel({ draft, patchDraft }: { draft: AgentDraft; patchDraft: (patch: Partial<AgentDraft>) => void }) {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      <Field label="Instrucciones del sistema">
        <textarea
          value={draft.instructions}
          onChange={(event) => patchDraft({ instructions: event.target.value })}
          className="min-h-[360px] rounded-md border bg-background px-3 py-2 font-mono text-sm"
        />
      </Field>
      <Field label="Personalidad / tono operativo">
        <textarea
          value={draft.personality}
          onChange={(event) => patchDraft({ personality: event.target.value })}
          className="min-h-[360px] rounded-md border bg-background px-3 py-2 text-sm"
        />
      </Field>
    </div>
  );
}

function ToolsPanel({
  servers,
  loading,
  error,
  checked,
  onToggle,
  onRetry,
}: {
  servers: Record<string, AgentTool[]>;
  loading: boolean;
  error: boolean;
  checked: string[];
  onToggle: (toolId: string) => void;
  onRetry: () => void;
}) {
  const checkedSet = new Set(checked);
  if (loading) return <SkeletonGrid />;
  if (error) {
    return (
      <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
        <p className="font-medium text-destructive">No se pudo cargar el catálogo MCP.</p>
        <button type="button" onClick={onRetry} className="mt-2 min-h-[40px] rounded-md border px-3 text-xs font-medium">Reintentar</button>
      </div>
    );
  }
  return (
    <div className="space-y-4">
      {Object.entries(servers).sort(([a], [b]) => a.localeCompare(b)).map(([server, tools]) => (
        <section key={server} className="rounded-lg border bg-background p-3">
          <h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-muted-foreground">{server}</h3>
          {tools.length ? (
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {tools.map((tool) => {
                const id = `${server}__${tool.name}`;
                return (
                  <label key={id} className="flex cursor-pointer items-start gap-3 rounded-md border bg-card p-3 text-sm hover:bg-accent/5">
                    <input type="checkbox" checked={checkedSet.has(id)} onChange={() => onToggle(id)} className="mt-1" />
                    <span className="min-w-0">
                      <span className="block font-mono text-xs font-semibold">{tool.name}</span>
                      <span className="mt-1 line-clamp-2 block text-xs text-muted-foreground">{tool.description || "Sin descripción."}</span>
                    </span>
                  </label>
                );
              })}
            </div>
          ) : (
            <p className="rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground">Servidor sin tools disponibles.</p>
          )}
        </section>
      ))}
    </div>
  );
}

function ModelPanel({ draft, patchDraft }: { draft: AgentDraft; patchDraft: (patch: Partial<AgentDraft>) => void }) {
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <Field label="Modelo">
        <select value={draft.model} onChange={(event) => patchDraft({ model: event.target.value })} className="min-h-[44px] rounded-md border bg-background px-3 text-sm">
          {MODEL_OPTIONS.map((model) => <option key={model} value={model}>{model}</option>)}
        </select>
      </Field>
      <Field label="Max tokens">
        <input type="number" min={1} value={draft.max_tokens} onChange={(event) => patchDraft({ max_tokens: Number(event.target.value) })} className="min-h-[44px] rounded-md border bg-background px-3 text-sm" />
      </Field>
      <Field label="Temperature">
        <input type="number" min={0} max={2} step={0.1} value={draft.temperature} onChange={(event) => patchDraft({ temperature: Number(event.target.value) })} className="min-h-[44px] rounded-md border bg-background px-3 text-sm" />
      </Field>
    </div>
  );
}

function ContextPanel({
  draft,
  patchDraft,
  toggleRagKind,
}: {
  draft: AgentDraft;
  patchDraft: (patch: Partial<AgentDraft>) => void;
  toggleRagKind: (kind: string) => void;
}) {
  const kinds = new Set(draft.rag_filter.kinds ?? []);
  return (
    <div className="space-y-4">
      <section className="rounded-lg border bg-background p-4">
        <h3 className="text-sm font-semibold">Fuentes RAG permitidas</h3>
        <div className="mt-3 flex flex-wrap gap-2">
          {RAG_KINDS.map((kind) => (
            <label key={kind} className="flex min-h-[40px] items-center gap-2 rounded-md border bg-card px-3 text-sm">
              <input type="checkbox" checked={kinds.has(kind)} onChange={() => toggleRagKind(kind)} />
              {kind}
            </label>
          ))}
        </div>
      </section>
      <Field label="Filtro RAG JSON">
        <textarea
          value={JSON.stringify(draft.rag_filter, null, 2)}
          onChange={(event) => {
            try {
              const parsed = JSON.parse(event.target.value) as AgentRagFilter;
              patchDraft({ rag_filter: parsed });
            } catch {
              patchDraft({ rag_filter: draft.rag_filter });
            }
          }}
          className="min-h-64 rounded-md border bg-background px-3 py-2 font-mono text-sm"
        />
      </Field>
    </div>
  );
}

function ExtraPanel({
  draft,
  addVariable,
  removeVariable,
  updateVariable,
  updateSchedule,
}: {
  draft: AgentDraft;
  addVariable: () => void;
  removeVariable: (key: string) => void;
  updateVariable: (oldKey: string, nextKey: string, value: string) => void;
  updateSchedule: (key: string, value: string | boolean) => void;
}) {
  const variables = Object.entries(draft.extra.variables ?? {});
  const schedule = draft.extra.schedule ?? {};
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <section className="space-y-3 rounded-lg border bg-background p-4">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-semibold">Variables</h3>
          <button type="button" onClick={addVariable} className="inline-flex min-h-[36px] items-center gap-2 rounded-md border px-3 text-xs font-medium">
            <Plus aria-hidden className="h-4 w-4" />
            Añadir
          </button>
        </div>
        {variables.length ? variables.map(([key, value]) => (
          <div key={key} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_40px] gap-2">
            <input value={key} onChange={(event) => updateVariable(key, event.target.value, value)} className="min-h-[40px] rounded-md border bg-card px-3 text-sm" />
            <input value={value} onChange={(event) => updateVariable(key, key, event.target.value)} className="min-h-[40px] rounded-md border bg-card px-3 text-sm" />
            <button type="button" onClick={() => removeVariable(key)} aria-label={`Eliminar ${key}`} className="inline-flex min-h-[40px] items-center justify-center rounded-md border text-destructive">
              <X aria-hidden className="h-4 w-4" />
            </button>
          </div>
        )) : (
          <p className="rounded-md border bg-muted/30 p-3 text-sm text-muted-foreground">Sin variables.</p>
        )}
      </section>
      <section className="space-y-3 rounded-lg border bg-background p-4">
        <h3 className="text-sm font-semibold">Agenda programada</h3>
        <Field label="Cron">
          <input value={String(schedule.cron || schedule.cron_expression || "")} onChange={(event) => updateSchedule("cron", event.target.value)} className="min-h-[40px] rounded-md border bg-card px-3 font-mono text-sm" placeholder="*/30 * * * *" />
        </Field>
        <Field label="Zona horaria">
          <input value={String(schedule.tz || "")} onChange={(event) => updateSchedule("tz", event.target.value)} className="min-h-[40px] rounded-md border bg-card px-3 text-sm" placeholder="UTC" />
        </Field>
        <Field label="Prompt programado">
          <textarea value={String(schedule.prompt || "")} onChange={(event) => updateSchedule("prompt", event.target.value)} className="min-h-28 rounded-md border bg-card px-3 py-2 text-sm" />
        </Field>
        <label className="flex min-h-[40px] items-center gap-2 rounded-md border bg-card px-3 text-sm">
          <input type="checkbox" checked={schedule.enabled !== false} onChange={(event) => updateSchedule("enabled", event.target.checked)} />
          Agenda habilitada
        </label>
      </section>
    </div>
  );
}

function RunsPanel({
  activeAgentId,
  runs,
  runsLoading,
  selectedRunId,
  selectedRun,
  selectedRunLoading,
  onSelectRun,
  onRefresh,
  testInput,
  setTestInput,
  sendTest,
  testing,
  testLines,
  clearTest,
}: {
  activeAgentId: string | null;
  runs: AgentRun[];
  runsLoading: boolean;
  selectedRunId: number | null;
  selectedRun: AgentRunDetail | null | undefined;
  selectedRunLoading: boolean;
  onSelectRun: (id: number) => void;
  onRefresh: () => void;
  testInput: string;
  setTestInput: (value: string) => void;
  sendTest: () => void;
  testing: boolean;
  testLines: TestLine[];
  clearTest: () => void;
}) {
  if (!activeAgentId) {
    return <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">Guarda el agente para habilitar ejecuciones y prueba.</p>;
  }
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
      <section className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-semibold">Ejecuciones recientes</h3>
          <button type="button" onClick={onRefresh} className="inline-flex min-h-[36px] items-center gap-2 rounded-md border px-3 text-xs font-medium">
            <RefreshCw aria-hidden className="h-4 w-4" />
            Refrescar
          </button>
        </div>
        {runsLoading ? <SkeletonGrid /> : runs.length ? (
          <div className="overflow-x-auto rounded-lg border">
            <table className="w-full text-sm">
              <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
                <tr><th className="px-3 py-2">Run</th><th className="px-3 py-2">Estado</th><th className="px-3 py-2">Inicio</th><th className="px-3 py-2">Tools</th><th className="px-3 py-2">Salida</th></tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id} className={cn("border-t", selectedRunId === run.id && "bg-primary/10")}>
                    <td className="px-3 py-2">
                      <button type="button" onClick={() => onSelectRun(run.id)} className="font-mono text-xs text-primary hover:underline">#{run.id}</button>
                    </td>
                    <td className="px-3 py-2">{run.status || "-"}</td>
                    <td className="px-3 py-2 text-xs text-muted-foreground">{formatDate(run.started_at)}</td>
                    <td className="px-3 py-2">{run.n_tool_calls ?? 0}</td>
                    <td className="px-3 py-2">{run.out_chars ?? 0}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">Sin ejecuciones registradas.</p>
        )}
        {selectedRunLoading ? <SkeletonGrid /> : selectedRun ? <JsonBlock value={selectedRun} /> : null}
      </section>
      <section className="space-y-3 rounded-lg border bg-background p-4">
        <div className="flex items-center justify-between gap-3">
          <h3 className="flex items-center gap-2 text-sm font-semibold">
            <MessageSquareText aria-hidden className="h-4 w-4" />
            Probar agente
          </h3>
          <button type="button" onClick={clearTest} className="text-xs text-muted-foreground hover:text-foreground">limpiar</button>
        </div>
        <div className="max-h-[420px] min-h-[260px] space-y-2 overflow-auto rounded-md border bg-card p-3">
          {testLines.length ? testLines.map((line) => (
            <div key={line.id} className={cn(
              "rounded-md px-3 py-2 text-sm",
              line.role === "user" && "ml-8 bg-primary text-primary-foreground",
              line.role === "assistant" && "mr-8 bg-background",
              line.role === "tool" && "mx-4 border bg-muted/30 font-mono text-xs text-muted-foreground",
              line.role === "error" && "border border-destructive/40 bg-destructive/5 text-destructive",
            )}>
              {line.content}
            </div>
          )) : (
            <p className="p-6 text-center text-sm text-muted-foreground">Envía un mensaje para probar tools y streaming.</p>
          )}
        </div>
        <div className="flex gap-2">
          <input
            value={testInput}
            onChange={(event) => setTestInput(event.target.value)}
            onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) sendTest(); }}
            disabled={testing}
            className="min-h-[44px] min-w-0 flex-1 rounded-md border bg-card px-3 text-sm"
            placeholder="Escribe un mensaje..."
          />
          <button
            type="button"
            onClick={sendTest}
            disabled={testing || !testInput.trim()}
            className="inline-flex min-h-[44px] items-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground disabled:opacity-60"
          >
            <Send aria-hidden className="h-4 w-4" />
            Enviar
          </button>
        </div>
      </section>
    </div>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5 text-sm">
      <span className="font-medium text-foreground">{label}</span>
      {children}
    </label>
  );
}

function MetricCard({ label, value }: { label: string; value: number }) {
  return (
    <article className="rounded-lg border bg-card p-4 shadow-sm">
      <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{label}</p>
      <strong className="mt-2 block text-3xl font-semibold tracking-tight">{value}</strong>
    </article>
  );
}

function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="max-h-96 overflow-auto rounded-md border bg-background p-3 text-xs">
{JSON.stringify(value, null, 2)}
    </pre>
  );
}

function SkeletonGrid() {
  return (
    <div aria-busy="true" className="grid grid-cols-1 gap-3 md:grid-cols-2">
      {Array.from({ length: 4 }).map((_, index) => (
        <span key={index} className="block h-24 animate-pulse rounded-lg bg-muted" aria-hidden />
      ))}
    </div>
  );
}

function formatDate(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function parseStreamBlock(block: string): AgentStreamEvent | null {
  const data = block
    .split("\n")
    .filter((line) => line.startsWith("data: "))
    .map((line) => line.slice(6))
    .join("");
  if (!data) return null;
  try {
    const parsed = JSON.parse(data) as unknown;
    if (typeof parsed === "object" && parsed !== null && "type" in parsed) {
      return parsed as AgentStreamEvent;
    }
  } catch {
    return null;
  }
  return null;
}

function updateLine(lines: TestLine[], id: string, content: string, role?: TestLine["role"]): TestLine[] {
  return lines.map((line) => line.id === id ? { ...line, content, role: role ?? line.role } : line);
}

function insertBeforeAssistant(lines: TestLine[], assistantId: string, item: TestLine): TestLine[] {
  const index = lines.findIndex((line) => line.id === assistantId);
  if (index === -1) return [...lines, item];
  return [...lines.slice(0, index), item, ...lines.slice(index)];
}
