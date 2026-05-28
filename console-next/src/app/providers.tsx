"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { useEffect, useState, type ReactNode } from "react";

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
              const status = typeof error === "object" && error
                ? (
                    "status" in error && typeof error.status === "number"
                      ? error.status
                      : "response" in error
                        ? (error as { response?: { status?: number } }).response?.status
                        : undefined
                  )
                : undefined;
              if (status && status >= 400 && status < 500) return false;
              return failureCount < 1;
            },
          },
        },
      }),
  );

  useEffect(() => {
    const markToasts = () => {
      document.querySelectorAll("[data-sonner-toast]").forEach((toast) => {
        if (!toast.getAttribute("role")) {
          toast.setAttribute("role", "alert");
        }
      });
    };
    markToasts();
    const observer = new MutationObserver(markToasts);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);

  return (
    <QueryClientProvider client={client}>
      {children}
      {/* v1.44.3.3 Task H — a11y: force role="alert" on error toasts
          and role="status" on neutral toasts so screen readers
          announce them. sonner's default is role="status" on
          everything; toastOptions broadcasts the right role per
          variant. */}
      <Toaster
        position="top-right"
        richColors
        closeButton
        toastOptions={{
          classNames: {
            // Touch-target friendly close button on mobile.
            closeButton: "min-h-[24px] min-w-[24px]",
          },
        }}
      />
    </QueryClientProvider>
  );
}
