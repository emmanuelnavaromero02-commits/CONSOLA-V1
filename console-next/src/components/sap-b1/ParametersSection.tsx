"use client";

import { Download, Plus, Trash2, Upload } from "lucide-react";
import { useMemo, useState, type ChangeEvent, type FormEvent } from "react";
import { toast } from "sonner";

import { isApiError } from "@/lib/api";
import { downloadText, FINANCE_RUN_MAX_BYTES, financeRunTemplate, utf8Bytes } from "@/lib/sap-b1/csv";
import {
  useAddSapB1Recipient,
  useRemoveSapB1Recipient,
  useSapB1BusinessParameters,
  useSapB1Recipients,
  useSaveSapB1BusinessParameters,
  useUploadSapB1FinanceRun,
} from "@/lib/sap-b1/hooks";
import {
  ACCOUNT_KEYS,
  ANY,
  buildParametersText,
  entryText,
  formFromParameters,
  validateForm,
  type AccountKey,
  type ParametersForm,
} from "@/lib/sap-b1/parameters";
import { countLabel, formatCount, TRANSPORT_LABELS } from "@/lib/sap-b1/present";
import type { SapB1BusinessParameters, SapB1CatalogEntry, SapB1FinanceRunResult } from "@/lib/sap-b1/types";
import { cn } from "@/lib/utils";

import { ActionButton, errorText, INPUT, LoadingBlock, Notice, Panel, QueryError } from "./ui";

const CASE_LABELS: Record<string, string> = { finanzas: "Finanzas", ventas: "Ventas", compras: "Compras", general: "General" };
const CASE_ORDER = ["finanzas", "ventas", "compras", "general"];
const ACCOUNT_LABELS: Record<AccountKey, string> = { revenue: "Ingresos", cogs: "Costo de ventas" };

function validationText(error: unknown, prefix: string): string {
  if (isApiError(error) && (error.status === 422 || error.status === 409)) return `${prefix}: ${error.message}`;
  return errorText(error);
}

function CatalogField({
  item,
  value,
  onChange,
}: {
  item: SapB1CatalogEntry;
  value: string;
  onChange: (value: string) => void;
}) {
  const id = `param-${item.key}`;
  return (
    <div className="rounded-md border bg-background p-3 dark:border-sky-400/15 dark:bg-[#06111f]">
      <label htmlFor={id} className="block text-sm font-medium text-foreground dark:text-white">{item.label}</label>
      <div className="mt-1 flex items-center gap-2">
        <input
          id={id}
          value={value}
          inputMode="decimal"
          onChange={(event) => onChange(event.target.value)}
          placeholder={item.default != null ? item.default : "obligatorio"}
          className={cn(INPUT, "max-w-[10rem]")}
        />
        <span className="text-xs text-muted-foreground">{item.unit}</span>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        <span className="font-mono">{item.key}</span> ·{" "}
        {item.default != null ? `predeterminado ${item.default} ${item.unit}` : "sin predeterminado: hay que definirlo"}
      </p>
    </div>
  );
}

