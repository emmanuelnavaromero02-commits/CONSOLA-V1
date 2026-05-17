import type { Metadata } from "next";
import "@/styles/globals.css";
import { Providers } from "./providers";
import { AppChrome } from "@/components/AppChrome";

export const metadata: Metadata = {
  title: "OMEGA Console",
  description: "Decision platform — operate cartridges, copilot and data.",
};

/**
 * v1.44.4 Group 1: AppChrome mounted globally. It hides itself
 * on /login / /forgot-password / /reset-password and on the
 * full-viewport /workspace surface (the chat layout takes the
 * whole screen and renders its own header).
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
