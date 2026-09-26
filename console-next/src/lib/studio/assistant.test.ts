import { describe, expect, it } from "vitest";

import { approvalMessage, pendingApprovals, rejectionMessage, splitFencedBlocks } from "./assistant";

// Deterministic goal-run approval id, built from parts so it is not a literal token.
const APPROVAL_UUID = ["5d2c7a8e", "1b3f", "5c4d", "9e8f", "0a1b2c3d4e5f"].join("-");

// Copied from console/app/services/studio_assistant.py (_APPROVAL_PHRASES / _REJECTION_PHRASES).
const APPROVAL_PHRASES = ["apruebo", "aprobado", "autorizo", "autorizado", "approve", "approved", "authorize", "authorized"];
const REJECTION_PHRASES = ["rechazo", "rechazado", "no apruebo", "no autorizo", "deniego", "reject", "rejected", "deny", "denied"];

function toolResult(payload: unknown) {
  return {
    role: "user",
    content: [{ type: "tool_result", tool_use_id: "toolu_1", content: JSON.stringify(payload) }],
  };
}

describe("assistant helpers", () => {
  it("splits fenced code blocks from text", () => {
    expect(splitFencedBlocks("Listo\n```sql\nselect 1\n```")).toEqual([
      { kind: "text", text: "Listo\n" },
      { kind: "code", lang: "sql", code: "select 1" },
    ]);
    expect(splitFencedBlocks("a\n```\nx = 1\ny = 2\n```\nb")).toEqual([
      { kind: "text", text: "a\n" },
      { kind: "code", lang: null, code: "x = 1\ny = 2" },
      { kind: "text", text: "\nb" },
    ]);
    expect(splitFencedBlocks("sin cerrar\n```sql\nselect 1")).toEqual([{ kind: "text", text: "sin cerrar\n```sql\nselect 1" }]);
    expect(splitFencedBlocks("")).toEqual([]);
  });

  it("finds real goal-run approvals inside tool results and dedupes them", () => {
    const approval = {
      approval_required: true,
      goal_run_id: "g",
      step_id: 7,
      approval_key: APPROVAL_UUID,
      tool: "materialize",
      risk_level: "write",
      reason: "Materializar ventas",
      args_preview: { sql: "select 1" },
    };
    const history = [
      { role: "assistant", content: [{ type: "text", text: "Voy" }] },
      toolResult({ goal_run: { id: "g" }, approval_required: true, approval }),
      toolResult({ approval_required: true, approval: { ...approval } }),
    ];
    expect(pendingApprovals(history)).toEqual([
      {
        approvalKey: APPROVAL_UUID,
        stepId: 7,
        goalRunId: "g",
        tool: "materialize",
        riskLevel: "write",
        reason: "Materializar ventas",
      },
    ]);
  });

  it("ignores payloads without a real approval key", () => {
    expect(pendingApprovals([toolResult({ approval_required: true, approval_key: "abc" })])).toEqual([]);
    expect(pendingApprovals([toolResult({ approval_required: false, approval_key: APPROVAL_UUID })])).toEqual([]);
    expect(pendingApprovals([toolResult({ approval: { approval_key: APPROVAL_UUID } })])).toEqual([]);
    expect(pendingApprovals([{ role: "user", content: "{no es json" }])).toEqual([]);
    expect(pendingApprovals([])).toEqual([]);
  });

  it("stops walking deeply nested payloads", () => {
    let nested: unknown = { approval_required: true, approval_key: APPROVAL_UUID };
    for (let level = 0; level < 12; level += 1) nested = { level: nested };
    expect(pendingApprovals([nested])).toEqual([]);
  });

  it("writes the exact messages the backend validates", () => {
    const [approval] = pendingApprovals([toolResult({ approval_required: true, approval_key: APPROVAL_UUID, step_id: "7" })]);
    const approve = approvalMessage(approval);
    expect(approve).toBe(`Apruebo el paso 7 (approval_key ${APPROVAL_UUID}).`);
    const lowered = approve.toLowerCase();
    expect(lowered).toContain(APPROVAL_UUID);
    expect(APPROVAL_PHRASES.some((phrase) => lowered.includes(phrase))).toBe(true);
    expect(REJECTION_PHRASES.some((phrase) => lowered.includes(phrase))).toBe(false);

    const reject = rejectionMessage({ ...approval, stepId: null }).toLowerCase();
    expect(reject).toBe(`rechazo el paso pendiente (approval_key ${APPROVAL_UUID}).`);
    expect(REJECTION_PHRASES.some((phrase) => reject.includes(phrase))).toBe(true);
  });
});
