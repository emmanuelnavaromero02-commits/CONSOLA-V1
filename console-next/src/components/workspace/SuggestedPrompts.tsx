"use client";

interface SuggestedPrompt {
  icon:   string;
  title:  string;
  prompt: string;
}

interface Props {
  onSelect: (prompt: string) => void;
}

const PROMPTS: SuggestedPrompt[] = [
  {
    icon:   "✨",
    title:  "¿Qué puedes hacer?",
    prompt: "¿Qué puedes hacer? Dame ejemplos concretos para mi operación.",
  },
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
