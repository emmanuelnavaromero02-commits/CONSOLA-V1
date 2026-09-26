const COUNT_FORMAT = new Intl.NumberFormat("es-MX");

export function formatCount(value: number): string {
  return COUNT_FORMAT.format(value);
}

export function plural(count: number, singular: string, pluralForm: string): string {
  return `${formatCount(count)} ${count === 1 ? singular : pluralForm}`;
}
