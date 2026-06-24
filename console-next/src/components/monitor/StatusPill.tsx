import { cn } from "@/lib/utils";

const OK = new Set(["done", "success", "fresh", "operational", "completed"]);
const WARN = new Set(["queued", "running", "partial", "empty", "stale", "degraded", "never", "unknown"]);
const BAD = new Set(["failed", "error", "very_stale", "offline"]);

export function StatusPill({ status }: { status?: string | null }) {
  const value = (status || "unknown").toLowerCase();
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full border px-2 py-0.5 text-[11px] font-medium",
        OK.has(value) && "border-success/30 bg-success/10 text-success",
        WARN.has(value) && "border-warning/30 bg-warning/10 text-warning",
        BAD.has(value) && "border-destructive/30 bg-destructive/10 text-destructive",
        !OK.has(value) && !WARN.has(value) && !BAD.has(value) && "border-border bg-muted text-muted-foreground",
      )}
    >
      {status || "unknown"}
    </span>
  );
}
