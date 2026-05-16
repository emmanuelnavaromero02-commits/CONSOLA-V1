/**
 * v1.44.3 — typed API helpers for /api/cartridges/*.
 *
 * Backend surface (v1.44.1 + v1.44.2 R-Mac-3):
 *   GET    /api/cartridges                      → list cartridge IDs
 *   GET    /api/cartridges/{id}/connector_schema → dynamic form schema
 *   POST   /api/cartridges/{id}/credentials      → encrypt + store
 *   POST   /api/cartridges/{id}/test_connection  → probe live cartridge
 *   DELETE /api/cartridges/{id}/credentials      → revoke
 *
 * All routes are RBAC-gated server-side (cartridges.* or
 * vault.connections.write) and CSRF-gated on mutations. The cookie
 * is forwarded via the shared axios `withCredentials: true`.
 */
import { api } from "@/lib/api";

export type FieldType = "string" | "url" | "password" | "select" | "boolean" | "number";

export interface ConnectorField {
  name:        string;
  type:        FieldType;
  label?:      string;
  description?: string;
  required?:   boolean;
  default?:    string | number | boolean;
  options?:    { value: string; label: string }[];   // for select
  pattern?:    string;
  min_length?: number;
  max_length?: number;
}

export interface ConnectorSchema {
  // The backend's connector.yaml may use either ``fields`` or a
  // flat top-level dict. We type the canonical ``fields`` shape;
  // the page-level adapter handles both forms.
  fields: ConnectorField[];
  // Free-form metadata the schema author may emit; the page only
  // displays it when present.
  name?:        string;
  description?: string;
}

export interface TestConnectionResult {
  ok:        boolean;
  message:   string;
  latency_ms: number;
}

export const KNOWN_CARTRIDGES = ["replicon", "sap_hcm", "sap_s4hana", "sap_successfactors"] as const;
export type CartridgeId = typeof KNOWN_CARTRIDGES[number];

export async function listCartridges(): Promise<{ cartridges: string[] }> {
  const { data } = await api.get("/api/cartridges");
  return data;
}

export async function getConnectorSchema(id: string): Promise<ConnectorSchema> {
  const { data } = await api.get(`/api/cartridges/${encodeURIComponent(id)}/connector_schema`);
  // Tolerate both shapes — ``{ fields: [...] }`` AND the older
  // ``{ field_a: {...}, field_b: {...} }`` dict form.
  if (Array.isArray(data?.fields)) return data;
  if (data && typeof data === "object") {
    const fields: ConnectorField[] = Object.entries(data)
      .filter(([k]) => !["name", "description"].includes(k))
      .map(([name, spec]) => ({
        name,
        ...(typeof spec === "object" && spec !== null ? spec : {}),
      })) as ConnectorField[];
    return { fields, name: data.name, description: data.description };
  }
  return { fields: [] };
}

export async function saveCredentials(
  id: string,
  payload: Record<string, string | number | boolean>,
): Promise<{ ok: true; encrypted_count: number; conn_id: string }> {
  const { data } = await api.post(
    `/api/cartridges/${encodeURIComponent(id)}/credentials`,
    payload,
  );
  return data;
}

export async function testConnection(id: string): Promise<TestConnectionResult> {
  const { data } = await api.post(
    `/api/cartridges/${encodeURIComponent(id)}/test_connection`,
  );
  return data;
}

export async function deleteCredentials(id: string): Promise<{ ok: true }> {
  const { data } = await api.delete(
    `/api/cartridges/${encodeURIComponent(id)}/credentials`,
  );
  return data;
}
