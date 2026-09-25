#!/usr/bin/env python3

from __future__ import annotations

import argparse
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "console"))

from app.domains.apps.manifests import load_packaged_manifests  # noqa: E402

TARGET = REPO / "infra/init/99zzu_analytic_app_manifest_registry.sql"
EXPECTED_APPS = 18

HEADER = """\
-- GENERATED FILE — do not edit by hand.
--
-- Produced by scripts/generate_app_manifest_registry.py from the reviewed
-- manifests at cartridges/<cartridge>/apps/<app>.json and their HTML. Run the
-- generator to regenerate; CI runs it with --check and fails on any drift.
--
-- This is the database's copy of "what was reviewed". The reconciliation
-- function resolves the cartridge, the digest and the dataset list from here,
-- so a caller cannot supply any of them. Nothing in this file comes from
-- analytic_apps.datasets_used, from runtime metadata, or from scraping HTML.
--
-- The tables are created by 99zzt; this file only carries their contents.
--
-- Loaded as an upsert rather than a truncate: grants reference these rows with
-- ON DELETE RESTRICT, so wiping the table would either fail or, worse, need a
-- CASCADE that silently deleted live grants. Rows that leave the packaged set
-- are marked superseded and their dataset rows dropped only when nothing
-- references them, so history stays intact and authority still narrows.
"""

FOOTER = """
-- Anything no longer packaged stops being active, and its dataset rows go only
-- when no grant still points at them.
UPDATE public.analytic_app_manifests m
   SET revision = 'superseded'
 WHERE m.revision = 'active'
   AND m.app_name NOT IN ({packaged_names});

DELETE FROM public.analytic_app_manifest_datasets d
 WHERE NOT EXISTS (
        SELECT 1 FROM public.analytic_app_manifests m
         WHERE m.app_name = d.app_name
           AND m.manifest_digest = d.manifest_digest
           AND m.revision = 'active')
   AND NOT EXISTS (
        SELECT 1 FROM public.analytic_app_dataset_grants g
         WHERE g.app_name = d.app_name
           AND g.manifest_digest = d.manifest_digest
           AND g.dataset_name = d.dataset_name);

-- Exactly the packaged set, no more and no less. A mismatch here means the
-- image and this file disagree, which must stop the migration rather than
-- quietly grant from a stale list.
DO $registry_count$
DECLARE
    app_rows BIGINT;
BEGIN
    SELECT count(*) INTO app_rows FROM public.analytic_app_manifests
     WHERE revision = 'active';
    IF app_rows <> {expected} THEN
        RAISE EXCEPTION 'app manifest registry expected % rows, found %',
            {expected}, app_rows;
    END IF;
END
$registry_count$;

"""


def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def render() -> str:
    manifests = load_packaged_manifests()
    if len(manifests) != EXPECTED_APPS:
        raise SystemExit(
            f"expected {EXPECTED_APPS} packaged manifests, found {len(manifests)}"
        )
    lines = [HEADER]
    lines.append("\nINSERT INTO public.analytic_app_manifests")
    lines.append(
        "    (app_name, cartridge_id, manifest_digest, html_sha256, revision, source)"
    )
    lines.append("VALUES")
    rows = []
    import hashlib

    for name in sorted(manifests):
        m = manifests[name]
        html_sha = hashlib.sha256(m["packaged_html"].encode("utf-8")).hexdigest()
        rows.append(
            "    ({}, {}, {}, {}, 'active', 'packaged_manifest')".format(
                _sql_literal(m["app_name"]),
                _sql_literal(m["cartridge_id"]),
                _sql_literal(m["packaged_digest"]),
                _sql_literal(html_sha),
            )
        )
    lines.append(",\n".join(rows))
    lines.append("""ON CONFLICT (app_name) DO UPDATE SET
    cartridge_id = EXCLUDED.cartridge_id,
    manifest_digest = EXCLUDED.manifest_digest,
    html_sha256 = EXCLUDED.html_sha256,
    revision = 'active',
    source = 'packaged_manifest',
    generated_at = clock_timestamp();""")

    dataset_rows = []
    for name in sorted(manifests):
        m = manifests[name]
        for dataset in sorted(m["datasets"]):
            dataset_rows.append(
                "    ({}, {}, {})".format(
                    _sql_literal(m["app_name"]),
                    _sql_literal(m["packaged_digest"]),
                    _sql_literal(dataset),
                )
            )
    if dataset_rows:
        lines.append("\nINSERT INTO public.analytic_app_manifest_datasets")
        lines.append("    (app_name, manifest_digest, dataset_name)")
        lines.append("VALUES")
        lines.append(",\n".join(dataset_rows))
        lines.append("ON CONFLICT (app_name, manifest_digest, dataset_name) DO NOTHING;")
    packaged_names = ",\n        ".join(_sql_literal(n) for n in sorted(manifests))
    lines.append(FOOTER.format(expected=EXPECTED_APPS, packaged_names=packaged_names))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = render()
    if args.check:
        if not TARGET.exists():
            print(f"{TARGET} is missing; run the generator", file=sys.stderr)
            return 1
        current = TARGET.read_text(encoding="utf-8")
        if current != rendered:
            print(
                f"{TARGET} is out of date with the packaged manifests; "
                "run scripts/generate_app_manifest_registry.py",
                file=sys.stderr,
            )
            return 1
        print(f"{TARGET.name}: up to date ({EXPECTED_APPS} apps)")
        return 0
    TARGET.write_text(rendered, encoding="utf-8")
    print(f"wrote {TARGET.name} ({EXPECTED_APPS} apps)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
