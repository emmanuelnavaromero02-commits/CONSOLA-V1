import type { ReactNode } from "react";

import { OperationsSubNav } from "@/components/operations/OperationsSubNav";

/**
 * v1.44.4 Group 1 — Operations module layout.
 *
 * Wraps every /operations/* sub-route with a horizontal
 * sub-nav (Resumen / Usuarios / Auditoría / Vault) under the
 * global AppChrome navbar.
 */
export default function OperationsLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-[calc(100vh-56px)] flex-col">
      <OperationsSubNav />
      <div className="flex-1">{children}</div>
    </div>
  );
}
