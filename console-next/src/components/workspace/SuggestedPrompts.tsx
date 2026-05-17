"use client";

interface SuggestedPrompt {
  icon:   string;
  title:  string;
  prompt: string;
}

interface Props {
  onSelect: (prompt: string) => void;
}

/**
 * v1.44.4 Task A — empty-state suggested prompts.
 *
 * Renders four canned prompts that the operator can click to
 * seed a brand-new conversation. The icons are emoji (no
 * dependency) and the prompt strings double as accessibility
 * labels. Clicking a card fires onSelect with the full prompt
 * which the page wires to the same send-message flow as the
 * input.
 *
 * Per the v1.44.4 brief these are the four "centro" prompts —
 * the slash-command palette covers the longer enterprise
 * report templates (/reporte_mensual, /turnover_analysis, …).
 */
const PROMPTS: SuggestedPrompt[] = [
  {
    icon:   "📊",
    title:  "Estado de cartuchos",
    prompt: "Dame el estado actual de todos los cartuchos conectados.",
  },
  {
    icon:   "💼",
    title:  "Nómina último mes",
    prompt: "Genera un reporte de nómina del último mes con totales y desglose por departamento.",
  },
  {
    icon:   "📈",
    title:  "Tendencia de horas",
    prompt: "Muestra la tendencia de horas registradas en Replicon en las últimas 8 semanas.",
  },
  {
    icon:   "💰",
    title:  "Top 5 clientes",
    prompt: "Top 5 clientes por facturación en el último trimestre.",
  },
];


export function SuggestedPrompts({ onSelect }: Props) {
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {PROMPTS.map((p) => (
        <button
          key={p.title}
          type="button"
          onClick={() => onSelect(p.prompt)}
          className="group rounded-lg border bg-card p-4 text-left shadow-sm transition-colors hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          aria-label={p.prompt}
        >
          <div className="flex items-center gap-2 text-sm font-semibold tracking-tight">
            <span aria-hidden className="text-lg">
              {p.icon}
            </span>
            {p.title}
          </div>
          <p className="mt-1.5 text-xs text-muted-foreground">{p.prompt}</p>
        </button>
      ))}
    </div>
  );
}
