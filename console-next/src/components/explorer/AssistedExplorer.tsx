"use client";

import { Notice, Spinner } from "@/components/studio/ui";
import { mergeParsedSpec, specProblems, type ExplorerColumn, type ExplorerSpec } from "@/lib/explorer/spec";

import { FilterBuilder } from "./FilterBuilder";
import { NaturalLanguageBar } from "./NaturalLanguageBar";

export function AssistedExplorer({
  columns,
  spec,
  onSpecChange,
  rowCap,
  latestAvailable,
  showRowLimit = true,
  loading = false,
  error = null,
  disabled = false,
  today,
}: {
  columns: ExplorerColumn[];
  spec: ExplorerSpec;
  onSpecChange: (spec: ExplorerSpec) => void;
  rowCap: number;
  latestAvailable: boolean;
  showRowLimit?: boolean;
  loading?: boolean;
  error?: string | null;
  disabled?: boolean;
  today?: Date;
}) {
  if (loading) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground" data-testid="assisted-explorer-loading">
        <Spinner /> Cargando columnas de la fuente de datos…
      </p>
    );
  }
  if (error) return <Notice tone="error" title="No se pudo leer el esquema">{error}</Notice>;
  if (!columns.length) return <Notice>La fuente de datos no tiene columnas legibles todavía.</Notice>;

  const problems = specProblems(spec, columns);
  return (
    <div className="space-y-4" data-testid="assisted-explorer">
      <NaturalLanguageBar
        columns={columns}
        latestAvailable={latestAvailable}
        rowCap={rowCap}
        allowLimit={showRowLimit}
        onApply={(result) => onSpecChange(mergeParsedSpec(spec, result))}
        disabled={disabled}
        today={today}
      />
      <FilterBuilder
        columns={columns}
        spec={spec}
        onChange={onSpecChange}
        rowCap={rowCap}
        showRowLimit={showRowLimit}
        latestAvailable={latestAvailable}
        disabled={disabled}
      />
      {problems.length ? (
        <Notice tone="warning" title="Completa el constructor antes de consultar" testId="explorer-problems">
          <ul className="list-disc space-y-0.5 pl-4">
            {problems.map((problem) => <li key={problem}>{problem}</li>)}
          </ul>
        </Notice>
      ) : null}
    </div>
  );
}
