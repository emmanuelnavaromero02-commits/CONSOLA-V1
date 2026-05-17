import type { ReactNode } from "react";

/**
 * v1.44.4 Task A — workspace layout.
 *
 * Full-viewport flex shell so the ChatLayout can fill the
 * available height without fighting the global ``<main
 * className="mx-auto max-w-6xl">`` constraint used by
 * /dashboard and /cartridges. The chat surface NEEDS the full
 * viewport (sidebar + message list + input form), so this
 * layout opts out of the centred container.
 */
export default function WorkspaceLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-screen flex-col bg-background">
      {children}
    </div>
  );
}
