import { useMemo } from "react";

import { basisLabel, confidenceLabel } from "@/lib/catalog/classifications";
import {
  GRAPH_MAX_NODES,
  cardinalitySentence,
  matrixKey,
  relationMatrix,
  type CatalogEdge,
} from "@/lib/catalog/relations";
import { cn } from "@/lib/utils";

interface RelationsMatrixProps {
  edges: readonly CatalogEdge[];
  labelOf: (name: string) => string;
  max?: number;
}

function edgeTooltip(edge: CatalogEdge, labelOf: (name: string) => string): string {
  return [
    cardinalitySentence(edge, labelOf),
    `${edge.fromColumn} → ${edge.toColumn}`,
    edge.origin === "copilot" ? "Detectada por el Copiloto" : edge.origin === "packaged" ? "Incluida en la fuente de datos" : "Registrada por una persona",
    confidenceLabel(edge.confidence),
    ...((edge.relationship.basis ?? []) as string[]).map(basisLabel),
  ]
    .filter((line): line is string => Boolean(line))
    .join("\n");
}

export function RelationsMatrix({ edges, labelOf, max = GRAPH_MAX_NODES }: RelationsMatrixProps) {
  const matrix = useMemo(() => relationMatrix(edges, max), [edges, max]);
  if (!matrix.rows.length) {
    return <p className="px-4 py-6 text-sm text-muted-foreground">Sin relaciones detectadas todavía.</p>;
  }
  return (
    <div className="space-y-2">
      <div className="overflow-x-auto">
        <table className="min-w-full border-collapse text-xs" aria-label="Matriz de relaciones entre tablas">
          <caption className="sr-only">
            Filas: tabla que hace la referencia. Columnas: tabla referenciada.
          </caption>
          <thead>
            <tr>
              <th scope="col" className="sticky left-0 bg-card px-2 py-2 text-left font-medium text-muted-foreground">
                Tabla / Referencia
              </th>
              {matrix.columns.map((column) => (
                <th key={column} scope="col" className="px-2 py-2 text-left font-medium" title={column}>
                  {labelOf(column)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.rows.map((row) => (
              <tr key={row} className="border-t">
                <th scope="row" className="sticky left-0 bg-card px-2 py-2 text-left font-medium" title={row}>
                  {labelOf(row)}
                </th>
                {matrix.columns.map((column) => {
                  const cell = matrix.cells.get(matrixKey(row, column)) ?? [];
                  return (
                    <td key={column} className="px-2 py-2 align-top">
                      <div className="flex flex-wrap gap-1">
                        {cell.map((edge) => (
                          <span
                            key={`${edge.fromColumn}>${edge.toColumn}`}
                            title={edgeTooltip(edge, labelOf)}
                            data-origin={edge.origin}
                            className={cn(
                              "inline-flex items-center rounded border px-1.5 py-0.5 font-medium",
                              edge.origin === "copilot"
                                ? "border-dashed border-violet-400 text-violet-800 dark:text-violet-200"
                                : "border-solid border-foreground/30",
                            )}
                          >
                            {edge.cardinality ?? "—"}
                            <span className="sr-only">. {cardinalitySentence(edge, labelOf)}</span>
                          </span>
                        ))}
                      </div>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {matrix.truncated ? (
        <p className="text-xs text-muted-foreground">
          Se muestran las {max} tablas con más relaciones de {matrix.total}.
        </p>
      ) : null}
    </div>
  );
}
