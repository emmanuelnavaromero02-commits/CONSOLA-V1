"use client";

import { useRef, type ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Tablist accesible (patrón WAI-ARIA Tabs con activación automática):
 * roving tabIndex, flechas izquierda/derecha con ciclo, Home/End, y
 * asociación tab ↔ panel vía aria-controls / aria-labelledby.
 */
export interface OiTabMeta<Id extends string = string> {
  id: Id;
  label: string;
  icon: LucideIcon;
}

export function tabDomId(id: string): string {
  return `oi-tab-${id}`;
}

export function panelDomId(id: string): string {
  return `oi-panel-${id}`;
}

export function nextTabIndexForKey(key: string, current: number, count: number): number | null {
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

export function OiTablist<Id extends string>({
  tabs,
  activeId,
  onChange,
  label,
}: {
  tabs: ReadonlyArray<OiTabMeta<Id>>;
  activeId: Id;
  onChange: (id: Id) => void;
  label: string;
}) {
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const activate = (index: number) => {
    onChange(tabs[index].id);
    tabRefs.current[index]?.focus();
  };

  return (
    <div className="flex flex-wrap gap-2 rounded-md border bg-card p-2" role="tablist" aria-label={label}>
      {tabs.map((tab, index) => {
        const Icon = tab.icon;
        const active = activeId === tab.id;
        return (
          <button
            key={tab.id}
            ref={(element) => {
              tabRefs.current[index] = element;
            }}
            type="button"
            role="tab"
            id={tabDomId(tab.id)}
            aria-selected={active}
            aria-controls={panelDomId(tab.id)}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(tab.id)}
            onKeyDown={(event) => {
              const next = nextTabIndexForKey(event.key, index, tabs.length);
              if (next == null) return;
              event.preventDefault();
              activate(next);
            }}
            className={cn(
              "inline-flex min-h-[40px] items-center gap-2 rounded-md px-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              active ? "bg-primary text-primary-foreground" : "border hover:bg-accent/10",
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

export function OiTabPanel({
  id,
  active,
  children,
}: {
  id: string;
  active: boolean;
  children: ReactNode;
}) {
  if (!active) return null;
  return (
    <div role="tabpanel" id={panelDomId(id)} aria-labelledby={tabDomId(id)}>
      {children}
    </div>
  );
}
