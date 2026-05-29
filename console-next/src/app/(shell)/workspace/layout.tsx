import type { ReactNode } from "react";

/**
 * v1.44.4 Task A — workspace layout.
 *
 * The chat surface fills the available viewport. Mobile still has the
 * compact AppChrome header (~56 px); desktop now uses the fixed service
 * sidebar, so it can consume the full viewport height.
 *
 * The h-[calc(...)] also lets the inner ChatLayout's message
 * list scroll independently while the navbar stays sticky.
 */
export default function WorkspaceLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-[calc(100vh-56px)] min-h-[calc(100vh-56px)] flex-col bg-background md:h-screen md:min-h-screen">
      {children}
    </div>
  );
}
