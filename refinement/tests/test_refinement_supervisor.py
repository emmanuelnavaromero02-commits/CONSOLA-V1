from __future__ import annotations

import inspect

import pytest

from refinement.scripts import refinement_supervisor


def test_publication_verifier_startup_timeout_defaults_to_60_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PUBLICATION_VERIFIER_STARTUP_TIMEOUT_SECONDS", raising=False)

    assert refinement_supervisor._verifier_startup_timeout_seconds() == 60.0


def test_publication_verifier_startup_timeout_accepts_bounded_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLICATION_VERIFIER_STARTUP_TIMEOUT_SECONDS", "45.5")

    assert refinement_supervisor._verifier_startup_timeout_seconds() == 45.5


@pytest.mark.parametrize("value", ["0", "121", "nan", "invalid"])
def test_publication_verifier_startup_timeout_rejects_unsafe_values(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("PUBLICATION_VERIFIER_STARTUP_TIMEOUT_SECONDS", value)

    with pytest.raises(RuntimeError, match="startup timeout must be between"):
        refinement_supervisor._verifier_startup_timeout_seconds()


def test_supervisor_uses_bounded_publication_verifier_startup_timeout() -> None:
    source = inspect.getsource(refinement_supervisor.supervise)

    timeout_assignment = (
        "verifier_startup_timeout = _verifier_startup_timeout_seconds()"
    )
    assert timeout_assignment in source
    assert source.index(timeout_assignment) < source.index(
        "verifier = subprocess.Popen"
    )
    assert "time.monotonic() + verifier_startup_timeout" in source
    assert "time.monotonic() + 10" not in source
