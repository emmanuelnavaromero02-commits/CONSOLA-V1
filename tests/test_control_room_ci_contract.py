from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/control-room-postgres-rls.yml"


def test_control_room_postgres_workflow_covers_command_and_orchestrator_paths():
    text = WORKFLOW.read_text(encoding="utf-8")
    for path in (
        "console/app/main.py",
        "console/app/routers/control_room.py",
        "console/app/domains/decisions/**",
        "console/app/services/intelligence/decision_orchestrator.py",
        "console/app/services/control_room/**",
        "console/tests/test_control_room*.py",
        "tests/test_control_room*.py",
    ):
        assert f'"{path}"' in text


def test_control_room_postgres_junit_guard_requires_nine_and_zero_bad_results():
    text = WORKFLOW.read_text(encoding="utf-8")
    verifier = text.split("python - <<'PY'", 1)[1]
    assert "tests < 9" in verifier
    assert "skipped" in verifier
    assert "failures" in verifier
    assert "errors" in verifier
    assert "report missing" in verifier.lower()
