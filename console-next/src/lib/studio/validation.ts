import type { EntityPatch, StudioManifest, SystemInfo } from "./types";

export const CARTRIDGE_ID_RE = /^[a-z][a-z0-9_]{0,79}$/;
export const IDENTIFIER_RE = /^[A-Za-z_][A-Za-z0-9_]{0,127}$/;
export const DAG_ID_RE = /^[A-Za-z][A-Za-z0-9_]{0,127}$/;

export function cartridgeIdError(value: string): string | null {
  if (!value.trim()) return "El identificador es obligatorio.";
  if (!CARTRIDGE_ID_RE.test(value.trim())) {
    return "Usa minúsculas, números y guion bajo; debe empezar con letra.";
  }
  return null;
}

export function identifierError(value: string, label: string): string | null {
  if (!value.trim()) return `${label} es obligatorio.`;
  if (!IDENTIFIER_RE.test(value.trim())) return `${label}: usa letras, números y guion bajo.`;
  return null;
}

export function dagIdError(value: string, cartridge: string): string | null {
  const dagId = value.trim();
  if (!dagId) return "El dag_id es obligatorio.";
  if (!DAG_ID_RE.test(dagId)) return "dag_id inválido: solo letras, números y guion bajo.";
  if (!dagId.startsWith(`${cartridge}_`)) return `El dag_id debe empezar con «${cartridge}_».`;
  return null;
}

export function managedDagIds(manifest: StudioManifest | null | undefined): Set<string> {
  const ids = new Set<string>();
  for (const dag of manifest?.dags ?? []) {
    if (typeof dag === "string" && dag) ids.add(dag);
    else if (dag && typeof dag === "object" && dag.dag_id) ids.add(String(dag.dag_id));
  }
  for (const entity of manifest?.entities ?? []) {
    if (entity?.dag_id) ids.add(String(entity.dag_id));
  }
  return ids;
}

export interface DeployGate {
  enabled: boolean;
  reason: string | null;
}

export function deployGate(info: SystemInfo | null | undefined, failed: boolean): DeployGate {
  if (failed) return { enabled: false, reason: "No se pudo leer /api/system/info; deploy y borrado deshabilitados." };
  if (!info) return { enabled: false, reason: "Verificando si el entorno permite desplegar DAGs…" };
  if (info.dag_deploy_enabled) return { enabled: true, reason: null };
  if (!info.dev_mode) {
    return {
      enabled: false,
      reason: "Deploy deshabilitado: solo se permite en entornos de desarrollo; en producción los DAGs llegan por CI/CD.",
    };
  }
  return { enabled: false, reason: "Deploy deshabilitado: falta ALLOW_RCE_TOOLS=true en el entorno local." };
}

export interface EntityDraft {
  display_name: string;
  mode: string;
  primary_key: string;
  dag_id: string;
  cron_expression: string;
  description: string;
}

export function changedEntityFields(before: EntityDraft, after: EntityDraft): EntityPatch {
  const patch: EntityPatch = {};
  if (after.display_name !== before.display_name) patch.display_name = after.display_name.trim();
  if (after.mode !== before.mode) patch.mode = after.mode;
  if (after.primary_key !== before.primary_key) patch.primary_key = after.primary_key.trim();
  if (after.dag_id !== before.dag_id) patch.dag_id = after.dag_id.trim();
  if (after.description !== before.description) patch.description = after.description.trim();
  if (after.cron_expression !== before.cron_expression) {
    const cron = after.cron_expression.trim();
    patch.cron_expression = cron || null;
    patch.trigger_type = cron ? "scheduled" : "manual";
  }
  return patch;
}
