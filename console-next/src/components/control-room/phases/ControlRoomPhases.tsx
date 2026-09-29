"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useRef, type ReactNode } from "react";
import {
  Eye,
  GitBranch,
  Gavel,
  Loader2,
  RefreshCw,
  Sprout,
  Telescope,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { getMeAccess, type MeAccessResponse } from "@/lib/admin-surfaces";
import { cn } from "@/lib/utils";

import { PhaseDecide } from "./PhaseDecide";
import { PhaseEjecuta } from "./PhaseEjecuta";
import { PhaseEvoluciona } from "./PhaseEvoluciona";
import { PhaseSupervisa } from "./PhaseSupervisa";

export type ControlRoomPhaseId =
  | "entiende"
  | "decide"
  | "ejecuta"
  | "supervisa"
  | "evoluciona";

interface PhaseMeta {
  id: ControlRoomPhaseId;
  label: string;
  icon: LucideIcon;
  permission?: string;
  capability?: string;
}

export const CONTROL_ROOM_PHASES: readonly PhaseMeta[] = [
  { id: "entiende", label: "Entiende", icon: Telescope, permission: "datasets.read" },
  { id: "decide", label: "Decide", icon: GitBranch, permission: "datasets.read" },
  { id: "ejecuta", label: "Ejecuta", icon: Gavel, capability: "can_view_decisions" },
  { id: "supervisa", label: "Supervisa", icon: Eye, permission: "datasets.read" },
  { id: "evoluciona", label: "Evoluciona", icon: Sprout, permission: "datasets.read" },
];

const PHASE_IDS = new Set<string>(CONTROL_ROOM_PHASES.map((phase) => phase.id));

export function resolveControlRoomPhase(value: string | null): ControlRoomPhaseId {
  return value && PHASE_IDS.has(value) ? (value as ControlRoomPhaseId) : "entiende";
}

export function canViewPhase(
  phase: PhaseMeta,
  access: MeAccessResponse | undefined,
): boolean {
  if (!access) return false;
  if (phase.permission && !(access.permissions ?? []).includes(phase.permission)) {
    return false;
  }
  if (phase.capability && access.ui_capabilities?.[phase.capability] !== true) {
    return false;
  }
  return true;
}

const PANEL_DOM_ID = "cr-phase-panel";

function tabDomId(id: ControlRoomPhaseId): string {
  return `cr-phase-tab-${id}`;
}

function focusTargetForKey(key: string, current: number, count: number): number | null {
  switch (key) {
    case "ArrowRight":
      return (current + 1) % count;
    case "ArrowLeft":
      return (current - 1 + count) % count;
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}

function PhasePanel({ phase, entiende }: { phase: ControlRoomPhaseId; entiende: ReactNode }) {
  switch (phase) {
    case "decide":
      return <PhaseDecide />;
    case "ejecuta":
      return <PhaseEjecuta />;
    case "supervisa":
      return <PhaseSupervisa />;
    case "evoluciona":
      return <PhaseEvoluciona />;
    default:
      return <>{entiende}</>;
  }
}

export function ControlRoomPhases({ entiende }: { entiende: ReactNode }) {
  const params = useSearchParams();
  const requested = resolveControlRoomPhase(params.get("fase"));
  const access = useQuery({
    queryKey: ["me", "access"],
    queryFn: getMeAccess,
    staleTime: 60_000,
    retry: false,
  });
  const tabRefs = useRef<Array<HTMLAnchorElement | null>>([]);

  if (access.isPending) {
    return (
      <div
        role="status"
        className="flex min-h-64 items-center justify-center gap-2 text-sm text-muted-foreground"
      >
        <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
        Cargando el ciclo operativo
      </div>
    );
  }

  if (access.isError) {
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-10 sm:px-6">
        <div
          role="alert"
          className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm"
        >
          <p className="font-medium text-destructive">No se pudo cargar tu acceso.</p>
          <button
            type="button"
            onClick={() => access.refetch()}
            className="mt-2 inline-flex min-h-[44px] items-center gap-2 rounded-md border border-destructive/40 px-3 text-xs font-medium text-destructive hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40"
          >
            <RefreshCw aria-hidden className="h-4 w-4" />
            Reintentar
          </button>
        </div>
      </div>
    );
  }

  const visible = CONTROL_ROOM_PHASES.filter((phase) => canViewPhase(phase, access.data));
  const active = visible.some((phase) => phase.id === requested)
    ? requested
    : visible[0]?.id ?? "entiende";

  return (
    <div>
      {visible.length > 1 ? (
        <div className="mx-auto w-full max-w-[1600px] px-4 pt-4 sm:px-6 lg:px-8">
          <nav
            role="tablist"
            aria-label="Ciclo operativo"
            className="flex flex-wrap gap-2 rounded-md border bg-card p-2"
          >
            {visible.map((phase, index) => {
              const Icon = phase.icon;
              const selected = phase.id === active;
              return (
                <Link
                  key={phase.id}
                  ref={(element) => {
                    tabRefs.current[index] = element;
                  }}
                  role="tab"
                  id={tabDomId(phase.id)}
                  aria-selected={selected}
                  aria-controls={PANEL_DOM_ID}
                  tabIndex={selected ? 0 : -1}
                  href={`/control-room?fase=${phase.id}`}
                  onKeyDown={(event) => {
                    const next = focusTargetForKey(event.key, index, visible.length);
                    if (next == null) return;
                    event.preventDefault();
                    tabRefs.current[next]?.focus();
                  }}
                  className={cn(
                    "inline-flex min-h-[40px] items-center gap-2 rounded-md px-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    selected
                      ? "bg-primary text-primary-foreground"
                      : "border hover:bg-accent/10",
                  )}
                >
                  <Icon aria-hidden className="h-4 w-4" />
                  {phase.label}
                </Link>
              );
            })}
          </nav>
        </div>
      ) : null}
      <div
        role="tabpanel"
        id={PANEL_DOM_ID}
        aria-labelledby={visible.length > 1 ? tabDomId(active) : undefined}
        aria-label={visible.length > 1 ? undefined : "Ciclo operativo"}
      >
        <PhasePanel phase={active} entiende={entiende} />
      </div>
    </div>
  );
}
