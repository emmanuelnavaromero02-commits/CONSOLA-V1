import { cn } from "@/lib/utils";
import type { TestConnectionResult as ResultShape } from "@/lib/cartridges";

interface Props {
  result: ResultShape | null;
}

/**
 * Renders the outcome of POST /test_connection. The two states are
 * deliberately verbose (full message + latency) so a failed probe
 * surfaces exactly what the cartridge reported — operators told us
 * "Connection failed" with no detail was a productivity killer.
 */
export function TestConnectionResult({ result }: Props) {
  if (!result) return null;

  const tone = result.ok
    ? "border-success/40 bg-success/5 text-success"
    : "border-destructive/40 bg-destructive/5 text-destructive";

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn("rounded-md border p-4 text-sm", tone)}
    >
      <header className="flex items-center gap-2 font-semibold">
        <span aria-hidden>{result.ok ? "✅" : "❌"}</span>
        <span>{result.ok ? "Conexión exitosa" : "Conexión fallida"}</span>
      </header>
      <p className="mt-1 text-foreground">{result.message}</p>
      <p className="mt-1 text-xs text-muted-foreground">
        Latencia: {result.latency_ms} ms
      </p>
    </div>
  );
}
