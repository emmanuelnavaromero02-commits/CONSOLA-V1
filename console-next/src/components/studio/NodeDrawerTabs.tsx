"use client";

import { ArrowDownLeft, ArrowUpRight, Clock, ExternalLink, FileCode2, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useState, type ReactNode } from "react";

import { formatRelativeFromNow } from "@/lib/control-room/experience-presenter";
import { useAnalyticsApps } from "@/lib/hooks/useAnalyticsApps";
import { useDatasetDetail, usePipeline, useSourceSchema } from "@/lib/monitor/hooks";
import type { DatasetSummary, PipelineEntity } from "@/lib/monitor/types";
import { studioErrorMessage } from "@/lib/studio/client";
import { absoluteTime, formatDay, plural } from "@/lib/studio/format";
import { graphNeighbours, kindLabel } from "@/lib/studio/graph-layout";
import { collectDownstream, nodeCaption, nodeRef, nodeStage, STAGE_LABEL, STAGE_TEXT } from "@/lib/studio/graph-view";
import { useCartridgeStatus } from "@/lib/studio/hooks";
import {
  aggregateStatus,
  appsUsing,
  bronzeLoad,
  datasetStatus,
  entityStatus,
  NODE_STATUS_LABEL,
  NODE_STATUS_TONE,
  registeredRun,
  runTime,
  scheduleText,
  type StatusFact,
} from "@/lib/studio/node-facts";
import type { StudioSectionId } from "@/lib/studio/sections";
import type {
  DagGraphEdge,
  DagGraphNode,
  StudioEditorTarget,
  StudioManifest,
  StudioManifestDag,
  StudioManifestEntity,
} from "@/lib/studio/types";
import { cn } from "@/lib/utils";

import { ProbeBadge } from "./CartridgeBar";
import { ExtractionTracker, type ExtractionLaunch } from "./ExtractionTracker";
import { buttonClass, Notice, primaryButtonClass, Spinner } from "./ui";

const COLUMN_PREVIEW = 12;
const NO_DESCRIPTION = "Sin descripción registrada.";
const NO_SCHEDULE = "Sin programación registrada.";
const NO_STATUS = "Sin información de estado";
const NO_COUNT = "Sin conteo registrado.";
const NO_DATE = "Sin fecha registrada.";

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function manifestEntity(manifest: StudioManifest | null | undefined, name: string): StudioManifestEntity | undefined {
  return (manifest?.entities ?? []).find((entity) => entity && (entity.entity || entity.name) === name);
}

function manifestDag(manifest: StudioManifest | null | undefined, dagId: string): StudioManifestDag | undefined {
  for (const dag of manifest?.dags ?? []) {
    if (dag && typeof dag === "object" && dag.dag_id === dagId) return dag;
  }
  return undefined;
}

function entityName(entity: StudioManifestEntity): string {
  return String(entity.entity || entity.name || "");
}

export function nodeTitle(node: DagGraphNode, manifest: StudioManifest | null | undefined): string {
  const ref = nodeRef(node);
  if (ref.kind === "entity") {
    const entity = manifestEntity(manifest, ref.name);
    return text(entity?.display_name) ?? text(entity?.business_name) ?? text(node.label) ?? ref.name;
  }
  if (ref.kind === "cartridge") return text(manifest?.name) ?? text(node.label) ?? ref.name;
  if (ref.kind === "dataset" || ref.kind === "dag") return ref.name;
  return text(node.label) ?? node.id;
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-0.5 py-2 sm:grid-cols-[150px_minmax(0,1fr)] sm:gap-3">
      <dt className="text-xs font-medium text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words text-sm">{children}</dd>
    </div>
  );
}

function Missing({ children }: { children: ReactNode }) {
  return <span className="text-muted-foreground">{children}</span>;
}

