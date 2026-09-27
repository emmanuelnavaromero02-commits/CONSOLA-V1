import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

import {
  COUNCIL_ENDPOINT,
  actionCouncilSchema,
  approveCouncilProposal,
  councilProposalSchema,
  discardCouncilProposal,
  getActionCouncil,
  renewCouncilProposal,
} from "./council-client";

vi.mock("@/lib/api", () => ({ api: { get: vi.fn(), post: vi.fn() } }));

const apiGet = vi.mocked(api.get);
const apiPost = vi.mocked(api.post);
const handle = "b".repeat(64);

const personProposal = {
  proposal_id: handle,
  origin: "person",
  authored_by_you: false,
  decision_id: 41,
  title: "Empleado terminado activo",
  section_title: "Personas",
  severity: "high",
  observed_at: "2026-09-20T00:00:00Z",
  created_at: "2026-09-25T10:00:00Z",
  commitment_date: "2026-10-02",
  state: "pending_approval",
  impact: {
    kind: "money",
    value: 12600,
    currency: "USD",
    basis: "rule",
    formula: "monthly_cost_usd * 3 meses de exposicion",
    label: "Regla: costo mensual × 3 meses",
  },
  evidence: [{ label: "Entidad", value: "Ana Gómez" }],
  can_approve: true,
  can_discard: true,
  can_renew: false,
};

function response(data: unknown) {
  return { data, status: 200, headers: new Headers(), requestId: "request-1" };
}

beforeEach(() => {
  apiGet.mockReset();
  apiPost.mockReset();
});

describe("council contract", () => {
  it("reads the council once and accepts the strict payload", async () => {
    apiGet.mockResolvedValue(
      response({
        schema_version: "control-room-council/v1",
        generated_at: "2026-09-26T10:00:00Z",
        proposals: [personProposal],
      }),
    );
    const council = await getActionCouncil();
    expect(apiGet).toHaveBeenCalledTimes(1);
    expect(apiGet).toHaveBeenCalledWith(COUNCIL_ENDPOINT);
    expect(council.proposals[0].decision_id).toBe(41);
  });

  it("rejects unknown keys, item ids and invented impact figures", () => {
    const parse = (value: unknown) => councilProposalSchema.safeParse(value).success;
    expect(parse(personProposal)).toBe(true);
    expect(parse({ ...personProposal, item_id: "employees:1001" })).toBe(false);
    expect(parse({ ...personProposal, proposal_id: "employees:1001" })).toBe(false);
    expect(
      parse({ ...personProposal, impact: { kind: "none", value: 10, label: "Sin estimación" } }),
    ).toBe(false);
    expect(parse({ ...personProposal, impact: { kind: "none", label: "Aproximado" } })).toBe(false);
    expect(
      parse({ ...personProposal, impact: { ...personProposal.impact, formula: undefined } }),
    ).toBe(false);
    expect(
      parse({ ...personProposal, impact: { kind: "time", value: 3, basis: "observed", formula: "x", label: "y" } }),
    ).toBe(false);
    expect(
      parse({ ...personProposal, disabled_reason: "Requiere la aprobación de otra persona del equipo." }),
    ).toBe(false);
    expect(parse({ ...personProposal, disabled_reason: "Cualquier texto", can_approve: false })).toBe(false);
    expect(parse({ ...personProposal, origin: "system" })).toBe(false);
    expect(parse({ ...personProposal, decision_id: undefined })).toBe(false);
    expect(parse({ ...personProposal, state: "completed" })).toBe(false);
    expect(
      actionCouncilSchema.safeParse({
        schema_version: "control-room-council/v1",
        generated_at: "2026-09-26T10:00:00Z",
        proposals: Array.from({ length: 51 }, () => personProposal),
      }).success,
    ).toBe(false);
  });

  it("posts each command to the proposal path with the exact body", async () => {
    apiPost.mockResolvedValueOnce(
      response({
        status: "approved_with_followup",
        decision_id: 41,
        followup_created: true,
        message: "Decisión aprobada.",
      }),
    );
    await approveCouncilProposal(handle, "key-approve-1");
    expect(apiPost).toHaveBeenLastCalledWith(`${COUNCIL_ENDPOINT}/${handle}/approve`, {
      idempotency_key: "key-approve-1",
      confirm: true,
    });

    apiPost.mockResolvedValueOnce(
      response({ status: "discarded", message: "Propuesta descartada." }),
    );
    const discarded = await discardCouncilProposal(handle, "Motivo suficiente", "key-discard-1");
    expect(discarded.decision_id).toBeUndefined();
    expect(apiPost).toHaveBeenLastCalledWith(`${COUNCIL_ENDPOINT}/${handle}/discard`, {
      reason: "Motivo suficiente",
      idempotency_key: "key-discard-1",
    });

    apiPost.mockResolvedValueOnce(
      response({ status: "renewed", decision_id: 41, message: "Propuesta renovada." }),
    );
    await renewCouncilProposal(handle);
    expect(apiPost).toHaveBeenLastCalledWith(`${COUNCIL_ENDPOINT}/${handle}/renew`, {
      confirm: true,
    });
  });

  it("never sends a non-opaque proposal id and rejects unexpected responses", async () => {
    await expect(approveCouncilProposal("../items/1", "key-approve-1")).rejects.toThrow();
    expect(apiPost).not.toHaveBeenCalled();
    apiPost.mockResolvedValueOnce(
      response({ status: "approved_followup_failed", decision_id: 41, followup_created: false, message: "x" }),
    );
    await expect(approveCouncilProposal(handle, "key-approve-2")).rejects.toThrow();
  });
});
