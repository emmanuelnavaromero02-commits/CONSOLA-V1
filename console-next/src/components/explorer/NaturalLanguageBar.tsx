"use client";

import { Sparkles } from "lucide-react";
import { useId, useState, type FormEvent } from "react";

import { buttonClass, inputClass } from "@/components/studio/ui";
import { parseNaturalLanguage, type NlColumn, type NlParseResult } from "@/lib/explorer/nl-parse";

const EXAMPLE = "Mostrar empleados con salario mayor que 20000 y fecha de ingreso antes de 2022";

function appliedParts(result: NlParseResult): string[] {
  const parts: string[] = [];
  if (result.filters.length) parts.push(`${result.filters.length} ${result.filters.length === 1 ? "filtro" : "filtros"}`);
  if (result.sort.length) parts.push(`${result.sort.length} ${result.sort.length === 1 ? "criterio de orden" : "criterios de orden"}`);
  if (result.limit !== null) parts.push(`${result.limit.toLocaleString("es-MX")} filas`);
  if (result.latestOnly) parts.push("solo la carga más reciente");
  return parts;
}

export function NaturalLanguageBar({
  columns,
  latestAvailable,
  rowCap,
  allowLimit = true,
  onApply,
  disabled = false,
  today,
}: {
  columns: NlColumn[];
  latestAvailable: boolean;
  rowCap: number;
  allowLimit?: boolean;
  onApply: (result: NlParseResult) => void;
  disabled?: boolean;
  today?: Date;
}) {
  const inputId = useId();
  const [text, setText] = useState("");
  const [result, setResult] = useState<NlParseResult | null>(null);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!text.trim()) return;
    const parsed = parseNaturalLanguage(text, columns, { latestAvailable, today, allowLimit });
    if (parsed.limit !== null && parsed.limit > rowCap) {
      parsed.notes.push(`Se pidieron ${parsed.limit.toLocaleString("es-MX")} filas; el máximo para esta fuente es ${rowCap.toLocaleString("es-MX")}.`);
      parsed.limit = rowCap;
    }
    setResult(parsed);
    if (appliedParts(parsed).length) onApply(parsed);
  }

  const applied = result ? appliedParts(result) : [];
  return (
    <form onSubmit={submit} className="space-y-2" data-testid="nl-bar">
      <label htmlFor={inputId} className="text-sm font-semibold">¿Qué deseas analizar o filtrar?</label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          id={inputId}
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={`Ej.: ${EXAMPLE}`}
          disabled={disabled}
          className={inputClass}
          autoComplete="off"
        />
        <button type="submit" className={buttonClass} disabled={disabled || !text.trim()}>
          <Sparkles aria-hidden className="h-4 w-4" /> Interpretar
        </button>
      </div>
      <p className="text-[11px] text-muted-foreground">
        Interpretación por reglas en español, sin IA: revisa los filtros resultantes antes de consultar.
      </p>
      {result ? (
        <div role="status" className="space-y-1 rounded-md border bg-muted/20 p-3 text-xs" data-testid="nl-feedback">
          <p className="font-medium">
            {applied.length ? `Se aplicó: ${applied.join(", ")}.` : "No se aplicó ningún cambio."}
          </p>
          {result.filters.length || result.sort.length ? (
            <p className="text-muted-foreground">
              {[
                result.filters.length ? "Los filtros interpretados reemplazan a los anteriores." : "",
                result.sort.length ? "El orden interpretado reemplaza al anterior." : "",
              ].filter(Boolean).join(" ")}
            </p>
          ) : null}
          {result.unrecognized.map((item, index) => (
            <p key={`u-${index}`} className="text-destructive">
              No entendí: «{item.text}». {item.hint}
            </p>
          ))}
          {result.notes.map((note, index) => (
            <p key={`n-${index}`} className="text-muted-foreground">{note}</p>
          ))}
        </div>
      ) : null}
    </form>
  );
}