function RelativeTime({ value }: { value: string | null | undefined }) {
  const iso = text(value);
  if (!iso || Number.isNaN(Date.parse(iso))) return <Missing>{NO_DATE}</Missing>;
  return (
    <span className="flex flex-col">
      <time dateTime={iso} title={absoluteTime(iso)} className="inline-flex items-center gap-1">
        <Clock aria-hidden className="h-3.5 w-3.5 text-muted-foreground" />
        {formatRelativeFromNow(iso)}
      </time>
      <span className="text-xs text-muted-foreground">{absoluteTime(iso)}</span>
    </span>
  );
}

function StatusValue({ fact }: { fact: StatusFact | null }) {
  if (!fact) return <Missing>{NO_STATUS}</Missing>;
  return (
    <span className="flex flex-col items-start gap-1">
      <span
        data-node-status={fact.status}
        className={cn(
          "inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium",
          NODE_STATUS_TONE[fact.status],
        )}
      >
        {NODE_STATUS_LABEL[fact.status]}
      </span>
      {fact.reason ? <span className="text-xs text-muted-foreground">{fact.reason}</span> : null}
    </span>
  );
}

function Volume({ count }: { count: number | null | undefined }) {
  return typeof count === "number" && Number.isFinite(count) ? (
    <>{plural(count, "registro", "registros")}</>
  ) : (
    <Missing>{NO_COUNT}</Missing>
  );
}

function PipelineNotice({ query }: { query: { isLoading: boolean; isError: boolean; error: unknown } }) {
  if (query.isLoading) {
    return (
      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <Spinner className="h-3 w-3" /> Consultando el estado de extracción…
      </p>
    );
  }
  if (query.isError) {
    return (
      <Notice tone="warning">
        {studioErrorMessage(query.error, "No se pudo consultar /api/pipeline; volumen, fecha y estado no están disponibles.")}
      </Notice>
    );
  }
  return null;
}

function pipelineEntry(entries: PipelineEntity[] | undefined, name: string): PipelineEntity | undefined {
  return (entries ?? []).find((entry) => entry.entity === name);
}

function EntityAbout({ cartridge, name, node, manifest }: { cartridge: string; name: string; node: DagGraphNode; manifest: StudioManifest | null }) {
  const pipeline = usePipeline(cartridge);
  const entity = manifestEntity(manifest, name);
  const entry = pipelineEntry(pipeline.data, name);
  const run = registeredRun(entry);
  const load = bronzeLoad(entry);
  const lastRun = runTime(run);
  return (
    <div className="space-y-2">
      <PipelineNotice query={pipeline} />
      <dl className="divide-y">
        <Fact label="Nombre de negocio">{nodeTitle(node, manifest)}</Fact>
        <Fact label="Descripción">{text(entity?.description) ?? <Missing>{NO_DESCRIPTION}</Missing>}</Fact>
        <Fact label="Capa">Bronce · tabla de origen</Fact>
        <Fact label="Frecuencia de actualización">{scheduleText(entity) ?? <Missing>{NO_SCHEDULE}</Missing>}</Fact>
        <Fact label="Última carga en bronce">
          {load ? (
            <span data-bronze-load className="flex flex-col">
              <span>{load.count !== null ? plural(load.count, "registro", "registros") : "Sin conteo registrado."}</span>
              {load.day ? <span className="text-xs text-muted-foreground">Cargados el {formatDay(load.day)}</span> : null}
              <span className="text-xs text-muted-foreground">Cuenta solo la última carga, no el total de la tabla.</span>
            </span>
          ) : (
            <Missing>{run ? "Sin carga confirmada por la última corrida." : "Sin información de cargas."}</Missing>
          )}
        </Fact>
        <Fact label="Última corrida">
          {lastRun ? <RelativeTime value={lastRun} /> : <Missing>Sin corridas registradas.</Missing>}
        </Fact>
        <Fact label="Estado de la última corrida">
          <StatusValue fact={entityStatus(entry)} />
        </Fact>
      </dl>
    </div>
  );
}

function layerText(layer: string | null | undefined): string | null {
  const value = String(layer ?? "").trim().toLowerCase();
  if (value === "silver") return "Plata (silver)";
  if (value === "gold") return "Oro (gold)";
  return value || null;
}

