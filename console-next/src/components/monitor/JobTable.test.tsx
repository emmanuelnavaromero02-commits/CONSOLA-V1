import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { JobTable } from "./JobTable";
import type { JobRun } from "@/lib/monitor/types";

describe("JobTable", () => {
  it("renders an explicit empty state", () => {
    const markup = renderToStaticMarkup(<JobTable jobs={[]} />);

    expect(markup).toContain("No hay ejecuciones registradas.");
  });

  it("renders job metadata and encoded log links", () => {
    const jobs: JobRun[] = [
      {
        job_id: "job/42",
        status: "success",
        tool: "sap_hcm.extract",
        created_at: "2026-06-01T08:00:00Z",
      },
    ];

    const markup = renderToStaticMarkup(<JobTable jobs={jobs} />);

    expect(markup).toContain("job/42");
    expect(markup).toContain("success");
    expect(markup).toContain("sap_hcm.extract");
    expect(markup).toContain("/viewer?type=job&amp;id=job%2F42");
    expect(markup).toContain("Ver logs");
  });
});
