"""Acceptance contracts carried forward for the v1.45.219 transition bridge."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

import scripts.select_previous_release as selector
from scripts.select_previous_release import ReleaseTrustError

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github/workflows/release.yml"
CURRENT_TAG = "v1.45.219-beta"
BASE_TAG = "v1.45.209-beta"
AUTHORITY = "refs/omega-release-authority/v1.45.219-beta"
EXPECTED_MARKERS = (
    (
        "v1.45.210-beta",
        "6c70e0067eb44dd991d355b3e5cab663300c790b",
        "2429e9a2bdab13ff00740fe318009fd5b101850d",
    ),
    (
        "v1.45.211-beta",
        "cadf0b28b771257bc6cb9129cf8b4cd72ef5adff",
        "cc0873e4d86bb5bd2f183a003d43ff8a0970df8c",
    ),
    (
        "v1.45.212-beta",
        "5e3bbc3d1475486bbc0ddabb57b440210ac0782c",
        "dd882bc08bb445d1446f9cbbe313448b24827720",
    ),
    (
        "v1.45.213-beta",
        "926330d8e1e2067e4e56429fb91e4585ffa4eb43",
        "4bcfda1811d4cbe0511624e0d5cd9c1f5205926b",
    ),
    (
        "v1.45.214-beta",
        "cea2ec51314248daf26710cfe8a94a483d7287eb",
        "325170ff109e88860df857c8615f05b059698fec",
    ),
    (
        "v1.45.215-beta",
        "f40a3ab516689343514411806318cffd4f67c3bd",
        "82a7e45adff10b4877b1bfb0e6acab4206744c60",
    ),
    (
        "v1.45.216-beta",
        "0f47139b7c3e8ba2b907804d0b2a3673da3a000c",
        "1544f511cb49375120e75d04e6f8b18c7564f3e7",
    ),
    (
        "v1.45.217-beta",
        "58e0958e5b1609fa3f3184ae7ccc33994516c7a3",
        "ea3bf6b13c2af884c621310047be43e4a4132362",
    ),
    (
        "v1.45.218-beta",
        "51bf1b642f8c0312c5a9c7eaf29ce68f568ff5f8",
        "019e4d279dbc3c97db7b00a55df98cb3ba6740c4",
    ),
)


@dataclass(frozen=True)
class TransitionHistory:
    repo: Path
    authority: str
    base: str
    base_object: str
    recovery: str
    markers: tuple[tuple[str, str, str], ...]


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    (repo / "history.txt").write_text(f"{message}\n", encoding="utf-8")
    _git(repo, "add", "history.txt")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _annotated_tag(repo: Path, tag: str, message: str, commit: str) -> str:
    _git(repo, "tag", "-a", tag, "-m", message, commit)
    return _git(repo, "rev-parse", f"refs/tags/{tag}")


def _transition_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TransitionHistory:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "ci@example.com")
    _git(tmp_path, "config", "user.name", "CI")

    base = _commit(tmp_path, "trusted base .209")
    base_object = _annotated_tag(tmp_path, BASE_TAG, "trusted base .209", base)
    markers: list[tuple[str, str, str]] = []
    for patch in range(210, 219):
        tag = f"v1.45.{patch}-beta"
        commit = _commit(tmp_path, f"failed release .{patch}")
        tag_object = _annotated_tag(tmp_path, tag, f"failed release .{patch}", commit)
        markers.append((tag, tag_object, commit))
    recovery = _commit(tmp_path, "recovery release .219")
    _annotated_tag(tmp_path, CURRENT_TAG, "recovery release .219", recovery)

    refs = [
        ("base", BASE_TAG),
        *((f"failed-{index}", marker[0]) for index, marker in enumerate(markers)),
        ("current", CURRENT_TAG),
    ]
    for name, tag in refs:
        _git(tmp_path, "update-ref", f"{AUTHORITY}/{name}", f"refs/tags/{tag}")

    frozen_markers = tuple(markers)
    monkeypatch.setattr(
        selector,
        "TRANSITION_RELEASES",
        {CURRENT_TAG: (BASE_TAG, base_object, base, frozen_markers)},
    )
    monkeypatch.setattr(
        selector,
        "TRANSITION_AUTHORITY_ROOTS",
        {CURRENT_TAG: AUTHORITY},
    )
    return TransitionHistory(
        repo=tmp_path,
        authority=AUTHORITY,
        base=base,
        base_object=base_object,
        recovery=recovery,
        markers=frozen_markers,
    )


def _release_jobs() -> dict[str, object]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]


def _named_step(job: dict[str, object], name: str) -> dict[str, object]:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_f219_ledger_has_nine_exact_failed_markers_and_exact_f218_pin() -> None:
    assert selector.TRANSITION_RELEASES == {
        CURRENT_TAG: (
            BASE_TAG,
            "713b2801a43c725eab68a31db858c1b5ec10e5cc",
            "21b6274ec6e416d2d808efe30cda19ce8176611f",
            EXPECTED_MARKERS,
        )
    }
    assert selector.TRANSITION_AUTHORITY_ROOTS == {CURRENT_TAG: AUTHORITY}
    assert EXPECTED_MARKERS[-1] == (
        "v1.45.218-beta",
        "51bf1b642f8c0312c5a9c7eaf29ce68f568ff5f8",
        "019e4d279dbc3c97db7b00a55df98cb3ba6740c4",
    )


def test_f219_workflow_binds_all_eleven_remote_refs_in_one_atomic_fetch() -> None:
    job = _release_jobs()["detect-release-changes"]
    binding = _named_step(job, "Bind exact remote recovery tag authority")
    source = binding["run"]

    assert binding["if"] == "github.ref_name == 'v1.45.219-beta'"
    assert f"authority={AUTHORITY}" in source
    expected_refspecs = (
        "+refs/tags/v1.45.219-beta:${authority}/current",
        "+refs/tags/v1.45.210-beta:${authority}/failed-0",
        "+refs/tags/v1.45.211-beta:${authority}/failed-1",
        "+refs/tags/v1.45.212-beta:${authority}/failed-2",
        "+refs/tags/v1.45.213-beta:${authority}/failed-3",
        "+refs/tags/v1.45.214-beta:${authority}/failed-4",
        "+refs/tags/v1.45.215-beta:${authority}/failed-5",
        "+refs/tags/v1.45.216-beta:${authority}/failed-6",
        "+refs/tags/v1.45.217-beta:${authority}/failed-7",
        "+refs/tags/v1.45.218-beta:${authority}/failed-8",
        "+refs/tags/v1.45.209-beta:${authority}/base",
    )
    assert all(refspec in source for refspec in expected_refspecs)
    assert source.count("git fetch --no-tags --force --atomic origin") == 1
    assert source.count("+refs/tags/") == 11
    assert (
        source.count(
            "for name in current failed-0 failed-1 failed-2 failed-3 "
            "failed-4 failed-5 failed-6 failed-7 failed-8 base; do"
        )
        == 2
    )


def test_f219_bridge_accepts_exact_nine_marker_chain_without_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    checked: list[tuple[str, str]] = []

    def no_evidence(tag: str, commit: str) -> bool:
        checked.append((tag, commit))
        return False

    assert selector.select_previous_release(
        history.recovery,
        CURRENT_TAG,
        repo=history.repo,
        trust_verifier=no_evidence,
    ) == (history.base, BASE_TAG)
    assert checked == [(tag, commit) for tag, _object, commit in history.markers]


def test_f219_bridge_fails_closed_when_f218_authority_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    _git(history.repo, "update-ref", "-d", f"{history.authority}/failed-8")

    with pytest.raises(ReleaseTrustError, match="failed release marker is missing"):
        selector.select_previous_release(
            history.recovery,
            CURRENT_TAG,
            repo=history.repo,
            trust_verifier=lambda _tag, _commit: False,
        )


def test_f219_bridge_fails_closed_when_f218_annotated_tag_is_recreated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    tag, original_object, commit = history.markers[-1]
    _git(history.repo, "tag", "-d", tag)
    recreated = _annotated_tag(history.repo, tag, "attacker-recreated .218", commit)
    assert recreated != original_object
    _git(
        history.repo,
        "update-ref",
        f"{history.authority}/failed-8",
        f"refs/tags/{tag}",
    )

    with pytest.raises(ReleaseTrustError, match="tag object differs"):
        selector.select_previous_release(
            history.recovery,
            CURRENT_TAG,
            repo=history.repo,
            trust_verifier=lambda _tag, _commit: False,
        )


def test_f219_bridge_fails_closed_when_f218_marker_is_lightweight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    _tag, _object, commit = history.markers[-1]
    _git(
        history.repo,
        "update-ref",
        f"{history.authority}/failed-8",
        commit,
    )

    with pytest.raises(ReleaseTrustError, match="tag object differs"):
        selector.select_previous_release(
            history.recovery,
            CURRENT_TAG,
            repo=history.repo,
            trust_verifier=lambda _tag, _commit: False,
        )


def test_f219_bridge_fails_closed_on_f218_canonical_evidence_contradiction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _transition_history(tmp_path, monkeypatch)
    f218_tag, _f218_object, f218_commit = history.markers[-1]

    with pytest.raises(ReleaseTrustError, match="contradictory canonical evidence"):
        selector.select_previous_release(
            history.recovery,
            CURRENT_TAG,
            repo=history.repo,
            trust_verifier=lambda tag, commit: (
                tag == f218_tag and commit == f218_commit
            ),
        )
