export type ResponseStyleId = "precision" | "creative";
export type ResponseStyleChoice = ResponseStyleId | "current";

export interface ResponseStyle {
  id: ResponseStyleId;
  label: string;
  hint: string;
  temperature: number;
}

export const PRECISION = 0.1;
export const CREATIVE = 0.8;

export const RESPONSE_STYLES: readonly ResponseStyle[] = [
  {
    id: "precision",
    label: "Máxima Precisión (Hechos exactos)",
    hint: "Respuestas consistentes y apegadas a los datos.",
    temperature: PRECISION,
  },
  {
    id: "creative",
    label: "Creativo y Exploratorio",
    hint: "Propone hipótesis y alternativas antes de concluir.",
    temperature: CREATIVE,
  },
];

export const CURRENT_STYLE_LABEL = "Equilibrado (valor actual)";

function same(a: number, b: number): boolean {
  return Math.abs(a - b) < 1e-9;
}

export function responseStyleFor(temperature: number | null | undefined): ResponseStyleChoice {
  const value = Number(temperature);
  if (!Number.isFinite(value)) return "current";
  return RESPONSE_STYLES.find((style) => same(style.temperature, value))?.id ?? "current";
}

export function temperatureFor(style: ResponseStyleId): number {
  return RESPONSE_STYLES.find((item) => item.id === style)?.temperature ?? PRECISION;
}
