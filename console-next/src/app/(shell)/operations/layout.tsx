import type { ReactNode } from "react";

import { OperationsSubNav } from "@/components/operations/OperationsSubNav";

/**
 * v1.44.4 Group 1 — Operations module layout.
 *
 * Wraps every /operations/* sub-route with the module sub-nav.
 * Mobile keeps the compact global header; desktop uses the fixed
 * service sidebar and can fill the full viewport height.
 */
export default function OperationsLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-[calc(100vh-56px)] flex-col md:min-h-screen">
      <OperationsSubNav />
      <div className="flex-1">{children}</div>
    </div>
  );
}
