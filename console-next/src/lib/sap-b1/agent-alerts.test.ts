import { describe, expect, it } from "vitest";

import type { ControlAlert, ControlItem } from "@/lib/control-room/types";

import { AGENT_ALERT_STATE_LABELS, sapB1AgentAlertRows } from "./agent-alerts";

function alert(itemId: string | null, overrides: Partial<ControlAlert> = {}): ControlAlert {
  return { id: `alert:${itemId}`, item_id: itemId, kind: "", cartridge: "sap_b1", title: itemId, severity: "medium", status: "open", ...overrides };
}

function item(id: string, overrides: Partial<ControlItem> = {}): ControlItem {
  return { id, kind: "agent_alert", status: "open", ...overrides };
}

describe("sapB1AgentAlertRows", () => {
  it("keeps SAP Business One alerts with an item id, newest first and undated last", () => {
    const rows = sapB1AgentAlertRows(
      [
        alert("a"),
        alert("b"),
        alert("c"),
        alert("d"),
        alert(null),
        alert("x", { cartridge: "sap_successfactors" }),
      ],
      [
        item("a", { detected_at: "2026-09-01T00:00:00Z" }),
        item("c", { detected_at: "2026-09-10T00:00:00Z" }),
        item("d", { detected_at: "no es fecha" }),
      ],
    );
    expect(rows.map((row) => row.itemId)).toEqual(["c", "a", "b", "d"]);
    expect(rows[2].detectedAt).toBeNull();
  });

  it("reads the decision from the item status or its omega decision", () => {
    const rows = sapB1AgentAlertRows(
      [alert("a"), alert("b"), alert("c"), alert("d", { status: "snoozed" })],
      [
        item("a", { status: "decision_created" }),
        item("b", { status: "in_review", omega: { decision: { status: "decision_created", label: "Decision #3" } } }),
        item("c", { status: "open", omega: { decision: { status: "open", label: "Pendiente" } } }),
      ],
    );
    expect(rows.map((row) => [row.itemId, row.state, row.hasDecision])).toEqual([
      ["a", "decision", true],
      ["b", "decision", true],
      ["c", "open", false],
      ["d", "snoozed", false],
    ]);
    expect(AGENT_ALERT_STATE_LABELS.snoozed).toBe("Pospuesta");
  });

  it("applies the marks recorded in this session before the refetch lands", () => {
    const rows = sapB1AgentAlertRows([alert("a"), alert("b"), alert("c", { status: "whatever" })], [], {
      decided: new Set(["a"]),
      falsePositives: new Set(["b"]),
    });
    expect(rows.map((row) => [row.itemId, row.state, row.falsePositive])).toEqual([
      ["a", "decision", false],
      ["b", "false_positive", true],
      ["c", "open", false],
    ]);
  });

  it("falls back to the item title and a neutral label", () => {
    const rows = sapB1AgentAlertRows(
      [alert("a", { title: null, severity: null }), alert("b", { title: "" })],
      [item("a", { title: "Desde el ítem", severity: "low" })],
    );
    expect(rows.map((row) => [row.title, row.severity])).toEqual([
      ["Desde el ítem", "low"],
      ["Alerta sin título", "medium"],
    ]);
    expect(sapB1AgentAlertRows(null, null)).toEqual([]);
  });
});
