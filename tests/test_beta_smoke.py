from __future__ import annotations

import ast
import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "beta_smoke.py"
MAKEFILE = REPO / "Makefile"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_beta_smoke_script_exists_and_parses() -> None:
    source = _read(SCRIPT)
    ast.parse(source)
    assert source.startswith("#!/usr/bin/env python3")


def test_makefile_exposes_beta_smoke_target() -> None:
    makefile = _read(MAKEFILE)
    assert "beta-smoke" in makefile
    assert "scripts/beta_smoke.py" in makefile
    assert "$(MAKE) smoke" in makefile
    assert "OMEGA_BETA_SMOKE_WARM_ACCEPTANCE" in makefile
    assert "$(MAKE) acceptance" in makefile
    assert "strict beta gate" in makefile


def test_beta_smoke_checks_release_identity_and_strict_readiness() -> None:
    source = _read(SCRIPT)
    for needle in (
        "VERSION",
        "git status",
        "release tree is clean",
        "OMEGA_BETA_SMOKE_ALLOW_DIRTY",
        "/healthz",
        "readyz?require_data=1&require_intelligence=1",
        "strict data readiness",
    ):
        assert needle in source


def test_beta_smoke_dirty_tree_filter_keeps_evidence_out_of_release_identity() -> None:
    source = _read(SCRIPT)
    assert "docs/release-evidence/" in source
    assert "dirty tree allowed for local dev" in source


def test_beta_smoke_checks_gold_lineage_rls_and_superset() -> None:
    source = _read(SCRIPT)
    for needle in (
        "omega_publication.published_lineage",
        "layer='gold'",
        "mode_postgres_gold",
        "rolbypassrls",
        "relforcerowsecurity",
        "public.gold_*",
        "8088/health",
    ):
        assert needle in source


def test_beta_smoke_keeps_external_writeback_disabled_for_beta() -> None:
    source = _read(SCRIPT)
    assert "CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK" in source
    assert "OMEGA_BETA_SMOKE_ALLOW_EXTERNAL_WRITEBACK" in source
    assert re.search(r"external write-back disabled by default", source)
