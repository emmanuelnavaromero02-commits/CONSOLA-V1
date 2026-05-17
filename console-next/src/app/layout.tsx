import type { Metadata } from "next";
import "@/styles/globals.css";
import { Providers } from "./providers";
import { AppChrome } from "@/components/AppChrome";

export const metadata: Metadata = {
  title: "OMEGA Console",
  description: "Decision platform — operate cartridges, copilot and data.",
};

/**
 * v1.44.4 Group 1: AppChrome is mounted exactly once here.
 * Providers only own client context (React Query + toasts), so
 * the authenticated shell cannot be duplicated by nested wrappers.
 */
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="es" suppressHydrationWarning>
      <body className="min-h-screen bg-background font-sans antialiased">
        <Providers>
          <AppChrome>{children}</AppChrome>
        </Providers>
      </body>
    </html>
  );
}
