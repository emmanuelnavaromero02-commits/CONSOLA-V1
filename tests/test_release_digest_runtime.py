from __future__ import annotations

import copy
import subprocess

import pytest

from scripts.release_digest_env import ManifestError
from scripts.verify_release_digest_runtime import (
    _config_hashes,
    _verify_compose_container,
    _verify_container,
)


def _container() -> tuple[dict[str, object], dict[str, object]]:
    reference = "ghcr.io/owner/console@sha256:" + "1" * 64
    image = {"Id": "sha256:image", "RepoDigests": [reference]}
    container = {
        "Image": "sha256:image",
        "Config": {
            "Image": reference,
            "Labels": {
                "com.docker.compose.project": "omega",
                "com.docker.compose.service": "console",
                "com.docker.compose.config-hash": "a" * 64,
            },
        },
        "State": {"Running": True, "Health": {"Status": "healthy"}},
    }
    return container, image


def test_runtime_container_binds_digest_compose_identity_and_config_hash() -> None:
    container, image = _container()
    _verify_container(
        container,
        image,
        compose_service="console",
        expected=str(container["Config"]["Image"]),  # type: ignore[index]
        project_name="omega",
        config_hash="a" * 64,
    )


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("com.docker.compose.project", "attacker"),
        ("com.docker.compose.service", "hubspot"),
        ("com.docker.compose.config-hash", "b" * 64),
    ],
)
def test_runtime_container_rejects_label_or_config_drift(
    label: str, value: str
) -> None:
    container, image = _container()
    mutated = copy.deepcopy(container)
    mutated["Config"]["Labels"][label] = value  # type: ignore[index]
    with pytest.raises(ManifestError, match="config/state differs"):
        _verify_container(
            mutated,
            image,
            compose_service="console",
            expected=str(container["Config"]["Image"]),  # type: ignore[index]
            project_name="omega",
            config_hash="a" * 64,
        )


def test_auxiliary_container_config_hash_drift_is_blocked() -> None:
    container, _image = _container()
    container["Config"]["Labels"] = {  # type: ignore[index]
        "com.docker.compose.project": "omega",
        "com.docker.compose.service": "postgres",
        "com.docker.compose.config-hash": "b" * 64,
    }
    with pytest.raises(ManifestError, match="config/state differs"):
        _verify_compose_container(
            container,
            compose_service="postgres",
            project_name="omega",
            config_hash="a" * 64,
        )


def test_config_hash_inventory_rejects_missing_or_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [], 0, f"console {'a' * 64}\nconsole {'b' * 64}\n", ""
        ),
    )
    with pytest.raises(ManifestError, match="duplicates"):
        _config_hashes(("docker", "compose"))
