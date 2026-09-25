import Link from "next/link";
import { BarChart3, Boxes, Calculator, Clock3, Handshake, Landmark, Package, TrendingUp, Users } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { StatusBadge, type ConnectionStatus } from "./StatusBadge";

interface CartridgeCardProps {
  id:          string;
  name:        string;
  description: string;
  status:      ConnectionStatus;
  ageHours?:   number | null;
  activating?: boolean;
  onActivate?: (id: string) => void;
}

const ICON_FOR_ID: Record<string, LucideIcon> = {
  replicon:            Clock3,
  hubspot:             Handshake,
  banxico:             Landmark,
  inegi:               BarChart3,
  sec_edgar:           Landmark,
  sap_hcm:             Users,
  sap_s4hana:          Calculator,
  sap_successfactors:  TrendingUp,
  sap_b1:              Boxes,
};

export function CartridgeCard({
  id,
  name,
  description,
  status,
  ageHours = null,
  activating = false,
  onActivate,
}: CartridgeCardProps) {
  const Icon = ICON_FOR_ID[id] ?? Package;
  const viewerHref = `/cartridges/viewer?id=${encodeURIComponent(id)}`;

  return (
    <article className="group relative flex min-h-48 flex-col gap-3 rounded-lg border bg-card p-5 shadow-sm transition-colors hover:border-primary/40">
      <Link
        href={viewerHref}
        aria-label={`Configurar ${name}`}
        className="absolute inset-0 z-0 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="sr-only">Configurar {name}</span>
      </Link>

      <header className="pointer-events-none relative z-10 flex items-start justify-between gap-2">
        <span
          aria-hidden
          className="flex h-10 w-10 items-center justify-center rounded-md bg-primary/10 text-xl"
        >
          <Icon aria-hidden className="h-5 w-5" />
        </span>
        <StatusBadge status={status} ageHours={ageHours} />
      </header>

      <div className="pointer-events-none relative z-10 space-y-1">
        <h3 className="text-base font-semibold leading-tight">{name}</h3>
        <p className="text-sm text-muted-foreground line-clamp-2">{description}</p>
      </div>

      <footer className="pointer-events-none relative z-10 mt-auto flex flex-wrap items-center gap-2 pt-2">
        <span
          aria-hidden
          className="pointer-events-none inline-flex min-h-[44px] items-center justify-center rounded-md border bg-background px-3 text-xs font-medium transition-colors group-hover:bg-accent/5"
        >
          Configurar
        </span>
        {onActivate ? (
          <button
            type="button"
            onClick={() => onActivate(id)}
            disabled={activating}
            className="pointer-events-auto inline-flex min-h-[44px] items-center justify-center rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-60"
          >
            {activating ? "Activando..." : "Activar"}
          </button>
        ) : null}
      </footer>
    </article>
  );
}
