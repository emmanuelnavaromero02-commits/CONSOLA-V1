import type { ReactNode } from "react";

export default function WorkspaceLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-[calc(100vh-56px)] min-h-[calc(100vh-56px)] flex-col bg-background md:h-screen md:min-h-screen">
      {children}
    </div>
  );
}
