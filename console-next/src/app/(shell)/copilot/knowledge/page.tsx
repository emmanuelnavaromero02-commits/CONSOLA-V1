"use client";

import { useMemo, useRef, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  FileText,
  Files,
  Layers3,
  Loader2,
  RefreshCw,
  RotateCcw,
  Search,
  Trash2,
  UploadCloud,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { toast } from "sonner";

import { ConfirmDialog } from "@/components/studio/ui";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const CARTRIDGES = ["sap_successfactors", "replicon", "hubspot", "sap_hcm", "sap_s4hana"] as const;
const SOURCE_KINDS = ["document", "schema"] as const;
const KIND_LABELS: Record<string, string> = {
  document: "Documento",
  schema: "Estructura de datos",
};
const MAX_UPLOAD_BYTES = 10 * 1024 * 1024;

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

interface UploadPayload {
  name: string;
  description: string;
  content: string;
  mime_type: string;
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
  cartridge: "sap_successfactors",
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
  const [uploadDescription, setUploadDescription] = useState("");
  const [reindexForm, setReindexForm] = useState<ReindexForm>(EMPTY_REINDEX);
  const [queryForm, setQueryForm] = useState<QueryForm>(EMPTY_QUERY);
  const [queryOutput, setQueryOutput] = useState<QueryOutput | null>(null);
  const [pendingDelete, setPendingDelete] = useState<RagSource | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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
      toast.success("Documento cargado.");
      setIngestForm(EMPTY_INGEST);
      queryClient.invalidateQueries({ queryKey: ["rag", "sources"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo cargar el documento."),
  });

  const uploadFile = useMutation({
    mutationFn: async (payload: UploadPayload) => {
      const { data } = await api.post<{ source_id?: number; error?: string }>(
        "/api/rag/ingest",
        {
          name: payload.name,
          description: payload.description,
          kind: "document",
          content: payload.content,
          mime_type: payload.mime_type,
        },
      );
      return data;
    },
    onSuccess: (data) => {
      if (data.error) {
        toast.error(data.error);
        return;
      }
      toast.success("Documento cargado.");
      setUploadDescription("");
      if (fileInputRef.current) fileInputRef.current.value = "";
      queryClient.invalidateQueries({ queryKey: ["rag", "sources"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo cargar el documento."),
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

  const removeSource = useMutation({
    mutationFn: async (source: RagSource) => {
      const id = deletableSourceId(source);
      if (id === null) throw new Error("La fuente no tiene un identificador numérico.");
      const { data } = await api.delete<Record<string, unknown>>(`/api/rag/sources/${id}`);
      return data;
    },
    onSuccess: (_data, source) => {
      toast.success(`Fuente ${source.name || source.id} borrada.`);
      queryClient.invalidateQueries({ queryKey: ["rag", "sources"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo borrar la fuente."),
    onSettled: () => setPendingDelete(null),
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
    onError: (error) => toast.error(error instanceof Error ? error.message : "No se pudo completar la consulta."),
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

  async function handleFileSelected(file: File | null) {
    if (!file) return;
    if (file.size > MAX_UPLOAD_BYTES) {
      toast.error("El archivo supera el máximo de 10 MB.");
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    const isPdf = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
    try {
      if (isPdf) {
        const content = await readFileAsBase64(file);
        uploadFile.mutate({
          name: file.name,
          description: uploadDescription.trim(),
          content,
          mime_type: "application/pdf",
        });
      } else {
        const content = await readFileAsText(file);
        uploadFile.mutate({
          name: file.name,
          description: uploadDescription.trim(),
          content,
          mime_type: "text/plain",
        });
      }
    } catch {
      toast.error("No se pudo leer el archivo.");
    }
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
          <h1 className="text-2xl font-semibold tracking-tight">Documentos y Políticas de la Empresa</h1>
          <p className="text-sm text-muted-foreground">
            Sube políticas, manuales y documentos; el Copiloto los usa al responder.
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

      <section className="grid grid-cols-1 gap-4 md:grid-cols-3" aria-label="Resumen de documentos">
        <MetricCard icon={Files} label="Documentos" value={metrics.sourceCount} />
        <MetricCard icon={Layers3} label="Fragmentos indexados" value={metrics.chunkCount} />
        <MetricCard icon={FileText} label="Caracteres" value={formatNumber(metrics.sizeChars)} />
      </section>

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(360px,0.85fr)]">
        <div className="space-y-4">
          <section className="rounded-lg border bg-card shadow-sm">
            <header className="flex flex-col gap-3 border-b px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-base font-semibold">Documentos</h2>
                <p className="text-xs text-muted-foreground">Documentos disponibles para el Copiloto.</p>
              </div>
              <select
                value={kindFilter}
                onChange={(event) => setKindFilter(event.target.value)}
                className="min-h-[40px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                aria-label="Tipo de fuente"
              >
                <option value="all">Todos</option>
                {SOURCE_KINDS.map((kind) => (
                  <option key={kind} value={kind}>{KIND_LABELS[kind] ?? kind}</option>
                ))}
              </select>
            </header>

            {sources.isError ? (
              <ErrorPanel message="No se pudieron cargar los documentos." onRetry={() => sources.refetch()} />
            ) : sources.isLoading ? (
              <SkeletonRows rows={5} />
            ) : (
              <SourcesTable sources={sources.data ?? []} onDelete={setPendingDelete} />
            )}
          </section>

          <section className="rounded-lg border bg-card p-4 shadow-sm">
            <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h2 className="text-base font-semibold">Prueba una pregunta</h2>
                <p className="text-xs text-muted-foreground">Verifica qué responde el Copiloto con estos documentos.</p>
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
                <span className="font-medium">Top K</span>
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
            <h2 className="text-base font-semibold">Subir documento</h2>
            <p className="mt-1 text-xs text-muted-foreground">PDF o texto plano, hasta 10 MB.</p>
            <div className="mt-3 space-y-3">
              <Field label="Descripción">
                <input
                  value={uploadDescription}
                  onChange={(event) => setUploadDescription(event.target.value)}
                  className="min-h-[44px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                />
              </Field>
              <Field label="Archivo">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="application/pdf,text/plain"
                  aria-label="Archivo del documento"
                  disabled={uploadFile.isPending}
                  onChange={(event) => handleFileSelected(event.target.files?.[0] ?? null)}
                  className="min-h-[44px] w-full rounded-md border bg-background px-3 py-2 text-sm file:mr-3 file:rounded-md file:border-0 file:bg-primary/10 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
                />
              </Field>
              {uploadFile.isPending ? (
                <p className="inline-flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
                  Cargando documento…
                </p>
              ) : null}
            </div>
          </section>

          <section className="rounded-lg border bg-card p-4 shadow-sm">
            <h2 className="text-base font-semibold">Pegar texto</h2>
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
                      <option key={kind} value={kind}>{KIND_LABELS[kind] ?? kind}</option>
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
                Guardar texto
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
                <Field label="Fuente de datos">
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
                Reindexar documentos
              </button>
            </div>
          </section>
        </div>
      </section>

      <ConfirmDialog
        open={Boolean(pendingDelete)}
        title="Borrar fuente"
        tone="danger"
        confirmLabel="Borrar fuente"
        pendingLabel="Borrando…"
        pending={removeSource.isPending}
        onConfirm={() => {
          if (pendingDelete) removeSource.mutate(pendingDelete);
        }}
        onCancel={() => setPendingDelete(null)}
        testId="delete-rag-source-dialog"
        description={`Se borrará «${pendingDelete?.name || pendingDelete?.id || ""}» y sus fragmentos del índice. Esta acción no se puede deshacer.`}
      />
    </main>
  );
}

function deletableSourceId(source: RagSource): number | null {
  const id = typeof source.id === "number" ? source.id : Number.parseInt(String(source.id ?? ""), 10);
  return Number.isInteger(id) && id > 0 && String(id) === String(source.id).trim() ? id : null;
}

function normalizeSources(payload: RagSourcesPayload | RagSource[]): RagSource[] {
  if (Array.isArray(payload)) return payload;
  if (Array.isArray(payload.sources)) return payload.sources;
  if (Array.isArray(payload.result)) return payload.result;
  if (payload.result && Array.isArray(payload.result.sources)) return payload.result.sources;
  return [];
}

function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () => {
      const value = String(reader.result ?? "");
      const separator = value.indexOf(",");
      resolve(separator >= 0 ? value.slice(separator + 1) : value);
    };
    reader.readAsDataURL(file);
  });
}

function readFileAsText(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error);
    reader.onload = () => resolve(String(reader.result ?? ""));
    reader.readAsText(file);
  });
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

function SourcesTable({ sources, onDelete }: { sources: RagSource[]; onDelete: (source: RagSource) => void }) {
  if (!sources.length) {
    return (
      <p className="m-4 rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">
        Sin documentos cargados.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-4 py-2 font-medium">Documento</th>
            <th className="px-4 py-2 font-medium">Tipo</th>
            <th className="px-4 py-2 font-medium">Fragmentos</th>
            <th className="px-4 py-2 font-medium">Tamaño</th>
            <th className="px-4 py-2 font-medium">Creada</th>
            <th className="px-4 py-2 text-right font-medium">Acciones</th>
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
                  {KIND_LABELS[source.kind || "document"] ?? source.kind}
                </span>
              </td>
              <td className="px-4 py-3 align-top text-muted-foreground">{formatNumber(Number(source.chunk_count ?? 0))}</td>
              <td className="px-4 py-3 align-top text-muted-foreground">{formatNumber(Number(source.size_chars ?? 0))}</td>
              <td className="px-4 py-3 align-top text-xs text-muted-foreground">{formatDate(source.created_at)}</td>
              <td className="px-4 py-3 text-right align-top">
                <button
                  type="button"
                  onClick={() => onDelete(source)}
                  disabled={deletableSourceId(source) === null}
                  aria-label={`Borrar fuente ${source.name || source.id || ""}`}
                  className="inline-flex min-h-[44px] items-center justify-center gap-2 rounded-md border border-destructive/40 px-3 text-xs font-medium text-destructive hover:bg-destructive/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-destructive/40 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <Trash2 aria-hidden className="h-4 w-4" />
                  Borrar fuente
                </button>
              </td>
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
                {result.similarity != null
                  ? `${Math.round(result.similarity * 100)}%`
                  : KIND_LABELS[result.source_kind || ""] ?? result.source_kind ?? "Documento"}
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
          {mode === "ask" ? "Responder" : "Buscar"}
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
