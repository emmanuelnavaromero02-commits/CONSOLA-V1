"use client";

import { useEffect } from "react";

import {
  clearPageContext,
  publishPageContext,
  type PageContextValue,
} from "./page-context";

export function usePageContextPublisher(
  ctx: Record<string, PageContextValue> | null | undefined,
): void {
  const serialized = ctx ? JSON.stringify(ctx) : null;

  useEffect(() => {
    if (!serialized) return undefined;
    publishPageContext(JSON.parse(serialized) as Record<string, PageContextValue>);
    return () => clearPageContext();
  }, [serialized]);
}
