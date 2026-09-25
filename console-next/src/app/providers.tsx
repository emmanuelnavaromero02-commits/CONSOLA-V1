"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { useEffect, useState, type ReactNode } from "react";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            retry: (failureCount, error: unknown) => {
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
      <Toaster
        position="top-right"
        richColors
        closeButton
        toastOptions={{
          classNames: {
            closeButton: "min-h-[24px] min-w-[24px]",
          },
        }}
      />
    </QueryClientProvider>
  );
}
