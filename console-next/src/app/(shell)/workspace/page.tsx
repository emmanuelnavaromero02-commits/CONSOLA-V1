"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

import { ChatLayout } from "@/components/workspace/ChatLayout";

function WorkspaceShell() {
  const params = useSearchParams();
  return <ChatLayout initialPrompt={params.get("prompt") ?? undefined} />;
}

export default function WorkspacePage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Cargando workspace...</div>}>
      <WorkspaceShell />
    </Suspense>
  );
}
