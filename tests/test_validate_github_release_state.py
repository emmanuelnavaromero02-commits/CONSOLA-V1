from __future__ import annotations

import copy

import pytest

from scripts.validate_github_release_state import ReleaseStateError, validate

TAG = "v1.46.0-rc.1"
SHA = "a" * 40


def _state(state: str) -> dict[str, object]:
    manifest = f"omega-release-manifest-{TAG}.json"
    return {
        "schema_version": 1,
        "state": state,
        "tag": TAG,
        "immutable": state == "present",
        "title": TAG,
        "body": (
            f"Automated OMEGA release manifest: 15 images bound to {SHA} "
            "and tested with exact source checkout bind mounts."
        ),
        "prerelease": True,
        "target": SHA,
        "assets": [manifest, f"{manifest}.sha256"],
    }


@pytest.mark.parametrize("state", ["draft", "present"])
def test_canonical_release_edges_pass(state: str) -> None:
    validate(_state(state), state=state, tag=TAG, source_sha=SHA)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "attacker"),
        ("body", "misleading"),
        ("prerelease", False),
        ("target", "b" * 40),
        ("assets", [f"omega-release-manifest-{TAG}.json"]),
        ("assets", [f"omega-release-manifest-{TAG}.json", "extra"]),
        ("immutable", True),
    ],
)
def test_draft_metadata_asset_and_race_mutations_block(
    field: str, value: object
) -> None:
    candidate = copy.deepcopy(_state("draft"))
    candidate[field] = value

    with pytest.raises(ReleaseStateError, match="not canonical"):
        validate(candidate, state="draft", tag=TAG, source_sha=SHA)
