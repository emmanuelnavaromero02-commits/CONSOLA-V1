"use client";

import { BarChart3, ExternalLink } from "lucide-react";
import { useMemo, useState } from "react";
import { toast } from "sonner";

import { useDatasets } from "@/lib/monitor/hooks";
import { studioErrorMessage, SUPERSET_INTERNAL_ONLY_COPY, supersetErrorMessage } from "@/lib/studio/client";
import { datasetsForLayer, goldTableName } from "@/lib/studio/datasets";
import { useLayerPreview, usePublishSuperset, useRuntimeConfig } from "@/lib/studio/hooks";
import type { StudioLayer, SupersetDatasetResult } from "@/lib/studio/types";
import { cn } from "@/lib/utils";

import { buttonClass, DataTable, Notice, primaryButtonClass, Spinner } from "./ui";

const LAYERS: Array<{ id: StudioLayer; label: string }> = [
  { id: "silver", label: "Silver" },
  { id: "gold", label: "Gold" },
];

function notifySuperset(result: SupersetDatasetResult, table: string) {
  if (result.needs_materialization) {
    toast.warning(result.message || `Materializa primero el dataset Gold ${table}.`);
    return;
  }
  if (result.created) {
    toast.success(`Dataset ${table} creado en Superset.`);
    return;
  }
  if (result.existing) {
    toast.info(`El dataset ${table} ya existía en Superset.`);
    return;
  }
  toast.error(result.error || result.message || "Superset no creó el dataset.");
}

function LayerPreview({ layer, cartridge, dataset }: { layer: StudioLayer; cartridge: string; dataset: string }) {
  const preview = useLayerPreview(layer, cartridge, dataset);
  if (preview.isLoading) {
    return <p className="flex items-center gap-2 text-sm text-muted-foreground"><Spinner /> Consultando {dataset}…</p>;
  }
  if (preview.isError) {
    return <Notice tone="error">{studioErrorMessage(preview.error, "No se pudo previsualizar el dataset.")}</Notice>;
  }
  const data = preview.data;
  if (!data) return null;
  if (data.available === false) {
    return (
      <Notice tone="warning" title="Dataset no disponible" testId="layer-unavailable">
        {data.reason || "El backend no devolvió el motivo."}
      </Notice>
    );
  }
  if (!data.rows.length) {
    return <Notice testId="layer-empty">{data.dataset || dataset} no tiene filas materializadas.</Notice>;
  }
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        {data.dataset || dataset} · {data.total} filas en la vista previa · {data.columns.length} columnas
      </p>
      <DataTable columns={data.columns} rows={data.rows} caption={`Vista previa de ${data.dataset || dataset}`} />
    </div>
  );
}

export function LayersPanel({ cartridge }: { cartridge: string }) {
  const datasets = useDatasets();
  const config = useRuntimeConfig();
  const publish = usePublishSuperset();
  const [layer, setLayer] = useState<StudioLayer>("silver");
  const [selectedByLayer, setSelectedByLayer] = useState<Record<string, string>>({});
  const list = useMemo(
    () => datasetsForLayer(datasets.data ?? [], cartridge, layer),
    [datasets.data, cartridge, layer],
  );
  const selectedName = selectedByLayer[`${cartridge}:${layer}`];
  const selected = list.find((dataset) => dataset.name === selectedName) ?? list[0];
  const supersetUrl = config.data?.superset_url ?? null;

  function publishSelected() {
    if (!selected) return;
    const table = goldTableName(selected);
    publish.mutate(
      { cartridge, tableName: table },
      {
        onSuccess: (result) => notifySuperset(result, table),
        onError: (error) => toast.error(supersetErrorMessage(error)),
      },
    );
  }

  return (
    <div className="space-y-4">
      <div role="tablist" aria-label="Capa" className="inline-flex rounded-md border bg-background p-1">
        {LAYERS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            id={`studio-layer-tab-${item.id}`}
            aria-selected={layer === item.id}
            aria-controls={`studio-layer-panel-${item.id}`}
            onClick={() => setLayer(item.id)}
            className={cn(
              "min-h-[44px] rounded px-4 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              layer === item.id ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent/10",
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div
        role="tabpanel"
        id={`studio-layer-panel-${layer}`}
        aria-labelledby={`studio-layer-tab-${layer}`}
        className="grid grid-cols-1 gap-4 xl:grid-cols-[280px_minmax(0,1fr)]"
      >
        <section aria-label={`Datasets ${layer}`} className="space-y-2">
          {datasets.isLoading ? (
            <p className="flex items-center gap-2 text-sm text-muted-foreground"><Spinner /> Cargando datasets…</p>
          ) : datasets.isError ? (
            <Notice
              tone="error"
              action={<button type="button" className={buttonClass} onClick={() => datasets.refetch()}>Reintentar</button>}
            >
              {studioErrorMessage(datasets.error, "No se pudo listar los datasets.")}
            </Notice>
          ) : !list.length ? (
            <Notice testId="layer-datasets-empty">
              No hay datasets {layer === "silver" ? "Silver" : "Gold"} registrados para {cartridge}.
            </Notice>
          ) : (
            <ul className="max-h-[480px] space-y-1 overflow-y-auto">
              {list.map((dataset) => (
                <li key={dataset.name}>
                  <button
                    type="button"
                    aria-pressed={selected?.name === dataset.name}
                    onClick={() => setSelectedByLayer((current) => ({ ...current, [`${cartridge}:${layer}`]: dataset.name }))}
                    className={cn(
                      "flex min-h-[44px] w-full flex-col items-start rounded-md border px-3 py-2 text-left text-sm",
                      "hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      selected?.name === dataset.name && "border-primary bg-primary/5",
                    )}
                  >
                    <span className="break-all font-mono text-xs">{dataset.name}</span>
                    <span className="text-[11px] text-muted-foreground">
                      {dataset.row_count != null ? `${dataset.row_count} filas` : "sin conteo"}
                      {dataset.is_stale ? " · desactualizado" : ""}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section aria-label="Vista previa" className="min-w-0 space-y-3">
          {layer === "gold" && selected ? (
            <div className="flex flex-wrap items-center gap-2">
              {supersetUrl ? (
                <>
                  <button
                    type="button"
                    className={primaryButtonClass}
                    onClick={publishSelected}
                    disabled={publish.isPending}
                  >
                    {publish.isPending ? <Spinner /> : <BarChart3 aria-hidden className="h-4 w-4" />} Publicar en Superset
                  </button>
                  <a href={supersetUrl} target="_blank" rel="noopener noreferrer" className={buttonClass}>
                    <ExternalLink aria-hidden className="h-4 w-4" /> Abrir Superset
                  </a>
                </>
              ) : config.isSuccess ? (
                <p className="text-xs text-muted-foreground">
                  Superset sin URL pública. {SUPERSET_INTERNAL_ONLY_COPY}
                </p>
              ) : null}
            </div>
          ) : null}
          {selected ? (
            <LayerPreview layer={layer} cartridge={cartridge} dataset={selected.name} />
          ) : (
            <Notice>Selecciona un dataset para ver sus filas.</Notice>
          )}
        </section>
      </div>
    </div>
  );
}
