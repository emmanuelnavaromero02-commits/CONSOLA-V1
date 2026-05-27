"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";

import { ChatLayout } from "@/components/workspace/ChatLayout";

function CopilotShell() {
  const params = useSearchParams();
  const prompt = params.get("prompt") ?? undefined;

  return (
    <div
      className="flex flex-col bg-background"
      style={{
        height: "calc(100vh - 56px)",
        minHeight: "calc(100vh - 56px)",
      }}
    >
      <ChatLayout initialPrompt={prompt} />
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