function ParametersFormEditor({
  data,
  saving,
  onSubmit,
}: {
  data: SapB1BusinessParameters;
  saving: boolean;
  onSubmit: (text: string) => void;
}) {
  const catalog = data.catalog;
  const [form, setForm] = useState<ParametersForm>(() => formFromParameters(data.parameters, catalog));
  const [mode, setMode] = useState<"form" | "text">("form");
  const [rawText, setRawText] = useState(data.text);
  const errors = useMemo(() => validateForm(form, catalog), [form, catalog]);
  const text = useMemo(() => buildParametersText(form, catalog), [form, catalog]);
  const groups = CASE_ORDER.map((id) => ({ id, items: catalog.filter((item) => (item.case || "general") === id) })).filter((group) => group.items.length);

  const updateBranch = (index: number, patch: Partial<ParametersForm["branches"][number]>) =>
    setForm((current) => ({ ...current, branches: current.branches.map((row, i) => (i === index ? { ...row, ...patch } : row)) }));
  const updateAccount = (index: number, patch: Partial<ParametersForm["accounts"][number]>) =>
    setForm((current) => ({ ...current, accounts: current.accounts.map((row, i) => (i === index ? { ...row, ...patch } : row)) }));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2" role="group" aria-label="Forma de edición">
        {(["form", "text"] as const).map((item) => (
          <button
            key={item}
            type="button"
            aria-pressed={mode === item}
            onClick={() => setMode(item)}
            className={cn(
              "min-h-[32px] rounded-full border px-3 text-xs font-semibold",
              mode === item ? "border-primary bg-primary text-primary-foreground" : "bg-background text-foreground hover:bg-muted dark:border-sky-400/20 dark:bg-[#06111f]",
            )}
          >
            {item === "form" ? "Formulario" : "Texto"}
          </button>
        ))}
      </div>

      {mode === "form" ? (
        <form
          className="space-y-5"
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            if (!errors.length) onSubmit(text);
          }}
        >
          {groups.map((group) => (
            <fieldset key={group.id} className="space-y-2">
              <legend className="mb-2 text-sm font-semibold text-foreground dark:text-white">{CASE_LABELS[group.id] ?? group.id}</legend>
              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                {group.items.map((item) => (
                  <CatalogField
                    key={item.key}
                    item={item}
                    value={form.values[item.key] ?? ""}
                    onChange={(value) => setForm((current) => ({ ...current, values: { ...current.values, [item.key]: value } }))}
                  />
                ))}
              </div>
            </fieldset>
          ))}

          <fieldset className="space-y-2">
            <legend className="mb-1 text-sm font-semibold text-foreground dark:text-white">Filiales (almacén → filial)</legend>
            <p className="text-xs text-muted-foreground">Cada almacén de Business One se agrupa en la filial que lo opera, por empresa.</p>
            {form.branches.map((row, index) => (
              <div key={`branch-${index}`} className="grid gap-2 sm:grid-cols-[8rem_8rem_minmax(0,1fr)_auto]">
                <input aria-label={`Empresa de la filial ${index + 1}`} placeholder="empresa" value={row.company} onChange={(event) => updateBranch(index, { company: event.target.value })} className={INPUT} />
                <input aria-label={`Código de almacén ${index + 1}`} placeholder="almacén" value={row.warehouse} onChange={(event) => updateBranch(index, { warehouse: event.target.value })} className={INPUT} />
                <input aria-label={`Nombre de la filial ${index + 1}`} placeholder="nombre de la filial" value={row.name} onChange={(event) => updateBranch(index, { name: event.target.value })} className={INPUT} />
                <ActionButton variant="danger" ariaLabel={`Quitar filial ${index + 1}`} onClick={() => setForm((current) => ({ ...current, branches: current.branches.filter((_, i) => i !== index) }))}>
                  <Trash2 aria-hidden className="h-4 w-4" />
                </ActionButton>
              </div>
            ))}
            <ActionButton onClick={() => setForm((current) => ({ ...current, branches: [...current.branches, { company: "", warehouse: "", name: "" }] }))}>
              <Plus aria-hidden className="h-4 w-4" />
              Agregar filial
            </ActionButton>
          </fieldset>

          <fieldset className="space-y-2">
            <legend className="mb-1 text-sm font-semibold text-foreground dark:text-white">Cuentas contables</legend>
            <p className="text-xs text-muted-foreground">Códigos separados por coma; un * al final toma todas las cuentas con ese prefijo. Empresa * aplica a todas.</p>
            {form.accounts.map((row, index) => (
              <div key={`account-${index}`} className="grid gap-2 sm:grid-cols-[8rem_10rem_minmax(0,1fr)_auto]">
                <input aria-label={`Empresa de la lista de cuentas ${index + 1}`} placeholder="empresa o *" value={row.company} onChange={(event) => updateAccount(index, { company: event.target.value })} className={INPUT} />
                <select aria-label={`Tipo de cuentas ${index + 1}`} value={row.key} onChange={(event) => updateAccount(index, { key: event.target.value as AccountKey })} className={INPUT}>
                  {ACCOUNT_KEYS.map((key) => <option key={key} value={key}>{ACCOUNT_LABELS[key]}</option>)}
                </select>
                <input aria-label={`Códigos de cuenta ${index + 1}`} placeholder="código, prefijo*" value={row.codes} onChange={(event) => updateAccount(index, { codes: event.target.value })} className={INPUT} />
                <ActionButton variant="danger" ariaLabel={`Quitar lista de cuentas ${index + 1}`} onClick={() => setForm((current) => ({ ...current, accounts: current.accounts.filter((_, i) => i !== index) }))}>
                  <Trash2 aria-hidden className="h-4 w-4" />
                </ActionButton>
              </div>
            ))}
            <ActionButton onClick={() => setForm((current) => ({ ...current, accounts: [...current.accounts, { company: ANY, key: "revenue", codes: "" }] }))}>
              <Plus aria-hidden className="h-4 w-4" />
              Agregar lista de cuentas
            </ActionButton>
          </fieldset>

          {form.extra.length ? (
            <div className="space-y-1">
              <h3 className="text-sm font-semibold text-foreground dark:text-white">Otras entradas</h3>
              <p className="text-xs text-muted-foreground">Umbrales por empresa o por mes; se conservan tal cual y se editan en la vista de texto.</p>
              <ul className="space-y-0.5 font-mono text-xs">
                {form.extra.map((item) => <li key={entryText(item)} className="break-all">{entryText(item)}</li>)}
              </ul>
            </div>
          ) : null}

          {errors.length ? (
            <Notice tone="warning" title="Corrige antes de guardar">
              <ul className="list-disc pl-4">
                {errors.map((error, index) => <li key={`${index}:${error}`}>{error}</li>)}
              </ul>
            </Notice>
          ) : null}

          <details className="text-xs">
            <summary className="cursor-pointer font-medium text-foreground dark:text-white">Texto que se guardará</summary>
            <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded-md border bg-muted/30 p-2 font-mono dark:border-sky-400/15">{text || "(vacío)"}</pre>
          </details>
          <ActionButton type="submit" variant="primary" busy={saving} disabled={errors.length > 0}>
            Guardar parámetros
          </ActionButton>
        </form>
      ) : (
        <form
          className="space-y-2"
          onSubmit={(event: FormEvent) => {
            event.preventDefault();
            onSubmit(rawText);
          }}
        >
          <label htmlFor="sap-b1-parameters-text" className="block text-sm font-medium text-foreground dark:text-white">
            Una entrada por línea: <span className="font-mono">tipo:empresa:periodo:clave=valor</span>
          </label>
          <p className="text-xs text-muted-foreground">
            Tipos: account, threshold, setting, branch. Empresa: alias en minúsculas o *. Periodo: AAAA-MM o *. Las líneas que empiezan con # se ignoran.
          </p>
          <textarea
            id="sap-b1-parameters-text"
            value={rawText}
            onChange={(event) => setRawText(event.target.value)}
            rows={14}
            spellCheck={false}
            className={cn(INPUT, "font-mono text-xs")}
          />
          <ActionButton type="submit" variant="primary" busy={saving}>
            Guardar texto
          </ActionButton>
        </form>
      )}
    </div>
  );
}

