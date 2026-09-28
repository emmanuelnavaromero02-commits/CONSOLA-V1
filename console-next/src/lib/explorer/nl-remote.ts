import type { ExploreCell, ExploreNlSpec } from "./client";
import type { NlFilter, NlParseResult } from "./nl-parse";
import { operatorArity } from "./operators";

function cellText(value: ExploreCell): string {
  if (value === null) return "";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}

export function nlResultFromRemoteSpec(spec: ExploreNlSpec): NlParseResult {
  const filters: NlFilter[] = spec.filters.map((filter) => {
    const arity = operatorArity(filter.op);
    if (arity === "two") {
      const [low, high] = filter.values ?? [];
      return { column: filter.column, op: filter.op, value: cellText(low ?? null), valueTo: cellText(high ?? null) };
    }
    if (arity === "many") {
      return {
        column: filter.column,
        op: filter.op,
        value: (filter.values ?? []).map(cellText).join(", "),
        valueTo: "",
      };
    }
    if (arity === "none") {
      return { column: filter.column, op: filter.op, value: "", valueTo: "" };
    }
    return { column: filter.column, op: filter.op, value: cellText(filter.value), valueTo: "" };
  });

  return {
    filters,
    sort: spec.sort.map((item) => ({ column: item.column, direction: item.direction })),
    limit: spec.limit,
    latestOnly: spec.latest_only,
    unrecognized: [],
    notes: ["Interpretación del copiloto: revisa y edita los filtros antes de consultar."],
  };
}
