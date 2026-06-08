"use client";

import { useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Database,
  FileText,
  Layers3,
  Loader2,
  RefreshCw,
  RotateCcw,
  Search,
  UploadCloud,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const CARTRIDGES = ["replicon", "hubspot", "sap_hcm", "sap_s4hana", "sap_successfactors"] as const;
const SOURCE_KINDS = ["document", "schema"] as const;

interface RagSource {
  id?: number | string | null;
  name?: string | null;
  description?: string | null;
  mime_type?: string | null;
  size_chars?: number | null;
  chunk_count?: number | null;
  kind?: string | null;
  created_at?: string | null;
}

interface RagResult {
  parent_id?: number | string | null;
  source_id?: number | string | null;
  source_name?: string | null;
  source_kind?: string | null;
  context?: string | null;
  child_content?: string | null;
  similarity?: number | null;
}

interface RagSourcesPayload {
  sources?: RagSource[];
  result?: { sources?: RagSource[] } | RagSource[];
}

interface RagAskPayload {
  answer?: string;
  results?: RagResult[];
}

interface RagSearchPayload {
  results?: RagResult[];
}

type QueryMode = "ask" | "search";

interface IngestForm {
  name: string;
  description: string;
  kind: string;
  content: string;
}

interface ReindexForm {
  kind: "dataset" | "raw";
  name: string;
  cartridge: string;
}

interface QueryForm {
  mode: QueryMode;
  query: string;
  topK: number;
  kinds: string;
}

interface QueryOutput {
  answer?: string;
  results: RagResult[];
}

const EMPTY_INGEST: IngestForm = {
  name: "",
  description: "",
  kind: "document",
  content: "",
};

const EMPTY_REINDEX: ReindexForm = {
  kind: "dataset",
  name: "",
  cartridge: "replicon",
};

const EMPTY_QUERY: QueryForm = {
  mode: "ask",
  query: "",
  topK: 5,
  kinds: "",
};

export default function KnowledgePage() {
  const queryClient = useQueryClient();
  const [kindFilter, setKindFilter] = useState("all");
  const [ingestForm, setIngestForm] = useState<IngestForm>(EMPTY_INGEST);
  const [reindexForm, setReindexForm] = useState<ReindexForm>(EMPTY_REINDEX);
  const [queryForm, setQueryForm] = useState<QueryForm>(EMPTY_QUERY);
  const [queryOutput, setQueryOutput] = useState<QueryOutput | null>(null);

  const sources = useQuery({
    queryKey: ["rag", "sources", kindFilter],
    queryFn: async () => {
      const suffix = kindFilter === "all" ? "" : `?kinds=${encodeURIComponent(kindFilter)}`;
      const { data } = await api.get<RagSourcesPayload>(`/api/rag/sources${suffix}`);
      return normalizeSources(data);
    },
  });

  const metrics = useMemo(() => {
    const rows = sources.data ?? [];
    return {
      sourceCount: rows.length,
      chunkCount: rows.reduce((total, row) => total + Number(row.chunk_count ?? 0), 0),
      sizeChars: rows.reduce((total, row) => total + Number(row.size_chars ?? 0), 0),
    };
  }, [sources.data]);

  const ingest = useMutation({
    mutationFn: async (form: IngestForm) => {
      const { data } = await api.post<{ source_id?: number; parents?: number; children?: number; error?: string }>(
        "/api/rag/ingest",
        {
          name: form.name.trim(),
          description: form.description.trim(),
          kind: form.kind,
          content: form.content,
          mime_type: "text/plain",
        },
      );
      return data;
    },
    onSuccess: (data) => {
      if (data.error) {
        toast.error(data.error);
        return;
      }
      toast.success("Fuente ingerida.");
      setIngestForm(EMPTY_INGEST);
      queryClient.invalidateQueries({ queryKey: ["rag", "sources"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo ingerir la fuente."),
  });

  const reindex = useMutation({
    mutationFn: async (form: ReindexForm) => {
      const payload = form.kind === "raw"
        ? { kind: form.kind, name: form.name.trim(), cartridge: form.cartridge }
        : { kind: form.kind, name: form.name.trim() };
      const { data } = await api.post<{ reindexed?: boolean; source?: string; children?: number }>(
        "/api/rag/reindex",
        payload,
      );
      return data;
    },
    onSuccess: (data) => {
      toast.success(data.source ? `Reindexado: ${data.source}` : "Reindexado.");
      queryClient.invalidateQueries({ queryKey: ["rag", "sources"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo reindexar."),
  });

  const quickQuery = useMutation({
    mutationFn: async (form: QueryForm): Promise<QueryOutput> => {
      const body = {
        query: form.query.trim(),
        top_k: form.topK,
        kinds: parseKinds(form.kinds),
      };
      if (form.mode === "ask") {
        const { data } = await api.post<RagAskPayload>("/api/rag/ask", body);
        return { answer: data.answer, results: data.results ?? [] };
      }
      const { data } = await api.post<RagSearchPayload>("/api/rag/search", body);
      return { results: data.results ?? [] };
    },
    onSuccess: (data) => {
      setQueryOutput(data);
      toast.success("Consulta completada.");
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo consultar RAG."),
  });

  function submitIngest() {
    if (!ingestForm.name.trim()) {
      toast.error("Nombre requerido.");
      return;
    }
    if (!ingestForm.content.trim()) {
      toast.error("Contenido requerido.");
      return;
    }
    ingest.mutate(ingestForm);
  }

  function submitReindex() {
    if (!reindexForm.name.trim()) {
      toast.error("Nombre requerido.");
      return;
    }
    reindex.mutate(reindexForm);
  }

  function submitQuery() {
    if (!queryForm.query.trim()) {
      toast.error("Consulta requerida.");
      return;
    }
    quickQuery.mutate(queryForm);
  }

  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-6">
      <header className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight">Base de conocimiento</h1>
          <p className="text-sm text-muted-foreground">
            Documentos y fuentes que el Copiloto usa para responder tus preguntas.
          </p>
        </div>
        <button
          type="button"
          onClick={() => sources.refetch()}
          className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RefreshCw aria-hidden className={cn("h-4 w-4", sources.isFetching && "animate-spin")} />
          Refrescar
        </button>
      </header>

      <section className="grid grid-cols-1 gap-4 md:grid-cols-3" aria-label="Resumen RAG">
        <MetricCard icon={Database} label="Fuentes" value={metrics.sourceCount} />
        <MetricCard icon={Layers3} label="Chunks" value={metrics.chunkCount} />
        <MetricCard icon={FileText} label="Caracteres" value={formatNumber(metrics.sizeChars)} />
      </section>

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(360px,0.85fr)]">
        <div className="space-y-4">
          <section className="rounded-lg border bg-card shadow-sm">
            <header className="flex flex-col gap-3 border-b px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-base font-semibold">Fuentes</h2>
                <p className="text-xs text-muted-foreground">Inventario indexado por pgvector.</p>
              </div>
              <select
                value={kindFilter}
                onChange={(event) => setKindFilter(event.target.value)}
                className="min-h-[40px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                aria-label="Tipo de fuente"
              >
                <option value="all">Todos</option>
                {SOURCE_KINDS.map((kind) => (
                  <option key={kind} value={kind}>{kind}</option>
                ))}
              </select>
            </header>

            {sources.isError ? (
              <ErrorPanel message="No se pudieron cargar fuentes RAG." onRetry={() => sources.refetch()} />
            ) : sources.isLoading ? (
              <SkeletonRows rows={5} />
            ) : (
              <SourcesTable sources={sources.data ?? []} />
            )}
          </section>

          <section className="rounded-lg border bg-card p-4 shadow-sm">
            <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-base font-semibold">Probador</h2>
                <p className="text-xs text-muted-foreground">Consulta directa contra RAG.</p>
              </div>
              <SegmentedControl
                value={queryForm.mode}
                onChange={(mode) => setQueryForm((current) => ({ ...current, mode }))}
              />
            </div>

            <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_180px]">
              <label className="flex flex-col gap-1.5 text-sm lg:col-span-2">
                <span className="font-medium">Consulta</span>
                <textarea
                  value={queryForm.query}
                  onChange={(event) => setQueryForm((current) => ({ ...current, query: event.target.value }))}
                  className="min-h-[96px] rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </label>
              <label className="flex flex-col gap-1.5 text-sm">
                <span className="font-medium">Tipos</span>
                <input
                  value={queryForm.kinds}
                  onChange={(event) => setQueryForm((current) => ({ ...current, kinds: event.target.value }))}
                  className="min-h-[44px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  placeholder="document,schema"
                />
              </label>
              <label className="flex flex-col gap-1.5 text-sm">
                <span className="font-medium">Resultados a mostrar</span>
                <input
                  type="number"
                  min={1}
                  max={20}
                  value={queryForm.topK}
                  onChange={(event) => setQueryForm((current) => ({ ...current, topK: boundedTopK(event.target.value) }))}
                  className="min-h-[44px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </label>
            </div>

            <button
              type="button"
              onClick={submitQuery}
              disabled={quickQuery.isPending}
              className="mt-3 inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
            >
              {quickQuery.isPending ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <Search aria-hidden className="h-4 w-4" />}
              Consultar
            </button>

            {queryOutput ? <QueryResults output={queryOutput} /> : null}
          </section>
        </div>

        <div className="space-y-4">
          <section className="rounded-lg border bg-card p-4 shadow-sm">
            <h2 className="text-base font-semibold">Ingesta</h2>
            <div className="mt-3 space-y-3">
              <Field label="Nombre">
                <input
                  value={ingestForm.name}
                  onChange={(event) => setIngestForm((current) => ({ ...current, name: event.target.value }))}
                  className="min-h-[44px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </Field>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="Tipo">
                  <select
                    value={ingestForm.kind}
                    onChange={(event) => setIngestForm((current) => ({ ...current, kind: event.target.value }))}
                    className="min-h-[44px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    {SOURCE_KINDS.map((kind) => (
                      <option key={kind} value={kind}>{kind}</option>
                    ))}
                  </select>
                </Field>
                <Field label="Descripción">
                  <input
                    value={ingestForm.description}
                    onChange={(event) => setIngestForm((current) => ({ ...current, description: event.target.value }))}
                    className="min-h-[44px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  />
                </Field>
              </div>
              <Field label="Contenido">
                <textarea
                  value={ingestForm.content}
                  onChange={(event) => setIngestForm((current) => ({ ...current, content: event.target.value }))}
                  className="min-h-[220px] rounded-md border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </Field>
              <button
                type="button"
                onClick={submitIngest}
                disabled={ingest.isPending}
                className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
              >
                {ingest.isPending ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <UploadCloud aria-hidden className="h-4 w-4" />}
                Ingerir
              </button>
            </div>
          </section>

          <section className="rounded-lg border bg-card p-4 shadow-sm">
            <h2 className="text-base font-semibold">Reindexado</h2>
            <div className="mt-3 space-y-3">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="Origen">
                  <select
                    value={reindexForm.kind}
                    onChange={(event) => setReindexForm((current) => ({ ...current, kind: event.target.value as ReindexForm["kind"] }))}
                    className="min-h-[44px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <option value="dataset">dataset</option>
                    <option value="raw">raw</option>
                  </select>
                </Field>
                <Field label="Cartucho">
                  <select
                    value={reindexForm.cartridge}
                    onChange={(event) => setReindexForm((current) => ({ ...current, cartridge: event.target.value }))}
                    disabled={reindexForm.kind !== "raw"}
                    className="min-h-[44px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {CARTRIDGES.map((id) => (
                      <option key={id} value={id}>{id}</option>
                    ))}
                  </select>
                </Field>
              </div>
              <Field label="Nombre">
                <input
                  value={reindexForm.name}
                  onChange={(event) => setReindexForm((current) => ({ ...current, name: event.target.value }))}
                  className="min-h-[44px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </Field>
              <button
                type="button"
                onClick={submitReindex}
                disabled={reindex.isPending}
                className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border bg-background px-4 text-sm font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
              >
                {reindex.isPending ? <Loader2 aria-hidden className="h-4 w-4 animate-spin" /> : <RotateCcw aria-hidden className="h-4 w-4" />}
                Reindexar
              </button>
            </div>
          </section>
        </div>
      </section>
    </main>
  );
}

function normalizeSources(payload: RagSourcesPayload | RagSource[]): RagSource[] {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload.sources)) return payload.sources;
  if (Array.isArray(payload.result)) return payload.result;
  if (payload.result && Array.isArray(payload.result.sources)) return payload.result.sources;
  return [];
}

