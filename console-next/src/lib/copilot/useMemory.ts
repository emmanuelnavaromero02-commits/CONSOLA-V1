"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { createFact, deleteFact, listMemory, setPreference } from "./client";
import type { MemoryResponse } from "./types";

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
    { fact: string; source?: string }
  >({
    mutationFn: ({ fact, source }) => createFact(fact, source),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ["copilot", "memory"] }),
  });

  const deleteFactMutation = useMutation<unknown, Error, number>({
    mutationFn: (id) => deleteFact(id),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ["copilot", "memory"] }),
  });

  const setPreferenceMutation = useMutation<
    unknown,
    Error,
    { key: string; value: string }
  >({
    mutationFn: ({ key, value }) => setPreference(key, value),
    onSuccess:  () => qc.invalidateQueries({ queryKey: ["copilot", "memory"] }),
  });

  return {
    memoryQuery,
    createFactMutation,
    deleteFactMutation,
    setPreferenceMutation,
  };
}
