"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Eye, KeyRound, RefreshCw, Save } from "lucide-react";

import {
  listSettings,
  revealSetting,
  rotateSetting,
  updateSetting,
  type SystemSetting,
} from "@/lib/admin-surfaces";

function displayValue(setting: SystemSetting, revealed?: string): string {
  if (revealed !== undefined) return revealed;
  if (setting.value === null || setting.value === undefined) return "";
  return String(setting.value);
}

export function SettingsTable() {
  const queryClient = useQueryClient();
  const [category, setCategory] = useState<string>("all");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [revealed, setRevealed] = useState<Record<string, string>>({});

  const settings = useQuery({
    queryKey: ["settings", category],
    queryFn: () => listSettings(category === "all" ? undefined : category),
    staleTime: 30_000,
  });

  const update = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) => updateSetting(key, value),
    onSuccess: (row) => {
      setDrafts((current) => ({ ...current, [row.key]: displayValue(row) }));
      queryClient.invalidateQueries({ queryKey: ["settings"] });
    },
  });

  const reveal = useMutation({
    mutationFn: revealSetting,
    onSuccess: (row) => {
      setRevealed((current) => ({ ...current, [row.key]: displayValue(row) }));
      setDrafts((current) => ({ ...current, [row.key]: displayValue(row) }));
    },
  });

  const rotate = useMutation({
    mutationFn: rotateSetting,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["settings"] }),
  });

  const categories = useMemo(() => {
    const values = new Set<string>();
    for (const item of settings.data ?? []) {
      if (item.category) values.add(item.category);
    }
    return ["all", ...Array.from(values).sort()];
  }, [settings.data]);

  if (settings.isLoading) {
    return (
      <div aria-busy="true" className="space-y-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <span key={i} className="block h-14 animate-pulse rounded bg-muted" aria-hidden />
        ))}
      </div>
    );
  }

  if (settings.isError) {
    return (
      <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-4 text-sm">
        <p className="font-medium text-destructive">No se pudo cargar la configuración.</p>
        <button
          type="button"
          onClick={() => settings.refetch()}
          className="mt-2 inline-flex min-h-[44px] items-center gap-2 rounded-md border px-3 text-xs font-medium"
        >
          <RefreshCw aria-hidden className="h-4 w-4" />
          Reintentar
        </button>
      </div>
    );
  }

  const rows = settings.data ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <label className="flex items-center gap-2 text-sm">
          <span className="font-medium">Categoría</span>
          <select
            value={category}
            onChange={(event) => setCategory(event.target.value)}
            className="min-h-[44px] rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {categories.map((item) => (
              <option key={item} value={item}>{item === "all" ? "Todas" : item}</option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={() => settings.refetch()}
          className="inline-flex min-h-[44px] items-center gap-2 rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <RefreshCw aria-hidden className="h-4 w-4" />
          Refrescar
        </button>
      </div>

      {rows.length === 0 ? (
        <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">
          No hay settings visibles para esta categoría.
        </p>
      ) : (
        <div className="overflow-x-auto rounded-lg border bg-card">
          <table className="w-full min-w-[980px] text-sm">
            <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="px-4 py-2 font-medium">Key</th>
                <th className="px-4 py-2 font-medium">Categoría</th>
                <th className="px-4 py-2 font-medium">Valor</th>
                <th className="px-4 py-2 font-medium">Descripción</th>
                <th className="px-4 py-2 font-medium">Acciones</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((setting) => {
                const value = drafts[setting.key] ?? displayValue(setting, revealed[setting.key]);
                const isSecret = Boolean(setting.is_secret);
                return (
                  <tr key={setting.key} className="border-t">
                    <td className="px-4 py-3 font-mono text-xs">{setting.key}</td>
                    <td className="px-4 py-3 text-xs text-muted-foreground">{setting.category || "general"}</td>
                    <td className="px-4 py-3">
                      <input
                        type={isSecret && !revealed[setting.key] ? "password" : "text"}
                        value={value}
                        onChange={(event) => setDrafts((current) => ({
                          ...current,
                          [setting.key]: event.target.value,
                        }))}
                        className="min-h-[44px] w-full rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      />
                    </td>
                    <td className="max-w-sm px-4 py-3 text-xs text-muted-foreground">
                      {setting.description || "—"}
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-2">
                        {isSecret ? (
                          <>
                            <button
                              type="button"
                              onClick={() => reveal.mutate(setting.key)}
                              disabled={reveal.isPending}
                              className="inline-flex min-h-[44px] items-center gap-2 rounded-md border px-3 text-xs font-medium hover:bg-accent/5 disabled:opacity-50"
                            >
                              <Eye aria-hidden className="h-4 w-4" />
                              Revelar
                            </button>
                            <button
                              type="button"
                              onClick={() => rotate.mutate(setting.key)}
                              disabled={rotate.isPending}
                              className="inline-flex min-h-[44px] items-center gap-2 rounded-md border px-3 text-xs font-medium hover:bg-accent/5 disabled:opacity-50"
                            >
                              <KeyRound aria-hidden className="h-4 w-4" />
                              Rotar
                            </button>
                          </>
                        ) : null}
                        <button
                          type="button"
                          onClick={() => update.mutate({ key: setting.key, value })}
                          disabled={update.isPending}
                          className="inline-flex min-h-[44px] items-center gap-2 rounded-md border bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                        >
                          <Save aria-hidden className="h-4 w-4" />
                          Guardar
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {update.isError || reveal.isError || rotate.isError ? (
        <p role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">
          La operación no se pudo completar. Revisa permisos y sesión.
        </p>
      ) : null}
    </div>
  );
}
