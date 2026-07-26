"use client";

import { AlertTriangle, RefreshCcw } from "lucide-react";

import type { ControlRoomExperience } from "@/lib/control-room/experience-contract";
import { experienceErrorKind } from "@/lib/control-room/experience-presenter";
import { useControlRoomExperience } from "@/lib/control-room/use-control-room-experience";
import { cn } from "@/lib/utils";

import { ExperienceLoadState } from "./ExperienceLoadState";
import { ExperienceSection } from "./ExperienceSection";

export function ControlRoomExperienceContent({
  experience,
  refreshing,
  refreshFailed,
  onRefresh,
}: {
  experience: ControlRoomExperience;
  refreshing: boolean;
  refreshFailed: boolean;
  onRefresh: () => void;
}) {
  const sections = experience.sections.filter((section) => section.facts.length > 0);

  return (
    <>
      <div className="flex items-start justify-between gap-4 border-b pb-6">
        <div>
          <p className="text-sm font-medium text-primary">Control Room</p>
          <h1 className="mt-1 text-2xl font-semibold text-foreground">Experiencia empresarial</h1>
        </div>
        <button
          type="button"
          aria-label="Actualizar información empresarial"
          onClick={onRefresh}
          disabled={refreshing}
          className="inline-flex min-h-10 min-w-10 items-center justify-center rounded-md border bg-card text-foreground shadow-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-wait disabled:opacity-60"
        >
          <RefreshCcw aria-hidden className={cn("h-4 w-4", refreshing && "animate-spin")} />
        </button>
      </div>

      {refreshFailed ? (
        <div className="mt-5 flex items-center gap-2 border-l-2 border-warning bg-warning/10 px-4 py-3 text-sm text-foreground" role="status">
          <AlertTriangle aria-hidden className="h-4 w-4 shrink-0 text-warning" />
          No se pudo actualizar. Se mantiene la última información disponible.
        </div>
      ) : null}

      <div className="pt-7">
        {sections.length === 0 ? (
          <ExperienceLoadState state="empty" />
        ) : (
          sections.map((section, index) => (
            <ExperienceSection
              key={`${section.domain}:${section.title}:${index}`}
              section={section}
            />
          ))
        )}
      </div>
    </>
  );
}

export function ControlRoomExperiencePage() {
  const query = useControlRoomExperience();
  const retry = () => void query.refetch();

  return (
    <main className="mx-auto w-full max-w-[1600px] px-4 py-6 sm:px-6 lg:px-8" aria-label="Experiencia empresarial">
      {query.data ? (
        <ControlRoomExperienceContent
          experience={query.data}
          refreshing={query.isFetching}
          refreshFailed={query.isRefetchError}
          onRefresh={retry}
        />
      ) : query.isPending ? (
        <ExperienceLoadState state="loading" />
      ) : (
        <ExperienceLoadState state={experienceErrorKind(query.error)} onRetry={retry} />
      )}
    </main>
  );
}
