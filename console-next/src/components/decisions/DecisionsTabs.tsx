export type DecisionsTab = "consejo" | "registro";

export function resolveDecisionsTab(value: string | null): DecisionsTab {
  return value === "registro" ? "registro" : "consejo";
}

export function resolveFocusedProposal(value: string | null): number | null {
  if (!value || !/^[1-9][0-9]{0,18}$/.test(value)) return null;
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) ? parsed : null;
}
