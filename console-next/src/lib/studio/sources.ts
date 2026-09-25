const STORAGE_LAYERS = new Set(["raw", "silver", "gold"]);
const READ_CALL_RE = /\b(?:read_parquet|read_csv|read_json|parquet_scan|csv_scan)\s*\(\s*['"]([^'"]+)['"]/gi;

export function normalizeStorageSourceRef(value: unknown): string {
  let ref = String(value ?? "").trim();
  if (!ref) return "";
  ref = ref.replace(/^s3:\/\/(?:\{bucket\}|[^/]+)\//, "").replace(/^\/+/, "");
  const parts = ref.split("/").filter(Boolean);
  if (parts.length >= 3 && STORAGE_LAYERS.has(parts[0])) {
    return `${parts[0]}/${parts[1]}/${parts[2]}`;
  }
  return "";
}

export function editorSources(input: {
  detailSources?: unknown[] | null;
  cartridge?: string | null;
  entity?: string | null;
  sql?: string | null;
}): string[] {
  const found = new Set<string>();
  for (const source of input.detailSources ?? []) {
    const normalized = normalizeStorageSourceRef(source);
    if (normalized) found.add(normalized);
  }
  if (input.cartridge && input.entity) found.add(`raw/${input.cartridge}/${input.entity}`);
  for (const match of (input.sql ?? "").matchAll(READ_CALL_RE)) {
    const normalized = normalizeStorageSourceRef(match[1]);
    if (normalized) found.add(normalized);
  }
  return [...found];
}
