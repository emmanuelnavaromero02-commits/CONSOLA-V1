"use client";

import { useEffect, useMemo, useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Eye, EyeOff } from "lucide-react";
import { toast } from "sonner";

import type { ConnectorField, ConnectorSchema, TestConnectionResult as Result } from "@/lib/cartridges";
import {
  useSaveCredentials,
  useTestConnection,
  useDeleteCredentials,
} from "@/lib/hooks/useCartridges";
import { TestConnectionResult } from "./TestConnectionResult";

interface Props {
  cartridgeId: string;
  schema:      ConnectorSchema;
}

/**
 * Renders a Zod-validated react-hook-form built dynamically from the
 * cartridge's connector_schema. The form layout is deliberately
 * conservative (one field per row, no fancy grid) because every
 * cartridge supplies a different field set and we want the markup to
 * stay legible at all widths.
 *
 * Three terminal actions:
 *   * Save credentials  → POST /api/cartridges/{id}/credentials
 *   * Test connection   → POST /api/cartridges/{id}/test_connection
 *   * Delete            → AlertDialog-confirmed → DELETE
 */
export function CredentialsForm({ cartridgeId, schema }: Props) {
  const zodSchema = useMemo(() => buildZodSchema(schema.fields), [schema.fields]);
  type FormValues = z.infer<typeof zodSchema>;

  const form = useForm<FormValues>({
    resolver: zodResolver(zodSchema),
    defaultValues: defaultsFor(schema.fields),
    mode: "onBlur",
  });

  const saveMut   = useSaveCredentials(cartridgeId);
  const testMut   = useTestConnection(cartridgeId);
  const deleteMut = useDeleteCredentials(cartridgeId);

  const [lastTest, setLastTest] = useState<Result | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [shownPasswords, setShownPasswords] = useState<Record<string, boolean>>({});

  const onSubmit = async (values: FormValues) => {
    try {
      await saveMut.mutateAsync(values as Record<string, string | number | boolean>);
      toast.success("Credenciales guardadas en el Vault.");
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error desconocido";
      toast.error(`No se pudo guardar: ${message}`);
    }
  };

  const onTest = async () => {
    try {
      const r = await testMut.mutateAsync();
      setLastTest(r);
      if (r.ok) toast.success("Conexión OK.");
      else      toast.error("Conexión fallida — ver detalle.");
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error desconocido";
      setLastTest({ ok: false, message, latency_ms: 0 });
      toast.error(`No se pudo probar: ${message}`);
    }
  };

  const onDelete = async () => {
    try {
      await deleteMut.mutateAsync();
      toast.success("Credenciales borradas.");
      form.reset(defaultsFor(schema.fields));
      setLastTest(null);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : "Error desconocido";
      toast.error(`No se pudieron borrar: ${message}`);
    } finally {
      setConfirmingDelete(false);
    }
  };

  return (
    <div className="space-y-6">
      <form
        onSubmit={form.handleSubmit(onSubmit)}
        noValidate
        className="space-y-4 rounded-lg border bg-card p-6 shadow-sm"
      >
        {schema.fields.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Este cartucho no expone un schema de configuración.
          </p>
        ) : (
          schema.fields.map((field) => (
            <Field
              key={field.name}
              field={field}
              register={form.register}
              error={form.formState.errors[field.name]?.message as string | undefined}
              isPasswordShown={shownPasswords[field.name] === true}
              onTogglePassword={() =>
                setShownPasswords((prev) => ({
                  ...prev,
                  [field.name]: !prev[field.name],
                }))
              }
            />
          ))
        )}

        <div className="flex flex-wrap items-center gap-2 pt-2">
          <button
            type="submit"
            disabled={saveMut.isPending}
            className="inline-flex h-9 items-center justify-center gap-1.5 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:pointer-events-none disabled:opacity-60"
          >
            <span aria-hidden>💾</span>
            {saveMut.isPending ? "Guardando…" : "Guardar credenciales"}
          </button>

          <button
            type="button"
            onClick={onTest}
            disabled={testMut.isPending}
            className="inline-flex h-9 items-center justify-center gap-1.5 rounded-md border bg-background px-4 text-sm font-medium transition-colors hover:bg-accent/5 disabled:pointer-events-none disabled:opacity-60"
          >
            <span aria-hidden>🔌</span>
            {testMut.isPending ? "Probando…" : "Probar conexión"}
          </button>

          <button
            type="button"
            onClick={() => setConfirmingDelete(true)}
            disabled={deleteMut.isPending}
            className="ml-auto inline-flex h-9 items-center justify-center gap-1.5 rounded-md border border-destructive/40 bg-background px-4 text-sm font-medium text-destructive transition-colors hover:bg-destructive/5 disabled:pointer-events-none disabled:opacity-60"
          >
            <span aria-hidden>🗑️</span>
            Borrar credenciales
          </button>
        </div>
      </form>

      <TestConnectionResult result={lastTest} />

      {confirmingDelete ? (
        <ConfirmDeleteDialog
          onCancel={() => setConfirmingDelete(false)}
          onConfirm={onDelete}
          pending={deleteMut.isPending}
        />
      ) : null}
    </div>
  );
}

// ── Inner field renderer ──────────────────────────────────────────────

