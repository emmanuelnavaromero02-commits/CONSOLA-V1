import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { CopilotActionsConsole } from "./CopilotActionsConsole";

let accessPermissions = ["operations.read", "control_room.write"];

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  useMutation: () => ({ mutate: vi.fn(), isPending: false }),
  useQuery: ({ queryKey }: { queryKey: string[] }) => {
    const name = queryKey.join(":");
    if (name === "me:access") {
      return {
        data: { permissions: accessPermissions },
        isLoading: false,
        refetch: vi.fn(),
      };
    }
    if (name.includes("goals")) {
      return {
        data: [{ id: "goal-1", goal_text: "Diagnosticar margen", status: "running", created_at: "2026-06-04T20:00:00Z" }],
        isLoading: false,
        refetch: vi.fn(),
      };
    }
    if (name.includes("briefing-v2")) {
      return {
        data: [{ id: "brief-1", title: "Riesgo de margen", severity: "high", summary: "Costos subiendo" }],
        isLoading: false,
        refetch: vi.fn(),
      };
    }
    if (name.includes("watchdogs")) {
      return {
        data: [{ id: "watch-1", slug: "revenue-drop", name: "Revenue drop", cartridge_id: "hubspot", risk_level: "high" }],
        isLoading: false,
        refetch: vi.fn(),
      };
    }
    if (name.includes("live-context")) {
      return {
        data: { status: "ready", sources: [] },
        isLoading: false,
        refetch: vi.fn(),
      };
    }
    if (name.includes("recommendations")) {
      return {
        data: [{ id: "rec-1", title: "Revisar permisos", status: "active" }],
        isLoading: false,
        refetch: vi.fn(),
      };
    }
    return {
      data: [{ id: "lesson-1", lesson_text: "Revisar descuentos", source_kind: "manual", confidence: 0.9 }],
      isLoading: false,
      refetch: vi.fn(),
    };
  },
}));

describe("CopilotActionsConsole", () => {
  it("renders critical copilot action surfaces from query data", () => {
    accessPermissions = ["operations.read", "control_room.write"];
    const markup = renderToStaticMarkup(<CopilotActionsConsole />);

    expect(markup).toContain("Acciones");
    expect(markup).toContain("Crear + diagnosticar");
    expect(markup).toContain("Buscar vigilancias");
    expect(markup).toContain("Analizar contexto");
    expect(markup).toContain("Mandar al chat");
    expect(markup).toContain("Contexto Vivo");
    expect(markup).toContain("Revisar permisos");
    expect(markup).toContain("Actualizar contexto");
    expect(markup).toContain("Descartar");
    expect(markup).toContain("Diagnosticar margen");
    expect(markup).toContain("Riesgo de margen");
    expect(markup).toContain("Revenue drop");
    expect(markup).toContain("Revisar descuentos");
  });

  it("keeps operational context read-only without control room write", () => {
    accessPermissions = ["operations.read"];

    const markup = renderToStaticMarkup(<CopilotActionsConsole />);

    expect(markup).toContain("Contexto Vivo");
    expect(markup).toContain("Revisar permisos");
    expect(markup).not.toContain("Actualizar contexto");
    expect(markup).not.toContain("Descartar");
  });

  it("lets a writer manage allowed recommendations without operations read", () => {
    accessPermissions = ["control_room.write"];

    const markup = renderToStaticMarkup(<CopilotActionsConsole />);

    expect(markup).toContain("Recomendaciones permitidas");
    expect(markup).toContain("Revisar permisos");
    expect(markup).toContain("Descartar");
    expect(markup).not.toContain("Contexto Vivo");
    expect(markup).not.toContain("Actualizar contexto");
  });

  it("does not request or expose operational controls to a viewer", () => {
    accessPermissions = [];

    const markup = renderToStaticMarkup(<CopilotActionsConsole />);

    expect(markup).toContain("Recomendaciones permitidas");
    expect(markup).toContain("Revisar permisos");
    expect(markup).not.toContain("Contexto Vivo");
    expect(markup).not.toContain("Actualizar contexto");
    expect(markup).not.toContain("Fuentes revisadas");
    expect(markup).not.toContain("Descartar");
  });
});