function BusinessParametersPanel() {
  const query = useSapB1BusinessParameters();
  const save = useSaveSapB1BusinessParameters();
  const [serverError, setServerError] = useState<string | null>(null);
  const [refreshWarning, setRefreshWarning] = useState<string | null>(null);
  const data = query.data;

  const submit = (payload: string) => {
    setServerError(null);
    setRefreshWarning(null);
    save.mutate(payload, {
      onSuccess: (result) => {
        toast.success(`Parámetros guardados: ${formatCount(result.count)} entradas.`);
        if (!result.refreshed) setRefreshWarning(result.refresh_error ?? "No se pudo actualizar la copia en la plataforma.");
      },
      onError: (error) => setServerError(validationText(error, "El texto no pasó la validación")),
    });
  };
  return (
    <Panel
      eyebrow="Parámetros"
      title="Parámetros de negocio"
      description="Umbrales, ajustes, filiales y cuentas que usan los indicadores. Se guardan en Vault y se copian a la plataforma al guardar."
    >
      {query.isPending ? <LoadingBlock label="Leyendo los parámetros…" /> : null}
      {query.isError ? <QueryError error={query.error} onRetry={() => void query.refetch()} /> : null}
      {data ? (
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            {formatCount(data.keys_set)} de {formatCount(data.keys_total)} parámetros definidos · {countLabel(data.branches, "filial", "filiales")} ·{" "}
            {countLabel(data.accounts, "lista de cuentas", "listas de cuentas")}
            {data.missing.length ? <> · <strong className="text-amber-700 dark:text-amber-300">faltan {data.missing.join(", ")}</strong></> : null}
          </p>
          {serverError ? <Notice tone="error" title="No se guardó">{serverError}</Notice> : null}
          {refreshWarning ? <Notice tone="warning" title="Guardado en Vault, sin actualizar la plataforma">{refreshWarning}</Notice> : null}
          <ParametersFormEditor key={data.text} data={data} saving={save.isPending} onSubmit={submit} />
        </div>
      ) : null}
    </Panel>
  );
}

