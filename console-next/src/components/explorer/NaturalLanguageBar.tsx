"use client";

import { Bot, Loader2, Sparkles } from "lucide-react";
import { useId, useState, type FormEvent } from "react";

import { buttonClass, inputClass } from "@/components/studio/ui";
import type { ApiError } from "@/lib/api";
import { exploreNl } from "@/lib/explorer/client";
import { parseNaturalLanguage, type NlColumn, type NlParseResult } from "@/lib/explorer/nl-parse";
import { nlResultFromRemoteSpec } from "@/lib/explorer/nl-remote";
import type { ExplorerSource } from "@/lib/explorer/spec";

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
  source,
}: {
  columns: NlColumn[];
  latestAvailable: boolean;
  rowCap: number;
  allowLimit?: boolean;
  onApply: (result: NlParseResult) => void;
  disabled?: boolean;
  today?: Date;
  source?: ExplorerSource;
}) {
  const inputId = useId();
  const [text, setText] = useState("");
  const [result, setResult] = useState<NlParseResult | null>(null);
  const [remotePending, setRemotePending] = useState(false);
  const [remoteError, setRemoteError] = useState<string | null>(null);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!text.trim()) return;
    setRemoteError(null);
    const parsed = parseNaturalLanguage(text, columns, { latestAvailable, today, allowLimit });
    if (parsed.limit !== null && parsed.limit > rowCap) {
      parsed.notes.push(`Se pidieron ${parsed.limit.toLocaleString("es-MX")} filas; el máximo para esta fuente es ${rowCap.toLocaleString("es-MX")}.`);
      parsed.limit = rowCap;
    }
    setResult(parsed);
    if (appliedParts(parsed).length) onApply(parsed);
  }

  async function askCopilot() {
    const question = text.trim();
    if (!question || !source || remotePending) return;
    setRemotePending(true);
    setRemoteError(null);
    try {
      const response = await exploreNl(source, question);
      const converted = nlResultFromRemoteSpec(response.spec);
      setResult(converted);
      if (appliedParts(converted).length) onApply(converted);
    } catch (error) {
      const status = error instanceof Error ? (error as ApiError).status : undefined;
      if (status === undefined || status >= 500) {
        // Network failures and 5xx: honest copy, never a raw error message.
        setRemoteError("Sin conexión al asistente");
      } else if (error instanceof Error && error.message) {
        setRemoteError(error.message);
      } else {
        setRemoteError("Sin conexión al asistente");
      }
    } finally {
      setRemotePending(false);
    }
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
        {source ? (
          <button
            type="button"
            onClick={() => void askCopilot()}
            className={buttonClass}
            disabled={disabled || remotePending || !text.trim()}
            data-testid="nl-ask-copilot"
          >
            {remotePending
              ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
              : <Bot aria-hidden className="h-4 w-4" />}
            Preguntar al copiloto
          </button>
        ) : null}
      </div>
      <p className="text-[11px] text-muted-foreground">
        Interpretación por reglas en español, sin IA: revisa los filtros resultantes antes de consultar.
        {source ? " «Preguntar al copiloto» usa IA y carga filtros editables en el constructor." : ""}
      </p>
      {remoteError ? (
        <p role="alert" className="text-xs text-destructive" data-testid="nl-remote-error">
          {remoteError}
        </p>
      ) : null}
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