function DatasetAbout({ name, summary }: { name: string; summary: DatasetSummary | undefined }) {
  const detail = useDatasetDetail(name);
  const data = detail.data;
  const schedule = data?.metadata?.schedule;
  return (
    <div className="space-y-2">
      {detail.isLoading ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <Spinner className="h-3 w-3" /> Leyendo la definición del dataset…
        </p>
      ) : null}
      {detail.isError ? (
        <Notice tone="warning">{studioErrorMessage(detail.error, "No se pudo leer la definición del dataset.")}</Notice>
      ) : null}
      <dl className="divide-y">
        <Fact label="Nombre">{name}</Fact>
        <Fact label="Descripción">{text(data?.metadata?.description) ?? <Missing>{NO_DESCRIPTION}</Missing>}</Fact>
        <Fact label="Capa">{layerText(summary?.layer ?? data?.layer) ?? <Missing>Sin capa registrada.</Missing>}</Fact>
        <Fact label="Frecuencia de actualización">{text(schedule) ?? <Missing>{NO_SCHEDULE}</Missing>}</Fact>
        <Fact label="Volumen">
          <Volume count={summary?.row_count ?? data?.row_count} />
        </Fact>
        <Fact label="Última actualización">
          <RelativeTime value={summary?.last_refresh ?? data?.last_refresh} />
        </Fact>
        <Fact label="Estado">
          <StatusValue fact={datasetStatus(summary, data)} />
        </Fact>
      </dl>
    </div>
  );
}

function latestRun(entries: Array<PipelineEntity | undefined>): string | null {
  let best: { time: number; value: string } | null = null;
  for (const entry of entries) {
    const value = runTime(registeredRun(entry));
    const time = value ? Date.parse(value) : Number.NaN;
    if (value && !Number.isNaN(time) && (!best || time > best.time)) best = { time, value };
  }
  return best?.value ?? null;
}

function DagAbout({ cartridge, dagId, manifest }: { cartridge: string; dagId: string; manifest: StudioManifest | null }) {
  const pipeline = usePipeline(cartridge);
  const dag = manifestDag(manifest, dagId);
  const entities = (manifest?.entities ?? []).filter((entity) => entity?.dag_id === dagId);
  const entries = entities.map((entity) => pipelineEntry(pipeline.data, entityName(entity)));
  const schedules = [...new Set(entities.map((entity) => scheduleText(entity)).filter((value): value is string => Boolean(value)))];
  return (
    <div className="space-y-2">
      <PipelineNotice query={pipeline} />
      <dl className="divide-y">
        <Fact label="Automatización">{dagId}</Fact>
        <Fact label="Descripción">{text(dag?.description) ?? <Missing>{NO_DESCRIPTION}</Missing>}</Fact>
        <Fact label="Tablas que extrae">
          {entities.length ? plural(entities.length, "tabla de origen", "tablas de origen") : <Missing>Sin tablas asignadas en el manifiesto.</Missing>}
        </Fact>
        <Fact label="Frecuencia de actualización">
          {schedules.length ? (
            <ul className="space-y-0.5">
              {schedules.slice(0, 4).map((value) => (
                <li key={value}>{value}</li>
              ))}
              {schedules.length > 4 ? <li className="text-xs text-muted-foreground">y {schedules.length - 4} más</li> : null}
            </ul>
          ) : (
            <Missing>{NO_SCHEDULE}</Missing>
          )}
        </Fact>
        <Fact label="Última corrida">
          <RelativeTime value={latestRun(entries)} />
        </Fact>
        <Fact label="Estado de las corridas">
          <StatusValue fact={aggregateStatus(entries.map((entry) => entityStatus(entry)))} />
        </Fact>
      </dl>
    </div>
  );
}