function FinanceRunPanel() {
  const upload = useUploadSapB1FinanceRun();
  const [csv, setCsv] = useState("");
  const [fileName, setFileName] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<SapB1FinanceRunResult | null>(null);

  const onFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setError(null);
    if (file.size > FINANCE_RUN_MAX_BYTES) {
      setError("El archivo supera 5 MB.");
      return;
    }
    setFileName(file.name);
    setCsv(await file.text());
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setResult(null);
    if (!csv.trim()) {
      setError("Pega la corrida o elige un archivo .csv.");
      return;
    }
    if (utf8Bytes(csv) > FINANCE_RUN_MAX_BYTES) {
      setError("La corrida supera 5 MB.");
      return;
    }
    upload.mutate(csv, {
      onSuccess: (data) => {
        setResult(data);
        toast.success(`Corrida de Finanzas cargada: ${formatCount(data.rows)} filas.`);
      },
      onError: (failure) => setError(validationText(failure, "La corrida no pasó la validación")),
    });
  };

  return (
    <Panel
      eyebrow="Finanzas"
      title="Corrida manual de Finanzas"
      description="La corrida que Finanzas calcula por su cuenta, para reconciliarla contra la plataforma. Por empresa y mes cuenta la carga más reciente: volver a subir un mes lo reemplaza."
      actions={
        <ActionButton onClick={() => downloadText("sap_b1_corrida_finanzas_plantilla.csv", financeRunTemplate())}>
          <Download aria-hidden className="h-4 w-4" />
          Descargar plantilla
        </ActionButton>
      }
    >
      <form className="space-y-3" onSubmit={submit}>
        <details className="rounded-md border p-3 text-xs dark:border-sky-400/15">
          <summary className="cursor-pointer font-medium text-foreground dark:text-white">Formato del archivo</summary>
          <ul className="mt-2 list-disc space-y-1 pl-4 text-muted-foreground">
            <li>Encabezado: <span className="font-mono">indicador,empresa,mes,dimension,clave,valor</span> y, opcionalmente, <span className="font-mono">unidad</span>.</li>
            <li>indicador: margen_bruto, margen_contribucion, destructores, concentracion_top20 o margen_vendedor.</li>
            <li>empresa: el alias de la empresa en minúsculas, o grupo (el grupo solo va en la dimensión total).</li>
            <li>mes: AAAA-MM.</li>
            <li>dimension: total, cliente, sku, canal o vendedor; margen_vendedor solo admite vendedor, destructores solo cliente y concentracion_top20 total o cliente.</li>
            <li>clave: obligatoria salvo en la dimensión total (hasta 100 caracteres).</li>
            <li>valor: un número; se aceptan $, % y comas de miles.</li>
            <li>unidad: monto o pct; si falta, monto (pct en concentracion_top20).</li>
            <li>Sin filas repetidas para el mismo indicador, empresa, mes, dimensión y clave; hasta 100 000 filas y 5 MB. Las líneas que empiezan con # se ignoran.</li>
          </ul>
        </details>
        <div className="flex flex-wrap items-center gap-2">
          <label className="inline-flex min-h-[36px] cursor-pointer items-center gap-1.5 rounded-md border bg-background px-3 py-1.5 text-sm font-medium hover:bg-muted dark:border-sky-400/20 dark:bg-[#06111f]">
            <Upload aria-hidden className="h-4 w-4" />
            Elegir archivo .csv
            <input type="file" accept=".csv,text/csv" className="sr-only" onChange={(event) => void onFile(event)} />
          </label>
          {fileName ? <span className="text-xs text-muted-foreground">{fileName}</span> : null}
        </div>
        <textarea
          aria-label="Corrida de Finanzas en CSV"
          value={csv}
          onChange={(event) => setCsv(event.target.value)}
          rows={8}
          spellCheck={false}
          placeholder="indicador,empresa,mes,dimension,clave,valor,unidad"
          className={cn(INPUT, "font-mono text-xs")}
        />
        {error ? <Notice tone="error" title="No se cargó">{error}</Notice> : null}
        {result ? (
          <Notice tone="info" title={`Corrida cargada: ${formatCount(result.rows)} filas`}>
            <p>Indicadores: {(result.indicators ?? []).join(", ") || "N/D"}</p>
            <p>
              Empresas:{" "}
              {Object.entries(result.companies ?? {}).map(([company, months]) => `${company} (${months.join(", ")})`).join("; ") || "N/D"}
            </p>
          </Notice>
        ) : null}
        <ActionButton type="submit" variant="primary" busy={upload.isPending}>
          Cargar corrida
        </ActionButton>
      </form>
    </Panel>
  );
}

