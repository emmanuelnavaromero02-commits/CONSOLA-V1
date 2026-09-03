"use client";

import {
  AlertTriangle,
  BrainCircuit,
  Calculator,
  CheckCircle2,
  Database,
  Loader2,
  Scale,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { isApiError } from "@/lib/api";
import {
  getControlRoomItemAnalysis,
  requestControlRoomItemAnalysis,
} from "@/lib/control-room/client";
import type {
  AnalysisClaim,
  AnalysisClaimType,
  AnalysisCompleteness,
  AnalysisEnvelope,
  SfTalentAnomaly,
} from "@/lib/control-room/types";
import { cn } from "@/lib/utils";

const POLL_INTERVAL_MS = 2_500;
const MAX_POLL_ATTEMPTS = 12;
const CLOCK_INTERVAL_MS = 30_000;

let clockSnapshot = Date.now();

function subscribeToClock(onStoreChange: () => void): () => void {
  const timer = window.setInterval(() => {
    clockSnapshot = Date.now();
    onStoreChange();
  }, CLOCK_INTERVAL_MS);
  return () => window.clearInterval(timer);
}

function getClockSnapshot(): number {
  return clockSnapshot;
}

const CLAIM_LABELS: Record<AnalysisClaimType, string> = {
  observed: "Hecho observado",
  computed: "Cálculo determinista",
  hypothesis: "Hipótesis IA",
};

const COMPLETENESS_LABELS: Record<AnalysisCompleteness, string> = {
  complete: "Completa",
  partial: "Parcial",
  unknown: "No confirmada",
};

function publicText(value: string): string {
  return value
    .replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g, "[dato protegido]")
    .replace(/\b(PERNR|employee[_ -]?id|user[_ -]?id)\s*[:=#-]?\s*[A-Za-z0-9._-]+/gi, "$1: [dato protegido]")
    .replace(/\b(?:\d[ -]?){10,}\b/g, "[dato protegido]")
    .slice(0, 800);
}

function formatDate(value: string | null): string {
  if (!value) return "N/D";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "N/D";
  return new Intl.DateTimeFormat("es-MX", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function safeScalar(value: unknown): string | null {
  if (typeof value === "number") {
    if (!Number.isFinite(value)) return null;
    return new Intl.NumberFormat("es-MX", { maximumFractionDigits: 4 }).format(value);
  }
  if (typeof value === "boolean") return value ? "Sí" : "No";
  if (typeof value === "string" && value.trim()) return publicText(value.trim());
  return null;
}

function validPopulation(value: number | null): string {
  if (value == null || !Number.isSafeInteger(value) || value < 0) return "N/D";
  return new Intl.NumberFormat("es-MX").format(value);
}

function claimTone(type: AnalysisClaimType): string {
  if (type === "observed") {
    return "border-cyan-500/30 bg-cyan-500/5 text-cyan-800 dark:text-cyan-200";
  }
  if (type === "computed") {
    return "border-indigo-500/30 bg-indigo-500/5 text-indigo-800 dark:text-indigo-200";
  }
  return "border-amber-500/30 bg-amber-500/5 text-amber-800 dark:text-amber-200";
}

function ClaimIcon({ type }: { type: AnalysisClaimType }) {
  if (type === "observed") return <Database aria-hidden className="h-4 w-4" />;
  if (type === "computed") return <Calculator aria-hidden className="h-4 w-4" />;
  return <BrainCircuit aria-hidden className="h-4 w-4" />;
}

function EvidenceDetails({
  claim,
  evidencePackId,
}: {
  claim: AnalysisClaim;
  evidencePackId: number;
}) {
  return (
    <details className="mt-3 rounded-md border bg-background/70 px-3 py-2 text-xs text-muted-foreground">
      <summary className="cursor-pointer font-semibold text-foreground dark:text-white">
        Ver evidencia
      </summary>
      <dl className="mt-2 grid gap-x-4 gap-y-1.5 sm:grid-cols-2">
        <div>
          <dt className="inline font-medium">Paquete verificado: </dt>
          <dd className="inline">#{evidencePackId}</dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="inline font-medium">Referencias exactas: </dt>
          <dd className="inline">
            {claim.evidence_refs
              .map((ref) => `#${ref.evidence_item_id} · ${publicText(ref.path)}`)
              .join(", ") || "N/D"}
          </dd>
        </div>
        <div>
          <dt className="inline font-medium">Población: </dt>
          <dd className="inline">{validPopulation(claim.population)}</dd>
        </div>
        <div>
          <dt className="inline font-medium">Unidad: </dt>
          <dd className="inline">{claim.unit ? publicText(claim.unit) : "N/D"}</dd>
        </div>
        <div>
          <dt className="inline font-medium">Fecha de corte: </dt>
          <dd className="inline">
            {claim.as_of ? <time dateTime={claim.as_of}>{formatDate(claim.as_of)}</time> : "N/D"}
          </dd>
        </div>
        <div>
          <dt className="inline font-medium">Completitud: </dt>
          <dd className="inline">{COMPLETENESS_LABELS[claim.completeness] ?? "No confirmada"}</dd>
        </div>
      </dl>
    </details>
  );
}

function ClaimCard({ claim, evidencePackId }: { claim: AnalysisClaim; evidencePackId: number }) {
  const value = safeScalar(claim.value);
  return (
    <article className={cn("rounded-lg border p-3", claimTone(claim.claim_type))}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <span className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide">
          <ClaimIcon type={claim.claim_type} />
          {CLAIM_LABELS[claim.claim_type]}
        </span>
        <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700 dark:text-emerald-300">
          <CheckCircle2 aria-hidden className="h-3.5 w-3.5" />
          {claim.claim_type === "hypothesis" ? "Evidencia vinculada" : "Verificado"}
        </span>
      </div>
      <p className="mt-2 text-sm font-medium text-foreground dark:text-white">
        {publicText(claim.statement)}
      </p>
      {value ? (
        <p className="mt-2 text-xl font-semibold tabular-nums text-foreground dark:text-white">
          {value}
          {claim.unit ? <span className="ml-1 text-sm font-medium text-muted-foreground">{publicText(claim.unit)}</span> : null}
        </p>
      ) : null}
      <p className="mt-2 text-xs text-muted-foreground">
        Población: {validPopulation(claim.population)} · Unidad: {claim.unit ? publicText(claim.unit) : "N/D"} · Corte: {formatDate(claim.as_of)} · Completitud: {COMPLETENESS_LABELS[claim.completeness] ?? "No confirmada"}
      </p>
      <EvidenceDetails claim={claim} evidencePackId={evidencePackId} />
    </article>
  );
}

function GroundingNotice({ envelope }: { envelope: AnalysisEnvelope }) {
  const title =
    envelope.grounding_status === "pending"
      ? "Verificación en curso"
      : envelope.grounding_status === "rejected"
        ? "Análisis rechazado por el verificador"
        : envelope.grounding_status === "insufficient_data"
          ? "Datos insuficientes"
          : "Análisis no publicable";
  return (
    <div
      role="status"
      className="rounded-lg border border-amber-500/35 bg-amber-500/10 p-3 text-sm text-amber-800 dark:text-amber-200"
    >
      <p className="flex items-center gap-2 font-semibold">
        {envelope.grounding_status === "pending" ? (
          <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
        ) : (
          <AlertTriangle aria-hidden className="h-4 w-4" />
        )}
        {title}
      </p>
      <p className="mt-1 text-xs">
        No se publican cifras, hipótesis ni recomendaciones hasta que toda afirmación tenga evidencia válida.
      </p>
      {Array.isArray(envelope.blockers) && envelope.blockers.length ? (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs">
          {envelope.blockers.map((blocker, index) => (
            <li key={`${blocker}:${index}`}>{publicText(blocker)}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function GroundedAnalysisEnvelopeView({ envelope }: { envelope: AnalysisEnvelope }) {
  const currentTime = useSyncExternalStore(
    subscribeToClock,
    getClockSnapshot,
    getClockSnapshot,
  );
  const expiryMillis = envelope.expires_at ? Date.parse(envelope.expires_at) : Number.NaN;
  const evidenceIsCurrent =
    Number.isFinite(expiryMillis) && expiryMillis > currentTime;
  const publishable =
    envelope.status === "verified" &&
    envelope.grounding_status === "verified" &&
    evidenceIsCurrent &&
    envelope.recommendation_only === true &&
    envelope.no_writeback === true &&
    typeof envelope.evidence_pack_id === "number";

  if (!publishable) return <GroundingNotice envelope={envelope} />;

  const claims = (Array.isArray(envelope.claims) ? envelope.claims : []).filter(
    (claim) =>
      claim.verification_status === "verified" &&
      Array.isArray(claim.evidence_refs) &&
      claim.evidence_refs.length > 0 &&
      (claim.claim_type === "observed" ||
        claim.claim_type === "computed" ||
        claim.claim_type === "hypothesis"),
  );
  const hiddenClaims = Math.max(0, (envelope.claims?.length ?? 0) - claims.length);
  const options = (Array.isArray(envelope.options) ? envelope.options : []).filter(
    (option) => Array.isArray(option.evidence_refs) && option.evidence_refs.length > 0,
  );
  const hiddenOptions = Math.max(0, (envelope.options?.length ?? 0) - options.length);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/35 bg-emerald-500/10 px-2.5 py-1 font-semibold text-emerald-700 dark:text-emerald-300">
          <ShieldCheck aria-hidden className="h-3.5 w-3.5" />
          Verificado contra evidencia
        </span>
        <span>Paquete #{envelope.evidence_pack_id}</span>
        {envelope.as_of ? <time dateTime={envelope.as_of}>Corte: {formatDate(envelope.as_of)}</time> : null}
        {envelope.expires_at ? <time dateTime={envelope.expires_at}>Vence: {formatDate(envelope.expires_at)}</time> : null}
      </div>

      {claims.map((claim) => (
        <ClaimCard key={claim.claim_id} claim={claim} evidencePackId={envelope.evidence_pack_id!} />
      ))}

      {hiddenClaims > 0 ? (
        <p role="status" className="rounded-md border border-amber-500/30 bg-amber-500/5 p-2 text-xs text-amber-800 dark:text-amber-200">
          {hiddenClaims} {hiddenClaims === 1 ? "afirmación fue ocultada" : "afirmaciones fueron ocultadas"} porque no tenía verificación o evidencia exacta.
        </p>
      ) : null}

      {options.length ? (
        <section className="rounded-lg border border-violet-500/25 bg-violet-500/5 p-3">
          <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-violet-800 dark:text-violet-200">
            <Scale aria-hidden className="h-4 w-4" />
            Opciones para decisión humana
          </p>
          <div className="mt-2 space-y-2">
            {options.map((option, index) => (
              <article key={`${option.label}:${index}`} className="rounded-md border bg-background/70 p-2.5">
                <p className="text-sm font-semibold text-foreground dark:text-white">{publicText(option.label)}</p>
                <p className="mt-1 text-xs text-muted-foreground">{publicText(option.rationale)}</p>
                <details className="mt-2 text-xs text-muted-foreground">
                  <summary className="cursor-pointer font-semibold text-foreground dark:text-white">Ver evidencia</summary>
                  <p className="mt-1">
                    Referencias exactas: {option.evidence_refs
                      .map((ref) => `#${ref.evidence_item_id} · ${publicText(ref.path)}`)
                      .join(", ") || "Sin referencias publicables"}
                  </p>
                </details>
              </article>
            ))}
          </div>
        </section>
      ) : null}

      {hiddenOptions > 0 ? (
        <p role="status" className="rounded-md border border-amber-500/30 bg-amber-500/5 p-2 text-xs text-amber-800 dark:text-amber-200">
          {hiddenOptions} {hiddenOptions === 1 ? "opción fue ocultada" : "opciones fueron ocultadas"} por no tener evidencia exacta.
        </p>
      ) : null}

      {Array.isArray(envelope.assumptions) && envelope.assumptions.length ? (
        <details className="rounded-lg border bg-background/70 p-3 text-xs text-muted-foreground">
          <summary className="cursor-pointer font-semibold text-foreground dark:text-white">Supuestos declarados</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {envelope.assumptions.map((assumption, index) => (
              <li key={`${assumption.statement}:${index}`}>
                {publicText(assumption.statement)}
                <span className="ml-1 text-muted-foreground">
                  (referencias: {assumption.evidence_refs
                    .map((ref) => `#${ref.evidence_item_id} · ${publicText(ref.path)}`)
                    .join(", ") || "N/D"})
                </span>
              </li>
            ))}
          </ul>
        </details>
      ) : null}

      <div className="rounded-lg border border-slate-500/30 bg-slate-500/5 p-3">
        <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-slate-700 dark:text-slate-200">
          <Scale aria-hidden className="h-4 w-4" />
          Decisión humana
        </p>
        <p className="mt-1 text-sm text-muted-foreground">
          Pendiente de revisión por RR. HH. o data steward. No hay promociones, PIP, terminaciones, compensación, movimientos ni write-back automáticos.
        </p>
      </div>
    </div>
  );
}

function GroundedAnalysisPanelForItem({
  anomaly,
  itemId,
}: {
  anomaly?: SfTalentAnomaly | null;
  itemId: string | null;
}) {
  const [envelope, setEnvelope] = useState<AnalysisEnvelope | null>(null);
  const [loading, setLoading] = useState(Boolean(itemId));
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [pollAttempt, setPollAttempt] = useState(0);
  const activeItemRef = useRef<string | null>(itemId);

  const loadAnalysis = useCallback(async (id: string, quiet = false) => {
    if (!quiet) setLoading(true);
    setError("");
    try {
      const result = await getControlRoomItemAnalysis(id);
      if (activeItemRef.current === id) setEnvelope(result);
      return result;
    } catch (loadError) {
      if (activeItemRef.current !== id) return null;
      if (isApiError(loadError) && loadError.status === 404) {
        setEnvelope(null);
        return null;
      }
      setError(isApiError(loadError) ? loadError.message : "No se pudo consultar el análisis.");
      return null;
    } finally {
      if (!quiet && activeItemRef.current === id) setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    if (!itemId) return () => { cancelled = true; };
    getControlRoomItemAnalysis(itemId)
      .then((result) => {
        if (!cancelled) setEnvelope(result);
      })
      .catch((loadError: unknown) => {
        if (cancelled) return;
        if (isApiError(loadError) && loadError.status === 404) {
          setEnvelope(null);
          return;
        }
        setError(isApiError(loadError) ? loadError.message : "No se pudo consultar el análisis.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [itemId]);

  useEffect(() => {
    if (!itemId || envelope?.grounding_status !== "pending" || pollAttempt >= MAX_POLL_ATTEMPTS) return;
    const timer = window.setTimeout(() => {
      void loadAnalysis(itemId, true).then(() => setPollAttempt((attempt) => attempt + 1));
    }, POLL_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [envelope?.grounding_status, itemId, loadAnalysis, pollAttempt]);

  const runAnalysis = useCallback(async () => {
    if (!itemId) return;
    setRunning(true);
    setError("");
    setPollAttempt(0);
    try {
      const result = await requestControlRoomItemAnalysis(itemId);
      if (activeItemRef.current === itemId) setEnvelope(result);
    } catch (runError) {
      if (activeItemRef.current !== itemId) return;
      setError(isApiError(runError) ? runError.message : "No se pudo solicitar el análisis.");
    } finally {
      if (activeItemRef.current === itemId) setRunning(false);
    }
  }, [itemId]);

  const refreshOrRun = useCallback(() => {
    if (!itemId) return;
    if (envelope?.grounding_status === "pending") {
      setPollAttempt(0);
      void loadAnalysis(itemId);
      return;
    }
    void runAnalysis();
  }, [envelope?.grounding_status, itemId, loadAnalysis, runAnalysis]);

  const activelyPolling =
    envelope?.grounding_status === "pending" && pollAttempt < MAX_POLL_ATTEMPTS;

  return (
    <section className="rounded-xl border bg-card p-4 shadow-sm dark:border-violet-400/20 dark:bg-[#081423]" aria-label="Análisis con evidencia">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-violet-700 dark:text-violet-300/80">
            <Sparkles aria-hidden className="h-4 w-4" />
            Análisis con evidencia
          </p>
          <h3 className="mt-1 text-base font-semibold text-foreground dark:text-white">
            {anomaly?.title ? publicText(anomaly.title) : "Selecciona una señal"}
          </h3>
          <p className="mt-1 text-xs text-muted-foreground">
            Claude propone; el verificador determinista decide qué se puede publicar.
          </p>
        </div>
        <button
          type="button"
          disabled={!itemId || loading || running || activelyPolling}
          onClick={refreshOrRun}
          className="inline-flex min-h-[38px] items-center justify-center gap-2 rounded-md border border-violet-500/35 bg-violet-500/10 px-3 py-2 text-xs font-semibold text-violet-800 transition hover:bg-violet-500/15 disabled:cursor-not-allowed disabled:opacity-50 dark:text-violet-200"
        >
          {running || activelyPolling ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <ShieldCheck aria-hidden className="h-4 w-4" />}
          {envelope?.grounding_status === "pending"
            ? activelyPolling
              ? "Verificando"
              : "Consultar estado"
            : "Analizar con evidencia"}
        </button>
      </div>

      {error ? (
        <div role="alert" className="rounded-lg border border-destructive/35 bg-destructive/10 p-3 text-sm text-destructive">
          {publicText(error)}
        </div>
      ) : loading ? (
        <div role="status" className="flex min-h-[120px] items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
          Consultando análisis verificado…
        </div>
      ) : envelope ? (
        <GroundedAnalysisEnvelopeView envelope={envelope} />
      ) : (
        <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
          {itemId
            ? "Aún no hay un análisis verificado para esta señal. Puedes solicitar uno sin ejecutar acciones externas."
            : anomaly
              ? "Esta señal proviene de una regla determinista de Gold. El análisis generativo se habilita únicamente cuando existe un paquete firmado y un item persistido de Control Room."
              : "Selecciona una señal de talento para consultar su evidencia."}
        </div>
      )}

      <p className="mt-3 flex items-center gap-1.5 text-xs text-muted-foreground">
        <ShieldCheck aria-hidden className="h-3.5 w-3.5" />
        Solo agregados sin PII · recommendation only · sin write-back
      </p>
    </section>
  );
}

export function GroundedAnalysisPanel({ anomaly }: { anomaly?: SfTalentAnomaly | null }) {
  // A Gold action candidate is a deterministic recommendation, not an
  // evidence-pack identity. Only a persisted Control Room item may cross the
  // boundary into the generative analysis API. Keying the stateful child by
  // that identity also prevents an earlier item's async state from leaking
  // into the next selection.
  const itemId = anomaly?.analysis_item_id || null;
  return (
    <GroundedAnalysisPanelForItem
      key={itemId ?? "no-analysis-item"}
      anomaly={anomaly}
      itemId={itemId}
    />
  );
}
