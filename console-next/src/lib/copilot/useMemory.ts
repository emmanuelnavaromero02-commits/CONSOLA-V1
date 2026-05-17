"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { createFact, deleteFact, listMemory, setPreference } from "./client";
import type { MemoryResponse } from "./types";

/**
 * v1.44.4 Task A — memory drawer hook.
 *
 * The drawer shows two collections from
 * GET /api/copilot/memory:
 *   - facts:       free-form key/value learned about the user
 *   - preferences: structured settings (tone, locale, target KPIs)
 *
 * Mutations (create / delete / set-preference) invalidate the
 * memory query so the drawer stays in sync after writes.
 */
export function useMemory() {
  const qc = useQueryClient();

  const memoryQuery = useQuery<MemoryResponse>({
    queryKey: ["copilot", "memory"],
    queryFn:  listMemory,
    staleTime: 60_000,
  });

  const createFactMutation = useMutation<
    unknown,
    Error,
    { key: string; value: string }
  >({
    mutationFn: ({ key, value }) => createFact(key, value),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["copilot", "memory"] }),
  });

  const deleteFactMutation = useMutation<unknown, Error, number>({
    mutationFn: (id) => deleteFact(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["copilot", "memory"] }),
  });

  const setPreferenceMutation = useMutation<
    unknown,
    Error,
    { key: string; value: string }
  >({
    mutationFn: ({ key, value }) => setPreference(key, value),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["copilot", "memory"] }),
  });

  return {
    memoryQuery,
    createFactMutation,
    deleteFactMutation,
    setPreferenceMutation,
  };
}
