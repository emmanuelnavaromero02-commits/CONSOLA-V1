import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { SfTalentNineBoxCell, SfTalentRosterPayload } from "@/lib/control-room/types";

import { MaskedTalentRoster, NineBoxMatrix } from "./TalentControlRoom";

describe("TalentControlRoom native panels", () => {
  it("renders the 9-box matrix as native React buttons", () => {
    const cells: SfTalentNineBoxCell[] = [
      {
        box_id: "estrella",
        box_label: "Estrella",
        potential_band: "high",
        performance_band: "high",
        movement_action: "Sucesion",
        display_order: 1,
        employee_count: 2,
        ready_count: 2,
        blocked_count: 0,
        status: "ready",
      },
    ];

    const markup = renderToStaticMarkup(
      <NineBoxMatrix cells={cells} selectedBoxId="estrella" onSelect={vi.fn()} />,
    );

    expect(markup).toContain("Estrella");
    expect(markup).toContain("button");
    expect(markup).not.toContain("iframe");
    expect(markup).not.toContain("omega-9box.html");
  });

  it("renders only masked roster fields", () => {
    const payload: SfTalentRosterPayload = {
      dataset: "sap_successfactors_talent_9box",
      status: "ready",
      count: 1,
      box: {
        box_id: "estrella",
        box_label: "Estrella",
        potential_band: "high",
        performance_band: "high",
        movement_action: "Sucesion",
        display_order: 1,
      },
      roster: [
        {
          employee_key: "tal_abc123456789",
          display_name: "Colaborador 6789",
          role: "Manager",
          unit: "People",
          region: "Monterrey",
          readiness_status: "ready",
          box_id: "estrella",
          box_label: "Estrella",
          performance_band: "high",
          potential_band: "high",
          fit_band: "high",
          movement_age_bucket: "12-24m",
          data_status: "ready",
        },
      ],
      blockers: [],
    };

    const markup = renderToStaticMarkup(<MaskedTalentRoster payload={payload} loading={false} />);

    expect(markup).toContain("Colaborador 6789");
    expect(markup).toContain("tal_abc123456789");
    expect(markup).not.toContain("Ana Gomez");
    expect(markup).not.toContain("user_id");
    expect(markup).not.toContain("full_name");
  });
});
