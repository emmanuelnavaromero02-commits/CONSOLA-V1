"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { useState, type ReactNode } from "react";

/**
 * Client-side providers that wrap the entire app:
 *   - TanStack Query for data fetching + caching
 *   - sonner for toast notifications
 *
 * Created lazily in a useState so React strict-mode double-mount in
 * dev doesn't tear and re-mount the cache.
 */
export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            retry: (failureCount, error: unknown) => {
              // Don't retry 4xx — those are deterministic. Retry once
              // on 5xx / network.
              const status =
                typeof error === "object" && error && "response" in error
                  ? (error as { response?: { status?: number } }).response?.status
                  : undefined;
              if (status && status >= 400 && status < 500) return false;
              return failureCount < 1;
            },
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={client}>
      {children}
      <Toaster position="top-right" richColors closeButton />
    </QueryClientProvider>
  );
}
