"use client";

import { ArrowDownUp, Columns3, Filter, Plus, X } from "lucide-react";

import { buttonClass, inputClass } from "@/components/studio/ui";
import {
  KIND_LABELS,
  OPERATOR_LABELS,
  isSortableKind,
  operatorArity,
  operatorsForKind,
  type ExplorerOp,
} from "@/lib/explorer/operators";
import {
  MAX_FILTERS,
  MAX_SORT,
  clampRows,
  emptyFilter,
  type ExplorerColumn,
  type ExplorerFilterDraft,
  type ExplorerSpec,
} from "@/lib/explorer/spec";
import { cn } from "@/lib/utils";

const iconButtonClass = cn(
  "inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-md border bg-background text-muted-foreground",
  "hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
);

function ValueInput({
  column,
  filter,
  index,
  onChange,
}: {
  column: ExplorerColumn | undefined;
  filter: ExplorerFilterDraft;
  index: number;
  onChange: (patch: Partial<ExplorerFilterDraft>) => void;
}) {
  const arity = operatorArity(filter.op);
  if (arity === "none") return <span className="hidden md:block" aria-hidden />;
  const kind = column?.kind ?? "text";
  const label = `Valor del filtro ${index + 1}`;
  if (kind === "boolean" && arity === "one") {
    return (
      <select aria-label={label} value={filter.value} onChange={(event) => onChange({ value: event.target.value })} className={inputClass}>
        <option value="">—</option>
        <option value="true">Sí</option>
        <option value="false">No</option>
      </select>
    );
  }
  const type = kind === "temporal" && arity !== "many" ? "date" : "text";
  const inputMode = kind === "number" ? "decimal" : undefined;
  if (arity === "two") {
    return (
      <div className="grid grid-cols-2 gap-2">
        <input
          aria-label={`Desde (filtro ${index + 1})`}
          type={type}
          inputMode={inputMode}
          value={filter.value}
          onChange={(event) => onChange({ value: event.target.value })}
          className={inputClass}
        />
        <input
          aria-label={`Hasta (filtro ${index + 1})`}
          type={type}
          inputMode={inputMode}
          value={filter.valueTo}
          onChange={(event) => onChange({ valueTo: event.target.value })}
          className={inputClass}
        />
      </div>
    );
  }
  return (
    <input
      aria-label={label}
      type={type}
      inputMode={inputMode}
      value={filter.value}
      placeholder={arity === "many" ? "Separa los valores con comas" : kind === "number" ? "Ej.: 1500.50" : "Valor"}
      onChange={(event) => onChange({ value: event.target.value })}
      className={inputClass}
    />
  );
}

