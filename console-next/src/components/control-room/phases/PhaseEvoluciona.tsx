"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { BadgeCheck, Inbox, Loader2, SlidersHorizontal } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { getControlRoomLessons } from "@/lib/control-room/client";
import type { Lesson } from "@/lib/control-room/types";
import { getSapB1View } from "@/lib/sap-b1/client";
import { useSapB1Access } from "@/lib/sap-b1/hooks";

export const CANDIDATE_COPY = "Candidata a incorporarse al paquete";

function shortDate(value?: string | null): string {
  if (!value) return "Sin fecha";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "Sin fecha";
  return parsed.toLocaleDateString("es", { dateStyle: "medium" });
}

function confidenceCopy(value?: number | null): string | null {
  if (typeof value !== "number" || !Number.isFinite(value)) return null;
  return `Confianza registrada: ${value.toFixed(2)}`;
}

function CandidateBadge() {
  return (
    <span className="inline-flex shrink-0 items-center gap-1.5 rounded border border-primary/25 bg-primary/10 px-2 py-1 text-xs font-semibold text-primary">
      <BadgeCheck aria-hidden className="h-3.5 w-3.5" />
      {CANDIDATE_COPY}
    </span>
  );
}

function LessonRow({ lesson }: { lesson: Lesson }) {
  const confidence = confidenceCopy(lesson.confidence);
  return (
    <li className="flex flex-col gap-2 rounded-md border bg-background p-3 md:flex-row md:items-start md:justify-between">
      <div className="min-w-0">
        <p className="break-words text-sm font-medium text-card-foreground">
          {lesson.rule || "Regla sin descripción"}
        </p>
        <p className="mt-1 text-xs text-muted-foreground">
          {[lesson.anomaly_type || null, shortDate(lesson.created_at), confidence]
            .filter(Boolean)
            .join(" · ")}
        </p>
      </div>
      <CandidateBadge />
    </li>
  );
}

function LessonsSection() {
  const lessons = useQuery({
    queryKey: ["control-room", "lessons"],
    queryFn: () => getControlRoomLessons(),
    staleTime: 30_000,
  });
  const rows = lessons.data?.lessons ?? [];

  return (
    <section aria-label="Reglas aprendidas" className="rounded-md border bg-card p-4">
      <h2 className="text-lg font-semibold">Reglas aprendidas</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Reglas registradas a partir de decisiones y hallazgos cerrados. Ninguna se aplica
        automáticamente.
      </p>
      <div className="mt-3">
        {lessons.isLoading ? (
          <div role="status" className="flex min-h-[140px] items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
            Cargando
          </div>
        ) : lessons.error ? (
          <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
            No se pudieron cargar las reglas aprendidas.
          </div>
        ) : rows.length === 0 ? (
          <EmptyState icon={Inbox} size="sm" title="Sin reglas aprendidas todavía." className="rounded-md border border-dashed" />
        ) : (
          <ul className="space-y-2">
            {rows.map((lesson, index) => (
              <LessonRow key={`${lesson.rule ?? "regla"}:${index}`} lesson={lesson} />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

export const PARAMETERS_HREF = "/control-room/sap-b1#parametros";

function SuggestionsSection() {
  const { installed, access, canWrite } = useSapB1Access();
  const learning = useQuery({
    queryKey: ["control-room", "sap-b1", "learning"],
    queryFn: () => getSapB1View("sap_b1_learning_kpis"),
    enabled: installed,
    staleTime: 30_000,
  });
  const suggestions = learning.data?.metrics?.aprendizaje?.suggestions ?? [];

  return (
    <section aria-label="Sugerencias de umbral" className="rounded-md border bg-card p-4">
      <h2 className="text-lg font-semibold">Sugerencias de umbral</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Umbrales que conviene revisar según el resultado real de alertas y decisiones.
      </p>
      <div className="mt-3">
        {access.isSuccess && !installed ? (
          <p className="text-sm text-muted-foreground">
            SAP Business One no está instalado en este workspace: sin sugerencias que evaluar.
          </p>
        ) : learning.isLoading || access.isPending ? (
          <div role="status" className="flex min-h-[140px] items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
            Cargando
          </div>
        ) : learning.error ? (
          <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
            No se pudieron consultar las sugerencias de umbral.
          </div>
        ) : suggestions.length === 0 ? (
          <EmptyState icon={Inbox} size="sm" title="Sin sugerencias de umbral." className="rounded-md border border-dashed" />
        ) : (
          <ul className="space-y-2">
            {suggestions.map((suggestion, index) => (
              <li
                key={`${suggestion.source ?? "sugerencia"}:${index}`}
                className="flex flex-col gap-2 rounded-md border bg-background p-3 md:flex-row md:items-start md:justify-between"
              >
                <div className="min-w-0">
                  <p className="break-words text-sm font-medium text-card-foreground">
                    {suggestion.reason || "Sugerencia sin motivo informado"}
                  </p>
                  {(suggestion.thresholds ?? []).length > 0 ? (
                    <p className="mt-1 break-words text-xs text-muted-foreground">
                      {`Umbrales: ${(suggestion.thresholds ?? []).join(", ")}`}
                    </p>
                  ) : null}
                  {canWrite ? (
                    <Link
                      href={PARAMETERS_HREF}
                      className="mt-2 inline-flex items-center gap-1.5 text-xs font-medium text-primary underline-offset-4 hover:underline"
                    >
                      <SlidersHorizontal aria-hidden className="h-3.5 w-3.5" />
                      Ajustar en Parámetros
                    </Link>
                  ) : null}
                </div>
                <CandidateBadge />
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

export function PhaseEvoluciona() {
  return (
    <main className="min-h-screen bg-background p-4 md:p-6" data-testid="phase-evoluciona">
      <div className="mx-auto flex max-w-7xl flex-col gap-4">
        <header className="border-b pb-4">
          <p className="text-xs font-semibold uppercase text-primary">Control Room</p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">Evoluciona</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted-foreground">
            Reglas aprendidas y sugerencias de umbral derivadas de la operación real.
          </p>
        </header>
        <LessonsSection />
        <SuggestionsSection />
      </div>
    </main>
  );
}
