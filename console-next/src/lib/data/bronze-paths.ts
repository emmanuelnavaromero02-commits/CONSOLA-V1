const DEFAULT_BUCKET = "lakehouse";
const STORAGE_LAYERS = new Set(["raw", "silver", "gold"]);

function stripS3Bucket(value: string): string {
  if (!value.toLowerCase().startsWith("s3://")) return value;
  const withoutScheme = value.slice(5);
  const slash = withoutScheme.indexOf("/");
  return slash >= 0 ? withoutScheme.slice(slash + 1) : "";
}

function stripParquetGlob(value: string): string {
  return value
    .replace(/\/\*\*\/\*\.parquet$/i, "")
    .replace(/\/\*\.parquet$/i, "")
    .replace(/\/load_date=\*\/batch_id=\*\/\*\.parquet$/i, "")
    .replace(/\/+$/g, "");
}

export function canonicalBronzeSource(source: string): string {
  const cleaned = stripParquetGlob(stripS3Bucket(source.trim()));
  const parts = cleaned.split("/").filter(Boolean);
  if (!parts.length || !STORAGE_LAYERS.has(parts[0])) return cleaned;
  return parts.slice(0, 3).join("/");
}

export function bronzeSourceToS3(source: string, bucket = DEFAULT_BUCKET): string {
  const trimmed = source.trim();
  if (!trimmed) return trimmed;
  if (trimmed.toLowerCase().startsWith("s3://")) return trimmed;

  const canonical = canonicalBronzeSource(trimmed);
  if (!canonical) return trimmed;
  if (canonical.startsWith("raw/") && !/[/*]\.parquet$/i.test(canonical)) {
    return `s3://${bucket}/${canonical}/**/*.parquet`;
  }
  return `s3://${bucket}/${canonical}`;
}

export function normalizeBronzeSql(sql: string, bucket = DEFAULT_BUCKET): string {
  return sql.replace(
    /\bread_(?:parquet|csv|json)\s*\(\s*(['"])([^'"]+)\1/gi,
    (match, quote: string, source: string) => match.replace(`${quote}${source}${quote}`, `${quote}${bronzeSourceToS3(source, bucket)}${quote}`),
  );
}

export function normalizeBronzeSources(sources: string[]): string[] {
  return Array.from(
    new Set(
      sources
        .map((source) => canonicalBronzeSource(source))
        .filter((source) => source && STORAGE_LAYERS.has(source.split("/")[0])),
    ),
  );
}
