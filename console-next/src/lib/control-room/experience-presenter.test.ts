import { describe, expect, it } from "vitest";

import {
  decisionLabel,
  experienceErrorKind,
  formatMetric,
} from "./experience-presenter";

function apiError(status: number): Error & { status: number } {
  return Object.assign(new Error("sensitive backend detail"), { status });
}

describe("experience presenter", () => {
  it("maps errors to fixed safe states", () => {
    expect(experienceErrorKind(apiError(403))).toBe("forbidden");
    expect(experienceErrorKind(apiError(404))).toBe("not-found");
    expect(experienceErrorKind(apiError(500))).toBe("unavailable");
    expect(experienceErrorKind(new Error("network internals"))).toBe("unavailable");
  });

  it("formats values without scaling or defaulting", () => {
    expect(formatMetric({ name: "Tasa", kind: "percentage", value: 0 })).toBe("0%");
    expect(formatMetric({ name: "Monto", kind: "amount", value: 42.5, unit: "MXN" })).toBe(
      "42.5 MXN",
    );
  });

  it("exposes only approved decision labels", () => {
    expect(decisionLabel({ reference: 1, status: "decision_created" })).toBe(
      "Decisión registrada",
    );
    expect(decisionLabel({ reference: 2, status: "approved" })).toBe(
      "Decisión aprobada",
    );
    expect(decisionLabel({ reference: 3, status: "resolved" })).toBe(
      "Decisión resuelta",
    );
  });
});
