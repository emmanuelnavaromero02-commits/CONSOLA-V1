import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  default: ({ href, children, ...props }: { href: string; children: ReactNode }) => (
    <a href={href} {...props}>{children}</a>
  ),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

import {
  ConnectedSourceCard,
  connectionStatusFor,
  deriveTab,
  mergeConnectedSources,
  stepLabel,
  type ConnectedSource,
  type TabAccess,
} from "./MarketplaceConsole";

const FULL_ACCESS: TabAccess = {
  canConnected: true,
  canCatalog: true,
  canAdmin: true,
  hasConnected: false,
};

describe("deriveTab", () => {
  it("honors an explicit allowed tab param", () => {
    expect(deriveTab("conectadas", FULL_ACCESS)).toBe("conectadas");
    expect(deriveTab("catalogo", { ...FULL_ACCESS, hasConnected: true })).toBe("catalogo");
    expect(deriveTab("licencias", FULL_ACCESS)).toBe("licencias");
  });

  it("defaults to catalogo when nothing is connected", () => {
    expect(deriveTab(null, FULL_ACCESS)).toBe("catalogo");
    expect(deriveTab("", FULL_ACCESS)).toBe("catalogo");
    expect(deriveTab("bogus", FULL_ACCESS)).toBe("catalogo");
  });

  it("defaults to conectadas when the user has connected sources", () => {
    expect(deriveTab(null, { ...FULL_ACCESS, hasConnected: true })).toBe("conectadas");
  });

  it("falls back when the requested tab is not allowed", () => {
    expect(deriveTab("licencias", { ...FULL_ACCESS, canAdmin: false })).toBe("catalogo");
    expect(
      deriveTab("catalogo", { canConnected: true, canCatalog: false, canAdmin: false, hasConnected: false }),
    ).toBe("conectadas");
  });

  it("sends cartridges-only users to conectadas", () => {
    expect(
      deriveTab(null, { canConnected: true, canCatalog: false, canAdmin: false, hasConnected: false }),
    ).toBe("conectadas");
  });

  it("sends admin-only users to licencias", () => {
    expect(
      deriveTab(null, { canConnected: false, canCatalog: false, canAdmin: true, hasConnected: false }),
    ).toBe("licencias");
  });
});

describe("stepLabel", () => {
  it("translates every known installation step to Spanish", () => {
    expect(stepLabel("pending_admin_approval")).toBe("Pendiente de aprobación");
    expect(stepLabel("approved_ready")).toBe("Aprobada y lista");
    expect(stepLabel("reactivated_ready")).toBe("Reactivada y lista");
    expect(stepLabel("paused_by_admin")).toBe("Pausada por administración");
    expect(stepLabel("revoked_by_admin")).toBe("Revocada por administración");
    expect(stepLabel("retry_requested")).toBe("Reintento solicitado");
  });

  it("never leaks a raw machine step", () => {
    expect(stepLabel("some_new_step")).toBe("Sin información");
    expect(stepLabel(null)).toBe("Sin información");
    expect(stepLabel(undefined)).toBe("Sin información");
    expect(stepLabel("")).toBe("Sin información");
  });
});

describe("connectionStatusFor", () => {
  it("maps freshness to connection status like the legacy grid", () => {
    expect(connectionStatusFor(undefined)).toBe("unconfigured");
    expect(connectionStatusFor({ age_hours: null, status: "never" })).toBe("unconfigured");
    expect(connectionStatusFor({ age_hours: 30, status: "stale" })).toBe("stale");
    expect(connectionStatusFor({ age_hours: 120, status: "very_stale" })).toBe("very_stale");
    expect(connectionStatusFor({ age_hours: 1, status: "fresh" })).toBe("connected");
  });
});