function CartridgeAbout({ cartridge, node, manifest }: { cartridge: string; node: DagGraphNode; manifest: StudioManifest | null }) {
  const status = useCartridgeStatus(cartridge);
  return (
    <dl className="divide-y">
      <Fact label="Conector">{nodeTitle(node, manifest)}</Fact>
      <Fact label="Descripción">{text(manifest?.description) ?? <Missing>{NO_DESCRIPTION}</Missing>}</Fact>
      <Fact label="Tablas de origen">
        {manifest ? plural((manifest.entities ?? []).length, "tabla", "tablas") : <Missing>{NO_COUNT}</Missing>}
      </Fact>
      <Fact label="Estado del servicio">
        {status.isLoading ? (
          <span className="inline-flex items-center gap-2 text-xs text-muted-foreground">
            <Spinner className="h-3 w-3" /> Sondeando servicio…
          </span>
        ) : status.isError ? (
          <span className="text-xs text-destructive">No se pudo consultar el estado.</span>
        ) : status.data ? (
          <ProbeBadge probe={status.data} testId="node-cartridge-status" />
        ) : (
          <Missing>{NO_STATUS}</Missing>
        )}
      </Fact>
    </dl>
  );
}

export function AboutPanel({
  cartridge,
  node,
  manifest,
  datasetsByName,
}: {
  cartridge: string;
  node: DagGraphNode;
  manifest: StudioManifest | null;
  datasetsByName: Map<string, DatasetSummary>;
}) {
  const ref = nodeRef(node);
  if (ref.kind === "entity") return <EntityAbout cartridge={cartridge} name={ref.name} node={node} manifest={manifest} />;
  if (ref.kind === "dataset") return <DatasetAbout name={ref.name} summary={datasetsByName.get(ref.name)} />;
  if (ref.kind === "dag") return <DagAbout cartridge={cartridge} dagId={ref.name} manifest={manifest} />;
  if (ref.kind === "cartridge") return <CartridgeAbout cartridge={cartridge} node={node} manifest={manifest} />;
  return <Notice>{`Nodo ${kindLabel(node.kind)} sin detalle disponible.`}</Notice>;
}

