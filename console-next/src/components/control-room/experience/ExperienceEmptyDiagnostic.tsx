"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, Loader2, Search } from "lucide-react";

import {
  diagnosticInstallationStatusLabel,
  diagnosticSourceStatusLabel,
  getControlRoomDiagnostics,
  isForbiddenDiagnostics,
  type ControlRoomDiagnosticsView,
} from "@/lib/control-room/diagnostics-client";

const MARKETPLACE_HREF = "/marketplace?tab=conectadas";
const STUDIO_HREF = "/studio";

function ActionPlan() {
  return (
    <section aria-label="Plan de acción sugerido" className="rounded-md border bg-card p-4">
      <h3 className="text-sm font-semibold text-card-foreground">Plan de acción sugerido</h3>
      <p className="mt-1 text-sm text-muted-foreground">
        Para que aparezcan observaciones empresariales, conecta una fuente y ejecuta una extracción.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Link
          href={MARKETPLACE_HREF}
          className="inline-flex min-h-[40px] items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          Revisar Fuentes de datos
        </Link>
        <Link
          href={STUDIO_HREF}
          className="inline-flex min-h-[40px] items-center justify-center rounded-md border bg-card px-4 text-sm font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          Ir a extracción
        </Link>
      </div>
    </section>
  );
}

function SourceRows({ diagnostics }: { diagnostics: ControlRoomDiagnosticsView }) {
  if (diagnostics.sources.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Ninguna fuente reportó estado. Sin información de diagnóstico por fuente.
      </p>
    );
  }
  return (
    <ul className="space-y-2">
      {diagnostics.sources.map((source, index) => (
        <li
          key={`${source.domain ?? "sin-dominio"}:${index}`}
          className="flex items-start justify-between gap-3 rounded-md border bg-background p-3"
        >
          <div className="min-w-0">
            <p className="break-words text-sm font-medium text-card-foreground">
              {source.domain ?? "Fuente sin dominio informado"}
            </p>
            {source.reason ? (
              <p className="mt-1 break-words text-xs text-muted-foreground">{source.reason}</p>
            ) : null}
          </div>
          <span className="inline-flex shrink-0 items-center gap-1.5 text-xs font-medium text-muted-foreground">
            {source.operationallyReady ? (
              <CheckCircle2 aria-hidden className="h-4 w-4 text-success" />
            ) : (
              <CircleDashed aria-hidden className="h-4 w-4" />
            )}
            {diagnosticSourceStatusLabel(source.status)}
          </span>
        </li>
      ))}
    </ul>
  );
}

function InstallationRows({ diagnostics }: { diagnostics: ControlRoomDiagnosticsView }) {
  if (diagnostics.installations.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No hay instalaciones de fuentes registradas en este workspace.
      </p>
    );
  }
  return (
    <ul className="space-y-1.5">
      {diagnostics.installations.map((installation, index) => (
        <li
          key={`${installation.label ?? "instalacion"}:${index}`}
          className="flex items-center justify-between gap-3 text-sm"
        >
          <span className="break-words text-card-foreground">
            {installation.label ?? installation.category ?? "Instalación sin nombre informado"}
          </span>
          <span className="shrink-0 text-xs font-medium text-muted-foreground">
            {diagnosticInstallationStatusLabel(installation.status)}
          </span>
        </li>
      ))}
    </ul>
  );
}

export function ExperienceEmptyDiagnostic() {
  const diagnostics = useQuery({
    queryKey: ["control-room", "diagnostics"],
    queryFn: getControlRoomDiagnostics,
    retry: false,
    refetchOnWindowFocus: false,
  });
  const forbidden = diagnostics.isError && isForbiddenDiagnostics(diagnostics.error);

  return (
    <div className="space-y-4" data-testid="experience-empty-diagnostic">
      <section
        aria-label="Diagnóstico preliminar de fuentes"
        className="rounded-md border bg-card p-4"
      >
        <h3 className="inline-flex items-center gap-2 text-sm font-semibold text-card-foreground">
          <Search aria-hidden className="h-4 w-4 text-primary" />
          Diagnóstico preliminar de fuentes
        </h3>
        <p className="mt-1 text-sm text-muted-foreground">
          No hay hallazgos que mostrar todavía. Este es el estado real de las fuentes conectadas.
        </p>
        <div className="mt-3">
          {diagnostics.isPending ? (
            <div role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
              Consultando el estado de las fuentes
            </div>
          ) : diagnostics.isError ? (
            <p role="status" className="text-sm text-muted-foreground">
              {forbidden
                ? "Tu perfil no tiene acceso al detalle del diagnóstico de fuentes."
                : "No se pudo consultar el diagnóstico de fuentes."}
            </p>
          ) : (
            <div className="space-y-4">
              <SourceRows diagnostics={diagnostics.data} />
              <div>
                <h4 className="text-xs font-semibold uppercase text-muted-foreground">
                  Instalaciones
                </h4>
                <div className="mt-2">
                  <InstallationRows diagnostics={diagnostics.data} />
                </div>
              </div>
            </div>
          )}
        </div>
      </section>
      <ActionPlan />
    </div>
  );
}
