"use client";

import { ArrowRight, Boxes } from "lucide-react";

import { useSapB1Access } from "@/lib/sap-b1/hooks";

export function SapB1ControlRoomEntry() {
  const { installed } = useSapB1Access();
  if (!installed) return null;
  return (
    <a
      href="/control-room/sap-b1"
      className="mb-5 flex items-center justify-between gap-3 rounded-lg border bg-card px-4 py-3 text-sm shadow-sm transition hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
    >
      <span className="flex min-w-0 items-center gap-3">
        <Boxes aria-hidden className="h-5 w-5 shrink-0 text-primary" />
        <span className="min-w-0">
          <span className="block font-semibold text-foreground">SAP Business One</span>
          <span className="block text-muted-foreground">Puesta en marcha, cargas, indicadores, agentes y semáforo del día</span>
        </span>
      </span>
      <ArrowRight aria-hidden className="h-4 w-4 shrink-0 text-muted-foreground" />
    </a>
  );
}
