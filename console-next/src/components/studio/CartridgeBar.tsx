"use client";

import { Download, Plus, RefreshCw, Upload } from "lucide-react";
import { useRef, useState } from "react";
import { toast } from "sonner";

import { exportCartridge, MAX_IMPORT_ZIP_BYTES, studioErrorMessage } from "@/lib/studio/client";
import { useCartridgeStatus, useCreateCartridge, useImportCartridge } from "@/lib/studio/hooks";
import type { CartridgeProbe, StudioCartridgeSummary } from "@/lib/studio/types";
import { cartridgeIdError } from "@/lib/studio/validation";
import { cn } from "@/lib/utils";

import { buttonClass, ConfirmDialog, inputClass, Notice, Spinner } from "./ui";

const PROBE_LABEL: Record<string, string> = {
  operational: "Operativo",
  degraded: "Degradado",
  offline: "Sin conexión",
  registered: "Registrado",
};

const PROBE_TONE: Record<string, string> = {
  operational: "border-success/30 bg-success/10 text-success",
  degraded: "border-warning/30 bg-warning/10 text-warning",
  offline: "border-destructive/30 bg-destructive/10 text-destructive",
  registered: "border-border bg-muted text-muted-foreground",
};

function probeDetail(probe: CartridgeProbe): string | null {
  if (typeof probe.detail === "string" && probe.detail.trim()) return probe.detail.trim();
  if (typeof probe.reason === "string" && probe.reason.trim()) return probe.reason.trim().slice(0, 160);
  return null;
}

export function ProbeBadge({ probe }: { probe: CartridgeProbe }) {
  const status = String(probe.status || "").toLowerCase();
  const label = PROBE_LABEL[status] ?? (status || "Desconocido");
  const detail = probeDetail(probe);
  return (
    <span data-testid="cartridge-status" data-status={status} className="inline-flex flex-wrap items-center gap-2 text-xs">
      <span
        title={detail ?? label}
        className={cn(
          "inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 font-medium",
          PROBE_TONE[status] ?? "border-border bg-muted text-muted-foreground",
        )}
      >
        {label}
      </span>
      {status === "registered" && detail ? <span className="text-muted-foreground">{detail}</span> : null}
    </span>
  );
}

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

const EMPTY_FORM = { id: "", name: "", description: "" };

