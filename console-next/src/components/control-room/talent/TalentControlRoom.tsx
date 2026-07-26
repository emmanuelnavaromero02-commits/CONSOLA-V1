"use client";

import {
  AlertTriangle,
  Boxes,
  BriefcaseBusiness,
  CheckCircle2,
  Filter,
  Grid3X3,
  Loader2,
  RefreshCcw,
  ShieldCheck,
  SlidersHorizontal,
  Table2,
  Target,
  UserRoundCheck,
  Users,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  getSuccessFactorsTalentAnomalies,
  getSuccessFactorsTalentBoxRoster,
  getSuccessFactorsTalentMetadataReadiness,
  getSuccessFactorsTalentNineBox,
  getSuccessFactorsTalentOverview,
} from "@/lib/control-room/client";
import type {
  SfTalentAnomaliesPayload,
  SfTalentAnomaly,
  SfTalentDesempenoCohort,
  SfTalentExtractionTarget,
  SfTalentMetadataReadinessPayload,
  SfTalentNineBoxCell,
  SfTalentNineBoxPayload,
  SfTalentOverviewPayload,
  SfTalentRosterPayload,
} from "@/lib/control-room/types";
import { isApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

import { CommandMetric, MiniBar, OperationalNotice, ReadinessBadge } from "../StatusBadge";
import type { ControlRoomStatus } from "../StatusBadge";

type Collar = "confianza" | "sindicalizado";

function formatNumber(value: number | null | undefined): string {
  return new Intl.NumberFormat("es-MX").format(value ?? 0);
}

function bandLabel(value?: string): string {
  if (value === "high") return "Alto";
  if (value === "medium") return "Medio";
  if (value === "low") return "Bajo";
  if (value === "insufficient_data") return "Sin datos";
  return value || "N/D";
}

function apiMessage(error: unknown): string {
  if (isApiError(error)) return error.message;
  if (error instanceof Error) return error.message;
  return "No se pudo completar la consulta.";
}

function cellTone(cell: SfTalentNineBoxCell): string {
  if ((cell.status === "ready" || cell.status === "benchmark_internal") && cell.performance_band === "high") {
    return "border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-200";
  }
  if (cell.status === "ready" || cell.status === "benchmark_internal") {
    return "border-cyan-500/30 bg-cyan-500/10 text-cyan-800 dark:text-cyan-200";
  }
  if (cell.status === "empty" || cell.employee_count === 0) {
    return "border-slate-500/25 bg-slate-500/10 text-slate-700 dark:text-slate-300";
  }
  if (cell.performance_band === "low" || cell.potential_band === "low") {
    return "border-rose-500/25 bg-rose-500/10 text-rose-800 dark:text-rose-200";
  }
  return "border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-200";
}

function extractionTargets(metadata: SfTalentMetadataReadinessPayload | null): SfTalentExtractionTarget[] {
  const targets = metadata?.live_preflight?.extraction_targets;
  return Array.isArray(targets) ? targets : [];
}

function metadataNextTargets(metadata: SfTalentMetadataReadinessPayload | null): SfTalentExtractionTarget[] {
  const liveTargets = extractionTargets(metadata);
  if (liveTargets.length) return liveTargets;
  return (metadata?.entities ?? []).map((entity) => ({
    component: entity.id,
    component_label: entity.kb,
    entity: entity.live_selected_entity || entity.odata_entity || entity.entity,
    odata_entity: entity.odata_entity || entity.live_selected_entity || undefined,
    status: entity.live_status || entity.status,
    ready_to_extract: entity.ready_to_extract,
    fields_found: entity.fields_found,
    fields_missing: entity.fields_missing,
  }));
}

function normalizeReadinessStatus(status?: string | null): ControlRoomStatus {
  const normalized = String(status || "missing").trim();
  if (
    normalized === "ready" ||
    normalized === "ok" ||
    normalized === "available" ||
    normalized === "partial" ||
    normalized === "stub" ||
    normalized === "empty" ||
    normalized === "missing" ||
    normalized === "unavailable" ||
    normalized === "invalid_schema" ||
    normalized === "blocked" ||
    normalized === "no_permission" ||
    normalized === "attention" ||
    normalized === "inactive" ||
    normalized === "no_sources" ||
    normalized === "benchmark_internal" ||
    normalized === "insufficient_data" ||
    normalized === "blocked_by_sap" ||
    normalized === "blocked_by_permission" ||
    normalized === "pending_approval" ||
    normalized === "partial_fields" ||
    normalized === "error"
  ) {
    return normalized;
  }
  if (normalized === "metadata_ready" || normalized === "ready_to_extract") return "partial";
  if (normalized === "entity_not_exposed_in_sap") return "blocked_by_sap";
  if (normalized === "permission_denied") return "blocked_by_permission";
  return "missing";
}

function readinessSummaryCopy(overview: SfTalentOverviewPayload | null, nineBox: SfTalentNineBoxPayload | null) {
  const sourceMode = String(overview?.readiness.source_mode || "");
  const referenceCount = nineBox?.totals.reference ?? 0;
  if (sourceMode === "cpa_real") {
    return { label: "Readiness SAP real", detail: "datos C/P/A del tenant" };
  }
  if (sourceMode === "benchmark_internal" || referenceCount > 0) {
    return { label: "Readiness con referencia", detail: "referencia interna aprobada" };
  }
  return { label: "Readiness", detail: "en espera de C/P/A o referencia" };
}

const talentComponents = [
  { id: "performance", label: "Performance" },
  { id: "competency", label: "Competencias" },
  { id: "aspiration", label: "Aspiración" },
  { id: "roles", label: "Roles" },
  { id: "learning", label: "Learning" },
  { id: "recruiting", label: "Recruiting" },
] as const;

function talentComponentReadiness(metadata: SfTalentMetadataReadinessPayload | null) {
  const entities = new Map((metadata?.entities ?? []).map((entity) => [entity.id, entity]));
  return talentComponents.map((component) => {
    const entity = entities.get(component.id);
    const status = normalizeReadinessStatus(entity?.live_status || entity?.status);
    const blockers = entity?.blockers ?? ["metadata/materialización pendiente"];
    // Desempeño presente pero C/P/A incompleto: copy explícito (no solo "Disponible")
    // y la nota se trata como "pendiente" (ámbar), nunca como bloqueo (naranja).
    const performanceAvailable = component.id === "performance" && status === "available";
    return {
      ...component,
      status,
      entity: entity?.entity || "pendiente",
      blockers,
      badgeLabel: performanceAvailable ? "Desempeño disponible" : undefined,
      note: performanceAvailable
        ? "Potencial pendiente (faltan Competencias y Aspiración)"
        : blockers.slice(0, 2).join(" · ") || "Sin blockers",
      notePending: performanceAvailable,
    };
  });
}

export function TalentOverviewPanel({
  overview,
  nineBox,
  anomalies,
  metadata,
}: {
  overview: SfTalentOverviewPayload | null;
  nineBox: SfTalentNineBoxPayload | null;
  anomalies: SfTalentAnomaliesPayload | null;
  metadata: SfTalentMetadataReadinessPayload | null;
}) {
  const profiled = overview?.readiness.profiled_employees ?? 0;
  const calculable = overview?.readiness.calculable_employees ?? 0;
  const classified = nineBox?.totals.ready ?? overview?.nine_box.totals?.ready ?? 0;
  const blockedEntities = metadata?.summary.blocked_entities ?? 0;
  const activeSignals = anomalies?.summary.total ?? overview?.anomalies.summary?.total ?? 0;
  const readinessCopy = readinessSummaryCopy(overview, nineBox);

  return (
    <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4" aria-label="Resumen Talento">
      <CommandMetric
        label="Perfilados"
        value={formatNumber(profiled)}
        detail="colaboradores con perfil Talent"
        icon={Users}
        tone={profiled ? "good" : "warning"}
      />
      <CommandMetric
        label={readinessCopy.label}
        value={`${formatNumber(calculable)}/${formatNumber(profiled)}`}
        detail={readinessCopy.detail}
        icon={ShieldCheck}
        tone={calculable ? "good" : "warning"}
      />
      <CommandMetric
        label="9-box clasificado"
        value={formatNumber(classified)}
        detail="sin nombres ni IDs crudos"
        icon={Grid3X3}
        tone={classified ? "good" : "warning"}
      />
      <CommandMetric
        label="Senales activas"
        value={formatNumber(activeSignals)}
        detail={`${formatNumber(blockedEntities)} entidades bloqueadas`}
        icon={AlertTriangle}
        tone={activeSignals || blockedEntities ? "warning" : "neutral"}
      />
    </section>
  );
}

export function TalentCollarSegmenter({
  value,
  onChange,
}: {
  value: Collar;
  onChange: (value: Collar) => void;
}) {
  const options: Array<{ id: Collar; label: string; detail: string }> = [
    { id: "confianza", label: "Confianza", detail: "9-box talento" },
    { id: "sindicalizado", label: "Sindicalizado", detail: "Escalafon futuro" },
  ];

  return (
    <div className="inline-grid grid-cols-2 rounded-lg border bg-background p-1 dark:border-sky-400/15 dark:bg-[#06111f]">
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          onClick={() => onChange(option.id)}
          className={cn(
            "min-h-[52px] rounded-md px-4 py-2 text-left text-sm transition-colors",
            value === option.id
              ? "bg-sky-500 text-white shadow-sm"
              : "text-muted-foreground hover:bg-muted/60 dark:hover:bg-white/5",
          )}
        >
          <span className="block font-semibold">{option.label}</span>
          <span className={cn("block text-xs", value === option.id ? "text-white/85" : "text-muted-foreground")}>
            {option.detail}
          </span>
        </button>
      ))}
    </div>
  );
}

