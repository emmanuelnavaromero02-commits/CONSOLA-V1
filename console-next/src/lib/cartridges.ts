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
 * is carried by the shared same-origin API client.
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
  authMethodValues?: string[];
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

export interface CartridgeActivation {
  installation?: {
    id?: string;
    cartridge_id?: string;
    status?: string;
    access_status?: string;
    usable?: boolean;
  };
  product?: unknown;
  [key: string]: unknown;
}

export const KNOWN_CARTRIDGES = [
  "replicon",
  "hubspot",
  "banxico",
  "inegi",
  "sap_hcm",
  "sap_s4hana",
  "sap_successfactors",
  "salesforce",
] as const;
export type CartridgeId = typeof KNOWN_CARTRIDGES[number];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function text(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value : undefined;
}

function isFieldType(value: unknown): value is FieldType {
  return ["string", "url", "password", "select", "boolean", "number"].includes(String(value));
}

function stringList(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const items = value.flatMap((item) => (typeof item === "string" && item.trim() ? [item.trim()] : []));
  return items.length ? items : undefined;
}

function addField(fields: ConnectorField[], field: ConnectorField): void {
  if (!fields.some((existing) => existing.name === field.name)) {
    fields.push(field);
  }
}

function envField(
  name: string,
  type: FieldType,
  label: string,
  description: unknown,
  required = true,
): ConnectorField {
  const field: ConnectorField = {
    name,
    type,
    label,
    required,
  };
  const descriptionText = text(description);
  if (descriptionText) field.description = descriptionText;
  return field;
}

function fieldFromSpec(name: string, spec: unknown): ConnectorField {
  const source = isRecord(spec) ? spec : {};
  const field: ConnectorField = {
    name,
    type: isFieldType(source.type) ? source.type : "string",
  };
  const label = text(source.label);
  const description = text(source.description);
  const pattern = text(source.pattern);
  if (label) field.label = label;
  if (description) field.description = description;
  if (pattern) field.pattern = pattern;
  if (typeof source.required === "boolean") field.required = source.required;
  if (typeof source.default === "string" || typeof source.default === "number" || typeof source.default === "boolean") {
    field.default = source.default;
  }
  if (typeof source.min_length === "number") field.min_length = source.min_length;
  if (typeof source.max_length === "number") field.max_length = source.max_length;
  if (Array.isArray(source.options)) {
    field.options = source.options.flatMap((option) => {
      if (!isRecord(option) || typeof option.value !== "string") return [];
      return [{ value: option.value, label: text(option.label) ?? option.value }];
    });
  }
  return field;
}

export async function listCartridges(): Promise<{ cartridges: string[] }> {
  const { data } = await api.get<{ cartridges: string[] }>("/api/cartridges");
  return data;
}

