const FORBIDDEN_BUSINESS_LABEL_CHARACTER = /[\p{C}\u034F\u115F-\u1160\u17B4-\u17B5\u180B-\u180F\u2800\u3164\uA8F9\uFE00-\uFE0F\uFFA0\u{10AF6}\u{1144E}\u{11945}\u{11C44}-\u{11C45}\u{11F48}\u{13441}-\u{13442}\u{16FE4}\u{1BCA0}-\u{1BCA3}\u{1D173}-\u{1D17A}\u{E0100}-\u{E01EF}]/u;

function containsForbiddenCharacter(value: string): boolean {
  return FORBIDDEN_BUSINESS_LABEL_CHARACTER.test(value);
}

export function businessLabel(value: string | null | undefined): string | null {
  if (typeof value !== "string" || containsForbiddenCharacter(value)) return null;
  const normalized = value.normalize("NFKC");
  if (containsForbiddenCharacter(normalized)) return null;
  const display = normalized.trim();
  if (!display || display.toLowerCase() === "(sin nombre)") return null;
  return display;
}
