"use client";

import { ChevronRight, Code2, CornerDownLeft } from "lucide-react";
import type { ReactNode } from "react";

import { buttonClass } from "@/components/studio/ui";
import { cn } from "@/lib/utils";

export function TechnicalSqlDisclosure({
  open,
  onToggle,
  generatedSql,
  onUseGenerated,
  children,
  testId = "technical-sql",
}: {
  open: boolean;
  onToggle: (open: boolean) => void;
  generatedSql?: string | null;
  onUseGenerated?: (sql: string) => void;
  children?: ReactNode;
  testId?: string;
}) {
  return (
    <details open={open} data-testid={testId} className="rounded-md border bg-muted/10">
      <summary
        onClick={(event) => {
          event.preventDefault();
          onToggle(!open);
        }}
        className={cn(
          "flex min-h-[44px] cursor-pointer select-none list-none items-center gap-2 px-3 text-sm font-medium",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden",
        )}
      >
        <ChevronRight aria-hidden className={cn("h-4 w-4 transition-transform", open && "rotate-90")} />
        <Code2 aria-hidden className="h-4 w-4 text-muted-foreground" />
        Ver consulta SQL técnica
        <span className="ml-auto text-xs font-normal text-muted-foreground">Usuarios avanzados</span>
      </summary>
      <div className="space-y-3 border-t p-3">
        {generatedSql ? (
          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">Consulta generada por el constructor (solo lectura)</p>
            <pre
              data-testid="generated-sql"
              className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-md border bg-muted/30 p-3 font-mono text-xs"
            >
              {generatedSql}
            </pre>
            {onUseGenerated ? (
              <button type="button" className={buttonClass} onClick={() => onUseGenerated(generatedSql)}>
                <CornerDownLeft aria-hidden className="h-4 w-4" /> Copiar al editor
              </button>
            ) : null}
          </div>
        ) : null}
        {children}
      </div>
    </details>
  );
}
