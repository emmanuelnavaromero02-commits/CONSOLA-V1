import { SettingsTable } from "@/components/settings/SettingsTable";

export default function SettingsPage() {
  return (
    <main className="mx-auto max-w-7xl space-y-6 px-6 py-8">
      <header className="space-y-2">
        <h1 className="text-3xl font-semibold tracking-tight">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Configuración del sistema con secretos enmascarados, revelado auditado y rotación.
        </p>
      </header>
      <SettingsTable />
    </main>
  );
}
