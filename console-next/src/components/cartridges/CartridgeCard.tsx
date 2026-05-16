import Link from "next/link";
import { StatusBadge, type ConnectionStatus } from "./StatusBadge";

interface CartridgeCardProps {
  id:          string;
  name:        string;
  description: string;
  status:      ConnectionStatus;
}

const ICON_FOR_ID: Record<string, string> = {
  replicon:            "⏱",
  sap_hcm:             "👥",
  sap_s4hana:          "🧮",
  sap_successfactors:  "📈",
};

/**
 * Single tile on the cartridges grid. Status badge derives from
 * the parent's known state (credentials present + last test_connection
 * result). Click → /cartridges/[id] detail page.
 */
export function CartridgeCard({ id, name, description, status }: CartridgeCardProps) {
  return (
    <Link
      href={`/cartridges/${id}`}
      className="group flex flex-col gap-3 rounded-lg border bg-card p-5 shadow-sm transition-colors hover:border-primary/50 hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <header className="flex items-start justify-between gap-2">
        <span
          aria-hidden
          className="flex h-10 w-10 items-center justify-center rounded-md bg-primary/10 text-xl"
        >
          {ICON_FOR_ID[id] ?? "🧩"}
        </span>
        <StatusBadge status={status} />
      </header>

      <div className="space-y-1">
        <h3 className="text-base font-semibold leading-tight">{name}</h3>
        <p className="text-sm text-muted-foreground line-clamp-2">{description}</p>
      </div>

      <footer className="mt-auto flex items-center justify-between pt-2 text-xs text-muted-foreground">
        <span>Configurar</span>
        <span aria-hidden className="transition-transform group-hover:translate-x-0.5">→</span>
      </footer>
    </Link>
  );
}
