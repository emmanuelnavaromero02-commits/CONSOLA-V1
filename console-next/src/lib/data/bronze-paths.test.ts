import { describe, expect, it } from "vitest";

import {
  bronzeSourceToS3,
  normalizeBronzeSources,
  normalizeBronzeSql,
} from "./bronze-paths";

describe("Bronze source path normalization", () => {
  it("converts raw lakehouse paths into s3:// paths", () => {
    expect(bronzeSourceToS3("raw/sap_successfactors/PerPerson")).toBe(
      "s3://lakehouse/raw/sap_successfactors/PerPerson/**/*.parquet",
    );
  });

  it("keeps existing s3:// sources unchanged", () => {
    expect(bronzeSourceToS3("s3://modecissions-lakehouse-783792/raw/sap_successfactors/PerPerson")).toBe(
      "s3://modecissions-lakehouse-783792/raw/sap_successfactors/PerPerson",
    );
  });

  it("normalizes SQL reader literals before posting to the backend", () => {
    expect(
      normalizeBronzeSql("select * from read_parquet('raw/sap_successfactors/PerPerson') limit 50"),
    ).toBe("select * from read_parquet('s3://lakehouse/raw/sap_successfactors/PerPerson/**/*.parquet') limit 50");
  });

  it("deduplicates sources as canonical raw keys for backend scoping", () => {
    expect(
      normalizeBronzeSources([
        "raw/sap_successfactors/PerPerson",
        "s3://lakehouse/raw/sap_successfactors/PerPerson/**/*.parquet",
      ]),
    ).toEqual(["raw/sap_successfactors/PerPerson"]);
  });
});
