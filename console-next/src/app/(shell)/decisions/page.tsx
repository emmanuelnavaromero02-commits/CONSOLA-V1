"use client";

import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import { ActionCouncil } from "@/components/decisions/ActionCouncil";
import { DecisionsBoard } from "@/components/decisions/DecisionsBoard";
import {
  DecisionsTabs,
  resolveDecisionsTab,
  resolveFocusedProposal,
} from "@/components/decisions/DecisionsTabs";

function DecisionsShell() {
  const params = useSearchParams();
  const router = useRouter();
  const tab = resolveDecisionsTab(params.get("tab"));
  const focus = resolveFocusedProposal(params.get("propuesta"));

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-8">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Decisiones</h1>
        <p className="text-sm text-muted-foreground">
          Propuestas por aprobar, compromisos, seguimiento y cierre.
        </p>
      </header>
      <DecisionsTabs
        active={tab}
        onChange={(next) => router.replace(`/decisions?tab=${next}`, { scroll: false })}
      />
      <div role="tabpanel" id={`decisions-panel-${tab}`} aria-labelledby={`decisions-tab-${tab}`}>
        {tab === "consejo" ? <ActionCouncil focusDecisionId={focus} /> : <DecisionsBoard />}
      </div>
    </main>
  );
}

export default function DecisionsPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Cargando decisiones…</div>}>
      <DecisionsShell />
    </Suspense>
  );
}