export function NineBoxMatrix({
  cells,
  selectedBoxId,
  onSelect,
  disabled = false,
}: {
  cells: SfTalentNineBoxCell[];
  selectedBoxId?: string | null;
  onSelect: (boxId: string) => void;
  disabled?: boolean;
}) {
  const ordered = [...cells].sort((a, b) => a.display_order - b.display_order);
  return (
    <section className="rounded-xl border bg-card p-4 shadow-sm dark:border-cyan-400/20 dark:bg-[#081423]">
      <div className="mb-4 flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-cyan-700 dark:text-cyan-300/80">Matriz 9-box</p>
          <h2 className="text-lg font-semibold text-foreground dark:text-white">Potencial x desempeno</h2>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Target aria-hidden className="h-4 w-4" />
          <span>Roster siempre enmascarado</span>
        </div>
      </div>
      <div className="grid gap-2 md:grid-cols-3">
        {ordered.map((cell) => {
          const referenceCount = cell.reference_count ?? 0;
          const visibleCount = cell.ready_count || cell.employee_count;
          const statusLabel =
            referenceCount > 0
              ? "Referencia interna"
              : cell.ready_count > 0
                ? "Datos SAP reales"
                : cell.employee_count === 0
                  ? "Sin empleados"
                  : undefined;
          const badgeStatus: ControlRoomStatus =
            referenceCount > 0 ? "benchmark_internal" : cell.employee_count === 0 ? "empty" : normalizeReadinessStatus(cell.status);
          return (
            <button
              key={cell.box_id}
              type="button"
              disabled={disabled}
              onClick={() => onSelect(cell.box_id)}
              className={cn(
                "min-h-[132px] rounded-lg border p-3 text-left transition hover:-translate-y-0.5 hover:shadow-md disabled:cursor-not-allowed disabled:opacity-60",
                cellTone(cell),
                selectedBoxId === cell.box_id ? "ring-2 ring-sky-500" : "",
              )}
            >
              <div className="flex items-start justify-between gap-2">
                <strong className="text-sm">{cell.box_label}</strong>
                <ReadinessBadge status={badgeStatus} label={statusLabel} compact />
              </div>
              <p className="mt-2 text-3xl font-semibold tabular-nums">{formatNumber(visibleCount)}</p>
              <p className="text-xs opacity-80">
                {bandLabel(cell.potential_band)} potencial · {bandLabel(cell.performance_band)} desempeno
              </p>
              <MiniBar
                value={cell.ready_count}
                max={Math.max(1, cell.employee_count)}
                label="clasificables"
                tone={cell.ready_count > 0 ? "good" : "warning"}
              />
              <p className="mt-2 min-h-[34px] text-xs opacity-85">{cell.movement_action}</p>
            </button>
          );
        })}
      </div>
    </section>
  );
}

