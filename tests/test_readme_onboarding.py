from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
ARCHIVED_CHANGELOG = REPO / "docs/audits/security-phase-hardening-changelog.md"


def test_readme_is_onboarding_not_phase_changelog() -> None:
    src = README.read_text(encoding="utf-8")
    first_lines = "\n".join(src.splitlines()[:12])

    assert src.startswith("# CONSOLA-V1 / OMEGA")
    assert "## Primer Arranque Local" in src
    assert "## Validacion Rapida" in src
    assert "## Reset y Reparacion Local" in src
    assert "Security Phase 1 Residuals" not in first_lines
    assert "Phase 2 - Quick Wins" not in first_lines


def test_hardening_changelog_was_archived_from_readme() -> None:
    src = ARCHIVED_CHANGELOG.read_text(encoding="utf-8")

    assert "Security Phase Hardening Changelog" in src
    assert "Phase 1 - Residual Closures" in src
    assert "Phase 2 - Quick Wins" in src
    assert "README principal ahora es una guia de onboarding" in src


def test_readme_points_to_v1_gate_and_runbooks() -> None:
    src = README.read_text(encoding="utf-8")

    for needle in (
        "docs/release-checklist-v1.md",
        "docs/runbook/01_arrancar_desde_cero.md",
        "docs/runbook/09_demo_beta.md",
        "make preflight",
        "make up",
        "make smoke",
        "make e2e",
        "make acceptance",
    ):
        assert needle in src
