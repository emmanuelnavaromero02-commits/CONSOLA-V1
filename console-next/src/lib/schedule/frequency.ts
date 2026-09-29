import { dailyParts } from "@/lib/control-room/wisdom-bit-monitors";

export type FrequencyPresetId = "manual" | "hourly" | "daily_morning" | "end_of_day" | "weekly_monday";
export type FrequencyChoice = FrequencyPresetId | "custom";

export interface FrequencyPreset {
  id: FrequencyPresetId;
  label: string;
  hint: string;
  summary: string;
  cron: string | null;
}

export const FREQUENCY_PRESETS: readonly FrequencyPreset[] = [
  {
    id: "manual",
    label: "Bajo Demanda (solo manual)",
    hint: "Solo se ejecuta cuando alguien lo inicia.",
    summary: "Bajo demanda",
    cron: null,
  },
  {
    id: "hourly",
    label: "Cada hora",
    hint: "Al inicio de cada hora, todos los días.",
    summary: "Cada hora",
    cron: "0 * * * *",
  },
  {
    id: "daily_morning",
    label: "Diario a primera hora (08:00)",
    hint: "Todos los días a las 08:00.",
    summary: "Diario a primera hora · 08:00",
    cron: "0 8 * * *",
  },
  {
    id: "end_of_day",
    label: "Al finalizar la jornada laboral (19:00)",
    hint: "De lunes a viernes a las 19:00.",
    summary: "Al finalizar la jornada laboral · 19:00, lunes a viernes",
    cron: "0 19 * * 1-5",
  },
  {
    id: "weekly_monday",
    label: "Semanal (Lunes por la mañana)",
    hint: "Cada lunes a las 08:00.",
    summary: "Semanal, lunes · 08:00",
    cron: "0 8 * * 1",
  },
];

export const CUSTOM_FREQUENCY_LABEL = "Programación personalizada (avanzado)";

export function normalizeCron(cron: string | null | undefined): string {
  return (cron ?? "").trim().split(/\s+/).filter(Boolean).join(" ");
}

export function presetById(id: FrequencyPresetId): FrequencyPreset {
  return FREQUENCY_PRESETS.find((preset) => preset.id === id) ?? FREQUENCY_PRESETS[0];
}

export function presetFromCron(cron: string | null | undefined): FrequencyChoice {
  const value = normalizeCron(cron);
  if (!value) return "manual";
  return FREQUENCY_PRESETS.find((preset) => preset.cron === value)?.id ?? "custom";
}

const TIME_ZONE_NAMES: Record<string, string> = {
  UTC: "UTC",
  "America/Mexico_City": "Ciudad de México",
  "America/Monterrey": "Monterrey",
  "America/Tijuana": "Tijuana",
  "America/Cancun": "Cancún",
  "America/Bogota": "Bogotá",
  "America/Lima": "Lima",
  "America/Santiago": "Santiago de Chile",
  "America/Argentina/Buenos_Aires": "Buenos Aires",
  "America/Sao_Paulo": "São Paulo",
  "America/Caracas": "Caracas",
  "America/Guatemala": "Guatemala",
  "America/Costa_Rica": "Costa Rica",
  "America/Panama": "Panamá",
  "America/New_York": "Nueva York",
  "Europe/Madrid": "Madrid",
};

export const COMMON_TIME_ZONES: readonly string[] = Object.keys(TIME_ZONE_NAMES);

export function timeZoneName(timeZone: string | null | undefined): string {
  const zone = (timeZone ?? "").trim() || "UTC";
  return TIME_ZONE_NAMES[zone] ?? zone;
}

export function isValidTimeZone(timeZone: string | null | undefined): boolean {
  const zone = (timeZone ?? "").trim();
  if (!zone) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

export function defaultTimeZone(): string {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return zone && isValidTimeZone(zone) ? zone : "UTC";
  } catch {
    return "UTC";
  }
}

export function timeZoneOptions(...extra: Array<string | null | undefined>): string[] {
  const zones = [...COMMON_TIME_ZONES];
  for (const zone of extra) {
    const value = (zone ?? "").trim();
    if (value && !zones.includes(value)) zones.push(value);
  }
  return zones;
}

const CRON_FIELD = /^[0-9A-Za-z*/,?#-]+$/;

export function customScheduleError(cron: string | null | undefined): string | null {
  const value = normalizeCron(cron);
  if (!value) return "Escribe la programación o elige una de las frecuencias.";
  if (/^@(hourly|daily|weekly|monthly|yearly|annually|midnight)$/.test(value)) return null;
  const fields = value.split(" ");
  if (fields.length !== 5 || !fields.every((field) => CRON_FIELD.test(field))) {
    return "La programación debe tener 5 campos: minuto, hora, día del mes, mes y día de la semana.";
  }
  return null;
}

function clock(hour: number, minute: number): string {
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

const HOURLY_MINUTES_CRON = /^(\d{1,2}(?:,\d{1,2})+)\s+\*\s+\*\s+\*\s+\*$/;

export function hourlyMinutes(cron: string | null | undefined): number[] | null {
  const match = HOURLY_MINUTES_CRON.exec(normalizeCron(cron));
  if (!match) return null;
  const minutes = match[1].split(",").map(Number);
  return minutes.every((minute) => minute <= 59) ? minutes : null;
}

export function describeSchedule(cron: string | null | undefined, timeZone?: string | null): string {
  const choice = presetFromCron(cron);
  if (choice === "manual") return presetById("manual").summary;
  const zone = timeZoneName(timeZone);
  if (choice !== "custom") return `${presetById(choice).summary} (${zone})`;
  const daily = dailyParts(normalizeCron(cron));
  if (daily) return `Diario · ${clock(daily.hour, daily.minute)} (${zone})`;
  const minutes = hourlyMinutes(cron);
  if (minutes) return `Varias veces por hora (${minutes.map((minute) => `:${String(minute).padStart(2, "0")}`).join(", ")}) (${zone})`;
  return `Programación personalizada (${zone})`;
}
