"use client";

import { AlertTriangle } from "lucide-react";

import type { SfTalentBlocker } from "@/lib/control-room/types";
import { cn } from "@/lib/utils";

import { ReadinessBadge, readinessLabels } from "../StatusBadge";
import type { ControlRoomStatus } from "../StatusBadge";

// Normaliza estados payload-level del contrato Talent al vocabulario visual del
// control room. No inventa estados: lo que no se reconoce se reporta como "missing".
export function normalizeReadinessStatus(status?: string | null): ControlRoomStatus {
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

function formatGeneratedAt(iso: string): string | null {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat("es-MX", { dateStyle: "medium", timeStyle: "short" }).format(date);
}

// Copy honesto por bloqueo: usa el título de negocio que entrega el contrato;
// si solo llega un status, lo traduce con el vocabulario aprobado (nunca crudo).
function blockerCopy(blocker: SfTalentBlocker): string {
  if (blocker.title) return blocker.title;
  if (blocker.status) {
    return readinessLabels[normalizeReadinessStatus(blocker.status)] || "Bloqueo reportado por la fuente.";
  }
  return "Bloqueo reportado por la fuente.";
}

/**
 * Metadatos payload-level que el contrato Talent YA entrega (status, blockers,
 * generated_at). Solo renderiza lo que llega: sin datos, no ocupa espacio.
 * - Badge de status únicamente cuando NO es "ready" (ready se asume implícito).
 * - Blockers como lista de texto de negocio, anunciada con role="status".
 * - "Actualizado:" con <time dateTime> para lectores y tooling.
 */
export function TalentPayloadMeta({
  status,
  blockers,
  generatedAt,
  className,
}: {
  status?: string | null;
  blockers?: SfTalentBlocker[] | null;
  generatedAt?: string | null;
  className?: string;
}) {
  const showStatus = Boolean(status) && status !== "ready";
  const blockerItems = (blockers ?? []).filter((blocker) => blocker.title || blocker.status);
  const formattedDate = generatedAt ? formatGeneratedAt(generatedAt) : null;
  if (!showStatus && !blockerItems.length && !formattedDate) return null;

  return (
    <div className={cn("space-y-2 text-xs", className)}>
      {showStatus || formattedDate ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {showStatus ? <ReadinessBadge status={normalizeReadinessStatus(status)} compact /> : null}
          {formattedDate && generatedAt ? (
            <p className="text-muted-foreground">
              Actualizado: <time dateTime={generatedAt}>{formattedDate}</time>
            </p>
          ) : null}
        </div>
      ) : null}
      {blockerItems.length ? (
        <div
          role="status"
          className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2.5 text-amber-800 dark:text-amber-200"
        >
          <p className="flex items-center gap-1.5 font-semibold">
            <AlertTriangle aria-hidden className="h-3.5 w-3.5 shrink-0" />
            Bloqueos reportados por la fuente
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {blockerItems.map((blocker, index) => (
              <li key={blocker.id || `${blocker.title || blocker.status}:${index}`}>{blockerCopy(blocker)}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}
