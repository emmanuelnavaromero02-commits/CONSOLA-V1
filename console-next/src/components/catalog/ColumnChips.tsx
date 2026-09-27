import { KeyRound } from "lucide-react";

import {
  basisLabel,
  classificationLabel,
  confidenceLabel,
  orderedClassifications,
} from "@/lib/catalog/classifications";
import { humanTypeLabel } from "@/lib/catalog/human-types";
import type { CatalogClassification, CatalogColumn } from "@/lib/data/types";
import { cn } from "@/lib/utils";

const CHIP_TONE: Record<CatalogClassification, string> = {
  pii: "border-rose-300 bg-rose-50 text-rose-800 dark:border-rose-800 dark:bg-rose-950/40 dark:text-rose-200",
  financial: "border-amber-300 bg-amber-50 text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200",
  confidential: "border-slate-300 bg-slate-100 text-slate-700 dark:border-slate-700 dark:bg-slate-900/60 dark:text-slate-200",
};

function classificationTooltip(column: CatalogColumn): string {
  const origin = column.classification_origin === "packaged"
    ? "Declarado por la fuente de datos"
    : column.classification_origin === "manual"
      ? "Clasificado por una persona"
      : "Inferido por el Copiloto";
  return [
    origin,
    confidenceLabel(column.copilot_confidence),
    ...(column.copilot_basis ?? []).map(basisLabel),
  ]
    .filter((line): line is string => Boolean(line))
    .join("\n");
}

export function ColumnChips({ column, className }: { column: CatalogColumn; className?: string }) {
  const classes = orderedClassifications(column.classifications);
  const rawType = String(column.type ?? "").trim();
  return (
    <div className={cn("flex flex-wrap items-center gap-1", className)}>
      <span
        className="inline-flex items-center rounded-md border bg-muted/40 px-2 py-0.5 text-xs font-medium text-muted-foreground"
        title={rawType ? `Tipo técnico: ${rawType}` : undefined}
      >
        {humanTypeLabel(column)}
      </span>
      {column.is_key ? (
        <span className="inline-flex items-center gap-1 rounded-md border border-primary/30 bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">
          <KeyRound aria-hidden className="h-3 w-3" />
          Llave
        </span>
      ) : null}
      {classes.map((name) => (
        <span
          key={name}
          title={classificationTooltip(column)}
          className={cn("inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium", CHIP_TONE[name])}
        >
          {classificationLabel(name)}
        </span>
      ))}
      {column.stats_redacted ? (
        <span className="text-xs text-muted-foreground" title="Valores mínimos, máximos y ejemplos ocultos por ser datos sensibles">
          Valores ocultos
        </span>
      ) : null}
    </div>
  );
}
