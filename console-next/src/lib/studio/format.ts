const COUNT_FORMAT = new Intl.NumberFormat("es-MX");
const ABSOLUTE_TIME = new Intl.DateTimeFormat("es-MX", { dateStyle: "medium", timeStyle: "short", timeZone: "UTC" });
const DAY = new Intl.DateTimeFormat("es-MX", { dateStyle: "medium", timeZone: "UTC" });
const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

export function formatCount(value: number): string {
  return COUNT_FORMAT.format(value);
}

export function plural(count: number, singular: string, pluralForm: string): string {
  return `${formatCount(count)} ${count === 1 ? singular : pluralForm}`;
}

export function absoluteTime(value: string): string {
  const time = Date.parse(value);
  return Number.isNaN(time) ? value : `${ABSOLUTE_TIME.format(new Date(time))} UTC`;
}

export function formatDay(value: string): string {
  const time = Date.parse(ISO_DAY.test(value) ? `${value}T00:00:00Z` : value);
  return Number.isNaN(time) ? value : DAY.format(new Date(time));
}
