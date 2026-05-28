"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";

import { AppsGallery } from "@/components/apps/AppsGallery";
import { DecisionsBoard } from "@/components/decisions/DecisionsBoard";
import { ChatLayout } from "@/components/workspace/ChatLayout";
import { cn } from "@/lib/utils";

type WorkspaceTab = "chat" | "apps" | "decisions";

const TABS: Array<{ id: WorkspaceTab; label: string; detail: string }> = [
  { id: "chat", label: "Chat", detail: "Asistente operativo" },
  { id: "apps", label: "Apps", detail: "Publicadas" },
  { id: "decisions", label: "Decisiones", detail: "Compromisos" },
];

function WorkspaceShell() {
  const params = useSearchParams();
  const [tab, setTab] = useState<WorkspaceTab>("chat");

  return (
    <div className="flex h-full min-h-0 flex-col bg-background">
      <header className="shrink-0 border-b bg-card px-4 py-3">
        <div className="mx-auto flex max-w-7xl flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <h1 className="text-lg font-semibold tracking-tight">Workspace</h1>
            <p className="text-xs text-muted-foreground">
              Chat, apps analíticas y decisiones en la misma superficie de trabajo.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            {TABS.map((item) => (
              <button
                key={item.id}
                type="button"
                onClick={() => setTab(item.id)}
                className={cn(
                  "min-h-[44px] rounded-md border px-3 text-left text-sm font-medium",
                  tab === item.id ? "border-primary bg-primary text-primary-foreground" : "bg-background hover:bg-accent/5",
                )}
              >
                <span className="block leading-tight">{item.label}</span>
                <span className={cn("block text-[10px] leading-tight", tab === item.id ? "text-primary-foreground/80" : "text-muted-foreground")}>
                  {item.detail}
                </span>
              </button>
            ))}
            <Link
              href="/dashboard"
              className="inline-flex min-h-[44px] items-center rounded-md border bg-background px-3 text-sm font-medium hover:bg-accent/5"
            >
              Panel
            </Link>
          </div>
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-hidden">
        {tab === "chat" ? (
          <ChatLayout initialPrompt={params.get("prompt") ?? undefined} />
        ) : (
          <main className="h-full overflow-auto px-6 py-6">
            <div className="mx-auto max-w-7xl">
              {tab === "apps" ? <AppsGallery /> : <DecisionsBoard />}
            </div>
          </main>
        )}
      </div>
    </div>
  );
}

export default function WorkspacePage() {
  return (
    <Suspense fallback={<div className="p-6 text-sm text-muted-foreground">Cargando workspace...</div>}>
      <WorkspaceShell />
    </Suspense>
  );
}
