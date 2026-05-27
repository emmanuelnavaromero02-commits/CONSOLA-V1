import Link from "next/link";

import type { JobRun } from "@/lib/monitor/types";
import { StatusPill } from "./StatusPill";

function fmt(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function JobTable({ jobs }: { jobs: JobRun[] }) {
  if (!jobs.length) {
    return (
      <p className="rounded-md border bg-muted/30 p-4 text-sm text-muted-foreground">
        No hay ejecuciones registradas.
      </p>
    );
  }

  return (
    <div className="overflow-x-auto rounded-lg border bg-card">
      <table className="w-full text-sm">
        <thead className="bg-muted/40 text-left text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Job</th>
            <th className="px-3 py-2 font-medium">Estado</th>
            <th className="px-3 py-2 font-medium">Tool</th>
            <th className="px-3 py-2 font-medium">Creado</th>
            <th className="px-3 py-2 font-medium">Detalle</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.job_id} className="border-t">
              <td className="px-3 py-2 align-top font-mono text-xs">{job.job_id}</td>
              <td className="px-3 py-2 align-top"><StatusPill status={job.status} /></td>
              <td className="px-3 py-2 align-top">{job.tool || "-"}</td>
              <td className="px-3 py-2 align-top text-xs text-muted-foreground">{fmt(job.created_at)}</td>
              <td className="px-3 py-2 align-top">
                <Link
                  href={`/viewer?type=job&id=${encodeURIComponent(job.job_id)}`}
                  className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-3 text-xs font-medium hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  Ver logs
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
