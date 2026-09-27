const REASON_COPY: Record<string, string> = {
  extract_all_already_running:
    "Ya hay una extracción completa en curso para este origen. Sigue su avance en la tarjeta de extracción.",
  too_many_active_entity_extracts:
    "Hay demasiadas extracciones individuales en curso. Espera a que terminen o usa «Extraer Todo».",
  backpressure_unavailable:
    "No se pudo reservar un turno de extracción en este momento. Intenta de nuevo en unos minutos.",
  dag_unavailable:
    "El proceso de extracción no está disponible en Airflow en este momento. Intenta de nuevo en unos minutos o avisa a un administrador.",
  dag_paused_by_operator:
    "Este proceso tiene una programación automática y un operador de la plataforma lo pausó. La consola no lo reactiva; pide a un administrador que lo reanude.",
  stale_runs_require_recovery:
    "Hay corridas anteriores de este proceso atascadas en cola. Revisa y cierra las corridas atascadas antes de lanzar una nueva extracción.",
  foreign_backlog_requires_platform_recovery:
    "El proceso tiene corridas atascadas que no pertenecen a este espacio de trabajo. Un administrador de la plataforma debe liberarlas.",
  auto_unpause_disabled:
    "El proceso está en pausa y la reactivación automática está desactivada. Pide a un administrador que lo reanude.",
  plan_changed:
    "Las corridas cambiaron desde la revisión. Revisa el nuevo plan y confírmalo de nuevo.",
};

const SAFE_ID = /^[A-Za-z0-9_.:+-]{1,250}$/;
const SAFE_DIGEST = /^[a-f0-9]{64}$/;

export interface PipelineErrorDetail {
  reason: string;
  copy: string;
  jobId: string | null;
  planDigest: string | null;
}

export function pipelineReasonCopy(reason: unknown): string | null {
  if (typeof reason !== "string") return null;
  return Object.prototype.hasOwnProperty.call(REASON_COPY, reason) ? REASON_COPY[reason] : null;
}

function detailObject(payload: unknown): Record<string, unknown> | null {
  if (!payload || typeof payload !== "object" || !("detail" in payload)) return null;
  const detail = (payload as { detail?: unknown }).detail;
  return detail && typeof detail === "object" && !Array.isArray(detail) ? (detail as Record<string, unknown>) : null;
}

export function pipelineErrorDetail(payload: unknown): PipelineErrorDetail | null {
  const detail = detailObject(payload);
  if (!detail) return null;
  const copy = pipelineReasonCopy(detail.reason);
  if (!copy) return null;
  const jobId = typeof detail.job_id === "string" && SAFE_ID.test(detail.job_id) ? detail.job_id : null;
  const planDigest =
    typeof detail.plan_digest === "string" && SAFE_DIGEST.test(detail.plan_digest) ? detail.plan_digest : null;
  return { reason: String(detail.reason), copy, jobId, planDigest };
}
