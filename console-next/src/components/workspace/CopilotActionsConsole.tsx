"use client";

import type { ReactNode } from "react";
import { useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowLeft,
  Brain,
  Crosshair,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Target,
} from "lucide-react";
import { toast } from "sonner";

import {
  askCopilotWithContext,
  createCopilotGoal,
  diagnoseCopilotGoal,
  dismissCopilotRecommendation,
  getCopilotContextSnapshot,
  listCopilotBriefingV2,
  listCopilotGoals,
  listCopilotLessons,
  listCopilotRecommendations,
  listCopilotWatchdogs,
  matchCopilotWatchdogs,
  refreshCopilotContext,
} from "@/lib/copilot/client";
import { getMeAccess } from "@/lib/admin-surfaces";
import type {
  BriefingV2Highlight,
  CopilotContextSnapshot,
  CopilotGoal,
  CopilotGoalDiagnosisResponse,
  CopilotLesson,
  CopilotRecommendation,
  CopilotWatchdog,
} from "@/lib/copilot/types";
import { cn } from "@/lib/utils";

const QUICK_GOALS = [
  "Tengo problema de margen este trimestre; diagnostica causa raíz y próximos pasos.",
  "Detecta riesgos operativos urgentes y prioriza qué atender primero.",
  "Cruza pipeline, costos y facturación para explicar qué cambió esta semana.",
];

function statusLabel(status?: string) {
  switch (status) {
    case "planning": return "planeando";
    case "running": return "diagnosticando";
    case "awaiting_approval": return "requiere aprobación";
    case "completed": return "completado";
    case "failed": return "falló";
    case "cancelled": return "cancelado";
    default: return status || "sin estado";
  }
}