function NeighbourList({
  title,
  icon,
  ids,
  byId,
  manifest,
  empty,
  onSelect,
}: {
  title: string;
  icon: ReactNode;
  ids: string[];
  byId: Map<string, DagGraphNode>;
  manifest: StudioManifest | null;
  empty: string;
  onSelect: (id: string) => void;
}) {
  return (
    <section className="space-y-2">
      <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {icon} {title} ({ids.length})
      </h3>
      {ids.length ? (
        <ul className="space-y-1">
          {ids.map((id) => {
            const neighbour = byId.get(id);
            const stage = neighbour ? nodeStage(neighbour) : "otro";
            return (
              <li key={id}>
                <button
                  type="button"
                  onClick={() => onSelect(id)}
                  className={cn(
                    "flex min-h-[44px] w-full items-center justify-between gap-2 rounded-md border px-3 py-1.5 text-left text-sm",
                    "hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  )}
                >
                  <span className="min-w-0 truncate">{neighbour ? nodeTitle(neighbour, manifest) : id}</span>
                  <span className={cn("shrink-0 text-[11px] font-medium uppercase", STAGE_TEXT[stage])}>
                    {neighbour ? nodeCaption(neighbour) : STAGE_LABEL[stage]}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      ) : (
        <p className="text-xs text-muted-foreground">{empty}</p>
      )}
    </section>
  );
}

export function LineagePanel({
  cartridge,
  node,
  edges,
  byId,
  manifest,
  onSelect,
}: {
  cartridge: string;
  node: DagGraphNode;
  edges: DagGraphEdge[];
  byId: Map<string, DagGraphNode>;
  manifest: StudioManifest | null;
  onSelect: (id: string) => void;
}) {
  const apps = useAnalyticsApps({ cartridge });
  const neighbours = graphNeighbours(node.id, edges);
  const datasetNames = [node.id, ...collectDownstream(node.id, edges)]
    .map((id) => byId.get(id))
    .filter((item): item is DagGraphNode => Boolean(item) && String(item?.kind) === "dataset")
    .map((item) => nodeRef(item).name);
  const dependent = appsUsing(datasetNames, apps.data?.apps);
  return (
    <div className="space-y-5">
      <NeighbourList
        title="Lo alimentan"
        icon={<ArrowDownLeft aria-hidden className="h-3.5 w-3.5 text-sky-500" />}
        ids={neighbours.incoming}
        byId={byId}
        manifest={manifest}
        empty="Nada lo alimenta dentro de este cartucho."
        onSelect={onSelect}
      />
      <NeighbourList
        title="Lo usan"
        icon={<ArrowUpRight aria-hidden className="h-3.5 w-3.5 text-emerald-500" />}
        ids={neighbours.outgoing}
        byId={byId}
        manifest={manifest}
        empty="Ningún nodo de este cartucho lo usa todavía."
        onSelect={onSelect}
      />
      <section className="space-y-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          Apps analíticas que dependen de este dato
        </h3>
        {apps.isLoading ? (
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <Spinner className="h-3 w-3" /> Consultando apps analíticas…
          </p>
        ) : apps.isError ? (
          <Notice tone="error">{studioErrorMessage(apps.error, "No se pudo consultar /api/apps.")}</Notice>
        ) : dependent.length ? (
          <>
            <Notice tone="warning" testId="lineage-impact">
              {dependent.length === 1
                ? "1 app analítica depende de este dato. Revisa el impacto antes de modificarlo o forzar su actualización."
                : `${dependent.length} apps analíticas dependen de este dato. Revisa el impacto antes de modificarlo o forzar su actualización.`}
            </Notice>
            <ul className="space-y-1">
              {dependent.map((app) => (
                <li key={app.name}>
                  <Link
                    href={`/analytics/viewer?app=${encodeURIComponent(app.name)}`}
                    className={cn(
                      "flex min-h-[44px] items-center justify-between gap-2 rounded-md border px-3 py-1.5 text-sm",
                      "hover:bg-accent/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    )}
                  >
                    <span className="min-w-0 truncate">{text(app.title) ?? app.name}</span>
                    <ExternalLink aria-hidden className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  </Link>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-xs text-muted-foreground">Ninguna app analítica registrada usa este dato ni sus derivados.</p>
        )}
      </section>
    </div>
  );
}

interface ColumnFact {
  name: string;
  type: string | null;
  description: string | null;
}

function columnFacts(columns: Array<Record<string, unknown>> | null | undefined): ColumnFact[] {
  return (columns ?? []).flatMap((column) => {
    const name = text(column?.name);
    if (!name) return [];
    return [
      {
        name,
        type: text(column.type) ?? text(column.data_type) ?? text(column.dtype),
        description: text(column.description) ?? text(column.comment),
      },
    ];
  });
}

function ColumnsTable({ columns }: { columns: ColumnFact[] }) {
  const [expanded, setExpanded] = useState(false);
  if (!columns.length) return <p className="text-xs text-muted-foreground">Sin columnas registradas.</p>;
  const visible = expanded ? columns : columns.slice(0, COLUMN_PREVIEW);
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted-foreground">
        {columns.length > COLUMN_PREVIEW && !expanded
          ? `Primeras ${COLUMN_PREVIEW} columnas de ${columns.length}`
          : plural(columns.length, "columna", "columnas")}
      </p>
      <div className="overflow-auto rounded-md border">
        <table className="min-w-full divide-y text-xs">
          <thead className="bg-muted text-left text-muted-foreground">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">Columna</th>
              <th scope="col" className="px-3 py-2 font-medium">Tipo</th>
              <th scope="col" className="px-3 py-2 font-medium">Descripción</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {visible.map((column) => (
              <tr key={column.name}>
                <td className="break-all px-3 py-1.5 font-mono">{column.name}</td>
                <td className="px-3 py-1.5 font-mono text-muted-foreground">{column.type ?? "—"}</td>
                <td className="px-3 py-1.5 text-muted-foreground">{column.description ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {columns.length > COLUMN_PREVIEW ? (
        <button type="button" className={buttonClass} aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
          {expanded ? "Mostrar menos" : "Mostrar todas"}
        </button>
      ) : null}
    </div>
  );
}

function DatasetColumns({ name }: { name: string }) {
  const detail = useDatasetDetail(name);
  if (detail.isLoading) {
    return <p className="flex items-center gap-2 text-xs text-muted-foreground"><Spinner className="h-3 w-3" /> Leyendo columnas…</p>;
  }
  if (detail.isError) return <Notice tone="error">{studioErrorMessage(detail.error, "No se pudo leer el esquema del dataset.")}</Notice>;
  return <ColumnsTable columns={columnFacts(detail.data?.columns)} />;
}

function EntityColumns({ cartridge, name }: { cartridge: string; name: string }) {
  const schema = useSourceSchema(`raw/${cartridge}/${name}`);
  if (schema.isLoading) {
    return <p className="flex items-center gap-2 text-xs text-muted-foreground"><Spinner className="h-3 w-3" /> Leyendo columnas de bronze…</p>;
  }
  if (schema.isError) return <Notice tone="error">{studioErrorMessage(schema.error, "No se pudo leer el esquema de bronze.")}</Notice>;
  return <ColumnsTable columns={columnFacts(schema.data?.preview?.schema)} />;
}

export function InfoPanel({
  cartridge,
  node,
  manifest,
  launch,
  refreshing,
  onOpenEditor,
  onOpenSection,
  onRequestRefresh,
  onRequestExtract,
}: {
  cartridge: string;
  node: DagGraphNode;
  manifest: StudioManifest | null;
  launch: ExtractionLaunch | null;
  refreshing: boolean;
  onOpenEditor?: (target: StudioEditorTarget) => void;
  onOpenSection?: (id: StudioSectionId) => void;
  onRequestRefresh: () => void;
  onRequestExtract: () => void;
}) {
  const ref = nodeRef(node);
  if (ref.kind === "dag") {
    const entities = (manifest?.entities ?? []).filter((entity) => entity?.dag_id === ref.name);
    return (
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          {entities.length
            ? `Extrae ${plural(entities.length, "tabla de origen", "tablas de origen")}: ${entities.map(entityName).join(", ")}.`
            : "El manifiesto no asigna tablas de origen a esta automatización."}
        </p>
        <button type="button" className={primaryButtonClass} onClick={() => onOpenSection?.("dags")} disabled={!onOpenSection}>
          <FileCode2 aria-hidden className="h-4 w-4" /> Abrir en Automatizaciones
        </button>
      </div>
    );
  }
  if (ref.kind !== "dataset" && ref.kind !== "entity") {
    return <p className="text-sm text-muted-foreground">El conector no tiene columnas propias; selecciona una tabla de origen o un dataset.</p>;
  }
  return (
    <div className="space-y-4">
      {ref.kind === "dataset" ? <DatasetColumns name={ref.name} /> : <EntityColumns cartridge={cartridge} name={ref.name} />}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className={primaryButtonClass}
          disabled={!onOpenEditor}
          onClick={() => onOpenEditor?.(ref.kind === "dataset" ? { dataset: ref.name } : { entity: ref.name })}
        >
          <FileCode2 aria-hidden className="h-4 w-4" /> Abrir en Editor de Consultas
        </button>
        <button
          type="button"
          className={buttonClass}
          disabled={refreshing}
          onClick={ref.kind === "dataset" ? onRequestRefresh : onRequestExtract}
        >
          {refreshing ? <Spinner /> : <RefreshCw aria-hidden className="h-4 w-4" />} Forzar actualización ahora
        </button>
      </div>
      {ref.kind === "entity" ? (
        <p className="text-xs text-muted-foreground">
          En tablas de origen, forzar la actualización lanza una extracción desde {text(manifest?.name) ?? cartridge}.
        </p>
      ) : null}
      {launch ? <ExtractionTracker cartridge={cartridge} launch={launch} /> : null}
    </div>
  );
}