export async function getConnectorSchema(id: string): Promise<ConnectorSchema> {
  const { data } = await api.get<unknown>(`/api/cartridges/${encodeURIComponent(id)}/connector_schema`);
  // Tolerate both shapes — ``{ fields: [...] }`` AND the older
  // ``{ field_a: {...}, field_b: {...} }`` dict form.
  if (isRecord(data) && Array.isArray(data.fields)) {
    const auth = isRecord(data.auth) ? data.auth : {};
    return {
      fields: data.fields.map((field, index) => (
        isRecord(field) && typeof field.name === "string"
          ? fieldFromSpec(field.name, field)
          : fieldFromSpec(`field_${index + 1}`, field)
      )),
      authMethodValues: stringList(auth.auth_method_values),
      name: text(data.name),
      description: text(data.description),
    };
  }
  if (isRecord(data) && isRecord(data.connector)) {
    const connector = data.connector;
    const apiSpec = isRecord(connector.api) ? connector.api : {};
    const authSpec = isRecord(connector.auth) ? connector.auth : {};
    const fields: ConnectorField[] = [];
    const baseUrlEnv = text(apiSpec.base_url_env);
    const authMethodValues = stringList(authSpec.auth_method_values);
    const authType = text(authSpec.type);
    const structuredAuth = authType !== "bearer_token";
    const supportsOauthClientCredentials =
      authType === "oauth2_client_credentials"
      || (structuredAuth && authMethodValues?.includes("oauth2_client_credentials"));
    const supportsSamlBearer =
      authType === "saml_bearer_assertion"
      || (structuredAuth && authMethodValues?.includes("saml_bearer_assertion"));
    if (baseUrlEnv) {
      addField(fields, {
        name: "base_url",
        type: "url",
        label: "Base URL",
        description: baseUrlEnv,
        required: true,
      });
    }
    if (authMethodValues?.length && (supportsOauthClientCredentials || supportsSamlBearer)) {
      const authMethodField: ConnectorField = {
        name: "auth_method",
        type: "select",
        label: "Método de autenticación",
        required: true,
        default: authType ?? authMethodValues[0],
        options: authMethodValues.map((value) => ({ value, label: value })),
      };
      const authMethodEnv = text(authSpec.auth_method_env);
      if (authMethodEnv) authMethodField.description = authMethodEnv;
      addField(fields, authMethodField);
    }
    if (authType === "bearer_token" || authType === "bmx_token") {
      addField(fields, {
        name: "token",
        type: "password",
        label: authType === "bmx_token" ? "Bmx-Token" : "Bearer token",
        description: text(authSpec.env_var) || "API token",
        required: true,
      });
    }
    if (supportsOauthClientCredentials || supportsSamlBearer) {
      addField(fields, envField("client_id", "string", "Client ID", authSpec.client_id_env));
      addField(fields, envField("token_url", "url", "Token URL", authSpec.token_url_env));
      addField(fields, envField("company_id", "string", "Company ID", authSpec.company_id_env));
    }
    if (supportsOauthClientCredentials) {
      addField(fields, envField("client_secret", "password", "Client secret", authSpec.client_secret_env, !supportsSamlBearer));
    }
    if (supportsSamlBearer) {
      addField(fields, envField("admin_user", "string", "Admin user", authSpec.admin_user_env));
      addField(fields, envField("idp_url", "url", "IDP URL", authSpec.idp_url_env, false));
      addField(fields, envField("private_key_pem", "password", "Private key PEM", "SF_PRIVATE_KEY_PEM / SF_PRIVATE_KEY_PATH"));
    }
    return {
      fields,
      authMethodValues,
      name: text(connector.name),
      description: text(connector.description),
    };
  }
  if (isRecord(data)) {
    const fields: ConnectorField[] = Object.entries(data)
      .filter(([k]) => !["name", "description"].includes(k))
      .map(([name, spec]) => fieldFromSpec(name, spec));
    const auth = isRecord(data.auth) ? data.auth : {};
    return { fields, authMethodValues: stringList(auth.auth_method_values), name: text(data.name), description: text(data.description) };
  }
  return { fields: [] };
}

export async function saveCredentials(
  id: string,
  payload: Record<string, string | number | boolean>,
): Promise<{ ok: true; encrypted_count: number; conn_id: string }> {
  const { data } = await api.post<{ ok: true; encrypted_count: number; conn_id: string }>(
    `/api/cartridges/${encodeURIComponent(id)}/credentials`,
    payload,
  );
  return data;
}

export async function testConnection(id: string, connId?: string): Promise<TestConnectionResult> {
  const query = connId?.trim() ? `?conn_id=${encodeURIComponent(connId.trim())}` : "";
  const { data } = await api.post<TestConnectionResult>(
    `/api/cartridges/${encodeURIComponent(id)}/test_connection${query}`,
  );
  return data;
}

export async function deleteCredentials(id: string): Promise<{ ok: true }> {
  const { data } = await api.delete<{ ok: true }>(
    `/api/cartridges/${encodeURIComponent(id)}/credentials`,
  );
  return data;
}

export async function activateCartridge(id: string): Promise<CartridgeActivation> {
  const { data } = await api.post<CartridgeActivation>(
    `/api/marketplace/products/${encodeURIComponent(id)}/activate`,
    {},
  );
  return data;
}
