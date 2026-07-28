// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ControlRoomExperienceV2 } from "@/lib/control-room/experience-contract";
import { controlRoomExperienceKey } from "@/lib/control-room/use-control-room-experience";
import { useControlRoomExperiencePreview } from "@/lib/control-room/use-control-room-experience-preview";

import { ControlRoomExperienceContent } from "./ControlRoomExperiencePage";
import { ExperiencePreviewFlow } from "./ExperiencePreviewFlow";

const clientBoundary = vi.hoisted(() => ({ preview: vi.fn() }));

vi.mock("@/lib/control-room/experience-client", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("@/lib/control-room/experience-client")
  >();
  return { ...actual, previewControlRoomExperienceAction: clientBoundary.preview };
});

const actionHandle = "c".repeat(64);
const success = {
  action_handle: actionHandle,
  operation: "preview" as const,
  status: "generated" as const,
  message: "Preview generado; no se ejecuto ningun cambio externo." as const,
};
const experience: ControlRoomExperienceV2 = {
  schema_version: "control-room-experience/v2",
  generated_at: "2026-07-25T12:30:00Z",
  sections: [
    {
      title: "Performance",
      facts: [
        {
          kind: "kpi",
          title: "Cobertura crítica",
          severity: "low",
          observed_at: "2026-07-24T00:00:00Z",
          stale: false,
          actions: [
            {
              action_handle: actionHandle,
              label: "Solicitar revisión de owner",
              enabled: true,
              requires_approval: true,
            },
          ],
        },
      ],
    },
  ],
};

let container: HTMLDivElement;
let root: Root;
let queryClient: QueryClient;

function Harness({ workspaceId }: { workspaceId: string }) {
  const preview = useControlRoomExperiencePreview(experience, workspaceId);
  return (
    <>
      <ControlRoomExperienceContent
        experience={experience}
        refreshing={false}
        refreshFailed={false}
        onRefresh={vi.fn()}
        onPreviewAction={preview.openPreview}
      />
      <ExperiencePreviewFlow {...preview} />
    </>
  );
}

async function renderWorkspace(workspaceId: string) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <Harness workspaceId={workspaceId} />
      </QueryClientProvider>,
    );
  });
}

function button(name: string) {
  return [...container.querySelectorAll("button")].find(
    (candidate) => candidate.textContent?.trim() === name,
  );
}

beforeEach(() => {
  clientBoundary.preview.mockReset();
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  queryClient.clear();
  container.remove();
});

describe("Experience preview isolation while submitting", () => {
  it("never publishes workspace A success after the UI changes to workspace B", async () => {
    let resolvePreview!: (value: typeof success) => void;
    clientBoundary.preview.mockReturnValue(
      new Promise((resolve) => {
        resolvePreview = resolve;
      }),
    );
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    await renderWorkspace("workspace-a");
    await act(async () => button("Generar preview")?.click());
    await act(async () => button("Confirmar preview")?.click());
    await renderWorkspace("workspace-b");

    expect(container.querySelector('[role="status"]')).toBeNull();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "La acción ya no está disponible.",
    );
    resolvePreview(success);
    await act(async () => undefined);

    expect(container.querySelector('[role="status"]')).toBeNull();
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: controlRoomExperienceKey("workspace-b"),
      exact: true,
      refetchType: "active",
    });
  });

  it("invalidates workspace A before a layout-commit resolution in workspace B", async () => {
    let resolvePreview!: (value: typeof success) => void;
    clientBoundary.preview.mockReturnValue(
      new Promise((resolve) => {
        resolvePreview = resolve;
      }),
    );
    await renderWorkspace("workspace-a");
    await act(async () => button("Generar preview")?.click());
    await act(async () => button("Confirmar preview")?.click());

    flushSync(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <Harness workspaceId="workspace-b" />
        </QueryClientProvider>,
      );
    });
    resolvePreview(success);
    await act(async () => Promise.resolve());

    expect(container.querySelector('[role="status"]')).toBeNull();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "La acción ya no está disponible.",
    );
  });

  it("prevents Tab from leaving the modal while the POST is pending", async () => {
    let resolvePreview!: (value: typeof success) => void;
    clientBoundary.preview.mockReturnValue(
      new Promise((resolve) => {
        resolvePreview = resolve;
      }),
    );
    await renderWorkspace("workspace-a");
    await act(async () => button("Generar preview")?.click());
    await act(async () => button("Confirmar preview")?.click());
    const dialog = container.querySelector<HTMLElement>('[role="dialog"]');
    expect(document.activeElement).toBe(dialog);

    const event = new KeyboardEvent("keydown", {
      key: "Tab",
      bubbles: true,
      cancelable: true,
    });
    expect(document.dispatchEvent(event)).toBe(false);
    expect(document.activeElement).toBe(dialog);
    resolvePreview(success);
    await act(async () => undefined);
  });

  it("refreshes only the active V2 workspace after a stale server response", async () => {
    clientBoundary.preview.mockRejectedValue(
      Object.assign(new Error("private backend detail"), { status: 404 }),
    );
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    await renderWorkspace("workspace-a");
    await act(async () => button("Generar preview")?.click());
    await act(async () => button("Confirmar preview")?.click());

    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "La acción ya no está disponible.",
    );
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: controlRoomExperienceKey("workspace-a"),
      exact: true,
      refetchType: "active",
    });
    expect(container.textContent).not.toContain("private backend detail");
  });
});
