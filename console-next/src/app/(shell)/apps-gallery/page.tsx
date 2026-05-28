import { AppsGallery } from "@/components/apps/AppsGallery";

export default function AppsGalleryPage() {
  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-8">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Apps Analíticas</h1>
        <p className="text-sm text-muted-foreground">
          Aplicaciones publicadas por los flujos analíticos de OMEGA.
        </p>
      </header>
      <AppsGallery />
    </main>
  );
}
