"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Download, FolderOpen, RefreshCw, Trash2 } from "lucide-react";

import {
  deleteExplorerObject,
  getExplorerDownload,
  listExplorerBuckets,
  listExplorerObjects,
  type ExplorerBucket,
  type ExplorerObject,
} from "@/lib/admin-surfaces";

function bytes(value?: number | null): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(size < 10 && index > 0 ? 1 : 0)} ${units[index]}`;
}

function dateLabel(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 19).replace("T", " ");
  return date.toLocaleString("es-MX", { dateStyle: "medium", timeStyle: "short" });
}

function bucketName(bucket?: ExplorerBucket): string {
  if (!bucket) return "";
  return bucket.name || bucket.id;
}

export function ObjectExplorer() {
  const [bucketId, setBucketId] = useState("");
  const [prefix, setPrefix] = useState("");
  const [submittedPrefix, setSubmittedPrefix] = useState("");
  const [continuationToken, setContinuationToken] = useState<string | undefined>(undefined);
  const [message, setMessage] = useState("");

  const buckets = useQuery({
    queryKey: ["explorer", "buckets"],
    queryFn: listExplorerBuckets,
    staleTime: 60_000,
  });

  const selectedBucket = useMemo(() => {
    const rows = buckets.data?.buckets ?? [];
    return rows.find((bucket) => bucket.id === bucketId) ?? rows[0] ?? null;
  }, [bucketId, buckets.data]);

  const objects = useQuery({
    queryKey: ["explorer", "objects", bucketName(selectedBucket), submittedPrefix, continuationToken],
    queryFn: () => listExplorerObjects({
      bucket: bucketName(selectedBucket || undefined),
      prefix: submittedPrefix,
      continuationToken,
    }),
    enabled: Boolean(selectedBucket),
    staleTime: 10_000,
  });

  const removeObject = useMutation({
    mutationFn: ({ bucket, key }: { bucket: string; key: string }) => deleteExplorerObject(bucket, key),
    onSuccess: () => {
      setMessage("Objeto eliminado.");
      objects.refetch();
    },
    onError: () => setMessage("No se pudo eliminar el objeto."),
  });

  async function submitList(nextToken?: string) {
    setContinuationToken(nextToken);
    setSubmittedPrefix(prefix);
    setMessage("");
    await objects.refetch();
  }

  async function downloadObject(object: ExplorerObject) {
    if (!selectedBucket) return;
    setMessage("");
    try {
      const response = await getExplorerDownload(bucketName(selectedBucket), object.key);
      if (response.url) {
        window.open(response.url, "_blank", "noopener");
      } else {
        setMessage("El backend no devolvió URL de descarga.");
      }
    } catch {
      setMessage("No se pudo crear la URL de descarga.");
    }
  }

  function folderLabel(folder: string): string {
    return folder.replace(submittedPrefix, "") || folder;
  }

  if (buckets.isLoading) {
    return (
      <div aria-busy="true" className="space-y-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <span key={i} className="block h-14 animate-pulse rounded bg-muted" aria-hidden />
        ))}
      </div>
    );
  }

  if (buckets.isError) {
    return (
      <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
        <p className="font-medium text-destructive">No se pudo cargar Explorer.</p>
        <button
          type="button"
          onClick={() => buckets.refetch()}
          className="mt-2 inline-flex min-h-[44px] items-center gap-2 rounded-md border px-3 text-xs font-medium"
        >
          <RefreshCw aria-hidden className="h-4 w-4" />
          Reintentar
        </button>
      </div>
    );
  }

  const quicklinks = buckets.data?.quicklinks ?? [];
  const folders = objects.data?.folders ?? [];
  const rows = objects.data?.objects ?? [];

  return (
    <div className="space-y-5">
      <section className="space-y-4 rounded-lg border bg-card p-4" aria-label="Controles de Explorer">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-[240px_1fr_auto]">
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium">Bucket</span>
            <select
              value={selectedBucket?.id ?? ""}
              onChange={(event) => {
                setBucketId(event.target.value);
                setContinuationToken(undefined);
              }}
              className="min-h-[44px] rounded-md border bg-background px-3 text-sm"
            >
              {(buckets.data?.buckets ?? []).map((bucket) => (
                <option key={bucket.id} value={bucket.id}>
                  {bucket.label || bucket.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium">Prefijo</span>
            <input
              value={prefix}
              onChange={(event) => setPrefix(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") submitList();
              }}
              placeholder="tenant_id=.../workspace_id=..."
              className="min-h-[44px] rounded-md border bg-background px-3 text-sm"
            />
          </label>
          <button
            type="button"
            onClick={() => submitList()}
            disabled={!selectedBucket || objects.isFetching}
            className="inline-flex min-h-[44px] items-center justify-center gap-2 self-end rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            <RefreshCw aria-hidden className="h-4 w-4" />
            Listar
          </button>
        </div>

        {quicklinks.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {quicklinks.map((quick, index) => (
              <button
                key={`${quick.bucket ?? ""}-${quick.prefix ?? ""}-${index}`}
                type="button"
                onClick={() => {
                  if (quick.bucket) {
                    const found = buckets.data?.buckets.find((bucket) => bucket.name === quick.bucket || bucket.id === quick.bucket);
                    if (found) setBucketId(found.id);
                  }
                  setPrefix(quick.prefix ?? "");
                  setSubmittedPrefix(quick.prefix ?? "");
                  setContinuationToken(undefined);
                }}
                className="inline-flex min-h-[36px] items-center rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5"
              >
                {quick.label || quick.prefix || quick.bucket || `Ruta ${index + 1}`}
              </button>
            ))}
          </div>
        ) : null}
      </section>

      {message ? (
        <p role="status" className="rounded-md border bg-muted/30 p-3 text-sm text-muted-foreground">{message}</p>
      ) : null}

      <section className="rounded-lg border bg-card" aria-label="Objetos">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b p-4">
          <div>
            <h2 className="text-base font-semibold tracking-tight">
              s3://{bucketName(selectedBucket || undefined)}/{submittedPrefix}
            </h2>
            <p className="text-xs text-muted-foreground">
              {folders.length} carpetas · {rows.length} objetos
            </p>
          </div>
          {objects.data?.next_token ? (
            <button
              type="button"
              onClick={() => submitList(objects.data?.next_token ?? undefined)}
              className="inline-flex min-h-[44px] items-center rounded-md border px-3 text-xs font-medium hover:bg-accent/5"
            >
              Siguiente página
            </button>
          ) : null}
        </header>

        {objects.isFetching ? (
          <div aria-busy="true" className="space-y-2 p-4">
            {Array.from({ length: 5 }).map((_, i) => (
              <span key={i} className="block h-12 animate-pulse rounded bg-muted" aria-hidden />
            ))}
          </div>
        ) : objects.isError ? (
          <p role="alert" className="p-4 text-sm text-destructive">No se pudieron listar objetos.</p>
        ) : folders.length === 0 && rows.length === 0 ? (
          <p className="p-6 text-sm text-muted-foreground">No hay objetos en este prefijo.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[780px] text-sm">
              <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="px-4 py-2 font-medium">Key</th>
                  <th className="px-4 py-2 font-medium">Tamaño</th>
                  <th className="px-4 py-2 font-medium">Última modificación</th>
                  <th className="px-4 py-2 font-medium"></th>
                </tr>
              </thead>
              <tbody>
                {folders.map((folder) => (
                  <tr key={folder} className="border-t">
                    <td className="px-4 py-3">
                      <button
                        type="button"
                        onClick={() => {
                          setPrefix(folder);
                          setSubmittedPrefix(folder);
                          setContinuationToken(undefined);
                        }}
                        className="inline-flex min-h-[44px] items-center gap-2 font-mono text-xs font-semibold text-primary"
                      >
                        <FolderOpen aria-hidden className="h-4 w-4" />
                        {folderLabel(folder)}
                      </button>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">carpeta</td>
                    <td className="px-4 py-3 text-muted-foreground">—</td>
                    <td className="px-4 py-3"></td>
                  </tr>
                ))}
                {rows.map((object) => (
                  <tr key={object.key} className="border-t">
                    <td className="max-w-lg px-4 py-3 font-mono text-xs">{object.key.replace(submittedPrefix, "") || object.key}</td>
                    <td className="px-4 py-3 text-xs text-muted-foreground">{bytes(object.size)}</td>
                    <td className="px-4 py-3 text-xs text-muted-foreground">{dateLabel(object.last_modified)}</td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => downloadObject(object)}
                          className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md border hover:bg-accent/5"
                          aria-label={`Descargar ${object.key}`}
                        >
                          <Download aria-hidden className="h-4 w-4" />
                        </button>
                        <button
                          type="button"
                          onClick={() => selectedBucket && removeObject.mutate({ bucket: bucketName(selectedBucket), key: object.key })}
                          disabled={removeObject.isPending}
                          className="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-md border border-destructive/40 text-destructive hover:bg-destructive/10 disabled:opacity-50"
                          aria-label={`Borrar ${object.key}`}
                        >
                          <Trash2 aria-hidden className="h-4 w-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
