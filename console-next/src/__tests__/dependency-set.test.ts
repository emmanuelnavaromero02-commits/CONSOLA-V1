import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

const pkg = JSON.parse(readFileSync(new URL("../../package.json", import.meta.url), "utf8")) as {
  dependencies: Record<string, string>;
  devDependencies: Record<string, string>;
};

describe("console-next dependency set", () => {
  it("does not grow: Studio is built with the existing stack only", () => {
    expect(Object.keys(pkg.dependencies).sort()).toEqual([
      "@hookform/resolvers",
      "@tanstack/react-query",
      "clsx",
      "date-fns",
      "lucide-react",
      "next",
      "react",
      "react-dom",
      "react-hook-form",
      "react-textarea-autosize",
      "sonner",
      "tailwind-merge",
      "zod",
      "zustand",
    ]);
    expect(Object.keys(pkg.devDependencies).sort()).toEqual([
      "@types/node",
      "@types/react",
      "@types/react-dom",
      "@vitest/coverage-v8",
      "autoprefixer",
      "brace-expansion",
      "eslint",
      "eslint-config-next",
      "jsdom",
      "postcss",
      "tailwindcss",
      "typescript",
      "vitest",
    ]);
  });
});