function shortDate(value?: string | null) {
  if (!value) return "";
  const dt = new Date(value);
  if (Number.isNaN(dt.getTime())) return "";
  return dt.toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function compactListLabel(items: string[] | undefined) {
  if (!items || items.length === 0) return "sin cartuchos explícitos";
  return items.slice(0, 3).join(", ");
}

export function CopilotActionsConsole() {
  const qc = useQueryClient();
  const [goalText, setGoalText] = useState(QUICK_GOALS[0]);
  const [diagnosis, setDiagnosis] = useState<CopilotGoalDiagnosisResponse | null>(null);
  const [contextAnswer, setContextAnswer] = useState("");
  const [matchedWatchdogs, setMatchedWatchdogs] = useState<CopilotWatchdog[]>([]);
  const access = useQuery({
    queryKey: ["me", "access"],
    queryFn: getMeAccess,
    staleTime: 60_000,
  });
  const permissions = new Set(access.data?.permissions ?? []);
  const canViewOperationalContext = permissions.has("operations.read");
  const canManageOperationalContext = permissions.has("control_room.write");

  const goalsQuery = useQuery<CopilotGoal[]>({
    queryKey: ["copilot", "actions", "goals"],
    queryFn: () => listCopilotGoals(6),
    staleTime: 20_000,
  });
  const briefingQuery = useQuery<BriefingV2Highlight[]>({
    queryKey: ["copilot", "actions", "briefing-v2"],
    queryFn: () => listCopilotBriefingV2(6),
    staleTime: 20_000,
  });
  const watchdogsQuery = useQuery<CopilotWatchdog[]>({
    queryKey: ["copilot", "actions", "watchdogs"],
    queryFn: () => listCopilotWatchdogs(12),
    staleTime: 60_000,
  });
  const lessonsQuery = useQuery<CopilotLesson[]>({
    queryKey: ["copilot", "actions", "lessons"],
    queryFn: () => listCopilotLessons(6),
    staleTime: 30_000,
  });
  const liveContextQuery = useQuery<CopilotContextSnapshot>({
    queryKey: ["copilot", "actions", "live-context"],
    queryFn: getCopilotContextSnapshot,
    enabled: canViewOperationalContext,
    staleTime: 30_000,
  });
  const recommendationsQuery = useQuery<CopilotRecommendation[]>({
    queryKey: ["copilot", "actions", "recommendations"],
    queryFn: () => listCopilotRecommendations(8, false),
    staleTime: 30_000,
  });

  const topGoal = goalsQuery.data?.[0];
  const topBriefing = briefingQuery.data?.[0];

  const createAndDiagnose = useMutation({
    mutationFn: async (text: string) => {
      const goal = await createCopilotGoal(text);
      const result = await diagnoseCopilotGoal(goal.id);
      return { goal, result };
    },
    onSuccess: async ({ result }) => {
      setDiagnosis(result);
      await qc.invalidateQueries({ queryKey: ["copilot", "actions", "goals"] });
      toast.success("Objetivo diagnosticado.");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "No se pudo diagnosticar el objetivo.");
    },
  });

  const askContext = useMutation({
    mutationFn: async () => askCopilotWithContext(
      "Con esta pantalla de acciones del copiloto, resume qué requiere atención y cuál sería la próxima acción.",
      {
        route: "/copilot/actions",
        surface: "copilot_actions",
        active_goal: goalText,
        latest_goal_status: topGoal?.status,
        top_briefing: topBriefing?.title,
      },
    ),
    onSuccess: (result) => {
      setContextAnswer(result.answer);
      toast.success("Contexto analizado.");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "No se pudo preguntar con contexto.");
    },
  });

  const refreshContext = useMutation({
    mutationFn: refreshCopilotContext,
    onSuccess: async () => {
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["copilot", "actions", "live-context"] }),
        qc.invalidateQueries({ queryKey: ["copilot", "actions", "recommendations"] }),
        qc.invalidateQueries({ queryKey: ["copilot", "actions", "briefing-v2"] }),
      ]);
      toast.success("Contexto actualizado.");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "No se pudo actualizar el contexto.");
    },
  });

  const dismissRecommendation = useMutation({
    mutationFn: dismissCopilotRecommendation,
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["copilot", "actions", "recommendations"] });
      toast.success("Recomendación descartada.");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "No se pudo descartar la recomendación.");
    },
  });

  const matchWatchdogs = useMutation({
    mutationFn: async (text: string) => matchCopilotWatchdogs(text, 5),
    onSuccess: (rows) => {
      setMatchedWatchdogs(rows);
      toast.success(rows.length ? "Vigilancias encontradas." : "Sin vigilancias relevantes.");
    },
    onError: (error) => {
      toast.error(error instanceof Error ? error.message : "No se pudieron buscar vigilancias.");
    },
  });

  const diagnostics = diagnosis?.diagnosis;
  const diagnosticSubgoals = useMemo(
    () => diagnostics?.subgoals?.slice(0, 4) ?? [],
    [diagnostics],
  );

  const loadingAny =
    goalsQuery.isLoading ||
    briefingQuery.isLoading ||
    watchdogsQuery.isLoading ||
    lessonsQuery.isLoading ||
    liveContextQuery.isLoading ||
    recommendationsQuery.isLoading;

  return (
    <main
      aria-label="Acciones del copiloto"
      data-testid="copilot-actions-console"
      className="min-h-screen bg-background p-4 md:p-6"
    >
      <div className="mx-auto flex max-w-7xl flex-col gap-4">
        <header className="flex flex-col gap-3 border-b pb-4 md:flex-row md:items-center md:justify-between">
          <div className="min-w-0">
            <div className="flex items-center gap-2 text-xs font-medium uppercase text-muted-foreground">
              <ShieldCheck aria-hidden className="h-4 w-4 text-primary" />
              Copiloto
            </div>
            <h1 className="mt-1 text-2xl font-semibold tracking-tight">Acciones</h1>
            <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
              Objetivos, recomendaciones, contexto vivo, vigilancias y aprendizajes viven aquí para no invadir el chat normal.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link
              href="/copilot"
              className="inline-flex min-h-[40px] items-center gap-2 rounded-md border px-3 text-sm font-medium hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <ArrowLeft aria-hidden className="h-4 w-4" />
              Volver al chat
            </Link>
            <button
              type="button"
              onClick={() => {
                goalsQuery.refetch();
                briefingQuery.refetch();
                watchdogsQuery.refetch();
                lessonsQuery.refetch();
                if (canViewOperationalContext) liveContextQuery.refetch();
                recommendationsQuery.refetch();
              }}
              className="inline-flex min-h-[40px] items-center gap-2 rounded-md border px-3 text-sm font-medium hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <RefreshCw aria-hidden className={cn("h-4 w-4", loadingAny && "animate-spin")} />
              Actualizar
            </button>
          </div>
        </header>

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.25fr)_minmax(340px,0.75fr)]">
          <section className="space-y-3 rounded-md border bg-card p-4">
            <div className="flex items-center gap-2 text-xs font-semibold uppercase text-muted-foreground">
              <Target aria-hidden className="h-4 w-4" />
              Objetivo end-to-end
            </div>
            <textarea
              value={goalText}
              onChange={(event) => setGoalText(event.target.value)}
              rows={4}
              className="min-h-[120px] w-full resize-y rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              placeholder="Describe el objetivo de negocio..."
            />
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={createAndDiagnose.isPending || goalText.trim().length < 8}
                onClick={() => createAndDiagnose.mutate(goalText.trim())}
                className="inline-flex min-h-[40px] items-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {createAndDiagnose.isPending ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Brain aria-hidden className="h-4 w-4" />}
                Crear + diagnosticar
              </button>
              <button
                type="button"
                disabled={matchWatchdogs.isPending || goalText.trim().length < 4}
                onClick={() => matchWatchdogs.mutate(goalText.trim())}
                className="inline-flex min-h-[40px] items-center gap-2 rounded-md border px-3 text-sm font-medium hover:bg-accent/10 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {matchWatchdogs.isPending ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Crosshair aria-hidden className="h-4 w-4" />}
                Buscar vigilancias
              </button>
              <button
                type="button"
                disabled={askContext.isPending}
                onClick={() => askContext.mutate()}
                className="inline-flex min-h-[40px] items-center gap-2 rounded-md border px-3 text-sm font-medium hover:bg-accent/10 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {askContext.isPending ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Activity aria-hidden className="h-4 w-4" />}
                Analizar contexto
              </button>
              <Link
                href={`/copilot?prompt=${encodeURIComponent(goalText)}`}
                className="inline-flex min-h-[40px] items-center rounded-md border px-3 text-sm font-medium hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                Mandar al chat
              </Link>
            </div>
            <div className="flex flex-wrap gap-2">
              {QUICK_GOALS.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => setGoalText(prompt)}
                  className="rounded-md border px-2 py-1 text-xs text-muted-foreground hover:bg-accent/10 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {prompt}
                </button>
              ))}
            </div>

            {diagnostics ? (
              <div className="space-y-3 rounded-md border border-primary/30 bg-primary/5 p-3 text-sm">
                <div className="font-semibold">{diagnostics.plan_summary || "Plan generado"}</div>
                {diagnosticSubgoals.length ? (
                  <div className="grid gap-2 md:grid-cols-2">
                    {diagnosticSubgoals.map((subgoal, idx) => (
                      <div key={`${subgoal.description}-${idx}`} className="rounded-md border bg-background p-3">
                        <div className="text-xs font-medium text-muted-foreground">
                          Paso {idx + 1} · {compactListLabel(subgoal.expected_cartridges)}
                        </div>
                        <div className="mt-1 text-sm">{subgoal.description || "Subobjetivo sin descripción"}</div>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}

            {contextAnswer ? (
              <div className="rounded-md border bg-muted/30 p-3 text-sm">
                <div className="mb-1 text-xs font-semibold uppercase text-muted-foreground">
                  Respuesta con contexto
                </div>
                {contextAnswer}
              </div>
            ) : null}
          </section>

          <aside className="grid gap-4 md:grid-cols-2 xl:grid-cols-1">
            <MiniPanel
              title="Recomendaciones"
              value={briefingQuery.data?.length ?? 0}
              loading={briefingQuery.isLoading}
              empty="Sin alertas proactivas."
            >
              {(briefingQuery.data ?? []).slice(0, 5).map((item) => (
                <BriefingMiniRow key={item.id} item={item} />
              ))}
            </MiniPanel>

            <LiveContextPanel
              snapshot={liveContextQuery.data}
              recommendations={recommendationsQuery.data ?? []}
              loading={
                (canViewOperationalContext && liveContextQuery.isLoading)
                || recommendationsQuery.isLoading
              }
              showOperationalContext={canViewOperationalContext}
              canManageOperationalContext={canManageOperationalContext}
              refreshing={refreshContext.isPending}
              dismissingId={dismissRecommendation.variables}
              onRefresh={() => refreshContext.mutate()}
              onDismiss={(id) => dismissRecommendation.mutate(id)}
            />

            <MiniPanel
              title="Vigilancias"
              value={(matchedWatchdogs.length || watchdogsQuery.data?.length || 0)}
              loading={watchdogsQuery.isLoading || matchWatchdogs.isPending}
              empty="Sin vigilancias visibles."
            >
              {(matchedWatchdogs.length ? matchedWatchdogs : watchdogsQuery.data ?? []).slice(0, 6).map((watchdog) => (
                <div key={`${watchdog.cartridge_id}:${watchdog.slug}`} className="rounded-md border bg-background p-2">
                  <div className="text-sm font-medium">{watchdog.name}</div>
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    {watchdog.cartridge_id} · {watchdog.risk_level || "read"}
                  </div>
                </div>
              ))}
            </MiniPanel>

            <MiniPanel
              title="Goals recientes"
              value={goalsQuery.data?.length ?? 0}
              loading={goalsQuery.isLoading}
              empty="Sin objetivos todavía."
            >
              {(goalsQuery.data ?? []).slice(0, 5).map((goal) => (
                <GoalMiniRow key={goal.id} goal={goal} />
              ))}
            </MiniPanel>

            <MiniPanel
              title="Aprendizajes"
              value={lessonsQuery.data?.length ?? 0}
              loading={lessonsQuery.isLoading}
              empty="Sin lecciones activas."
            >
              {(lessonsQuery.data ?? []).slice(0, 5).map((lesson) => (
                <div key={lesson.id} className="rounded-md border bg-background p-2 text-sm">
                  <div className="font-medium">{lesson.lesson_text || lesson.trigger_pattern || "Lección"}</div>
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    {lesson.source_kind || "manual"} · {lesson.confidence ?? "?"}
                  </div>
                </div>
              ))}
            </MiniPanel>
          </aside>
        </div>
      </div>
    </main>
  );
}

function LiveContextPanel({
  snapshot,
  recommendations,
  loading,
  showOperationalContext,
  canManageOperationalContext,
  refreshing,
  dismissingId,
  onRefresh,
  onDismiss,
}: {
  snapshot?: CopilotContextSnapshot;
  recommendations: CopilotRecommendation[];
  loading: boolean;
  showOperationalContext: boolean;
  canManageOperationalContext: boolean;
  refreshing: boolean;
  dismissingId?: string;
  onRefresh: () => void;
  onDismiss: (id: string) => void;
}) {
  const sources = Array.isArray(snapshot?.sources) ? snapshot.sources : [];
  const lastRead = shortDate(snapshot?.generated_at || snapshot?.materialized_at || null);
  const readySources = sources.filter((source) => ["ready", "success", "ok"].includes(String(source.status || ""))).length;
  return (
    <section className="rounded-md border bg-card p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-xs font-semibold uppercase text-muted-foreground">
            {showOperationalContext ? "Contexto Vivo" : "Recomendaciones permitidas"}
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            {showOperationalContext
              ? (lastRead ? `Última lectura ${lastRead}` : "Contexto no actualizado")
              : "Señales disponibles para tu nivel de acceso"}
          </p>
        </div>
        {showOperationalContext && canManageOperationalContext ? (
          <button
            type="button"
            onClick={onRefresh}
            disabled={refreshing}
            className="inline-flex min-h-[36px] items-center gap-2 rounded-md border px-2 text-xs font-medium hover:bg-accent/10 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <RefreshCw aria-hidden className={cn("h-4 w-4", refreshing && "animate-spin")} />
            Actualizar contexto
          </button>
        ) : null}
      </div>
      {loading ? (
        <div className="h-16 animate-pulse rounded-md bg-muted" />
      ) : (
        <div className="space-y-3">
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
            {showOperationalContext ? (
              <div className="rounded-md border bg-background p-2">
                <div className="text-xs text-muted-foreground">Fuentes revisadas</div>
                <div className="mt-1 text-lg font-semibold">{readySources}/{sources.length}</div>
              </div>
            ) : null}
            <div className="rounded-md border bg-background p-2">
              <div className="text-xs text-muted-foreground">Recomendaciones nuevas</div>
              <div className="mt-1 text-lg font-semibold">{recommendations.length}</div>
            </div>
          </div>
          {showOperationalContext && sources.length ? (
            <div className="space-y-1">
              {sources.slice(0, 4).map((source, index) => (
                <div key={String(source.id || source.key || index)} className="flex items-center justify-between gap-2 rounded-md border bg-background px-2 py-1.5 text-xs">
                  <span className="truncate">{source.label || source.key || source.id || "Fuente"}</span>
                  <span className="shrink-0 text-muted-foreground">{source.status || "sin estado"}</span>
                </div>
              ))}
            </div>
          ) : showOperationalContext ? (
            <p className="text-sm text-muted-foreground">Sin fuentes revisadas todavía.</p>
          ) : null}
          <div className="space-y-2">
            {recommendations.slice(0, 4).map((item) => (
              <div key={item.id} className="rounded-md border bg-background p-2">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <div className="line-clamp-2 text-sm font-medium">{item.title || "Recomendación"}</div>
                    {item.body ? <div className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{item.body}</div> : null}
                  </div>
                  {canManageOperationalContext ? (
                    <button
                      type="button"
                      onClick={() => onDismiss(item.id)}
                      disabled={dismissingId === item.id}
                      className="shrink-0 rounded-md border px-2 py-1 text-xs hover:bg-accent/10 disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      {dismissingId === item.id ? "..." : "Descartar"}
                    </button>
                  ) : null}
                </div>
              </div>
            ))}
            {recommendations.length === 0 ? (
              <p className="text-sm text-muted-foreground">Sin recomendaciones nuevas.</p>
            ) : null}
          </div>
        </div>
      )}
    </section>
  );
}

function MiniPanel({
  title,
  value,
  loading,
  empty,
  children,
}: {
  title: string;
  value: number;
  loading: boolean;
  empty: string;
  children: ReactNode;
}) {
  const hasChildren = value > 0;
  return (
    <section className="rounded-md border bg-card p-4">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 className="text-xs font-semibold uppercase text-muted-foreground">{title}</h2>
        <span className="rounded-full border px-2 py-0.5 text-xs text-muted-foreground">
          {loading ? "..." : value}
        </span>
      </div>
      <div className="space-y-2">
        {loading ? (
          <div className="h-12 animate-pulse rounded-md bg-muted" />
        ) : hasChildren ? children : (
          <p className="text-sm text-muted-foreground">{empty}</p>
        )}
      </div>
    </section>
  );
}

function GoalMiniRow({ goal }: { goal: CopilotGoal }) {
  return (
    <div className="rounded-md border bg-background p-2">
      <div className="line-clamp-2 text-sm font-medium">{goal.goal_text}</div>
      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span>{statusLabel(goal.status)}</span>
        {shortDate(goal.created_at) ? <span>{shortDate(goal.created_at)}</span> : null}
      </div>
    </div>
  );
}

function BriefingMiniRow({ item }: { item: BriefingV2Highlight }) {
  return (
    <div className="rounded-md border bg-background p-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="line-clamp-2 text-sm font-medium">{item.title}</div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            {item.category} · prioridad {item.priority_score ?? 0}
          </div>
        </div>
        <span className={cn(
          "shrink-0 rounded-full px-2 py-0.5 text-xs",
          item.severity === "critical" && "bg-destructive/10 text-destructive",
          item.severity === "warning" && "bg-warning/10 text-warning",
          item.severity === "info" && "bg-primary/10 text-primary",
        )}>
          {item.severity}
        </span>
      </div>
      {item.next_action ? (
        <div className="mt-1 text-xs font-medium text-primary">{item.next_action.label}</div>
      ) : null}
    </div>
  );
}
