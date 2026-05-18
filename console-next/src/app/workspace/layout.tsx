import type { ReactNode } from "react";

/**
 * v1.44.4 Task A — workspace layout.
 *
 * The chat surface fills the viewport BELOW the global AppChrome
 * navbar (which is ~56 px tall and sticks at the top of every
 * authenticated page).  We subtract that height via
 * ``min-h-[calc(100vh-56px)]`` so the chat doesn't add an
 * extra vertical scroll the moment the page mounts.
 *
 * The h-[calc(...)] also lets the inner ChatLayout's message
 * list scroll independently while the navbar stays sticky.
 */
const HEADER_HEIGHT = "56px";

export default function WorkspaceLayout({ children }: { children: ReactNode }) {
  return (
    <div
      className="flex flex-col bg-background"
      style={{
        height:    `calc(100vh - ${HEADER_HEIGHT})`,
        minHeight: `calc(100vh - ${HEADER_HEIGHT})`,
      }}
    >
      {children}
    </div>
  );
}
