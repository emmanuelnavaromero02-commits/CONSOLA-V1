import type { PipelineEntity } from "@/lib/monitor/types";
import { StatusPill } from "./StatusPill";

function countNodes(row: PipelineEntity): string {
  return `${row.silver.length} silver · ${row.gold.length} gold`;
}

export function PipelineTable({ rows }: { rows: PipelineEntity[] }) {
  if (!rows.length) {
    return (
      <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">
        No hay entidades para este cartucho.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border bg-card">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Entidad</th>
            <th className="px-3 py-2 font-medium">Bronze</th>
            <th className="px-3 py-2 font-medium">Watermark</th>
            <th className="px-3 py-2 font-medium">Última corrida</th>
            <th className="px-3 py-2 font-medium">Capas</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.cartridge}:${row.entity}`} className="border-t">
              <td className="px-3 py-2 align-top font-medium">{row.entity}</td>
              <td className="px-3 py-2 align-top">
                <div className="space-y-1">
                  <StatusPill status={row.bronze.status} />
                  <div className="text-xs text-muted-foreground">
                    {row.bronze.record_count ?? 0} filas · {row.bronze.latest_date || "sin fecha"}
                  </div>
                </div>
              </td>
              <td className="px-3 py-2 align-top font-mono text-xs text-muted-foreground">
                {row.watermark || "-"}
              </td>
              <td className="px-3 py-2 align-top">
                <div className="space-y-1">
                  <StatusPill status={row.last_run?.status || row.last_job?.status} />
                  <div className="text-xs text-muted-foreground">
                    {row.last_run?.finished_at || row.last_job?.finished_at || row.last_run?.started_at || "-"}
                  </div>
                </div>
              </td>
              <td className="px-3 py-2 align-top text-xs text-muted-foreground">{countNodes(row)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