interface FieldProps {
  field:    ConnectorField;
  register: ReturnType<typeof useForm>["register"];
  error?:   string;
  isPasswordShown: boolean;
  onTogglePassword: () => void;
}

function Field({ field, register, error, isPasswordShown, onTogglePassword }: FieldProps) {
  const label = field.label ?? field.name;
  const id = `field-${field.name}`;
  const baseClass =
    "w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-60";

  const common = {
    id,
    ...register(field.name),
    "aria-invalid": error ? "true" as const : undefined,
    "aria-describedby": error ? `${id}-err` : undefined,
  };

  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
        {field.required ? <span className="text-destructive"> *</span> : null}
      </label>
      {field.description ? (
        <p className="text-xs text-muted-foreground">{field.description}</p>
      ) : null}

      {field.type === "boolean" ? (
        <input type="checkbox" {...common} className="h-4 w-4 rounded border-input" />
      ) : field.type === "select" && field.options ? (
        <select {...common} className={baseClass}>
          <option value="">{`-- elegir ${label} --`}</option>
          {field.options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      ) : field.type === "password" ? (
        <div className="relative">
          <input
            {...common}
            type={isPasswordShown ? "text" : "password"}
            autoComplete="new-password"
            className={`${baseClass} pr-10`}
          />
          <button
            type="button"
            onClick={onTogglePassword}
            aria-label={isPasswordShown ? "Ocultar contraseña" : "Mostrar contraseña"}
            className="absolute right-1.5 top-1/2 inline-flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded text-muted-foreground hover:bg-accent/10"
          >
            {isPasswordShown ? <EyeOff size={16} /> : <Eye size={16} />}
          </button>
        </div>
      ) : (
        <input
          {...common}
          type={field.type === "number" ? "number" : field.type === "url" ? "url" : "text"}
          className={baseClass}
        />
      )}

      {error ? (
        <p id={`${id}-err`} role="alert" className="text-xs text-destructive">
          {error}
        </p>
      ) : null}
    </div>
  );
}

// ── Confirm-delete dialog (light-weight; no shadcn dep yet) ───────────

function ConfirmDeleteDialog({
  onCancel,
  onConfirm,
  pending,
}: {
  onCancel: () => void;
  onConfirm: () => void;
  pending: boolean;
}) {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onCancel]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-delete-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/80 p-4 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div className="w-full max-w-md rounded-lg border bg-card p-6 shadow-lg">
        <h2 id="confirm-delete-title" className="text-lg font-semibold">
          ¿Borrar credenciales?
        </h2>
        <p className="mt-2 text-sm text-muted-foreground">
          Las credenciales del cartucho serán eliminadas del Vault. Esta
          acción no se puede deshacer; tendrás que volver a configurarlo
          desde cero.
        </p>
        <div className="mt-6 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            className="inline-flex h-9 items-center justify-center rounded-md border bg-background px-4 text-sm font-medium hover:bg-accent/5"
          >
            Cancelar
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={pending}
            className="inline-flex h-9 items-center justify-center rounded-md bg-destructive px-4 text-sm font-medium text-destructive-foreground hover:bg-destructive/90 disabled:pointer-events-none disabled:opacity-60"
          >
            {pending ? "Borrando…" : "Borrar"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Schema → Zod adapter ─────────────────────────────────────────────

function buildZodSchema(fields: ConnectorField[]): z.ZodObject<z.ZodRawShape> {
  // Zod's exported ZodRawShape is `readonly`; assigning directly trips
  // TS2542. Build into a plain mutable Record and pass it to z.object,
  // which accepts the structural shape.
  const shape: Record<string, z.ZodTypeAny> = {};
  for (const f of fields) {
    shape[f.name] = fieldToZod(f);
  }
  return z.object(shape);
}

function fieldToZod(f: ConnectorField): z.ZodTypeAny {
  let base: z.ZodTypeAny;
  if (f.type === "boolean") {
    base = z.boolean();
  } else if (f.type === "number") {
    base = z.coerce.number();
  } else {
    // string-shaped fields (string, url, password, select)
    let s = z.string();
    if (f.min_length) s = s.min(f.min_length, `Mínimo ${f.min_length} caracteres.`);
    if (f.max_length) s = s.max(f.max_length, `Máximo ${f.max_length} caracteres.`);
    if (f.type === "url")   s = s.url("Debe ser una URL válida.") as unknown as typeof s;
    if (f.pattern) {
      const re = new RegExp(f.pattern);
      s = s.regex(re, `Formato inválido para ${f.label ?? f.name}.`) as unknown as typeof s;
    }
    base = s;
  }
  if (!f.required) {
    base = base.optional() as z.ZodTypeAny;
  } else if (f.type !== "boolean") {
    // Required strings must also be non-empty.
    base = (base as z.ZodString).min(1, `${f.label ?? f.name} es obligatorio.`) as z.ZodTypeAny;
  }
  return base;
}

function defaultsFor(fields: ConnectorField[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of fields) {
    if (f.default !== undefined) out[f.name] = f.default;
    else if (f.type === "boolean") out[f.name] = false;
    else if (f.type === "number")  out[f.name] = "";
    else out[f.name] = "";
  }
  return out;
}
