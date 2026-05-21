import type { Metadata } from "next";
import "@/styles/globals.css";

export const metadata: Metadata = {
  title: "OMEGA Console",
  description: "Decision platform — operate cartridges, copilot and data.",
};

/**
 * Root layout stays intentionally lean so public/auth pages do not
 * load the authenticated console shell. The shell lives in the
 * `(shell)` route group, which preserves URLs while keeping /login
 * below the E2E performance budget.
 */
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="es" suppressHydrationWarning>
      <body className="min-h-screen bg-background font-sans antialiased">
        {children}
      </body>
    </html>
  );
}
