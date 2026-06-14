import type { ReactNode } from "react";

/**
 * Operations module layout.
 *
 * The per-module sub-nav was removed: it mixed config/admin surfaces with
 * unrelated ones (Vault, Workflows, Métricas) and duplicated the main
 * sidebar, which confused navigation. Each /operations/* page now renders
 * standalone and is reached from the global sidebar.
 */
export default function OperationsLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-[calc(100vh-56px)] flex-col md:min-h-screen">
      <div className="flex-1">{children}</div>
    </div>
  );
}