export function MaskedTalentRoster({
  payload,
  loading,
}: {
  payload: SfTalentRosterPayload | null;
  loading: boolean;
}) {
  return (
    <section className="rounded-xl border bg-card p-4 shadow-sm dark:border-slate-500/30 dark:bg-[#081423]">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-700 dark:text-slate-300">Roster enmascarado</p>
          <h3 className="text-base font-semibold text-foreground dark:text-white">
            {payload?.box.box_label || "Selecciona una caja"}
          </h3>
        </div>
        <ReadinessBadge status={payload?.status || "missing"} compact />
      </div>
      {loading ? (
        <div className="flex min-h-[220px] items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
          Cargando roster seguro
        </div>
      ) : payload?.roster.length ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[680px] text-left text-sm">
            <thead className="text-xs uppercase text-muted-foreground">
              <tr className="border-b dark:border-slate-500/20">
                <th className="py-2 pr-3 font-semibold">Colaborador</th>
                <th className="py-2 pr-3 font-semibold">Rol</th>
                <th className="py-2 pr-3 font-semibold">Unidad</th>
                <th className="py-2 pr-3 font-semibold">Region</th>
                <th className="py-2 pr-3 font-semibold">Fit</th>
                <th className="py-2 pr-3 font-semibold">Movimiento</th>
              </tr>
            </thead>
            <tbody>
              {payload.roster.slice(0, 8).map((row) => (
                <tr key={row.employee_key} className="border-b last:border-0 dark:border-slate-500/10">
                  <td className="py-2 pr-3">
                    <span className="font-medium text-foreground dark:text-white">{row.display_name}</span>
                    <span className="block text-xs text-muted-foreground">{row.employee_key}</span>
                  </td>
                  <td className="py-2 pr-3 text-muted-foreground">{row.role}</td>
                  <td className="py-2 pr-3 text-muted-foreground">{row.unit}</td>
                  <td className="py-2 pr-3 text-muted-foreground">{row.region}</td>
                  <td className="py-2 pr-3">
                    <ReadinessBadge status={row.fit_band === "high" ? "ready" : row.fit_band === "medium" ? "partial" : "blocked"} label={bandLabel(row.fit_band)} compact />
                  </td>
                  <td className="py-2 pr-3 text-muted-foreground">{row.movement_age_bucket}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="min-h-[220px] rounded-lg border border-dashed p-6 text-sm text-muted-foreground dark:border-slate-500/20">
          <Table2 aria-hidden className="mb-3 h-5 w-5" />
          Selecciona una caja con empleados clasificados. Si no hay empleados, esta caja esta vacia para la corrida actual.
        </div>
      )}
    </section>
  );
}

export function TalentAnomalyList({
  anomalies,
  selectedId,
  onSelect,
}: {
  anomalies: SfTalentAnomaly[];
  selectedId?: string | null;
  onSelect: (item: SfTalentAnomaly) => void;
}) {
  return (
    <section className="rounded-xl border bg-card p-4 shadow-sm dark:border-amber-400/20 dark:bg-[#081423]">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-amber-700 dark:text-amber-300/80">Anomalias</p>
          <h3 className="text-base font-semibold text-foreground dark:text-white">Senales WB-TALENTO</h3>
        </div>
        <Filter aria-hidden className="h-4 w-4 text-muted-foreground" />
      </div>
      <div className="space-y-2">
        {anomalies.map((item) => (
          <button
            key={item.id}
            type="button"
            onClick={() => onSelect(item)}
            className={cn(
              "w-full rounded-lg border bg-background p-3 text-left transition hover:border-amber-500/40 dark:bg-[#06111f]",
              selectedId === item.id ? "border-amber-500/50 ring-1 ring-amber-500/30" : "dark:border-amber-400/10",
            )}
          >
            <div className="flex items-start justify-between gap-3">
              <strong className="text-sm text-foreground dark:text-white">{item.title}</strong>
              <span className="rounded-full border px-2 py-0.5 text-xs font-medium text-muted-foreground dark:border-amber-400/20">
                {item.severity}
              </span>
            </div>
            <p className="mt-1 text-xs text-muted-foreground">{item.detail}</p>
            <p className="mt-2 text-xs font-medium text-amber-700 dark:text-amber-300">
              {formatNumber(item.affected_count)} afectados · {item.method}
            </p>
          </button>
        ))}
        {!anomalies.length ? (
          <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground dark:border-amber-400/20">
            Sin senales activas para esta corrida. Revisa fuentes pendientes si esperabas alertas de talento.
          </div>
        ) : null}
      </div>
    </section>
  );
}

const PERF_BAND_ORDER: Record<string, number> = { high: 3, medium: 2, low: 1 };
const POTENCIAL_PENDIENTE_TOOLTIP =
  "El Potencial requiere Competencias y Aspiración. SuccessFactors aún no expone esas entidades para este tenant, por eso permanece pendiente. No se infiere del desempeño.";

// Banda horizontal de desempeño (teal, ordinal Alto/Medio/Bajo). Nunca verde "Listo".
function PerformanceBand({ band }: { band: string }) {
  const filled = PERF_BAND_ORDER[band] ?? 0;
  return (
    <span className="inline-flex items-center gap-2" title={`Desempeño ${bandLabel(band)} (dato real)`}>
      <span className="flex gap-[3px]" aria-hidden>
        {[0, 1, 2].map((index) => (
          <span
            key={index}
            className={cn(
              "h-2 w-4 rounded-[3px]",
              index < filled ? "bg-teal-500 dark:bg-teal-400" : "bg-slate-200 dark:bg-slate-700",
            )}
          />
        ))}
      </span>
      <span className="text-xs font-semibold text-teal-700 dark:text-teal-300">{bandLabel(band)}</span>
    </span>
  );
}

// Opción 1 (B + C): superficie "Desempeño disponible". Desempeño es un eje independiente
// (columna + franja); Potencial queda pendiente (requiere C+A); Fit es independiente y no se
// infiere. Shortlist manual: solo agrupa personas, sin acciones automáticas ni write-back.
export function DesempenoDisponiblePanel({ cohort }: { cohort?: SfTalentDesempenoCohort | null }) {
  const [sortDesc, setSortDesc] = useState(true);
  const [bandFilter, setBandFilter] = useState<"all" | "high" | "medium" | "low">("all");
  const [shortlist, setShortlist] = useState<Set<string>>(() => new Set());

  const rows = useMemo(() => cohort?.roster ?? [], [cohort]);
  const visibleRows = useMemo(() => {
    const filtered = rows.filter((row) => bandFilter === "all" || row.performance_band_available === bandFilter);
    const sorted = [...filtered].sort((a, b) => {
      const delta = (PERF_BAND_ORDER[b.performance_band_available] ?? 0) - (PERF_BAND_ORDER[a.performance_band_available] ?? 0);
      return sortDesc ? delta : -delta;
    });
    return sorted;
  }, [rows, bandFilter, sortDesc]);

  const toggleShortlist = useCallback((key: string) => {
    setShortlist((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);

  if (!cohort || cohort.count === 0) return null;
  const bands = cohort.band_counts;
  const shortlistRows = rows.filter((row) => shortlist.has(row.employee_key));

  const zones: Array<{ id: "high" | "medium" | "low"; count: number }> = [
    { id: "low", count: bands.low },
    { id: "medium", count: bands.medium },
    { id: "high", count: bands.high },
  ];

  return (
    <section className="rounded-xl border border-teal-500/30 bg-card p-4 shadow-sm dark:bg-[#06141a]" aria-label="Desempeño disponible">
      {/* Contador visible */}
      <div className="flex flex-col gap-3 border-b border-teal-500/20 pb-4 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-teal-700 dark:text-teal-300">Desempeño disponible</p>
          <div className="mt-1 flex items-baseline gap-2">
            <strong className="text-3xl font-semibold tabular-nums text-foreground dark:text-white">{formatNumber(cohort.count)}</strong>
            <span className="text-sm text-muted-foreground">personas esperando Competencias y Aspiración</span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Desempeño real conocido · Potencial pendiente · Fit no inferido.
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs">
          {zones.slice().reverse().map((zone) => (
            <span key={zone.id} className="inline-flex items-center gap-2 rounded-full border border-teal-500/30 bg-teal-500/10 px-3 py-1 font-medium text-teal-700 dark:text-teal-300">
              {bandLabel(zone.id)} <span className="tabular-nums">{formatNumber(zone.count)}</span>
            </span>
          ))}
        </div>
      </div>

      {/* B — roster con columna Desempeño */}
      <div className="mt-4">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => setSortDesc((value) => !value)}
            className="inline-flex min-h-[36px] items-center gap-1.5 rounded-md border bg-background px-3 py-1.5 text-xs font-semibold text-foreground shadow-sm transition hover:bg-muted dark:border-teal-400/20 dark:bg-[#06111f] dark:text-white"
          >
            <SlidersHorizontal aria-hidden className="h-3.5 w-3.5" />
            Desempeño {sortDesc ? "↓" : "↑"}
          </button>
          <span className="inline-flex items-center gap-1 rounded-md border bg-background p-0.5 dark:border-teal-400/20 dark:bg-[#06111f]">
            <Filter aria-hidden className="ml-1.5 h-3.5 w-3.5 text-muted-foreground" />
            {(["all", "high", "medium", "low"] as const).map((value) => (
              <button
                key={value}
                type="button"
                onClick={() => setBandFilter(value)}
                className={cn(
                  "min-h-[30px] rounded px-2.5 py-1 text-xs font-medium transition",
                  bandFilter === value ? "bg-teal-500 text-white" : "text-muted-foreground hover:bg-muted",
                )}
              >
                {value === "all" ? "Todos" : bandLabel(value)}
              </button>
            ))}
          </span>
          <span className="ml-auto text-xs text-muted-foreground">
            {formatNumber(visibleRows.length)} de {formatNumber(cohort.count)}{cohort.roster_truncated ? " (muestra)" : ""}
          </span>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-left text-sm">
            <thead className="text-xs uppercase text-muted-foreground">
              <tr className="border-b dark:border-teal-500/20">
                <th className="w-9 py-2" />
                <th className="py-2 pr-3 font-semibold">Persona</th>
                <th className="py-2 pr-3 font-semibold">Desempeño</th>
                <th className="py-2 pr-3 font-semibold">Potencial</th>
                <th className="py-2 pr-3 font-semibold">Fit</th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row) => {
                const selected = shortlist.has(row.employee_key);
                return (
                  <tr
                    key={row.employee_key}
                    className={cn("border-b last:border-0 dark:border-teal-500/10", selected ? "bg-teal-500/5" : "")}
                  >
                    <td className="py-2">
                      <button
                        type="button"
                        aria-pressed={selected}
                        aria-label={selected ? "Quitar de shortlist" : "Agregar a shortlist"}
                        onClick={() => toggleShortlist(row.employee_key)}
                        className={cn(
                          "grid h-5 w-5 place-items-center rounded border transition",
                          selected ? "border-teal-500 bg-teal-500 text-white" : "border-slate-300 bg-background dark:border-slate-600",
                        )}
                      >
                        {selected ? <CheckCircle2 aria-hidden className="h-4 w-4" /> : null}
                      </button>
                    </td>
                    <td className="py-2 pr-3">
                      <span className="font-medium text-foreground dark:text-white">{row.display_name}</span>
                      <span className="block text-xs text-muted-foreground">{row.role}</span>
                    </td>
                    <td className="py-2 pr-3"><PerformanceBand band={row.performance_band_available} /></td>
                    <td className="py-2 pr-3">
                      <span
                        title={POTENCIAL_PENDIENTE_TOOLTIP}
                        className="inline-flex cursor-help items-center gap-1 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-xs font-medium text-amber-700 underline decoration-dotted underline-offset-2 dark:text-amber-300"
                      >
                        Pendiente · Requiere Competencias y Aspiración
                      </span>
                    </td>
                    <td className="py-2 pr-3">
                      <span className="inline-flex items-center gap-1 rounded-md border border-slate-500/30 bg-slate-500/10 px-2 py-0.5 text-xs font-medium text-slate-600 dark:text-slate-300">
                        Requiere Competencias y Aspiración
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {shortlistRows.length ? (
          <div className="mt-4 rounded-lg border border-teal-500/30 bg-teal-500/5 p-3">
            <div className="flex items-center gap-2 text-xs font-semibold text-teal-700 dark:text-teal-300">
              <UserRoundCheck aria-hidden className="h-4 w-4" />
              Shortlist por desempeño · {formatNumber(shortlistRows.length)} seleccionadas
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5">
              {shortlistRows.map((row) => (
                <span key={row.employee_key} className="inline-flex items-center gap-1.5 rounded-full border border-teal-500/30 bg-background px-2 py-0.5 text-xs dark:bg-[#06111f]">
                  {row.display_name} · {bandLabel(row.performance_band_available)}
                </span>
              ))}
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              Lista manual. Sin acciones automáticas, sin write-back a SuccessFactors: solo agrupa personas por desempeño para revisión humana.
            </p>
          </div>
        ) : null}
      </div>

      {/* C — franja "Desempeño disponible" (1-D, independiente del 9-box) */}
      <div className="mt-5 rounded-lg border border-teal-500/30 bg-background p-3 dark:bg-[#06111f]">
        <div className="mb-2 flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-xs font-semibold text-teal-700 dark:text-teal-300">
            <Target aria-hidden className="h-4 w-4" />
            Franja Desempeño disponible
          </div>
          <span className="text-xs text-muted-foreground">Un solo eje · fuera de la malla 2D</span>
        </div>
        <div className="grid grid-cols-3 gap-2">
          {zones.map((zone) => (
            <div
              key={zone.id}
              className={cn(
                "rounded-md border p-2 text-center",
                zone.id === "high"
                  ? "border-teal-500/50 bg-teal-500/15"
                  : zone.id === "medium"
                    ? "border-teal-500/35 bg-teal-500/10"
                    : "border-teal-500/20 bg-teal-500/5",
              )}
            >
              <p className="text-[11px] font-semibold uppercase tracking-wide text-teal-700 dark:text-teal-300">{bandLabel(zone.id)}</p>
              <p className="text-2xl font-semibold tabular-nums text-foreground dark:text-white">{formatNumber(zone.count)}</p>
            </div>
          ))}
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          Misma banda que la columna. No se ubica en las cajas 2D porque el Potencial no existe (falta Competencias y Aspiración).
        </p>
      </div>
    </section>
  );
}

export function TalentControlRoom() {
  const [overview, setOverview] = useState<SfTalentOverviewPayload | null>(null);
  const [nineBox, setNineBox] = useState<SfTalentNineBoxPayload | null>(null);
  const [anomalies, setAnomalies] = useState<SfTalentAnomaliesPayload | null>(null);
  const [metadata, setMetadata] = useState<SfTalentMetadataReadinessPayload | null>(null);
  const [roster, setRoster] = useState<SfTalentRosterPayload | null>(null);
  const [selectedBox, setSelectedBox] = useState<string | null>(null);
  const [collar, setCollar] = useState<Collar>("confianza");
  const [selectedAnomaly, setSelectedAnomaly] = useState<SfTalentAnomaly | null>(null);
  const [loading, setLoading] = useState(true);
  const [rosterLoading, setRosterLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [overviewPayload, matrixPayload, anomalyPayload, metadataPayload] = await Promise.all([
        getSuccessFactorsTalentOverview(),
        getSuccessFactorsTalentNineBox(),
        getSuccessFactorsTalentAnomalies(),
        getSuccessFactorsTalentMetadataReadiness(),
      ]);
      setOverview(overviewPayload);
      setNineBox(matrixPayload);
      setAnomalies(anomalyPayload);
      setMetadata(metadataPayload);
      const initialBox = new URLSearchParams(window.location.search).get("box");
      if (!selectedBox) {
        setSelectedBox(initialBox || matrixPayload.cells[4]?.box_id || matrixPayload.cells[0]?.box_id || null);
      }
      if (!selectedAnomaly) setSelectedAnomaly(anomalyPayload.items[0] || null);
    } catch (err) {
      setError(apiMessage(err));
    } finally {
      setLoading(false);
    }
  }, [selectedAnomaly, selectedBox]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void load();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    if (!selectedBox || collar !== "confianza") {
      const timer = window.setTimeout(() => {
        if (!cancelled) setRoster(null);
      }, 0);
      return () => {
        cancelled = true;
        window.clearTimeout(timer);
      };
    }
    const timer = window.setTimeout(() => {
      if (cancelled) return;
      setRosterLoading(true);
      getSuccessFactorsTalentBoxRoster(selectedBox)
        .then((payload) => {
          if (!cancelled) setRoster(payload);
        })
        .catch(() => {
          if (!cancelled) setRoster(null);
        })
        .finally(() => {
          if (!cancelled) setRosterLoading(false);
        });
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [collar, selectedBox]);

  const cells = useMemo(() => nineBox?.cells ?? overview?.nine_box.cells ?? [], [nineBox, overview]);
  const anomalyItems = anomalies?.items ?? overview?.anomalies.items ?? [];
  const cpaExtractionTargets = useMemo(() => metadataNextTargets(metadata), [metadata]);
  const componentReadiness = useMemo(() => talentComponentReadiness(metadata), [metadata]);

  return (
    <main className="min-h-screen bg-background text-foreground dark:bg-[#050b14]">
      <div className="mx-auto w-full max-w-[1500px] space-y-5 px-4 py-5 sm:px-6 lg:px-8">
        <section className="rounded-xl border bg-card p-5 shadow-sm dark:border-sky-400/20 dark:bg-[#081423]">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-wide text-sky-700 dark:text-sky-300/80">WB-TALENTO · SuccessFactors</p>
              <h1 className="mt-1 text-2xl font-semibold text-foreground dark:text-white">Control Room Talento</h1>
              <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
                9-box operativo, readiness y senales con roster enmascarado para decisiones supervisadas.
              </p>
            </div>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <TalentCollarSegmenter value={collar} onChange={setCollar} />
              <button
                type="button"
                onClick={() => void load()}
                className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border bg-background px-4 py-2 text-sm font-semibold text-foreground shadow-sm transition hover:bg-muted dark:border-sky-400/20 dark:bg-[#06111f] dark:text-white dark:hover:bg-white/5"
              >
                {loading ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <RefreshCcw aria-hidden className="h-4 w-4" />}
                Refrescar
              </button>
            </div>
          </div>
        </section>

        {error ? (
          <OperationalNotice tone="error" title="Talento no pudo actualizarse">
            {error}
          </OperationalNotice>
        ) : null}
        {loading ? (
          <OperationalNotice tone="info" title="Consultando Talent Gold">
            Actualizando overview, 9-box, metadata readiness y anomalias.
          </OperationalNotice>
        ) : null}

        <TalentOverviewPanel overview={overview} nineBox={nineBox} anomalies={anomalies} metadata={metadata} />

        <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-6" aria-label="Blockers por componente Talent">
          {componentReadiness.map((component) => (
            <article key={component.id} className="rounded-lg border bg-card p-3 text-sm shadow-sm dark:border-sky-400/15 dark:bg-[#081423]">
              <div className="flex items-start justify-between gap-2">
                <strong className="text-foreground dark:text-white">{component.label}</strong>
                <ReadinessBadge status={component.status} label={component.badgeLabel} compact />
              </div>
              <p className="mt-1 truncate text-xs text-muted-foreground">{component.entity}</p>
              <p className={`mt-2 line-clamp-2 text-xs ${component.notePending ? "text-amber-700 dark:text-amber-300" : "text-orange-700 dark:text-orange-300"}`}>
                {component.note}
              </p>
            </article>
          ))}
        </section>

        {collar === "sindicalizado" ? (
          <OperationalNotice tone="warning" title="Segmento sindicalizado pendiente">
            Esta vista no reutiliza la matriz de confianza. Requiere escalafon, certificaciones y reglas de contrato colectivo como datasets propios.
          </OperationalNotice>
        ) : null}

        <div className="grid gap-4 xl:grid-cols-[minmax(0,1.35fr)_minmax(360px,0.65fr)]">
          <NineBoxMatrix
            cells={cells}
            selectedBoxId={selectedBox}
            onSelect={setSelectedBox}
            disabled={collar !== "confianza"}
          />
          <MaskedTalentRoster payload={roster} loading={rosterLoading} />
        </div>

        {collar === "confianza" ? (
          <DesempenoDisponiblePanel cohort={nineBox?.desempeno_disponible} />
        ) : null}

        <div className="grid gap-4 xl:grid-cols-2">
          <TalentAnomalyList
            anomalies={anomalyItems}
            selectedId={selectedAnomaly?.id}
            onSelect={setSelectedAnomaly}
          />
        </div>

        <section className="grid gap-4 xl:grid-cols-2">
          <div className="rounded-xl border bg-card p-4 shadow-sm dark:border-orange-400/20 dark:bg-[#081423]">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-orange-700 dark:text-orange-300/80">Metadata readiness</p>
                <h3 className="text-base font-semibold text-foreground dark:text-white">Entidades requeridas C/P/A</h3>
              </div>
              <ReadinessBadge status={metadata?.status || "missing"} compact />
            </div>
            <div className="mt-4 grid gap-2">
              {(metadata?.entities ?? []).map((entity) => (
                <article key={entity.id} className="rounded-lg border bg-background p-3 text-sm dark:border-orange-400/10 dark:bg-[#06111f]">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <strong className="text-foreground dark:text-white">{entity.kb}</strong>
                      <p className="text-xs text-muted-foreground">{entity.entity} · {entity.required_for}</p>
                    </div>
                    <ReadinessBadge status={entity.status} compact />
                  </div>
                  {entity.blockers.length ? (
                    <p className="mt-2 text-xs text-orange-700 dark:text-orange-300">{entity.blockers.slice(0, 2).join(" · ")}</p>
                  ) : null}
                </article>
              ))}
            </div>
            <div className="mt-4 rounded-lg border bg-background p-3 dark:border-emerald-400/10 dark:bg-[#06111f]">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-300/80">
                    Siguiente extraccion C/P/A
                  </p>
                  <p className="text-sm text-muted-foreground">
                    Targets vivos, pendientes y bloqueados desde metadata y permisos SuccessFactors.
                  </p>
                </div>
                <ReadinessBadge
                  status={cpaExtractionTargets.some((target) => target.ready_to_extract) ? "ready" : cpaExtractionTargets.length ? "partial" : "blocked"}
                  label={cpaExtractionTargets.length ? `${cpaExtractionTargets.length} targets` : "sin targets"}
                  compact
                />
              </div>
              <div className="mt-3 grid gap-2">
                {cpaExtractionTargets.slice(0, 6).map((target) => (
                  <div
                    key={`${target.component}:${target.entity}:${target.odata_entity || target.entity}`}
                    className="flex items-center justify-between gap-3 rounded-md border px-3 py-2 text-xs dark:border-emerald-400/10"
                  >
                    <div className="min-w-0">
                      <span className="block truncate font-semibold text-foreground dark:text-white">
                        {target.component_label || target.component}
                      </span>
                      <span className="block truncate text-muted-foreground">
                      {target.entity}
                        {target.odata_entity && target.odata_entity !== target.entity ? ` -> ${target.odata_entity}` : ""}
                      </span>
                    </div>
                    <ReadinessBadge status={normalizeReadinessStatus(target.status || target.sample_status)} compact />
                  </div>
                ))}
                {!cpaExtractionTargets.length ? (
                  <p className="rounded-md border border-dashed p-3 text-xs text-muted-foreground dark:border-orange-400/20">
                    Cuando metadata confirme Performance, Competencias y Aspiracion, aqui apareceran las entidades que Sync Now debe extraer.
                  </p>
                ) : null}
              </div>
            </div>
          </div>

          <div className="rounded-xl border bg-card p-4 shadow-sm dark:border-emerald-400/20 dark:bg-[#081423]">
            <div className="flex items-start gap-3">
              <span className="grid h-10 w-10 place-items-center rounded-md border border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300">
                <CheckCircle2 aria-hidden className="h-5 w-5" />
              </span>
              <div>
                <p className="text-xs font-semibold uppercase tracking-wide text-emerald-700 dark:text-emerald-300/80">Politica segura</p>
                <h3 className="text-base font-semibold text-foreground dark:text-white">Recommendation only</h3>
                <p className="mt-2 text-sm text-muted-foreground">
                  La experiencia muestra decisiones y previews. No escribe a SuccessFactors, no activa compensacion y no expone nombres completos ni IDs crudos.
                </p>
              </div>
            </div>
            <div className="mt-4 grid gap-3 sm:grid-cols-3">
              <div className="rounded-lg border bg-background p-3 text-sm dark:border-emerald-400/10 dark:bg-[#06111f]">
                <Boxes aria-hidden className="mb-2 h-4 w-4 text-emerald-700 dark:text-emerald-300" />
                <strong className="text-foreground dark:text-white">Gold Talent</strong>
                <p className="text-xs text-muted-foreground">contract + operational</p>
              </div>
              <div className="rounded-lg border bg-background p-3 text-sm dark:border-emerald-400/10 dark:bg-[#06111f]">
                <UserRoundCheck aria-hidden className="mb-2 h-4 w-4 text-emerald-700 dark:text-emerald-300" />
                <strong className="text-foreground dark:text-white">PII safe</strong>
                <p className="text-xs text-muted-foreground">employee_key enmascarado</p>
              </div>
              <div className="rounded-lg border bg-background p-3 text-sm dark:border-emerald-400/10 dark:bg-[#06111f]">
                <BriefcaseBusiness aria-hidden className="mb-2 h-4 w-4 text-emerald-700 dark:text-emerald-300" />
                <strong className="text-foreground dark:text-white">Sin write-back</strong>
                <p className="text-xs text-muted-foreground">preview supervisado</p>
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
