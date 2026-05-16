import Link from "next/link";
import { cn } from "@/lib/utils";

interface FreshnessRow {
  cartridge: string;
  ageHours:  number | null;
  status:    "fresh" | "stale" | "very_stale" | "never";
}

interface FreshnessTableProps {
  rows:    FreshnessRow[];
  loading: boolean;
}

const STATUS_LABEL = {
  fresh:      "Fresca",
  stale:      "Antigua",
  very_stale: "Muy antigua",
  never:      "Nunca",
} as const;

const STATUS_BADGE = {
  fresh:      "bg-success/15 text-success",
  stale:      "bg-warning/15 text-warning",
  very_stale: "bg-destructive/15 text-destructive",
  never:      "bg-muted text-muted-foreground",
} as const;

function formatAge(ageHours: number | null): string {
  if (ageHours === null) return "—";
  if (ageHours < 1)  return "< 1 h";
  if (ageHours < 48) return `${Math.round(ageHours)} h`;
  return `${Math.round(ageHours / 24)} d`;
}

/**
 * "Frescura de datos" — one row per cartridge, click navigates to
 * /cartridges/<id>. Loading state renders three skeleton rows so the
 * table's height stays stable across polls.
 */
export function FreshnessTable({ rows, loading }: FreshnessTableProps) {
  return (
    <div className="rounded-lg border bg-card shadow-sm">
      <header className="border-b px-5 py-3">
        <h2 className="text-sm font-semibold tracking-tight">Frescura de datos</h2>
      </header>
      <table className="w-full text-sm">
        <thead className="text-left text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-5 py-2 font-medium">Cartucho</th>
            <th className="px-5 py-2 font-medium">Última extracción</th>
            <th className="px-5 py-2 font-medium">Estado</th>
          </tr>
        </thead>
        <tbody>
          {loading
            ? Array.from({ length: 3 }).map((_, i) => (
                <tr key={i} className="border-t">
                  <td className="px-5 py-3">
                    <span className="block h-4 w-32 animate-pulse rounded bg-muted" />
                  </td>
                  <td className="px-5 py-3">
                    <span className="block h-4 w-16 animate-pulse rounded bg-muted" />
                  </td>
                  <td className="px-5 py-3">
                    <span className="block h-4 w-20 animate-pulse rounded bg-muted" />
                  </td>
                </tr>
              ))
            : rows.map((row) => (
                <tr
                  key={row.cartridge}
                  className="cursor-pointer border-t transition-colors hover:bg-accent/5"
                >
                  <td className="px-5 py-3">
                    <Link
                      href={`/cartridges/${row.cartridge}`}
                      className="font-medium hover:underline"
                    >
                      {row.cartridge}
                    </Link>
                  </td>
                  <td className="px-5 py-3 text-muted-foreground">
                    {formatAge(row.ageHours)}
                  </td>
                  <td className="px-5 py-3">
                    <span
                      className={cn(
                        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
                        STATUS_BADGE[row.status],
                      )}
                    >
                      {STATUS_LABEL[row.status]}
                    </span>
                  </td>
                </tr>
              ))}
        </tbody>
      </table>
    </div>
  );
}