function RecipientsPanel() {
  const query = useSapB1Recipients();
  const add = useAddSapB1Recipient();
  const remove = useRemoveSapB1Recipient();
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const recipients = query.data?.recipients ?? [];
  const full = query.data ? recipients.length >= query.data.max : false;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    const value = email.trim();
    if (!value) return;
    add.mutate(value, {
      onSuccess: (result) => {
        setEmail("");
        toast.success(result.added ? `Se agregó ${result.email}.` : `${result.email} ya estaba en la lista.`);
      },
      onError: (failure) => {
        if (isApiError(failure) && failure.status === 422) setError("El correo no es válido.");
        else if (isApiError(failure) && failure.status === 409) setError(`Se alcanzó el máximo de ${query.data?.max ?? ""} destinatarios.`);
        else setError(errorText(failure));
      },
    });
  };

  const onRemove = (address: string) => {
    if (!window.confirm(`¿Quitar a ${address} del semáforo de las 8:00?`)) return;
    setError(null);
    remove.mutate(address, {
      onSuccess: () => toast.success(`Se quitó ${address}.`),
      onError: (failure) => setError(errorText(failure)),
    });
  };

  return (
    <Panel
      eyebrow="Semáforo"
      title="Destinatarios del semáforo"
      description="Quién recibe el correo de las 8:00 de este workspace."
    >
      {query.isPending ? <LoadingBlock label="Leyendo destinatarios…" /> : null}
      {query.isError ? <QueryError error={query.error} onRetry={() => void query.refetch()} /> : null}
      {query.data ? (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            {recipients.length} de {query.data.max} · {TRANSPORT_LABELS[query.data.transport] ?? query.data.transport}
          </p>
          {query.data.transport === "sin_configurar" ? (
            <Notice tone="warning" title="Transporte de correo sin configurar">Los destinatarios se guardan, pero el correo no saldrá hasta configurar SES o SMTP.</Notice>
          ) : null}
          {recipients.length ? (
            <ul className="divide-y rounded-md border dark:divide-sky-400/10 dark:border-sky-400/15">
              {recipients.map((address) => (
                <li key={address} className="flex items-center justify-between gap-2 px-3 py-2 text-sm">
                  <span className="min-w-0 break-all">{address}</span>
                  <ActionButton variant="danger" ariaLabel={`Quitar ${address}`} busy={remove.isPending && remove.variables === address} onClick={() => onRemove(address)}>
                    <Trash2 aria-hidden className="h-4 w-4" />
                    Quitar
                  </ActionButton>
                </li>
              ))}
            </ul>
          ) : (
            <Notice tone="empty" title="Sin destinatarios">Nadie recibirá el correo de las 8:00 hasta agregar al menos uno.</Notice>
          )}
          <form className="flex flex-col gap-2 sm:flex-row" onSubmit={submit}>
            <label htmlFor="sap-b1-recipient" className="sr-only">Correo del destinatario</label>
            <input
              id="sap-b1-recipient"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="correo@empresa.com"
              className={cn(INPUT, "sm:max-w-sm")}
              disabled={full}
            />
            <ActionButton type="submit" variant="primary" busy={add.isPending} disabled={full || !email.trim()}>
              <Plus aria-hidden className="h-4 w-4" />
              Agregar
            </ActionButton>
          </form>
          {full ? <p className="text-xs text-muted-foreground">Se alcanzó el máximo de destinatarios.</p> : null}
          {error ? <Notice tone="error" title="No se guardó">{error}</Notice> : null}
        </div>
      ) : null}
    </Panel>
  );
}

export function ParametersSection({ canWrite }: { canWrite: boolean }) {
  if (!canWrite) {
    return <Notice tone="warning" title="Solo lectura">Tu rol no puede cambiar los parámetros de SAP Business One.</Notice>;
  }
  return (
    <div className="space-y-4">
      <BusinessParametersPanel />
      <FinanceRunPanel />
      <RecipientsPanel />
    </div>
  );
}
