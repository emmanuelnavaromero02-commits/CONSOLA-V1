"use client";

import { ArrowRight, Boxes } from "lucide-react";

import { useSapB1Access } from "@/lib/sap-b1/hooks";

const AREAS = [
  { hash: "finanzas", label: "Finanzas" },
  { hash: "ventas", label: "Ventas" },
  { hash: "compras", label: "Compras" },
] as const;

export function SapB1ControlRoomEntry() {
  const { installed } = useSapB1Access();
  if (!installed) return null;
  return (
    <div className="mb-5 rounded-lg border bg-card text-sm shadow-sm">
      <a
        href="/control-room/sap-b1"
        className="flex items-center justify-between gap-3 px-4 py-3 transition hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span className="flex min-w-0 items-center gap-3">
          <Boxes aria-hidden className="h-5 w-5 shrink-0 text-primary" />
          <span className="min-w-0">
            <span className="block font-semibold text-foreground">SAP Business One</span>
            <span className="block text-muted-foreground">Puesta en marcha, indicadores de Finanzas, Ventas y Compras, agentes y semáforo del día</span>
          </span>
        </span>
        <ArrowRight aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" />
      </a>
      <div className="flex flex-wrap gap-2 border-t px-4 py-2" aria-label="Áreas de SAP Business One">
        {AREAS.map((area) => (
          <a
            key={area.hash}
            href={`/control-room/sap-b1#${area.hash}`}
            className="inline-flex min-h-[28px] items-center rounded-full border bg-background px-3 text-xs font-semibold text-foreground transition hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {area.label}
          </a>
        ))}
      </div>
    </div>
  );
}
