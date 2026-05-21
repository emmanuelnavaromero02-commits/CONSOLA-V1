import { AppChrome } from "@/components/AppChrome";
import { Providers } from "../providers";

/**
 * Authenticated console shell. Route groups keep public URLs stable
 * (/dashboard, /studio, /workspace, etc.) while avoiding this client
 * bundle on /login and other public endpoints.
 */
export default function ShellLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <Providers>
      <AppChrome>{children}</AppChrome>
    </Providers>
  );
}