describe("mergeConnectedSources", () => {
  it("unions technical cartridges and marketplace installations by cartridge id", () => {
    const rows = mergeConnectedSources({
      cartridgeIds: ["replicon", "hubspot"],
      installations: [
        { id: "i1", cartridge_id: "replicon", product_name: "Replicon Time", status: "ready" },
      ],
      products: [{ cartridge_id: "hubspot", name: "HubSpot CRM Plus" }],
      freshness: { replicon: { age_hours: 2, status: "fresh" } },
    });
    expect(rows).toHaveLength(2);
    const replicon = rows.find((row) => row.cartridgeId === "replicon");
    const hubspot = rows.find((row) => row.cartridgeId === "hubspot");
    expect(replicon?.installation?.id).toBe("i1");
    expect(replicon?.name).toBe("Replicon Time");
    expect(replicon?.connection).toBe("connected");
    expect(replicon?.ageHours).toBe(2);
    expect(hubspot?.name).toBe("HubSpot CRM Plus");
    expect(hubspot?.installation).toBeNull();
    expect(hubspot?.connection).toBe("unconfigured");
  });

  it("keeps installations whose cartridge is not in the technical list", () => {
    const rows = mergeConnectedSources({
      cartridgeIds: [],
      installations: [{ id: "i2", cartridge_id: "sap_b1" }],
      products: [],
      freshness: undefined,
    });
    expect(rows).toHaveLength(1);
    expect(rows[0].name).toBe("SAP Business One");
    expect(rows[0].installation?.id).toBe("i2");
  });

  it("does not duplicate a cartridge present in both lists", () => {
    const rows = mergeConnectedSources({
      cartridgeIds: ["banxico"],
      installations: [{ id: "i3", cartridge_id: "banxico" }],
      products: [],
      freshness: undefined,
    });
    expect(rows).toHaveLength(1);
  });
});

function card(overrides: Partial<ConnectedSource>, props?: Partial<Parameters<typeof ConnectedSourceCard>[0]>): string {
  const source: ConnectedSource = {
    cartridgeId: "replicon",
    name: "Replicon",
    description: null,
    installation: null,
    product: null,
    connection: "unconfigured",
    ageHours: null,
    ...overrides,
  };
  return renderToStaticMarkup(
    <ConnectedSourceCard
      source={source}
      canAdmin={false}
      canConfigure
      busyRetry={false}
      busyActivate={false}
      onRetry={() => {}}
      onActivate={() => {}}
      {...props}
    />,
  );
}

describe("ConnectedSourceCard freshness-to-status rendering", () => {
  it("does NOT show a stale source as Conectado", () => {
    const markup = card({ connection: "stale", ageHours: 30 });
    expect(markup).toContain("Datos antiguos");
    expect(markup).not.toContain("Conectado");
    expect(markup).not.toContain("Falló");
  });

  it("does NOT show a very_stale source as Falló", () => {
    const markup = card({ connection: "very_stale", ageHours: 120 });
    expect(markup).toContain("Datos muy antiguos");
    expect(markup).not.toContain("Falló");
    expect(markup).not.toContain("Conectado");
  });

  it("still shows fresh data as Conectado", () => {
    const markup = card({ connection: "connected", ageHours: 1 });
    expect(markup).toContain("Conectado");
    expect(markup).not.toContain("Datos antiguos");
  });

  it("shows never-extracted sources as Sin configurar", () => {
    const markup = card({ connection: "unconfigured" });
    expect(markup).toContain("Sin configurar");
    expect(markup).not.toContain("Conectado");
    expect(markup).not.toContain("Falló");
  });
});

describe("ConnectedSourceCard actions and copy", () => {
  it("links to the technical viewer when the user can configure", () => {
    expect(card({})).toContain("/cartridges/viewer?id=replicon");
  });

  it("hides the viewer link without cartridges access", () => {
    expect(card({}, { canConfigure: false })).not.toContain("/cartridges/viewer");
  });

  it("shows the retry action only when the installation allows it", () => {
    const retryable = card({
      installation: { id: "i1", cartridge_id: "replicon", can_retry: true, status: "failed" },
    });
    expect(retryable).toContain("Reintentar");
    const notRetryable = card({
      installation: { id: "i1", cartridge_id: "replicon", can_retry: false, status: "ready" },
    });
    expect(notRetryable).not.toContain("Reintentar");
  });

  it("offers Activar to admins only while the source is not active", () => {
    expect(card({}, { canAdmin: true })).toContain("Activar");
    expect(
      card(
        { installation: { id: "i1", cartridge_id: "replicon", status: "ready" } },
        { canAdmin: true },
      ),
    ).not.toContain("Activar");
    expect(card({})).not.toContain("Activar");
  });

  it("translates the installation step and never renders the raw value", () => {
    const markup = card({
      installation: { id: "i1", cartridge_id: "replicon", status: "requested", current_step: "retry_requested" },
    });
    expect(markup).toContain("Reintento solicitado");
    expect(markup).not.toContain("retry_requested");
    const unknown = card({
      installation: { id: "i1", cartridge_id: "replicon", status: "requested", current_step: "brand_new_step" },
    });
    expect(unknown).toContain("Sin información");
    expect(unknown).not.toContain("brand_new_step");
  });
});
