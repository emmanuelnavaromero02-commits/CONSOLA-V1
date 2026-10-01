from __future__ import annotations

import difflib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "infra/init_gold"
CAS = GOLD / "42_staged_publication_cas.sql"
MIGRATION = GOLD / "45_legacy_text_compatible_publication.sql"
SIGNATURE = "CREATE OR REPLACE FUNCTION omega_publication.publish_materialization("


def _function(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    start = text.index(SIGNATURE)
    end = text.index("END $$;", start) + len("END $$;")
    return text[start:end].splitlines()


def _changed_lines(before: list[str], after: list[str]) -> list[str]:
    return [
        line
        for line in difflib.unified_diff(before, after, lineterm="", n=0)
        if line[:1] in "+-" and not line.startswith(("+++", "---"))
    ]


def test_publish_function_differs_from_42_only_by_the_approved_edits() -> None:
    assert _changed_lines(_function(CAS), _function(MIGRATION)) == [
        "-SET search_path = pg_catalog, omega_publication",
        "+SET search_path = pg_catalog, omega_publication, pg_temp",
        "-    IF staged IS NULL THEN RAISE EXCEPTION 'prepared Gold stage missing'; END IF;",
        "+    IF staged IS NULL THEN",
        "+      RAISE EXCEPTION 'prepared Gold stage missing' USING ERRCODE='55000';",
        "+    END IF;",
        "-        FROM pg_attribute a",
        "+        FROM pg_catalog.pg_attribute a",
        "-        FROM pg_attribute a",
        "+        FROM pg_catalog.pg_attribute a",
        "-      ELSIF existing_type <> column_record.data_type THEN",
        "-        RAISE EXCEPTION 'legacy Gold column type conflict' USING ERRCODE='23514';",
        "+      ELSIF existing_type <> column_record.data_type",
        "+            AND NOT (existing_type = 'character varying'",
        "+                     AND column_record.data_type = 'text') THEN",
        "+        RAISE EXCEPTION 'legacy Gold column type conflict' USING ERRCODE='42804',",
        "+          DETAIL=format('column %I: %s <> %s', column_record.attname,",
        "+                        existing_type, column_record.data_type);",
        "-      INTO column_list FROM pg_attribute a",
        "+      INTO column_list FROM pg_catalog.pg_attribute a",
    ]


def test_migration_keeps_the_function_authority_and_records_itself() -> None:
    text = MIGRATION.read_text(encoding="utf-8")
    tail = text.split("END $$;", 1)[1]
    assert text.count(SIGNATURE) == 1
    assert "SECURITY DEFINER" in text
    assert "SET search_path = pg_catalog, omega_publication, pg_temp\n" in text
    assert "FROM pg_attribute" not in text
    assert text.count("FROM pg_catalog.pg_attribute a") == 3
    assert (
        "ALTER FUNCTION omega_publication.publish_materialization(uuid,uuid)\n"
        "  OWNER TO omega_gold_owner;"
    ) in tail
    assert (
        "REVOKE ALL ON FUNCTION omega_publication.publish_materialization(uuid,uuid)\n"
        "  FROM PUBLIC, omega_refinement_gold, omega_gold_verifier;"
    ) in tail
    assert (
        "GRANT EXECUTE ON FUNCTION omega_publication.publish_materialization(uuid,uuid)\n"
        "  TO omega_gold_publisher;"
    ) in tail
    assert "ON ALL FUNCTIONS" not in text
    assert "GRANT" not in tail.replace(
        "GRANT EXECUTE ON FUNCTION omega_publication.publish_materialization(uuid,uuid)",
        "",
    )
    assert "BEGIN;" not in text and "COMMIT;" not in text
    assert "'gold/45_legacy_text_compatible_publication.sql'" in tail
    assert "ON CONFLICT (filename) DO NOTHING" in tail


def test_only_unbounded_varchar_is_equivalent_to_text() -> None:
    body = "\n".join(_function(MIGRATION))
    assert body.count("existing_type = 'character varying'") == 1
    assert "character varying(" not in body
    assert "ALTER COLUMN" not in body
    assert "USING ERRCODE='23514'" in body
    assert body.count("USING ERRCODE='42804'") == 1
