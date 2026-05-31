"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

import { ChatLayout } from "@/components/workspace/ChatLayout";

function CopilotShell() {
  const params = useSearchParams();
  const prompt = params.get("prompt") ?? undefined;

  return (
    <div className="flex h-[calc(100vh-56px)] min-h-[calc(100vh-56px)] flex-col bg-background md:h-screen md:min-h-screen">
      <ChatLayout initialPrompt={prompt} actionsHref="/copilot/actions" />
    </div>
  );
}

export default function CopilotPage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Cargando copiloto...</div>}>
      <CopilotShell />
    </Suspense>
  );
}
