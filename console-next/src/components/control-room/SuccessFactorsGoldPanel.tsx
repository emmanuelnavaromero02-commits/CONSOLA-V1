import { Activity, AlertTriangle, ArrowRight, BookOpen, BriefcaseBusiness, Building2, CalendarDays, CreditCard, GraduationCap, Network, ShieldCheck, TrendingDown, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type { SfDecisionEntity, SfDecisionModelPayload, SfDecisionTerm, SfGoldKpisPayload, SfGoldWidget, SfGoldWidgetRow, SfTalentKpisPayload, SourceStatus } from "@/lib/control-room/types";
import { cn } from "@/lib/utils";

import { CommandMetric, MiniBar, OperationalNotice, ReadinessBadge, Sparkline } from "./StatusBadge";

function formatNumber(value: number): string {
  return new Intl.NumberFormat("es-MX").format(value);
}

function rowLabel(row: SfGoldWidgetRow): string {
  const candidates = [row.label, row.fact, row.status];
  const value = candidates.find((item) => typeof item === "string" && item.trim().length > 0);
  return String(value || "Registro");
}

function businessWidgetTitle(widget: SfGoldWidget): string {
  const value = `${widget.id || ""} ${widget.title || ""}`.toLowerCase();
  if (value.includes("employee_360")) return "Vista de personal";
  if (value.includes("org_structure")) return "Estructura organizacional";
  if (value.includes("manager_hierarchy")) return "Jerarquía de supervisión";
  if (value.includes("headcount_by_location")) return "Plantilla por ubicación";
  if (value.includes("headcount_by_department")) return "Plantilla por departamento";
  if (value.includes("headcount_by_company")) return "Plantilla por compañía";
  return (widget.title || "Indicador ejecutivo").replace(/_/g, " ");
}

function businessWidgetDetail(widget: SfGoldWidget): string {
  const value = `${widget.id || ""} ${widget.title || ""}`.toLowerCase();
  if (value.includes("employee_360")) return "Personas activas consideradas para decisiones de RR. HH.";
  if (value.includes("org_structure")) return "Relaciones organizacionales y cobertura por estructura.";
  if (value.includes("manager_hierarchy")) return "Supervisión y líneas de reporte disponibles.";
  if (value.includes("headcount")) return "Distribución de plantilla actualizada.";
  return "Indicador ejecutivo alimentado por datos reales.";
}

function widgetSearchText(widget: SfGoldWidget): string {
  return `${widget.id || ""} ${widget.title || ""}`.toLowerCase();
}

function businessSourceLabel(source: SourceStatus): string {
  const businessModule = `${source.module || ""} ${source.domain || ""} ${source.cartridge || ""}`.toLowerCase();
  if (businessModule.includes("employee central")) return "Personal";
  if (businessModule.includes("recruitment") || businessModule.includes("recruiting")) return "Reclutamiento";
  if (businessModule.includes("performance")) return "Desempeño";
  if (businessModule.includes("estructura") || businessModule.includes("org")) return "Estructura organizacional";
  return source.module || source.domain || "Información operativa";
}

function modelEntities(payload?: SfDecisionModelPayload | null): SfDecisionEntity[] {
  const direct = payload?.entities;
  if (Array.isArray(direct)) return direct;
  if (direct && typeof direct === "object") return Object.values(direct);
  return payload?.server?.entities ?? [];
}

function modelTerms(payload?: SfDecisionModelPayload | null): SfDecisionTerm[] {
  return payload?.server?.semantic_model?.vocabulary ?? [];
}

function searchableModelText(entity: SfDecisionEntity | SfDecisionTerm): string {
  const values = [
    "entity" in entity ? entity.entity : "",
    "name" in entity ? entity.name : "",
    "display_name" in entity ? entity.display_name : "",
    "description" in entity ? entity.description : "",
    "term" in entity ? entity.term : "",
    "definition" in entity ? entity.definition : "",
    "maps_to" in entity ? entity.maps_to : "",
    "fields" in entity && Array.isArray(entity.fields) ? entity.fields.join(" ") : "",
    "columns" in entity && Array.isArray(entity.columns) ? entity.columns.join(" ") : "",
    "select_fields" in entity && Array.isArray(entity.select_fields) ? entity.select_fields.join(" ") : "",
  ];
  return values.join(" ").toLowerCase();
}

type SuccessFactorsFront = {
  id: string;
  title: string;
  metric: string;
  detail: string;
  decision: string;
  status: NonNullable<SourceStatus["status"]> | NonNullable<SourceStatus["data_readiness"]>;
  signals: number;
  ready: number;
  total: number;
  icon: LucideIcon;
  tone: "good" | "warning" | "danger" | "neutral";
};

function sourceMatches(source: SourceStatus, terms: string[]): boolean {
  const value = `${source.module || ""} ${source.domain || ""} ${source.cartridge || ""}`.toLowerCase();
  return terms.some((term) => value.includes(term));
}

function widgetValue(widgets: SfGoldKpisPayload["widgets"], terms: string[]): number | null {
  const widget = (widgets ?? []).find((item) => {
    const value = `${item.id || ""} ${item.title || ""}`.toLowerCase();
    return terms.some((term) => value.includes(term));
  });
  return typeof widget?.value === "number" ? widget.value : null;
}

function sourceReadiness(source: SourceStatus): SuccessFactorsFront["status"] {
  if (source.status && source.status !== "ok") return source.status;
  return source.data_readiness || "ready";
}

function businessFrontStatus(sources: SourceStatus[], fallbackValue: number | null): SuccessFactorsFront["status"] {
  if (!sources.length) {
    if (fallbackValue !== null) return fallbackValue > 0 ? "ready" : "empty";
    return "missing";
  }
  const statuses = sources.map(sourceReadiness);
  for (const status of ["no_permission", "blocked", "unavailable", "invalid_schema", "missing", "stub", "partial", "empty", "ready"] as const) {
    if (statuses.includes(status)) return status;
  }
  return "missing";
}

function unavailableBusinessDetail(status: SuccessFactorsFront["status"]): string {
  if (status === "partial") return "Datos parciales";
  if (status === "no_permission" || status === "blocked") return "Requiere permisos OData";
  if (status === "unavailable") return "Dependencia no configurada";
  if (status === "invalid_schema") return "Información no disponible";
  if (status === "missing" || status === "stub") return "Fuera de alcance actual";
  if (status === "empty") return "Sin datos configurados";
  return "Información no disponible";
}

function widgetStatus(widget: SfGoldWidget | undefined): SuccessFactorsFront["status"] {
  if (!widget) return "missing";
  if (widget.status) return widget.status;
  if (typeof widget.value !== "number") return "missing";
  return widget.value > 0 ? "ready" : "empty";
}

function widgetValueText(widget: SfGoldWidget | undefined): string {
  return typeof widget?.value === "number" ? formatNumber(widget.value) : "N/D";
}

function widgetTone(status: SuccessFactorsFront["status"]): "good" | "warning" | "danger" | "neutral" {
  if (status === "ready" || status === "ok") return "good";
  if (status === "partial" || status === "empty") return "warning";
  if (status === "no_permission" || status === "blocked" || status === "unavailable" || status === "invalid_schema") return "danger";
  return "neutral";
}

function buildBusinessFronts(widgets: SfGoldKpisPayload["widgets"], sources: SourceStatus[]): SuccessFactorsFront[] {
  const definitions = [
    {
      id: "personal",
      title: "Personal",
      icon: Users,
      terms: ["employee_360", "person", "personal", "employment", "email"],
      metric: widgetValue(widgets, ["employee_360"]),
      unit: "personas activas",
      decision: "Priorizar limpieza de datos de plantilla y cobertura básica.",
    },
    {
      id: "estructura",
      title: "Estructura organizacional",
      icon: Network,
      terms: ["org_structure", "manager_hierarchy", "headcount_by", "department", "division", "location", "businessunit", "business unit", "company", "jobcode", "position", "costcenter", "cost center"],
      metric: widgetValue(widgets, ["org_structure", "manager_hierarchy"]),
      unit: "relaciones consideradas",
      decision: "Revisar huecos de supervisión y distribución por compañía, ubicación y departamento.",
    },
    {
      id: "rotacion",
      title: "Rotación y bajas",
      icon: TrendingDown,
      terms: ["turnover", "termination", "empemploymenttermination", "eventreason", "baja", "rotacion"],
      metric: widgetValue(widgets, ["turnover", "termination"]),
      unit: "eventos considerados",
      decision: "Validar motivos de baja y periodos con mayor salida de personal.",
    },
    {
      id: "reclutamiento",
      title: "Reclutamiento",
      icon: BriefcaseBusiness,
      terms: ["recruitment", "recruiting", "jobrequisition", "candidate", "application"],
      metric: widgetValue(widgets, ["recruitment", "jobrequisition"]),
      unit: "señales disponibles",
      decision: "Validar si FEMSA requiere vacantes, requisiciones y embudo de candidatos.",
    },
    {
      id: "desempeno",
      title: "Desempeño",
      icon: GraduationCap,
      terms: ["performance", "review", "goal", "competency"],
      metric: widgetValue(widgets, ["performance", "review"]),
      unit: "evaluaciones disponibles",
      decision: "Usar evaluaciones solo cuando el cartucho tenga datos suficientes.",
    },
    {
      id: "aprendizaje",
      title: "Aprendizaje",
      icon: BookOpen,
      terms: ["learning", "training", "course", "skill", "competency"],
      metric: widgetValue(widgets, ["learning", "training", "course", "skill"]),
      unit: "registros disponibles",
      decision: "Activar formación y habilidades cuando el alcance de FEMSA lo confirme.",
    },
    {
      id: "compensacion",
      title: "Compensación y pagos",
      icon: CreditCard,
      terms: ["compensation", "paycomp", "payment", "paygroup", "pay group", "payroll", "salary", "amount", "currency"],
      metric: widgetValue(widgets, ["compensation", "paycomp", "payment"]),
      unit: "registros protegidos",
      decision: "Mantener importes sensibles protegidos y usar solo agregados aprobados para decisiones.",
    },
    {
      id: "tiempo",
      title: "Tiempo y asistencia",
      icon: CalendarDays,
      terms: ["employeetime", "timeaccount", "workschedule", "time off", "absence", "leave", "vacation", "schedule"],
      metric: widgetValue(widgets, ["employeetime", "timeaccount", "workschedule"]),
      unit: "registros disponibles",
      decision: "Supervisar ausencias, saldos y horarios cuando el alcance de FEMSA lo habilite.",
    },
  ];

  return definitions.map((definition) => {
    const matchingSources = sources.filter((source) => sourceMatches(source, definition.terms));
    const ready = matchingSources.filter((source) => sourceReadiness(source) === "ready").length;
    const signals = matchingSources.filter((source) => sourceReadiness(source) !== "ready").length;
    const status = businessFrontStatus(matchingSources, definition.metric);
    const countFallback = matchingSources.reduce((total, source) => total + Math.max(0, source.count || 0), 0);
    const value = definition.metric ?? (countFallback > 0 ? countFallback : null);
    const tone = widgetTone(status);

    return {
      id: definition.id,
      title: definition.title,
      metric: value === null ? "N/D" : formatNumber(value),
      detail: value === null ? unavailableBusinessDetail(status) : definition.unit,
      decision: definition.decision,
      status,
      signals,
      ready,
      total: matchingSources.length,
      icon: definition.icon,
      tone,
    };
  });
}

type DecisionCapability = {
  id: string;
  title: string;
  question: string;
  impact: string;
  terms: string[];
  status: SuccessFactorsFront["status"];
  evidence: number;
};

function buildDecisionCapabilities(
  payload: SfDecisionModelPayload | null,
  widgets: SfGoldKpisPayload["widgets"],
  sources: SourceStatus[],
): DecisionCapability[] {
  const modelText = [...modelEntities(payload), ...modelTerms(payload)].map(searchableModelText);
  const definitions = [
    {
      id: "plantilla",
      title: "Distribución de plantilla",
      question: "¿Dónde está concentrada la plantilla activa?",
      impact: "Permite decidir cobertura por compañía, ubicación y departamento.",
      terms: ["headcount", "employee", "employment", "department", "location", "company", "plantilla"],
    },
    {
      id: "rotacion",
      title: "Rotación y bajas",
      question: "¿Qué salidas recientes requieren atención?",
      impact: "Ayuda a priorizar retención y revisar motivos de baja.",
      terms: ["turnover", "termination", "empemploymenttermination", "eventreason", "rotación"],
    },
    {
      id: "supervision",
      title: "Jerarquía de supervisión",
      question: "¿Hay equipos sin cobertura o con carga excesiva?",
      impact: "Reduce riesgos de operación por falta de responsables.",
      terms: ["manager", "hierarchy", "direct_reports", "supervision"],
    },
    {
      id: "riesgos",
      title: "Riesgos de información de personal",
      question: "¿Qué datos incompletos bloquean decisiones?",
      impact: "Evita decisiones con información incompleta o inconsistente.",
      terms: ["anomal", "missing", "quality", "fojobcode", "job_code"],
    },
    {
      id: "reclutamiento",
      title: "Embudo de reclutamiento",
      question: "¿Qué vacantes y candidatos requieren seguimiento?",
      impact: "Da visibilidad a requisiciones abiertas y capacidad de contratación.",
      terms: ["recruitment", "candidate", "jobrequisition", "requisition"],
    },
    {
      id: "composicion",
      title: "Composición de plantilla",
      question: "¿Cómo se compone la plantilla por clase de empleo?",
      impact: "Ayuda a decidir mix operativo y cobertura de roles.",
      terms: ["workforce", "employee_class", "employeeclass", "composition"],
    },
    {
      id: "compensacion",
      title: "Compensación y pagos",
      question: "¿Hay cobertura suficiente para analizar pagos sin exponer datos sensibles?",
      impact: "Mantiene privacidad y evita mostrar importes no aprobados.",
      terms: ["compensation", "paycomp", "payment", "paygroup", "currency", "amount"],
    },
    {
      id: "aprendizaje",
      title: "Aprendizaje y desempeño",
      question: "¿Qué formación, objetivos y evaluaciones requieren seguimiento?",
      impact: "Conecta desarrollo, desempeño y preparación de talento.",
      terms: ["learning", "training", "performance", "goal", "review", "competency"],
    },
    {
      id: "tiempo",
      title: "Tiempo y asistencia",
      question: "¿Qué ausencias, saldos u horarios pueden afectar operación?",
      impact: "Permite anticipar capacidad y cobertura de equipos.",
      terms: ["employeetime", "timeaccount", "workschedule", "absence", "leave", "schedule"],
    },
  ];

  const publicWidgets = widgets ?? [];
  return definitions.map((definition) => {
    const modelMatches = modelText.filter((text) => definition.terms.some((term) => text.includes(term))).length;
    const sourceMatchesCount = sources.filter((source) => sourceMatches(source, definition.terms)).length;
    const widgetMatchesCount = publicWidgets.filter((widget) =>
      definition.terms.some((term) => widgetSearchText(widget).includes(term)),
    ).length;
    const evidence = modelMatches + sourceMatchesCount + widgetMatchesCount;
    const matchingSources = sources.filter((source) => sourceMatches(source, definition.terms));
    const status = businessFrontStatus(matchingSources, widgetMatchesCount > 0 ? widgetMatchesCount : null);
    return { ...definition, status: evidence > 0 && status === "missing" ? "partial" : status, evidence };
  });
}

function businessIssue(source: SourceStatus): string {
  if (source.data_readiness === "no_permission" || source.status === "no_permission") {
    return "Requiere permisos OData para este contexto.";
  }
  if (source.data_readiness === "partial") return "Datos parciales: dato no disponible por alcance actual.";
  if (source.data_readiness === "stub") return "Fuera de alcance actual.";
  if (source.data_readiness === "missing" || source.status === "missing") return "Información no disponible para este contexto.";
  if (source.data_readiness === "empty") return "Sin datos configurados para este alcance.";
  if (source.status === "unavailable") return "Dependencia no configurada o no disponible.";
  if (source.count === 0) return "No aplica para el alcance actual.";
  return `${formatNumber(source.count)} registros considerados.`;
}

export function SuccessFactorsGoldPanel({
  payload,
  loading,
  error,
  sources,
  talent,
  talentLoading = false,
  talentError = "",
  decisionModel,
  decisionModelLoading = false,
  decisionModelError = "",
}: {
  payload: SfGoldKpisPayload | null;
  loading: boolean;
  error: string;
  sources: SourceStatus[];
  talent?: SfTalentKpisPayload | null;
  talentLoading?: boolean;
  talentError?: string;
  decisionModel?: SfDecisionModelPayload | null;
  decisionModelLoading?: boolean;
  decisionModelError?: string;
}) {
  const widgets = payload?.widgets ?? [];
  const readySources = sources.filter((source) => sourceReadiness(source) === "ready").length;
  const blockedSources = sources.filter((source) => source.status === "blocked" || source.data_readiness === "blocked" || source.data_readiness === "no_permission").length;
  const employeeWidget = widgets.find((widget) => widgetSearchText(widget).includes("employee_360"));
  const orgWidget = widgets.find((widget) => widgetSearchText(widget).includes("org_structure"));
  const headcountWidgets = widgets.filter((widget) => widgetSearchText(widget).includes("headcount"));
  const totalSignals = sources.filter((source) => sourceReadiness(source) !== "ready").length;
  const businessFronts = buildBusinessFronts(widgets, sources);
  const decisionCapabilities = buildDecisionCapabilities(decisionModel ?? null, widgets, sources);
  const readyCapabilities = decisionCapabilities.filter((item) => item.status === "ready" || item.status === "ok").length;
  const employeeStatus = widgetStatus(employeeWidget);
  const orgStatus = widgetStatus(orgWidget);
  const readyHeadcountWidgets = headcountWidgets.filter((widget) => ["ready", "ok", "empty"].includes(widgetStatus(widget)));

  const talentWidgets = talent?.widgets ?? [];
  const talentSignals = talent?.signals ?? [];
  const talentBlockers = talent?.blockers ?? [];
  const talentWidgetById = (id: string) => talentWidgets.find((widget) => widget.id === id);
  const talentNumber = (id: string) => {
    const value = talentWidgetById(id)?.value;
    return typeof value === "number" ? value : null;
  };
  const talentReadinessTotal = talent?.readiness?.profiled_employees ?? talentNumber("sf_talent_profiled_employees") ?? 0;
  const talentReadinessCalculable = talent?.readiness?.calculable_employees ?? talentNumber("sf_talent_readiness_calculable") ?? 0;
  const talentNineBoxAvailable = talent?.readiness?.nine_box_available ?? talentNumber("sf_talent_9box_available") ?? 0;
  // Fase 3 P0: Workforce Trends — fuente unica (bundle del backend); no se agrega en frontend.
  const workforceTrends = talent?.workforce_trends;
  const wtKpis = workforceTrends?.kpis;
  const wtSeries = workforceTrends?.series;

  return (
    <section className="overflow-hidden rounded-xl border bg-card shadow-sm dark:border-emerald-400/20 dark:bg-[#081423] dark:shadow-[0_0_30px_rgba(16,185,129,0.08)]" aria-label="Indicadores ejecutivos de personal">
      <div className="border-b bg-gradient-to-r from-emerald-500/10 via-cyan-500/10 to-transparent p-4 dark:border-emerald-400/20 dark:from-emerald-400/10 dark:via-cyan-400/10">
        <div className="flex flex-col gap-3 xl:flex-row xl:items-start xl:justify-between">
          <div className="min-w-0">
            <p className="text-xs font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-300/90">FEMSA · SuccessFactors</p>
            <h2 className="mt-1 text-xl font-semibold text-foreground dark:text-white">Centro ejecutivo de personal</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Vista de plantilla, estructura y riesgos organizacionales alimentada por información real del contexto activo.
              {payload?.generated_at ? ` Actualizado ${new Date(payload.generated_at).toLocaleString("es-MX")}.` : ""}
            </p>
          </div>
          <div className="grid min-w-[260px] grid-cols-2 gap-2 rounded-lg border bg-background/80 p-3 dark:border-emerald-400/15 dark:bg-[#06111f]">
            <span className="text-xs text-muted-foreground">Cobertura</span>
            <strong className="text-right text-sm text-foreground dark:text-white">{readySources}/{sources.length || 0}</strong>
            <span className="text-xs text-muted-foreground">Señales de datos</span>
            <strong className={cn("text-right text-sm", totalSignals ? "text-amber-700 dark:text-amber-300" : "text-emerald-700 dark:text-emerald-300")}>{totalSignals}</strong>
          </div>
        </div>
      </div>

      <div className="space-y-4 p-4">
        {loading ? <OperationalNotice tone="info" title="Actualizando indicadores">Consultando información real de SuccessFactors.</OperationalNotice> : null}
        {error ? (
          <OperationalNotice tone="error" title="No se pudo actualizar información ejecutiva">
            La vista conserva el estado honesto y no inventa valores.
          </OperationalNotice>
        ) : null}
        {!loading && !error && widgets.length === 0 ? (
          <OperationalNotice tone="warning" title="Información ejecutiva no disponible">No hay indicadores reales para este contexto; no se muestran números simulados.</OperationalNotice>
        ) : null}

        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <CommandMetric
            label="Plantilla activa"
            value={widgetValueText(employeeWidget)}
            detail={employeeWidget && typeof employeeWidget.value === "number" ? "personas consideradas" : unavailableBusinessDetail(employeeStatus)}
            icon={Users}
            tone={widgetTone(employeeStatus)}
          />
          <CommandMetric
            label="Estructura organizacional"
            value={widgetValueText(orgWidget)}
            detail={orgWidget && typeof orgWidget.value === "number" ? "relaciones disponibles" : unavailableBusinessDetail(orgStatus)}
            icon={Network}
            tone={widgetTone(orgStatus)}
          />
          <CommandMetric
            label="Distribuciones"
            value={readyHeadcountWidgets.length}
            detail={headcountWidgets.length ? "compañía, ubicación y departamento" : "Información no disponible"}
            icon={Building2}
            tone={readyHeadcountWidgets.length ? "good" : "warning"}
          />
          <CommandMetric
            label="Riesgos de información"
            value={blockedSources}
            detail={blockedSources ? "requieren revisión" : "sin bloqueos visibles"}
            icon={AlertTriangle}
            tone={blockedSources ? "warning" : "good"}
          />
        </div>

        {talentLoading ? <OperationalNotice tone="info" title="Actualizando Talento">Consultando WisdomBit Talento y cobertura C/P/A.</OperationalNotice> : null}
        {talentError ? (
          <OperationalNotice tone="warning" title="Talento parcialmente disponible">
            No se pudo actualizar el bloque de Talento; la vista mantiene los bloqueos visibles.
          </OperationalNotice>
        ) : null}
        {talent ? (
          <div className="rounded-xl border bg-background p-4 shadow-sm dark:border-violet-400/15 dark:bg-[#06111f]">
            <div className="mb-4 flex flex-col gap-2 lg:flex-row lg:items-start lg:justify-between">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-violet-700 dark:text-violet-300/80">
                  WB-TALENTO · {talent.profile?.industry || "Talento"}/{talent.profile?.company_profile || "Organización"}
                </p>
                <h3 className="text-lg font-semibold text-foreground dark:text-white">Talento, readiness y 9-box</h3>
                <p className="mt-1 text-sm text-muted-foreground">
                  Recomendaciones sin write-back, compensación apagada y C/P/A bloqueado hasta validar metadata SAP.
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <ReadinessBadge status={talent.readiness?.status || "missing"} compact />
                <a
                  href="/control-room/talent"
                  className="inline-flex min-h-[32px] items-center gap-1.5 rounded-md border bg-card px-3 py-1.5 text-xs font-semibold text-foreground transition hover:bg-muted dark:border-violet-400/20 dark:bg-[#081423] dark:text-white dark:hover:bg-white/5"
                >
                  Abrir Talento
                  <ArrowRight aria-hidden className="h-3.5 w-3.5" />
                </a>
              </div>
            </div>

            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
              <CommandMetric
                label="Readiness calculable"
                value={`${formatNumber(talentReadinessCalculable)}/${formatNumber(talentReadinessTotal)}`}
                detail={`${formatNumber(talent.readiness?.insufficient_data_employees ?? 0)} con información insuficiente`}
                icon={ShieldCheck}
                tone={talentReadinessCalculable ? "good" : "warning"}
              />
              <CommandMetric
                label="9-box disponible"
                value={formatNumber(talentNineBoxAvailable)}
                detail={talentNineBoxAvailable ? "personas clasificables" : "bloqueado por C/P/A"}
                icon={Network}
                tone={talentNineBoxAvailable ? "good" : "warning"}
              />
              <CommandMetric
                label="Roles derivados"
                value={formatNumber(talentNumber("sf_talent_roles_profiled") ?? 0)}
                detail="desde job_code y FOJobCode"
                icon={BriefcaseBusiness}
                tone={(talentNumber("sf_talent_roles_profiled") ?? 0) ? "good" : "warning"}
              />
              <CommandMetric
                label="Señales Talento"
                value={formatNumber(talentSignals.length)}
                detail="solo recomendaciones"
                icon={GraduationCap}
                tone={talentSignals.length ? "warning" : "neutral"}
              />
            </div>

            {workforceTrends ? (
              <div className="mt-4 rounded-lg border bg-card p-4 dark:border-cyan-400/15 dark:bg-[#06131f]">
                <div className="flex items-center justify-between gap-2">
                  <p className="text-xs font-semibold uppercase tracking-wide text-cyan-700 dark:text-cyan-300/80">Workforce Trends</p>
                  <span className="text-xs text-muted-foreground">
                    {wtSeries?.months?.length
                      ? "serie mensual por cohorte · una sola fuente"
                      : "En espera de datos"}
                  </span>
                </div>
                <div className="mt-3 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                  <CommandMetric
                    label="Plantilla activa"
                    value={wtKpis?.active_headcount != null ? formatNumber(wtKpis.active_headcount) : "—"}
                    detail="empleados activos"
                    icon={Users}
                    tone={wtKpis?.active_headcount ? "good" : "neutral"}
                  />
                  <CommandMetric
                    label="Antigüedad promedio"
                    value={wtKpis?.avg_tenure_months != null ? `${(wtKpis.avg_tenure_months / 12).toFixed(1)} años` : "—"}
                    detail="ponderada por cohorte"
                    icon={CalendarDays}
                    tone={wtKpis?.avg_tenure_months ? "good" : "neutral"}
                  />
                  <CommandMetric
                    label="Rotación"
                    value={wtKpis?.attrition_rate != null ? `${(wtKpis.attrition_rate * 100).toFixed(1)}%` : "—"}
                    detail="último mes completo"
                    icon={TrendingDown}
                    tone={wtKpis?.attrition_rate ? "warning" : "good"}
                  />
                  <CommandMetric
                    label="Meses de historia"
                    value={wtKpis?.history_months != null ? formatNumber(wtKpis.history_months) : "—"}
                    detail="serie mensual por cohorte"
                    icon={Activity}
                    tone={wtKpis?.history_months ? "good" : "neutral"}
                  />
                </div>
                {wtSeries ? (
                  <div className="mt-4 grid gap-4 md:grid-cols-3">
                    <Sparkline label="Headcount" values={(wtSeries.headcount ?? []).slice(-12)} format={(value) => formatNumber(Math.round(value))} tone="good" />
                    <Sparkline label="Antigüedad" values={(wtSeries.avg_tenure_months ?? []).slice(-12)} format={(value) => `${(value / 12).toFixed(1)} a`} tone="neutral" />
                    <Sparkline label="Rotación" values={(wtSeries.attrition_rate ?? []).slice(-12)} format={(value) => `${(value * 100).toFixed(1)}%`} tone="warning" />
                  </div>
                ) : null}
              </div>
            ) : null}

            <div className="mt-4 grid gap-3 lg:grid-cols-2">
              <div className="rounded-lg border bg-card p-3 dark:border-violet-400/15 dark:bg-[#081423]">
                <p className="text-xs font-semibold uppercase tracking-wide text-violet-700 dark:text-violet-300/80">Bloqueos reales</p>
                <div className="mt-3 space-y-3">
                  {talentBlockers.slice(0, 3).map((blocker, index) => (
                    <div key={`${blocker.id || blocker.title || "blocker"}:${index}`} className="rounded-md border bg-background/70 p-3 text-sm dark:border-violet-400/10 dark:bg-[#06111f]">
                      <div className="flex items-start justify-between gap-2">
                        <strong className="text-foreground dark:text-white">{blocker.title || "Información pendiente"}</strong>
                        <ReadinessBadge status={blocker.status || "missing"} compact />
                      </div>
                    </div>
                  ))}
                  {!talentBlockers.length ? <p className="text-sm text-muted-foreground">Sin bloqueos reportados para Talento.</p> : null}
                </div>
              </div>

              <div className="rounded-lg border bg-card p-3 dark:border-violet-400/15 dark:bg-[#081423]">
                <p className="text-xs font-semibold uppercase tracking-wide text-violet-700 dark:text-violet-300/80">Señales WisdomBit</p>
                <div className="mt-3 space-y-3">
                  {talentSignals.slice(0, 3).map((signal, index) => (
                    <div key={`${signal.id || signal.title || "signal"}:${index}`} className="rounded-md border bg-background/70 p-3 text-sm dark:border-violet-400/10 dark:bg-[#06111f]">
                      <div className="flex items-start justify-between gap-2">
                        <strong className="text-foreground dark:text-white">{signal.title || "Señal de talento"}</strong>
                        <span className="rounded-full border px-2 py-0.5 text-xs font-medium text-muted-foreground dark:border-violet-400/15">
                          {signal.severity || "info"}
                        </span>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">{signal.recommendation || "Revisar cobertura antes de decidir."}</p>
                      <p className="mt-2 text-xs font-medium text-violet-700 dark:text-violet-300">
                        {formatNumber(signal.affected_count ?? 0)} afectados · {signal.status || "recommendation_only"}
                      </p>
                    </div>
                  ))}
                  {!talentSignals.length ? <p className="text-sm text-muted-foreground">Sin señales activas para este contexto.</p> : null}
                </div>
              </div>
            </div>
          </div>
        ) : null}

        <div className="rounded-xl border bg-background p-4 shadow-sm dark:border-cyan-400/15 dark:bg-[#06111f]">
          <div className="mb-4 flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-cyan-700 dark:text-cyan-300/80">Mapa ejecutivo SuccessFactors</p>
              <h3 className="text-lg font-semibold text-foreground dark:text-white">Frentes que importan al negocio</h3>
            </div>
            <p className="text-sm text-muted-foreground">Cada frente se alimenta de datos reales del cartucho FEMSA.</p>
          </div>
          <div className="grid gap-3 md:grid-cols-2 2xl:grid-cols-5">
            {businessFronts.map((front) => (
              <article key={front.id} className="rounded-lg border bg-card p-3 shadow-sm dark:border-cyan-400/15 dark:bg-[#081423]">
                <div className="flex items-start justify-between gap-2">
                  <span className={cn("grid h-9 w-9 place-items-center rounded-md border", front.tone === "good" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300" : "", front.tone === "warning" ? "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300" : "", front.tone === "danger" ? "border-red-500/30 bg-red-500/10 text-red-700 dark:text-red-300" : "", front.tone === "neutral" ? "border-cyan-500/20 bg-cyan-500/10 text-cyan-700 dark:text-cyan-200" : "")}>
                    <front.icon aria-hidden className="h-4 w-4" />
                  </span>
                  <ReadinessBadge status={front.status} compact />
                </div>
                <h4 className="mt-3 text-sm font-semibold text-foreground dark:text-white">{front.title}</h4>
                <p className="mt-2 text-2xl font-semibold tabular-nums text-foreground dark:text-white">{front.metric}</p>
                <p className="text-xs text-muted-foreground">{front.detail}</p>
                <MiniBar value={front.ready} max={Math.max(1, front.total)} label="cobertura" tone={front.tone === "good" ? "good" : front.tone === "danger" ? "danger" : "warning"} />
                <p className="mt-3 min-h-[44px] text-xs text-muted-foreground">{front.decision}</p>
                {front.signals ? <p className="mt-2 text-xs font-medium text-amber-700 dark:text-amber-300">{front.signals} señales por revisar</p> : null}
              </article>
            ))}
          </div>
        </div>

        <div className="rounded-xl border bg-background p-4 shadow-sm dark:border-sky-400/15 dark:bg-[#06111f]">
          <div className="mb-4 flex flex-col gap-2 lg:flex-row lg:items-end lg:justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-wide text-sky-700 dark:text-sky-300/80">Decisiones OMEGA</p>
              <h3 className="text-lg font-semibold text-foreground dark:text-white">Qué se puede decidir con SuccessFactors</h3>
              <p className="mt-1 text-sm text-muted-foreground">
                OMEGA traduce la información disponible en preguntas, impacto y acciones supervisables para dirección.
              </p>
            </div>
            <div className="rounded-lg border bg-card px-3 py-2 text-sm dark:border-sky-400/15 dark:bg-[#081423]">
              <span className="text-muted-foreground">Capacidades listas </span>
              <strong className="text-foreground dark:text-white">{readyCapabilities}/{decisionCapabilities.length}</strong>
            </div>
          </div>

          {decisionModelLoading ? (
            <OperationalNotice tone="info" title="Actualizando decisiones">
              Revisando las capacidades del cartucho para mostrar solo decisiones con respaldo real.
            </OperationalNotice>
          ) : null}
          {decisionModelError ? (
            <div className="mb-3">
              <OperationalNotice tone="warning" title="Decisiones parcialmente disponibles">
                No se pudo actualizar la traducción ejecutiva completa. La pantalla no marca nada como listo sin respaldo.
              </OperationalNotice>
            </div>
          ) : null}

          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {decisionCapabilities.map((capability) => (
              <article key={capability.id} className="rounded-lg border bg-card p-3 shadow-sm dark:border-sky-400/15 dark:bg-[#081423]">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h4 className="text-sm font-semibold text-foreground dark:text-white">{capability.title}</h4>
                    <p className="mt-1 text-xs text-muted-foreground">{capability.question}</p>
                  </div>
                  <ReadinessBadge status={capability.status} compact />
                </div>
                <div className="mt-3 rounded-md border bg-background/70 p-3 text-sm dark:border-sky-400/10 dark:bg-[#06111f]">
                  <p className="text-xs font-semibold uppercase tracking-wide text-sky-700 dark:text-sky-300/80">Por qué importa</p>
                  <p className="mt-1 text-muted-foreground">{capability.impact}</p>
                </div>
                <div className="mt-3 flex items-center justify-between gap-3 text-xs">
                  <span className="text-muted-foreground">
                    {capability.evidence > 0 ? `${capability.evidence} señales internas consideradas` : "Fuera de alcance actual"}
                  </span>
                  <span className="rounded-full border px-2 py-1 font-medium text-foreground dark:border-sky-400/15 dark:text-white">
                    Acción supervisada
                  </span>
                </div>
              </article>
            ))}
          </div>
        </div>

        {widgets.length ? (
          <div className="grid gap-3 xl:grid-cols-2">
            {widgets.map((widget, widgetIndex) => {
              const rows = widget.rows ?? [];
              const maxHeadcount = Math.max(1, ...rows.map((row) => typeof row.headcount === "number" ? row.headcount : 0));
              const status = widgetStatus(widget);
              return (
                <article key={`${widget.id || widget.title || "indicator"}:${widgetIndex}`} className="rounded-xl border bg-background p-4 shadow-sm dark:border-emerald-400/15 dark:bg-[#06111f]">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="text-xs font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-300/80">{businessWidgetTitle(widget)}</p>
                      <strong className="mt-1 block text-2xl font-semibold text-foreground dark:text-white">{widgetValueText(widget)}</strong>
                      <p className="text-xs text-muted-foreground">{typeof widget.value === "number" ? businessWidgetDetail(widget) : unavailableBusinessDetail(status)}</p>
                    </div>
                    <ReadinessBadge status={status} compact />
                  </div>
                  <div className="mt-4 space-y-3">
                    {rows.slice(0, 6).map((row, index) => {
                      const headcount = typeof row.headcount === "number" ? row.headcount : 0;
                      return (
                        <div key={`${widget.id || widget.title || "indicator"}:${index}`} className="space-y-1">
                          <div className="flex items-center justify-between gap-3 text-sm">
                            <span className="min-w-0 truncate text-muted-foreground">{rowLabel(row)}</span>
                            {typeof row.headcount === "number" ? <strong className="tabular-nums text-foreground dark:text-white">{formatNumber(headcount)}</strong> : null}
                          </div>
                          {typeof row.headcount === "number" ? <MiniBar value={headcount} max={maxHeadcount} /> : null}
                        </div>
                      );
                    })}
                    {!rows.length ? <p className="text-sm text-muted-foreground">Sin desglose disponible para este indicador.</p> : null}
                  </div>
                </article>
              );
            })}
          </div>
        ) : null}

        {sources.length ? (
          <div className="rounded-xl border bg-background p-3 shadow-sm dark:border-emerald-400/15 dark:bg-[#06111f]">
            <div className="mb-3 flex items-center justify-between gap-3">
              <p className="text-sm font-semibold text-foreground dark:text-white">Calidad de información para decisiones</p>
              <span className="text-xs text-muted-foreground">{readySources}/{sources.length} listos</span>
            </div>
            <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              {sources.slice(0, 9).map((source, index) => (
                <article
                  key={`${source.module || source.domain || source.cartridge || "source"}:${index}`}
                  className="rounded-md border bg-card p-3 text-sm shadow-sm dark:border-sky-400/15 dark:bg-[#081423]"
                >
                  <div className="flex items-start justify-between gap-2">
                    <span className="min-w-0 truncate font-medium text-foreground dark:text-white">{businessSourceLabel(source)}</span>
                    <ReadinessBadge status={source.data_readiness || source.status || "missing"} compact />
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{businessIssue(source)}</p>
                </article>
              ))}
            </div>
          </div>
        ) : null}

        <div className="grid gap-3 rounded-xl border bg-background p-4 dark:border-emerald-400/15 dark:bg-[#06111f] md:grid-cols-[1fr_auto]">
          <div>
            <p className="text-xs font-semibold uppercase text-emerald-700 dark:text-emerald-300/80">Recomendación OMEGA</p>
            <h3 className="mt-1 text-lg font-semibold text-foreground dark:text-white">Priorizar decisiones con información completa</h3>
            <p className="mt-1 text-sm text-muted-foreground">
              Usa las vistas listas para aprobar acciones. Las vistas incompletas quedan visibles como riesgo y no se convierten en éxito falso.
            </p>
          </div>
          <div className="flex items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm dark:border-emerald-400/15 dark:bg-[#081423]">
            <ShieldCheck aria-hidden className="h-4 w-4 text-emerald-600 dark:text-emerald-300" />
            <span className="font-medium text-foreground dark:text-white">Acción supervisada</span>
          </div>
        </div>
      </div>
    </section>
  );
}
