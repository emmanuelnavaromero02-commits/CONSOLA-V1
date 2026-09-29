"use client";

import { AlertTriangle, RefreshCcw } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, type ReactNode } from "react";

import { usePageContextPublisher } from "@/lib/copilot/use-page-context";

import type { ControlRoomExperienceV2 } from "@/lib/control-room/experience-contract";
import {
  experienceErrorKind,
  latestObservedAt,
} from "@/lib/control-room/experience-presenter";
import { useControlRoomExperience } from "@/lib/control-room/use-control-room-experience";
import {
  type OpenExperienceAction,
  useControlRoomExperienceAction,
} from "@/lib/control-room/use-control-room-experience-action";
import { useControlRoomLive } from "@/lib/control-room/use-control-room-live";
import { useControlRoomRefresh } from "@/lib/control-room/use-control-room-refresh";
import { cn } from "@/lib/utils";

import { ExperienceActionDialog } from "./ExperienceActionDialog";
import { ExperienceEmptyDiagnostic } from "./ExperienceEmptyDiagnostic";
import { ExperienceExceptions } from "./ExperienceExceptions";
import { ExperienceLiveBadge } from "./ExperienceLiveBadge";
import { ExperienceLoadState } from "./ExperienceLoadState";
import { ExperienceSection } from "./ExperienceSection";

export function ControlRoomExperienceContent({
  experience,
  refreshing,
  refreshFailed,
  onRefresh,
  onAction,
  checkedAt = null,
  liveOffline = false,
}: {
  experience: ControlRoomExperienceV2;
  refreshing: boolean;
  refreshFailed: boolean;
  onRefresh: () => void;
  onAction: OpenExperienceAction;
  checkedAt?: number | null;
  liveOffline?: boolean;
}) {
  const sections = experience.sections.filter((section) => section.facts.length > 0);

  return (
    <>
      <div className="flex items-start justify-between gap-4 border-b pb-6">
        <div>
          <p className="text-sm font-medium text-primary">Control Room</p>
          <h1 className="mt-1 text-2xl font-semibold text-foreground">Experiencia empresarial</h1>
          <ExperienceLiveBadge
            checkedAt={checkedAt}
            offline={liveOffline}
            sourceObservedAt={latestObservedAt(experience)}
          />
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
          <ExperienceEmptyDiagnostic />
        ) : (
          sections.map((section, index) => (
            <ExperienceSection
              key={`${section.title}:${index}`}
              section={section}
              onAction={onAction}
            />
          ))
        )}
        <ExperienceExceptions exceptions={experience.exceptions ?? []} onAction={onAction} />
      </div>
    </>
  );
}

export function ControlRoomExperiencePage({ entries }: { entries?: ReactNode } = {}) {
  const router = useRouter();
  const query = useControlRoomExperience();
  const action = useControlRoomExperienceAction(query.data, query.workspaceId, router.push);
  const live = useControlRoomLive({
    workspaceId: query.workspaceId,
    experience: query,
    paused: action.dialogOpen,
  });
  const persisted = useControlRoomRefresh({
    workspaceId: query.workspaceId,
    refetchOnly: live.refreshAll,
  });
  const retry = live.refreshAll;

  const publishedContext = useMemo(() => {
    if (!query.data) return null;
    const sections = query.data.sections.filter((section) => section.facts.length > 0);
    const factTitles = sections
      .flatMap((section) => section.facts.map((fact) => fact.title))
      .slice(0, 5);
    return {
      surface: "control-room",
      route: "/control-room",
      section_count: sections.length,
      fact_titles: factTitles.join(" | "),
    };
  }, [query.data]);
  usePageContextPublisher(publishedContext);

  return (
    <main className="mx-auto w-full max-w-[1600px] px-4 py-6 sm:px-6 lg:px-8" aria-label="Experiencia empresarial">
      {entries}
      {query.data ? (
        <ControlRoomExperienceContent
          experience={query.data}
          refreshing={query.isFetching || persisted.refreshing}
          refreshFailed={query.isRefetchError || persisted.failed}
          onRefresh={persisted.refresh}
          onAction={action.openAction}
          checkedAt={live.checkedAt}
          liveOffline={live.offline}
        />
      ) : query.isPending ? (
        <ExperienceLoadState state="loading" />
      ) : (
        <ExperienceLoadState state={experienceErrorKind(query.error)} onRetry={retry} />
      )}
      <ExperienceActionDialog {...action} />
    </main>
  );
}