function parseKinds(value: string): string[] | undefined {
  const kinds = value
    .split(",")
    .map((kind) => kind.trim())
    .filter(Boolean);
  return kinds.length ? kinds : undefined;
}

function boundedTopK(value: string): number {
  const parsed = Number.parseInt(value, 10);
  if (!Number.isFinite(parsed)) return 5;
  return Math.min(20, Math.max(1, parsed));
}

function formatNumber(value: number | string): string {
  const numeric = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return new Intl.NumberFormat("es").format(numeric);
}

function formatDate(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function sourceId(source: RagSource, index: number): string {
  return String(source.id ?? source.name ?? `source-${index}`);
}

function MetricCard({
  icon: Icon,
  label,
  value,
}: {
  icon: LucideIcon;
  label: string;
  value: number | string;
}) {
  return (
    <article className="rounded-lg border bg-card p-4 shadow-sm">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{label}</span>
        <Icon aria-hidden className="h-4 w-4 text-muted-foreground" />
      </div>
      <strong className="mt-2 block text-3xl font-semibold tracking-tight">{formatNumber(value)}</strong>
    </article>
  );
}

function SourcesTable({ sources }: { sources: RagSource[] }) {
  if (!sources.length) {
    return (
      <p className="m-4 rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">
        Sin fuentes RAG.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-4 py-2 font-medium">Fuente</th>
            <th className="px-4 py-2 font-medium">Tipo</th>
            <th className="px-4 py-2 font-medium">Fragmentos</th>
            <th className="px-4 py-2 font-medium">Tamaño</th>
            <th className="px-4 py-2 font-medium">Creada</th>
          </tr>
        </thead>
        <tbody>
          {sources.map((source, index) => (
            <tr key={sourceId(source, index)} className="border-t">
              <td className="px-4 py-3 align-top">
                <div className="font-medium">{source.name || "-"}</div>
                <div className="mt-1 max-w-xl text-xs text-muted-foreground">{source.description || "-"}</div>
              </td>
              <td className="px-4 py-3 align-top">
                <span className="inline-flex rounded-full border bg-muted/30 px-2 py-0.5 text-xs font-medium">
                  {source.kind || "document"}
                </span>
              </td>
              <td className="px-4 py-3 align-top text-muted-foreground">{formatNumber(Number(source.chunk_count ?? 0))}</td>
              <td className="px-4 py-3 align-top text-muted-foreground">{formatNumber(Number(source.size_chars ?? 0))}</td>
              <td className="px-4 py-3 align-top text-xs text-muted-foreground">{formatDate(source.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function QueryResults({ output }: { output: QueryOutput }) {
  return (
    <div className="mt-4 space-y-3">
      {output.answer ? (
        <div className="rounded-md border bg-muted/30 p-4 text-sm leading-6">
          {output.answer}
        </div>
      ) : null}

      <div className="space-y-2">
        {output.results.map((result, index) => (
          <article key={`${result.source_id ?? "source"}:${result.parent_id ?? index}`} className="rounded-md border bg-background p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-sm font-semibold">{result.source_name || "Fuente"}</h3>
              <span className="text-xs text-muted-foreground">
                {result.similarity != null ? `${Math.round(result.similarity * 100)}%` : result.source_kind || "RAG"}
              </span>
            </div>
            <p className="mt-2 line-clamp-4 text-sm text-muted-foreground">
              {result.child_content || result.context || "-"}
            </p>
          </article>
        ))}
        {!output.results.length ? (
          <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">
            Sin resultados.
          </p>
        ) : null}
      </div>
    </div>
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

function SegmentedControl({
  value,
  onChange,
}: {
  value: QueryMode;
  onChange: (value: QueryMode) => void;
}) {
  return (
    <div className="inline-flex rounded-md border bg-background p-1">
      {(["ask", "search"] as const).map((mode) => (
        <button
          key={mode}
          type="button"
          onClick={() => onChange(mode)}
          className={cn(
            "min-h-[36px] rounded px-3 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            value === mode ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent/10",
          )}
        >
          {mode === "ask" ? "Preguntar" : "Buscar"}
        </button>
      ))}
    </div>
  );
}

function ErrorPanel({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div role="alert" className="m-4 rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
      <p className="font-medium text-destructive">{message}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-2 inline-flex min-h-[40px] items-center justify-center rounded-md border border-destructive/40 px-3 text-xs font-medium text-destructive hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40"
      >
        Reintentar
      </button>
    </div>
  );
}

function SkeletonRows({ rows }: { rows: number }) {
  return (
    <div aria-busy="true" className="space-y-2 p-4">
      {Array.from({ length: rows }).map((_, index) => (
        <span key={index} className="block h-14 animate-pulse rounded bg-muted" aria-hidden />
      ))}
    </div>
  );
}
