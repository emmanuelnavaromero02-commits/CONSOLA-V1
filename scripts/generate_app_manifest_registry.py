#!/usr/bin/env python3
"""Generate the server-owned manifest registry SQL from the packaged apps.

The database must not take the caller's word for which datasets an app may
read, and it must not read the app's own HTML to find out. So the reviewed
manifests are compiled into a registry table at build time, and the
reconciliation function resolves everything from there.

Deterministic by construction: manifests are visited in sorted order, dataset
lists are sorted, and the digest is computed by the same
``app.domains.apps.manifests`` code the runtime uses. Running this twice
produces byte-identical SQL, which ``--check`` enforces in CI.

    python3 scripts/generate_app_manifest_registry.py            # write
    python3 scripts/generate_app_manifest_registry.py --check    # verify
"""

from __future__ import annotations

import argparse
import json
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
-- Both authority tables are new and must be empty when 99zzt creates them.
-- Plain INSERT is deliberate: adopting or reconciling any pre-existing row
-- would turn database state into an unaudited source of release authority.
"""

FOOTER = """
-- Exactly the packaged set, no more and no less. A mismatch here means the
-- image and this file disagree, which must stop the migration rather than
-- quietly grant from a stale list.
DO $registry_count$
DECLARE
    actual_apps JSONB;
    expected_apps JSONB;
    actual_datasets JSONB;
    expected_datasets JSONB;
BEGIN
    SELECT COALESCE(jsonb_agg(jsonb_build_array(
               app_name, cartridge_id, manifest_digest, html_sha256,
               revision, source) ORDER BY app_name), '[]'::jsonb)
      INTO actual_apps
      FROM public.analytic_app_manifests;
    SELECT jsonb_agg(value ORDER BY value->>0)
      INTO expected_apps
      FROM jsonb_array_elements({expected_apps_json}::jsonb) value;
    IF actual_apps IS DISTINCT FROM expected_apps THEN
        RAISE EXCEPTION 'app manifest registry differs from packaged authority';
    END IF;

    SELECT COALESCE(jsonb_agg(jsonb_build_array(
               app_name, manifest_digest, dataset_name)
               ORDER BY app_name, manifest_digest, dataset_name), '[]'::jsonb)
      INTO actual_datasets
      FROM public.analytic_app_manifest_datasets;
    SELECT jsonb_agg(value ORDER BY value->>0, value->>1, value->>2)
      INTO expected_datasets
      FROM jsonb_array_elements({expected_datasets_json}::jsonb) value;
    IF actual_datasets IS DISTINCT FROM expected_datasets THEN
        RAISE EXCEPTION 'app manifest dataset triples differ from packaged authority';
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
    lines[-1] += ";"

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
        lines[-1] += ";"
    expected_apps = []
    expected_datasets = []
    for name in sorted(manifests):
        manifest = manifests[name]
        html_sha = hashlib.sha256(manifest["packaged_html"].encode("utf-8")).hexdigest()
        expected_apps.append(
            [
                manifest["app_name"],
                manifest["cartridge_id"],
                manifest["packaged_digest"],
                html_sha,
                "active",
                "packaged_manifest",
            ]
        )
        for dataset in sorted(manifest["datasets"]):
            expected_datasets.append(
                [manifest["app_name"], manifest["packaged_digest"], dataset]
            )
    lines.append(
        FOOTER.format(
            expected_apps_json=_sql_literal(
                json.dumps(expected_apps, separators=(",", ":"))
            ),
            expected_datasets_json=_sql_literal(
                json.dumps(expected_datasets, separators=(",", ":"))
            ),
        )
    )
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
