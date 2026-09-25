import type { ReactNode } from "react";

import { OperationsSubNav } from "@/components/operations/OperationsSubNav";

export default function OperationsLayout({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-[calc(100vh-56px)] flex-col md:min-h-screen">
      <OperationsSubNav />
      <div className="flex-1">{children}</div>
    </div>
  );
}