export function FilterBuilder({
  columns,
  spec,
  onChange,
  rowCap,
  showRowLimit = true,
  latestAvailable = false,
  disabled = false,
}: {
  columns: ExplorerColumn[];
  spec: ExplorerSpec;
  onChange: (spec: ExplorerSpec) => void;
  rowCap: number;
  showRowLimit?: boolean;
  latestAvailable?: boolean;
  disabled?: boolean;
}) {
  const byName = new Map(columns.map((column) => [column.name, column]));
  const sortable = columns.filter((column) => isSortableKind(column.kind));

  function patchFilter(id: string, patch: Partial<ExplorerFilterDraft>) {
    onChange({
      ...spec,
      filters: spec.filters.map((filter) => {
        if (filter.id !== id) return filter;
        const next = { ...filter, ...patch };
        if (
          (patch.value !== undefined || patch.op !== undefined)
          && patch.values === undefined
        ) {
          delete next.values;
        }
        if (patch.column !== undefined && patch.column !== filter.column) {
          const kind = byName.get(patch.column)?.kind ?? "text";
          const allowed = operatorsForKind(kind);
          if (!allowed.includes(next.op)) next.op = allowed[0];
          next.value = "";
          next.valueTo = "";
          delete next.values;
        }
        return next;
      }),
    });
  }

  function toggleColumn(name: string) {
    const selected = spec.columns.includes(name)
      ? spec.columns.filter((item) => item !== name)
      : [...spec.columns, name].sort((a, b) => columns.findIndex((c) => c.name === a) - columns.findIndex((c) => c.name === b));
    onChange({ ...spec, columns: selected });
  }

  return (
    <fieldset disabled={disabled} className="space-y-4" data-testid="filter-builder">
      <legend className="sr-only">Constructor de filtros</legend>

      <section aria-label="Filtros" className="space-y-2">
        <h4 className="flex items-center gap-2 text-sm font-semibold">
          <Filter aria-hidden className="h-4 w-4" /> Filtros
        </h4>
        {!spec.filters.length ? (
          <p className="text-xs text-muted-foreground">Sin filtros: se muestran todas las filas hasta el límite.</p>
        ) : null}
        <ul className="space-y-2">
          {spec.filters.map((filter, index) => {
            const column = byName.get(filter.column);
            const ops: ExplorerOp[] = operatorsForKind(column?.kind ?? "text");
            return (
              <li key={filter.id} className="grid grid-cols-1 gap-2 rounded-md border p-2 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.4fr)_auto]">
                <select
                  aria-label={`Columna del filtro ${index + 1}`}
                  value={filter.column}
                  onChange={(event) => patchFilter(filter.id, { column: event.target.value })}
                  className={inputClass}
                >
                  {!column ? <option value="">Elige una columna</option> : null}
                  {columns.map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.name} · {KIND_LABELS[item.kind]}
                    </option>
                  ))}
                </select>
                <select
                  aria-label={`Condición del filtro ${index + 1}`}
                  value={filter.op}
                  onChange={(event) => patchFilter(filter.id, { op: event.target.value as ExplorerOp })}
                  className={inputClass}
                >
                  {ops.map((op) => (
                    <option key={op} value={op}>{OPERATOR_LABELS[op]}</option>
                  ))}
                </select>
                <ValueInput column={column} filter={filter} index={index} onChange={(patch) => patchFilter(filter.id, patch)} />
                <button
                  type="button"
                  aria-label={`Quitar filtro ${index + 1}`}
                  className={iconButtonClass}
                  onClick={() => onChange({ ...spec, filters: spec.filters.filter((item) => item.id !== filter.id) })}
                >
                  <X aria-hidden className="h-4 w-4" />
                </button>
              </li>
            );
          })}
        </ul>
        <button
          type="button"
          className={buttonClass}
          disabled={!columns.length || spec.filters.length >= MAX_FILTERS}
          onClick={() => onChange({ ...spec, filters: [...spec.filters, emptyFilter(columns)] })}
        >
          <Plus aria-hidden className="h-4 w-4" /> Agregar filtro
        </button>
      </section>

      <section aria-label="Orden" className="space-y-2">
        <h4 className="flex items-center gap-2 text-sm font-semibold">
          <ArrowDownUp aria-hidden className="h-4 w-4" /> Ordenar por
        </h4>
        <ul className="space-y-2">
          {spec.sort.map((sort, index) => (
            <li key={`${sort.column}-${index}`} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] gap-2">
              <select
                aria-label={`Columna de orden ${index + 1}`}
                value={sort.column}
                onChange={(event) =>
                  onChange({
                    ...spec,
                    sort: spec.sort.map((item, position) => (position === index ? { ...item, column: event.target.value } : item)),
                  })
                }
                className={inputClass}
              >
                {!byName.has(sort.column) ? <option value="">Elige una columna</option> : null}
                {sortable.map((item) => (
                  <option key={item.name} value={item.name}>{item.name}</option>
                ))}
              </select>
              <select
                aria-label={`Dirección de orden ${index + 1}`}
                value={sort.direction}
                onChange={(event) =>
                  onChange({
                    ...spec,
                    sort: spec.sort.map((item, position) =>
                      position === index ? { ...item, direction: event.target.value === "desc" ? "desc" : "asc" } : item,
                    ),
                  })
                }
                className={inputClass}
              >
                <option value="asc">De menor a mayor</option>
                <option value="desc">De mayor a menor</option>
              </select>
              <button
                type="button"
                aria-label={`Quitar orden ${index + 1}`}
                className={iconButtonClass}
                onClick={() => onChange({ ...spec, sort: spec.sort.filter((_, position) => position !== index) })}
              >
                <X aria-hidden className="h-4 w-4" />
              </button>
            </li>
          ))}
        </ul>
        <button
          type="button"
          className={buttonClass}
          disabled={!sortable.length || spec.sort.length >= MAX_SORT}
          onClick={() => {
            const next = sortable.find((item) => !spec.sort.some((sort) => sort.column === item.name));
            if (next) onChange({ ...spec, sort: [...spec.sort, { column: next.name, direction: "asc" }] });
          }}
        >
          <Plus aria-hidden className="h-4 w-4" /> Agregar orden
        </button>
      </section>

      <details className="rounded-md border">
        <summary className="flex min-h-[44px] cursor-pointer items-center gap-2 px-3 text-sm font-semibold">
          <Columns3 aria-hidden className="h-4 w-4" /> Columnas
          <span className="font-normal text-muted-foreground">
            {spec.columns.length ? `${spec.columns.length} de ${columns.length}` : "todas"}
          </span>
        </summary>
        <div className="max-h-56 space-y-1 overflow-y-auto border-t p-3">
          {spec.columns.length ? (
            <button type="button" className="text-xs underline" onClick={() => onChange({ ...spec, columns: [] })}>
              Mostrar todas las columnas
            </button>
          ) : null}
          {columns.map((column) => (
            <label key={column.name} className="flex min-h-[32px] items-center gap-2 text-sm">
              <input type="checkbox" checked={spec.columns.includes(column.name)} onChange={() => toggleColumn(column.name)} />
              <span className="break-all font-mono text-xs">{column.name}</span>
              <span className="text-[11px] text-muted-foreground">{KIND_LABELS[column.kind]}</span>
            </label>
          ))}
        </div>
      </details>

      <div className="flex flex-wrap items-end gap-4">
        {showRowLimit ? (
          <label className="flex flex-col gap-1 text-sm">
            <span className="font-medium">Filas a mostrar</span>
            <input
              type="number"
              min={1}
              max={rowCap}
              value={spec.limit}
              onChange={(event) => onChange({ ...spec, limit: clampRows(event.target.value, rowCap) })}
              className={cn(inputClass, "w-36")}
            />
            <span className="text-[11px] text-muted-foreground">Máximo {rowCap.toLocaleString("es-MX")}</span>
          </label>
        ) : null}
        {latestAvailable ? (
          <label className="flex min-h-[44px] items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={spec.latestOnly}
              onChange={(event) => onChange({ ...spec, latestOnly: event.target.checked })}
            />
            Solo la carga más reciente
          </label>
        ) : null}
      </div>
    </fieldset>
  );
}
