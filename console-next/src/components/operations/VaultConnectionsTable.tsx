"use client";

import type { ReactNode } from "react";
import { useState } from "react";
import { Eye, EyeOff, Plus, RefreshCw, Save, Trash2, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { toast } from "sonner";

import type {
  VaultAuthMethod,
  VaultConnection,
  VaultConnectionPayload,
  VaultSecret,
} from "@/lib/operations/types";
import {
  useDeleteVaultConnection,
  useDeleteVaultSecret,
  useRevealVaultConnection,
  useRevealVaultSecret,
  useUpsertVaultConnection,
  useUpsertVaultSecret,
  useVaultConnections,
  useVaultSecrets,
} from "@/lib/operations/hooks";
import { useConnectorSchema } from "@/lib/hooks/useCartridges";
import { cn } from "@/lib/utils";

const CARTRIDGES = [
  { id: "sap_successfactors", label: "SAP SuccessFactors" },
  { id: "replicon", label: "Replicon" },
  { id: "hubspot", label: "HubSpot CRM" },
  { id: "sap_hcm", label: "SAP HCM" },
  { id: "sap_s4hana", label: "SAP S/4HANA" },
];

type VaultTab = "connections" | "secrets";

export interface ConnForm {
  connId: string;
  baseUrl: string;
  authMethod: VaultAuthMethod;
  token: string;
  clientId: string;
  clientSecret: string;
  tokenUrl: string;
  companyId: string;
  adminUser: string;
  privateKeyPem: string;
  idpUrl: string;
  extraJson: string;
}

interface SecretForm {
  key: string;
  value: string;
}

const EMPTY_CONN_FORM: ConnForm = {
  connId: "",
  baseUrl: "",
  authMethod: "bearer_token",
  token: "",
  clientId: "",
  clientSecret: "",
  tokenUrl: "",
  companyId: "",
  adminUser: "",
  privateKeyPem: "",
  idpUrl: "",
  extraJson: "",
};

const EMPTY_SECRET_FORM: SecretForm = { key: "", value: "" };
const FALLBACK_AUTH_METHODS: VaultAuthMethod[] = ["bearer_token", "api_key", "basic", "none"];

// Mirrors connector.yaml auth.auth_method_values for cartridges that expose it.
// Most current cartridges only declare auth.type, so they intentionally fall
// back to the legacy four-method selector.
const CONNECTOR_AUTH_METHOD_VALUES: Record<string, VaultAuthMethod[]> = {
  sap_successfactors: ["oauth2_client_credentials", "saml_bearer_assertion"],
};

const EXPLICIT_CONNECTION_FIELDS = [
  "id",
  "conn_id",
  "base_url",
  "auth_method",
  "token",
  "client_id",
  "client_secret",
  "token_url",
  "company_id",
  "admin_user",
  "private_key_pem",
  "idp_url",
];

export function VaultConnectionsTable() {
  const [tab, setTab] = useState<VaultTab>("connections");
  const [cartridge, setCartridge] = useState("sap_successfactors");
  const [scope, setScope] = useState("llm");
  const [connForm, setConnForm] = useState<ConnForm>(EMPTY_CONN_FORM);
  const [secretForm, setSecretForm] = useState<SecretForm>(EMPTY_SECRET_FORM);
  const [editingConnId, setEditingConnId] = useState<string | null>(null);
  const [editingSecretKey, setEditingSecretKey] = useState<string | null>(null);
  const [revealedTokens, setRevealedTokens] = useState<Record<string, string>>({});
  const [revealedSecrets, setRevealedSecrets] = useState<Record<string, string>>({});
  const [deleteConnId, setDeleteConnId] = useState<string | null>(null);
  const [deleteSecretKey, setDeleteSecretKey] = useState<string | null>(null);

  const activeScope = scope.trim();
  const connections = useVaultConnections(cartridge);
  const secrets = useVaultSecrets(activeScope || null);
  const revealConnection = useRevealVaultConnection();
  const saveConnection = useUpsertVaultConnection();
  const removeConnection = useDeleteVaultConnection();
  const revealSecret = useRevealVaultSecret();
  const saveSecret = useUpsertVaultSecret();
  const removeSecret = useDeleteVaultSecret();
  const connectorSchema = useConnectorSchema(cartridge);

  const rows = connections.data?.connections ?? [];
  const secretRows = secrets.data?.secrets ?? [];

  function resetConnectionForm() {
    setEditingConnId(null);
    setConnForm(EMPTY_CONN_FORM);
  }

  function resetSecretForm() {
    setEditingSecretKey(null);
    setSecretForm(EMPTY_SECRET_FORM);
  }

  async function revealToken(connId: string) {
    if (revealedTokens[connId]) {
      setRevealedTokens((current) => {
        const next = { ...current };
        delete next[connId];
        return next;
      });
      return;
    }
    try {
      const data = await revealConnection.mutateAsync({ cartridge, connId });
      setRevealedTokens((current) => ({ ...current, [connId]: revealedConnectionSecret(data) }));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Sin permisos para revelar.");
    }
  }

  async function editConnection(conn: VaultConnection) {
    const id = connectionId(conn);
    if (!id) return;
    try {
      const data = await revealConnection.mutateAsync({ cartridge, connId: id });
      const extra = omitKeys(data, EXPLICIT_CONNECTION_FIELDS);
      setEditingConnId(id);
      setConnForm({
        connId: id,
        baseUrl: String(data.base_url ?? conn.base_url ?? ""),
        authMethod: String(data.auth_method ?? conn.auth_method ?? "bearer_token"),
        token: String(data.token ?? ""),
        clientId: String(data.client_id ?? ""),
        clientSecret: String(data.client_secret ?? ""),
        tokenUrl: String(data.token_url ?? ""),
        companyId: String(data.company_id ?? ""),
        adminUser: String(data.admin_user ?? ""),
        privateKeyPem: String(data.private_key_pem ?? ""),
        idpUrl: String(data.idp_url ?? ""),
        extraJson: Object.keys(extra).length ? JSON.stringify(extra, null, 2) : "",
      });
    } catch {
      setEditingConnId(id);
      setConnForm({
        connId: id,
        baseUrl: String(conn.base_url ?? ""),
        authMethod: String(conn.auth_method ?? conn.kind ?? "bearer_token"),
        token: "",
        clientId: String(conn.client_id ?? ""),
        clientSecret: "",
        tokenUrl: String(conn.token_url ?? ""),
        companyId: String(conn.company_id ?? ""),
        adminUser: String(conn.admin_user ?? ""),
        privateKeyPem: "",
        idpUrl: String(conn.idp_url ?? ""),
        extraJson: "",
      });
      toast.info("Sin revelado de secreto; puedes reemplazar la conexión guardando un nuevo token.");
    }
  }

  function saveConnectionForm() {
    const connId = connForm.connId.trim();
    if (!connId) {
      toast.error("Conn ID es obligatorio.");
      return;
    }
    const payload = buildVaultConnectionPayload(connForm);
    if (payload === null) return;
    saveConnection.mutate(
      { cartridge, connId, payload },
      {
        onSuccess: () => {
          toast.success("Conexión guardada.");
          resetConnectionForm();
        },
        onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo guardar."),
      },
    );
  }

  function confirmDeleteConnection(connId: string) {
    removeConnection.mutate(
      { cartridge, connId },
      {
        onSuccess: () => {
          toast.success("Conexión eliminada.");
          setDeleteConnId(null);
          setRevealedTokens((current) => {
            const next = { ...current };
            delete next[connId];
            return next;
          });
        },
        onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo eliminar."),
      },
    );
  }

  async function revealSecretValue(key: string) {
    if (!activeScope) {
      toast.error("Scope es obligatorio.");
      return;
    }
    if (revealedSecrets[key]) {
      setRevealedSecrets((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
      return;
    }
    try {
      const data = await revealSecret.mutateAsync({ scope: activeScope, key });
      setRevealedSecrets((current) => ({ ...current, [key]: String(data.value ?? "") }));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Sin permisos para revelar secret.");
    }
  }

  async function editSecret(secret: VaultSecret) {
    if (!activeScope) {
      toast.error("Scope es obligatorio.");
      return;
    }
    const key = secretKey(secret);
    if (!key) return;
    try {
      const data = await revealSecret.mutateAsync({ scope: activeScope, key });
      setEditingSecretKey(key);
      setSecretForm({ key, value: String(data.value ?? "") });
    } catch {
      setEditingSecretKey(key);
      setSecretForm({ key, value: "" });
      toast.info("Sin revelado de secreto; puedes reemplazarlo guardando un nuevo valor.");
    }
  }

  function saveSecretForm() {
    const key = secretForm.key.trim();
    if (!activeScope || !key) {
      toast.error("Scope y key son obligatorios.");
      return;
    }
    saveSecret.mutate(
      { scope: activeScope, key, payload: { value: secretForm.value } },
      {
        onSuccess: () => {
          toast.success("Secret guardado.");
          resetSecretForm();
        },
        onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo guardar."),
      },
    );
  }

  function confirmDeleteSecret(key: string) {
    if (!activeScope) {
      toast.error("Scope es obligatorio.");
      return;
    }
    removeSecret.mutate(
      { scope: activeScope, key },
      {
        onSuccess: () => {
          toast.success("Secret eliminado.");
          setDeleteSecretKey(null);
          setRevealedSecrets((current) => {
            const next = { ...current };
            delete next[key];
            return next;
          });
        },
        onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo eliminar."),
      },
    );
  }

  return (
    <div className="space-y-4">
      <section className="rounded-lg border bg-card p-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap gap-2">
            <button type="button" onClick={() => setTab("connections")} className={tabButton(tab === "connections")}>
              Conexiones
            </button>
            <button type="button" onClick={() => setTab("secrets")} className={tabButton(tab === "secrets")}>
              Secrets
            </button>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {tab === "connections" ? (
              <select
                value={cartridge}
                onChange={(event) => {
                  setCartridge(event.target.value);
                  setRevealedTokens({});
                  resetConnectionForm();
                }}
                className="min-h-[44px] rounded-md border bg-background px-3 text-sm"
              >
                {CARTRIDGES.map((item) => (
                  <option key={item.id} value={item.id}>{item.label} ({item.id})</option>
                ))}
              </select>
            ) : (
              <input
                value={scope}
                onChange={(event) => {
                  setScope(event.target.value);
                  setRevealedSecrets({});
                  resetSecretForm();
                }}
                className="min-h-[44px] rounded-md border bg-background px-3 text-sm"
                placeholder="platform o cartucho"
              />
            )}
            <button
              type="button"
              onClick={() => (tab === "connections" ? connections.refetch() : secrets.refetch())}
              className="inline-flex min-h-[44px] items-center gap-2 rounded-md border bg-background px-3 text-sm font-medium"
            >
              <RefreshCw aria-hidden className="h-4 w-4" />
              Refrescar
            </button>
          </div>
        </div>
      </section>

      {tab === "connections" ? (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
          <section className="space-y-3 rounded-lg border bg-card p-4">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-base font-semibold">Conexiones de {cartridge}</h2>
              <button type="button" onClick={resetConnectionForm} className="inline-flex min-h-[40px] items-center gap-2 rounded-md border px-3 text-xs font-medium">
                <Plus aria-hidden className="h-4 w-4" />
                Nueva
              </button>
            </div>
            {connections.isError ? (
              <ErrorBox message={queryErrorMessage(connections.error, "No se pudieron cargar las conexiones.")} onRetry={() => connections.refetch()} />
            ) : connections.isFetching ? (
              <SkeletonRows />
            ) : rows.length ? (
              <div className="overflow-x-auto rounded-lg border">
                <table className="w-full text-sm">
                  <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2">Conn ID</th>
                      <th className="px-3 py-2">Base URL</th>
                      <th className="px-3 py-2">Auth</th>
                      <th className="px-3 py-2">Secreto</th>
                      <th className="px-3 py-2">Acciones</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((conn, index) => {
                      const id = connectionId(conn) || `conn-${index}`;
                      const revealed = revealedTokens[id];
                      return (
                        <tr key={id} className="border-t">
                          <td className="px-3 py-2 font-mono text-xs">{id}</td>
                          <td className="max-w-xs truncate px-3 py-2 font-mono text-xs text-muted-foreground">{String(conn.base_url ?? "—")}</td>
                          <td className="px-3 py-2">{String(conn.auth_method ?? conn.kind ?? "—")}</td>
                          <td className="max-w-[220px] truncate px-3 py-2 font-mono text-xs">{revealed || maskedConnectionSecret(conn)}</td>
                          <td className="px-3 py-2">
                            <div className="flex flex-wrap gap-1">
                              <IconButton label={revealed ? "Ocultar secreto" : "Revelar secreto"} onClick={() => revealToken(id)} icon={revealed ? EyeOff : Eye} />
                              <button type="button" onClick={() => editConnection(conn)} className="min-h-[34px] rounded-md border px-2 text-xs">Editar</button>
                              {deleteConnId === id ? (
                                <button type="button" onClick={() => confirmDeleteConnection(id)} className="min-h-[34px] rounded-md bg-destructive px-2 text-xs text-destructive-foreground">Confirmar</button>
                              ) : (
                                <IconButton label="Eliminar conexión" onClick={() => setDeleteConnId(id)} icon={Trash2} danger />
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">Sin conexiones para este cartucho.</p>
            )}
          </section>
          <ConnectionForm
            cartridge={cartridge}
            authMethodValues={connectorSchema.data?.authMethodValues}
            form={connForm}
            editingId={editingConnId}
            setForm={setConnForm}
            onSave={saveConnectionForm}
            onCancel={resetConnectionForm}
            saving={saveConnection.isPending}
          />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
          <section className="space-y-3 rounded-lg border bg-card p-4">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-base font-semibold">Secrets de {activeScope || "scope"}</h2>
              <button type="button" onClick={resetSecretForm} className="inline-flex min-h-[40px] items-center gap-2 rounded-md border px-3 text-xs font-medium">
                <Plus aria-hidden className="h-4 w-4" />
                Nuevo
              </button>
            </div>
            {secrets.isError ? (
              <ErrorBox message={queryErrorMessage(secrets.error, "No se pudieron cargar los secrets.")} onRetry={() => secrets.refetch()} />
            ) : secrets.isFetching ? (
              <SkeletonRows />
            ) : secretRows.length ? (
              <div className="overflow-x-auto rounded-lg border">
                <table className="w-full text-sm">
                  <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2">Key</th>
                      <th className="px-3 py-2">Valor</th>
                      <th className="px-3 py-2">Acciones</th>
                    </tr>
                  </thead>
                  <tbody>
                    {secretRows.map((secret, index) => {
                      const key = secretKey(secret) || `secret-${index}`;
                      const revealed = revealedSecrets[key];
                      return (
                        <tr key={key} className="border-t">
                          <td className="px-3 py-2 font-mono text-xs">{key}</td>
                          <td className="px-3 py-2 font-mono text-xs">{revealed || String(secret.masked ?? secret.value ?? "••••••••••")}</td>
                          <td className="px-3 py-2">
                            <div className="flex flex-wrap gap-1">
                              <IconButton label={revealed ? "Ocultar secret" : "Revelar secret"} onClick={() => revealSecretValue(key)} icon={revealed ? EyeOff : Eye} />
                              <button type="button" onClick={() => editSecret(secret)} className="min-h-[34px] rounded-md border px-2 text-xs">Editar</button>
                              {deleteSecretKey === key ? (
                                <button type="button" onClick={() => confirmDeleteSecret(key)} className="min-h-[34px] rounded-md bg-destructive px-2 text-xs text-destructive-foreground">Confirmar</button>
                              ) : (
                                <IconButton label="Eliminar secret" onClick={() => setDeleteSecretKey(key)} icon={Trash2} danger />
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">Sin secrets para este scope.</p>
            )}
          </section>
          <SecretFormPanel
            form={secretForm}
            editingKey={editingSecretKey}
            setForm={setSecretForm}
            onSave={saveSecretForm}
            onCancel={resetSecretForm}
            saving={saveSecret.isPending}
            scope={activeScope}
          />
        </div>
      )}
    </div>
  );
}

export function ConnectionForm({
  cartridge,
  authMethodValues,
  form,
  editingId,
  setForm,
  onSave,
  onCancel,
  saving,
}: {
  cartridge: string;
  authMethodValues?: VaultAuthMethod[];
  form: ConnForm;
  editingId: string | null;
  setForm: (form: ConnForm) => void;
  onSave: () => void;
  onCancel: () => void;
  saving: boolean;
}) {
  const authOptions = authOptionsWithCurrent(getAuthMethodOptions(cartridge, authMethodValues), form.authMethod);
  const isSamlBearer = form.authMethod === "saml_bearer_assertion";
  const isClientCredentials = form.authMethod === "oauth2_client_credentials";
  const usesGenericToken = !isSamlBearer && !isClientCredentials;

  return (
    <aside className="space-y-3 rounded-lg border bg-card p-4">
      <h2 className="text-base font-semibold">{editingId ? `Editar ${editingId}` : "Nueva conexión"}</h2>
      <Field label="Conn ID">
        <input value={form.connId} readOnly={Boolean(editingId)} onChange={(event) => setForm({ ...form, connId: event.target.value })} className="min-h-[44px] rounded-md border bg-background px-3 font-mono text-sm read-only:bg-muted/40" />
      </Field>
      <Field label="Base URL">
        <input value={form.baseUrl} onChange={(event) => setForm({ ...form, baseUrl: event.target.value })} className="min-h-[44px] rounded-md border bg-background px-3 text-sm" placeholder="https://tenant/api" />
      </Field>
      <Field label="Auth method">
        <select value={form.authMethod} onChange={(event) => setForm({ ...form, authMethod: event.target.value })} className="min-h-[44px] rounded-md border bg-background px-3 text-sm">
          {authOptions.map((method) => (
            <option key={method} value={method}>{method}</option>
          ))}
        </select>
      </Field>
      {(isSamlBearer || isClientCredentials) ? (
        <>
          <Field label="Client ID">
            <input value={form.clientId} onChange={(event) => setForm({ ...form, clientId: event.target.value })} className="min-h-[44px] rounded-md border bg-background px-3 text-sm" placeholder="OAuth client_id" />
          </Field>
          {isClientCredentials ? (
            <Field label="Client secret">
              <input type="password" value={form.clientSecret} onChange={(event) => setForm({ ...form, clientSecret: event.target.value })} className="min-h-[44px] rounded-md border bg-background px-3 text-sm" autoComplete="off" />
            </Field>
          ) : null}
          <Field label="Token URL">
            <input value={form.tokenUrl} onChange={(event) => setForm({ ...form, tokenUrl: event.target.value })} className="min-h-[44px] rounded-md border bg-background px-3 text-sm" placeholder="https://api.successfactors.com/oauth/token" />
          </Field>
          <Field label="Company ID">
            <input value={form.companyId} onChange={(event) => setForm({ ...form, companyId: event.target.value })} className="min-h-[44px] rounded-md border bg-background px-3 text-sm" />
          </Field>
        </>
      ) : null}
      {isSamlBearer ? (
        <>
          <Field label="Admin user">
            <input value={form.adminUser} onChange={(event) => setForm({ ...form, adminUser: event.target.value })} className="min-h-[44px] rounded-md border bg-background px-3 text-sm" placeholder="sf-admin@empresa.com" />
          </Field>
          <Field label="Private key PEM">
            <textarea
              value={form.privateKeyPem}
              onChange={(event) => setForm({ ...form, privateKeyPem: event.target.value })}
              className="min-h-52 rounded-md border bg-background px-3 py-2 font-mono text-xs"
              placeholder={"-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"}
              autoComplete="off"
              spellCheck={false}
            />
          </Field>
          <Field label="IDP URL">
            <input value={form.idpUrl} onChange={(event) => setForm({ ...form, idpUrl: event.target.value })} className="min-h-[44px] rounded-md border bg-background px-3 text-sm" placeholder="Vacío = derivado de token_url como /oauth/idp" />
          </Field>
        </>
      ) : null}
      {usesGenericToken ? (
        <Field label="Token / password">
          <input type="password" value={form.token} onChange={(event) => setForm({ ...form, token: event.target.value })} className="min-h-[44px] rounded-md border bg-background px-3 text-sm" autoComplete="off" />
        </Field>
      ) : null}
      <Field label="Campos extra JSON">
        <textarea value={form.extraJson} onChange={(event) => setForm({ ...form, extraJson: event.target.value })} className="min-h-28 rounded-md border bg-background px-3 py-2 font-mono text-xs" placeholder='{"username":"user@company.com"}' />
      </Field>
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={onSave} disabled={saving} className="inline-flex min-h-[40px] items-center gap-2 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground disabled:opacity-60">
          <Save aria-hidden className="h-4 w-4" />
          Guardar
        </button>
        <button type="button" onClick={onCancel} className="inline-flex min-h-[40px] items-center gap-2 rounded-md border px-3 text-xs font-medium">
          <X aria-hidden className="h-4 w-4" />
          Limpiar
        </button>
      </div>
    </aside>
  );
}

function SecretFormPanel({
  form,
  editingKey,
  setForm,
  onSave,
  onCancel,
  saving,
  scope,
}: {
  form: SecretForm;
  editingKey: string | null;
  setForm: (form: SecretForm) => void;
  onSave: () => void;
  onCancel: () => void;
  saving: boolean;
  scope: string;
}) {
  return (
    <aside className="space-y-3 rounded-lg border bg-card p-4">
      <h2 className="text-base font-semibold">{editingKey ? `Editar ${editingKey}` : "Nuevo secret"}</h2>
      <p className="rounded-md border bg-muted/30 p-3 text-xs text-muted-foreground">Scope activo: <span className="font-mono">{scope || "—"}</span></p>
      <Field label="Key">
        <input value={form.key} readOnly={Boolean(editingKey)} onChange={(event) => setForm({ ...form, key: event.target.value })} className="min-h-[44px] rounded-md border bg-background px-3 font-mono text-sm read-only:bg-muted/40" />
      </Field>
      <Field label="Valor">
        <input type="password" value={form.value} onChange={(event) => setForm({ ...form, value: event.target.value })} className="min-h-[44px] rounded-md border bg-background px-3 text-sm" />
      </Field>
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={onSave} disabled={saving} className="inline-flex min-h-[40px] items-center gap-2 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground disabled:opacity-60">
          <Save aria-hidden className="h-4 w-4" />
          Guardar
        </button>
        <button type="button" onClick={onCancel} className="inline-flex min-h-[40px] items-center gap-2 rounded-md border px-3 text-xs font-medium">
          <X aria-hidden className="h-4 w-4" />
          Limpiar
        </button>
      </div>
    </aside>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5 text-sm">
      <span className="font-medium">{label}</span>
      {children}
    </label>
  );
}

function IconButton({
  label,
  icon: Icon,
  onClick,
  danger = false,
}: {
  label: string;
  icon: LucideIcon;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      title={label}
      className={cn(
        "inline-flex min-h-[34px] min-w-[34px] items-center justify-center rounded-md border",
        danger && "border-destructive/40 text-destructive hover:bg-destructive/10",
      )}
    >
      <Icon aria-hidden className="h-4 w-4" />
    </button>
  );
}

function ErrorBox({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
      <p className="font-medium text-destructive">{message}</p>
      <button type="button" onClick={onRetry} className="mt-2 min-h-[40px] rounded-md border px-3 text-xs font-medium">Reintentar</button>
    </div>
  );
}

function queryErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.trim()) {
    return `${fallback} ${error.message.trim()}`;
  }
  return fallback;
}

function SkeletonRows() {
  return (
    <div aria-busy="true" className="space-y-2">
      {Array.from({ length: 4 }).map((_, index) => (
        <span key={index} className="block h-14 animate-pulse rounded bg-muted" aria-hidden />
      ))}
    </div>
  );
}

function tabButton(active: boolean): string {
  return cn(
    "min-h-[40px] rounded-md border px-4 text-sm font-medium",
    active ? "border-primary bg-primary text-primary-foreground" : "bg-background hover:bg-accent/5",
  );
}

export function getAuthMethodOptions(cartridge: string, authMethodValues?: VaultAuthMethod[]): VaultAuthMethod[] {
  const declared = (authMethodValues ?? []).filter((method) => typeof method === "string" && method.trim());
  if (declared.length) return declared;
  return CONNECTOR_AUTH_METHOD_VALUES[cartridge] ?? FALLBACK_AUTH_METHODS;
}

function authOptionsWithCurrent(options: VaultAuthMethod[], current: VaultAuthMethod): VaultAuthMethod[] {
  return options.includes(current) ? options : [current, ...options];
}

export function buildVaultConnectionPayload(form: ConnForm): VaultConnectionPayload | null {
  const extra = parseExtra(form.extraJson);
  if (extra === null) return null;
  const payload: VaultConnectionPayload = {
    ...extra,
    base_url: form.baseUrl.trim(),
    auth_method: form.authMethod,
  };
  const putIfPresent = (key: string, value: string) => {
    const trimmed = value.trim();
    if (trimmed) payload[key] = trimmed;
  };

  if (form.authMethod === "oauth2_client_credentials") {
    putIfPresent("client_id", form.clientId);
    putIfPresent("client_secret", form.clientSecret);
    putIfPresent("token_url", form.tokenUrl);
    putIfPresent("company_id", form.companyId);
    return payload;
  }

  if (form.authMethod === "saml_bearer_assertion") {
    putIfPresent("client_id", form.clientId);
    putIfPresent("token_url", form.tokenUrl);
    putIfPresent("company_id", form.companyId);
    putIfPresent("admin_user", form.adminUser);
    putIfPresent("private_key_pem", form.privateKeyPem);
    putIfPresent("idp_url", form.idpUrl);
    return payload;
  }

  putIfPresent("token", form.token);
  return payload;
}

function connectionId(conn: VaultConnection): string {
  return String(conn.conn_id || conn.id || "");
}

function secretKey(secret: VaultSecret): string {
  return String(secret.key || secret.name || "");
}

function omitKeys(source: Record<string, unknown>, keys: string[]): Record<string, unknown> {
  const blocked = new Set(keys);
  return Object.fromEntries(Object.entries(source).filter(([key]) => !blocked.has(key)));
}

function connectionAuthMethod(conn: VaultConnection): string {
  return String(conn.auth_method ?? conn.kind ?? "");
}

function maskedConnectionSecret(conn: VaultConnection): string {
  return connectionAuthMethod(conn) === "saml_bearer_assertion" ? "•••• PEM ••••" : "••••••••••";
}

function revealedConnectionSecret(conn: VaultConnection): string {
  if (connectionAuthMethod(conn) === "saml_bearer_assertion") {
    return String(conn.private_key_pem ?? conn.private_key_path ?? "•••• PEM ••••");
  }
  return String(conn.token ?? conn.access_token ?? conn.client_secret ?? "••••••••••");
}

function parseExtra(value: string): Record<string, unknown> | null {
  const trimmed = value.trim();
  if (!trimmed) return {};
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (typeof parsed === "object" && parsed !== null && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>;
    }
    toast.error("El JSON extra debe ser un objeto.");
    return null;
  } catch {
    toast.error("JSON extra inválido.");
    return null;
  }
}
