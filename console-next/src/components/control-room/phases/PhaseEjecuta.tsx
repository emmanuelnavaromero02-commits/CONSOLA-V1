"use client";

import { useSearchParams } from "next/navigation";

import { ActionCouncil } from "@/components/decisions/ActionCouncil";
import { resolveFocusedProposal } from "@/components/decisions/DecisionsTabs";

export function PhaseEjecuta() {
  const params = useSearchParams();
  const focus = resolveFocusedProposal(params.get("propuesta"));

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-8" data-testid="phase-ejecuta">
      <header className="space-y-2">
        <p className="text-xs font-semibold uppercase text-primary">Control Room</p>
        <h1 className="text-2xl font-semibold tracking-tight">Ejecuta</h1>
        <p className="text-sm text-muted-foreground">
          Propuestas por aprobar del Consejo de Acciones, con aprobación a cuatro ojos.
        </p>
      </header>
      <ActionCouncil focusDecisionId={focus} />
    </main>
  );
}
