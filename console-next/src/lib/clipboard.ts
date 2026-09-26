export async function copyText(value: string): Promise<void> {
  const clipboard = typeof navigator === "undefined" ? undefined : navigator.clipboard;
  if (!clipboard || typeof clipboard.writeText !== "function") {
    throw new Error("El portapapeles no está disponible en este navegador.");
  }
  await clipboard.writeText(value);
}
