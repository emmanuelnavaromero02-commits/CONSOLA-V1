import { AppChrome } from "@/components/AppChrome";
import { Providers } from "../providers";

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
