"use client";

import { useRef } from "react";
import { ClipboardList, Scale } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export type DecisionsTab = "consejo" | "registro";

const TABS: ReadonlyArray<{ id: DecisionsTab; label: string; icon: LucideIcon }> = [
  { id: "consejo", label: "Consejo de Acciones Sugeridas", icon: Scale },
  { id: "registro", label: "Registro", icon: ClipboardList },
];

export function resolveDecisionsTab(value: string | null): DecisionsTab {
  return value === "registro" ? "registro" : "consejo";
}

export function resolveFocusedProposal(value: string | null): number | null {
  if (!value || !/^[1-9][0-9]{0,18}$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}

export function DecisionsTabs({
  active,
  onChange,
}: {
  active: DecisionsTab;
  onChange: (tab: DecisionsTab) => void;
}) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  return (
    <div role="tablist" aria-label="Secciones de decisiones" className="flex flex-wrap gap-2 border-b pb-3">
      {TABS.map((tab, index) => {
        const Icon = tab.icon;
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            ref={(element) => {
              refs.current[index] = element;
            }}
            type="button"
            role="tab"
            id={`decisions-tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={`decisions-panel-${tab.id}`}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.id)}
            onKeyDown={(event) => {
              if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
              event.preventDefault();
              const next = (index + (event.key === "ArrowRight" ? 1 : TABS.length - 1)) % TABS.length;
              onChange(TABS[next].id);
              refs.current[next]?.focus();
            }}
            className={cn(
              "inline-flex min-h-[40px] items-center gap-2 rounded-md px-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              selected ? "bg-primary text-primary-foreground" : "border hover:bg-muted",
            )}
          >
            <Icon aria-hidden className="h-4 w-4" />
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
