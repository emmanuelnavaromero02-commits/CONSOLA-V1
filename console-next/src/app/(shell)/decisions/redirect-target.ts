import {
  resolveDecisionsTab,
  resolveFocusedProposal,
} from "@/components/decisions/DecisionsTabs";

export function decisionsRedirectTarget(params: URLSearchParams): string {
  if (resolveDecisionsTab(params.get("tab")) === "registro") {
    return "/control-room?fase=supervisa";
  }
  const focus = resolveFocusedProposal(params.get("propuesta"));
  return focus === null
    ? "/control-room?fase=ejecuta"
    : `/control-room?fase=ejecuta&propuesta=${focus}`;
}
