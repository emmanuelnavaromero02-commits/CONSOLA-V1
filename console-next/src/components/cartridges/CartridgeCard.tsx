import Link from "next/link";
import { Calculator, Clock3, Package, TrendingUp, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { StatusBadge, type ConnectionStatus } from "./StatusBadge";

interface CartridgeCardProps {
  id:          string;
  name:        string;
  description: string;
  status:      ConnectionStatus;
  activating?: boolean;
  onActivate?: (id: string) => void;
}

const ICON_FOR_ID: Record<string, LucideIcon> = {
  replicon:            Clock3,
  sap_hcm:             Users,
  sap_s4hana:          Calculator,
  sap_successfactors:  TrendingUp,
};

/**
 * Single tile on the cartridges grid. Status badge derives from the
 * parent's known state and the primary CTA opens the static viewer
 * via query params so the exported build needs no dynamic route.
 */
export function CartridgeCard({
  id,
  name,
  description,
  status,
  activating = false,
  onActivate,
}: CartridgeCardProps) {
  const Icon = ICON_FOR_ID[id] ?? Package;

  return (
    <article className="flex min-h-48 flex-col gap-3 rounded-lg border bg-card p-5 shadow-sm transition-colors hover:border-primary/40">
      <header className="flex items-start justify-between gap-2">
        <span
          aria-hidden
          className="flex h-10 w-10 items-center justify-center rounded-md bg-primary/10 text-xl"
        >
          <Icon aria-hidden className="h-5 w-5" />
        </span>
        <StatusBadge status={status} />
      </header>

      <div className="space-y-1">
        <h3 className="text-base font-semibold leading-tight">{name}</h3>
        <p className="text-sm text-muted-foreground line-clamp-2">{description}</p>
      </div>

      <footer className="mt-auto flex flex-wrap items-center gap-2 pt-2">
        <Link
          href={`/cartridges/viewer?id=${encodeURIComponent(id)}`}
          className="inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-3 text-xs font-medium transition-colors hover:bg-accent/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          Configurar
        </Link>
        {onActivate ? (
          <button
            type="button"
            onClick={() => onActivate(id)}
            disabled={activating}
            className="inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
          >
            {activating ? "Activando..." : "Activar"}
          </button>
        ) : null}
      </footer>
    </article>
  );
}