export function CartridgeBar({
  cartridges,
  loading,
  error,
  onRetry,
  activeId,
  onSelect,
}: {
  cartridges: StudioCartridgeSummary[];
  loading: boolean;
  error: string | null;
  onRetry: () => void;
  activeId: string | null;
  onSelect: (id: string) => void;
}) {
  const status = useCartridgeStatus(activeId);
  const create = useCreateCartridge();
  const importZip = useImportCartridge();
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [exporting, setExporting] = useState(false);
  const idError = form.id ? cartridgeIdError(form.id) : null;

  async function handleExport() {
    if (!activeId) return;
    setExporting(true);
    try {
      const { blob, filename } = await exportCartridge(activeId);
      saveBlob(blob, filename);
      toast.success(`Cartucho ${activeId} exportado como ${filename}.`);
    } catch (err) {
      toast.error(studioErrorMessage(err, "No se pudo exportar el cartucho."));
    } finally {
      setExporting(false);
    }
  }

  function handleImport(file: File | undefined) {
    if (!file) return;
    importZip.mutate(file, {
      onSuccess: (manifest) => {
        toast.success(manifest?.id ? `Cartucho ${manifest.id} importado.` : "Cartucho importado.");
        if (manifest?.id) onSelect(manifest.id);
      },
      onError: (err) => toast.error(studioErrorMessage(err, "No se pudo importar el ZIP.")),
    });
  }

  function submitCreate() {
    const problem = cartridgeIdError(form.id) ?? (form.name.trim() ? null : "El nombre es obligatorio.");
    if (problem) {
      toast.error(problem);
      return;
    }
    create.mutate(form, {
      onSuccess: (manifest) => {
        toast.success(`Cartucho ${manifest?.id ?? form.id} creado.`);
        setCreating(false);
        setForm(EMPTY_FORM);
        onSelect(manifest?.id ?? form.id.trim());
      },
      onError: (err) => toast.error(studioErrorMessage(err, "No se pudo crear el cartucho.")),
    });
  }

  return (
    <section aria-label="Cartucho activo" className="rounded-lg border bg-card p-4 shadow-sm">
      <div className="flex flex-col gap-3 xl:flex-row xl:items-end xl:justify-between">
        <div className="flex flex-1 flex-col gap-2 sm:flex-row sm:items-end sm:gap-4">
          <label htmlFor="studio-cartridge" className="flex min-w-[220px] flex-1 flex-col gap-1 text-sm sm:max-w-sm">
            <span className="text-xs font-medium uppercase text-muted-foreground">Cartucho activo</span>
            <select
              id="studio-cartridge"
              name="cartridge"
              data-testid="cartridge-picker"
              value={activeId ?? ""}
              disabled={loading || !cartridges.length}
              onChange={(event) => onSelect(event.target.value)}
              className={inputClass}
            >
              {!cartridges.length ? <option value="">{loading ? "Cargando cartuchos…" : "Sin cartuchos"}</option> : null}
              {cartridges.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name ? `${item.name} (${item.id})` : item.id}
                </option>
              ))}
            </select>
          </label>
          <div className="flex min-h-[44px] items-center gap-2" aria-live="polite">
            {!activeId ? null : status.isLoading ? (
              <span className="inline-flex items-center gap-2 text-xs text-muted-foreground">
                <Spinner /> Sondeando servicio…
              </span>
            ) : status.isError ? (
              <span className="text-xs text-destructive">No se pudo consultar el estado.</span>
            ) : status.data ? (
              <ProbeBadge probe={status.data} />
            ) : null}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={buttonClass} onClick={() => setCreating(true)}>
            <Plus aria-hidden className="h-4 w-4" /> Nuevo cartucho
          </button>
          <button
            type="button"
            className={buttonClass}
            onClick={handleExport}
            disabled={!activeId || exporting}
          >
            {exporting ? <Spinner /> : <Download aria-hidden className="h-4 w-4" />} Exportar ZIP
          </button>
          <button
            type="button"
            className={buttonClass}
            onClick={() => fileRef.current?.click()}
            disabled={importZip.isPending}
          >
            {importZip.isPending ? <Spinner /> : <Upload aria-hidden className="h-4 w-4" />} Importar ZIP
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".zip,application/zip"
            className="sr-only"
            aria-label={`Archivo ZIP del cartucho (máximo ${MAX_IMPORT_ZIP_BYTES / (1024 * 1024)} MB)`}
            onChange={(event) => {
              handleImport(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
        </div>
      </div>
      {error ? (
        <div className="mt-3">
          <Notice
            tone="error"
            title="No se pudieron cargar los cartuchos."
            action={
              <button type="button" className={buttonClass} onClick={onRetry}>
                <RefreshCw aria-hidden className="h-4 w-4" /> Reintentar
              </button>
            }
          >
            {error}
          </Notice>
        </div>
      ) : null}

      <ConfirmDialog
        open={creating}
        title="Nuevo cartucho"
        description="Registra un cartucho vacío; después podrás agregar entidades, DAGs y datasets."
        confirmLabel="Crear cartucho"
        pendingLabel="Creando…"
        pending={create.isPending}
        confirmDisabled={Boolean(idError) || !form.id.trim() || !form.name.trim()}
        onConfirm={submitCreate}
        onCancel={() => {
          setCreating(false);
          setForm(EMPTY_FORM);
        }}
        testId="create-cartridge-dialog"
      >
        <label className="flex flex-col gap-1">
          <span className="font-medium">Identificador</span>
          <input
            value={form.id}
            onChange={(event) => setForm((current) => ({ ...current, id: event.target.value }))}
            className={inputClass}
            placeholder="mi_cartucho"
            aria-invalid={Boolean(idError)}
            aria-describedby={idError ? "create-cartridge-id-error" : undefined}
          />
          {idError ? <span id="create-cartridge-id-error" className="text-xs text-destructive">{idError}</span> : null}
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-medium">Nombre</span>
          <input
            value={form.name}
            onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
            className={inputClass}
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="font-medium">Descripción</span>
          <input
            value={form.description}
            onChange={(event) => setForm((current) => ({ ...current, description: event.target.value }))}
            className={inputClass}
          />
        </label>
      </ConfirmDialog>
    </section>
  );
}
