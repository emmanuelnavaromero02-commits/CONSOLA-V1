#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.release_digest_env import EXPECTED_COMPOSE, ManifestError, load_manifest

AUXILIARY_COMPOSE = {
    "mailhog", "minio", "minio-init", "postgres", "postgres_dev_seed",
    "postgres_gold", "redis", "superset", "superset-init",
}
ONE_SHOT_COMPOSE = {"airflow-init", "minio-init", "postgres_dev_seed", "superset-init"}
ALL_COMPOSE = set(EXPECTED_COMPOSE) | AUXILIARY_COMPOSE


def _config_hashes(compose: tuple[str, ...]) -> dict[str, str]:
    try:
        completed = subprocess.run(
            (*compose, "config", "--hash", "*"),
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise ManifestError("Docker Compose config hash lookup failed") from exc
    values: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        parts = line.split()
        if (
            len(parts) != 2
            or not parts[0]
            or re.fullmatch(r"[0-9a-f]{64}", parts[1]) is None
        ):
            raise ManifestError("Docker Compose config hash output is invalid")
        if parts[0] in values:
            raise ManifestError("Docker Compose config hash output has duplicates")
        values[parts[0]] = parts[1]
    if set(values) != ALL_COMPOSE:
        raise ManifestError("Docker Compose config hash inventory is not exact")
    return values


def _verify_compose_container(
    container: object,
    *,
    compose_service: str,
    project_name: str,
    config_hash: str,
) -> None:
    if not isinstance(container, dict):
        raise ManifestError("Docker runtime inspection schema is invalid")
    config = container.get("Config")
    state = container.get("State")
    labels = config.get("Labels") if isinstance(config, dict) else None
    expected_state = (
        isinstance(state, dict)
        and (
            state.get("Status") == "exited" and state.get("ExitCode") == 0
            if compose_service in ONE_SHOT_COMPOSE
            else state.get("Running") is True
        )
    )
    health = state.get("Health") if isinstance(state, dict) else None
    expected_health = (
        True
        if compose_service in ONE_SHOT_COMPOSE or health is None
        else isinstance(health, dict) and health.get("Status") == "healthy"
    )
    if (
        not isinstance(labels, dict)
        or labels.get("com.docker.compose.project") != project_name
        or labels.get("com.docker.compose.service") != compose_service
        or labels.get("com.docker.compose.config-hash") != config_hash
        or not expected_state
        or not expected_health
    ):
        raise ManifestError(f"Compose runtime config/state differs: {compose_service}")


def _verify_container(
    container: object,
    image: object,
    *,
    compose_service: str,
    expected: str,
    project_name: str,
    config_hash: str,
) -> None:
    if not isinstance(container, dict) or not isinstance(image, dict):
        raise ManifestError("Docker runtime inspection schema is invalid")
    config = container.get("Config")
    state = container.get("State")
    repo_digests = image.get("RepoDigests")
    _verify_compose_container(
        container,
        compose_service=compose_service,
        project_name=project_name,
        config_hash=config_hash,
    )
    if (
        not isinstance(config, dict)
        or config.get("Image") != expected
        or container.get("Image") != image.get("Id")
        or not isinstance(repo_digests, list)
        or expected not in repo_digests
    ):
        raise ManifestError(
            f"release container is not exact running digest/config: {compose_service}"
        )


def _json_command(*command: str) -> object:
    try:
        completed = subprocess.run(
            command,
            cwd=REPO,
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(completed.stdout)
    except (subprocess.CalledProcessError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"runtime inspection failed: {command[1]}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--checksum", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--build-run-id", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = load_manifest(
            args.manifest,
            checksum_path=args.checksum,
            repository=args.repository,
            release_tag=args.release_tag,
            source_sha=args.source_sha,
            build_run_id=args.build_run_id,
        )
        by_service = manifest["by_service"]
        compose = (
            "docker",
            "compose",
            "--env-file",
            "infra/.env",
            "-f",
            "infra/docker-compose.yml",
            "-f",
            "infra/docker-compose.dev.yml",
            "-f",
            "infra/terraform-gcp/release/docker-compose.release.yml",
            "--profile",
            "sap",
        )
        compose_model = _json_command(*compose, "config", "--format", "json")
        if not isinstance(compose_model, dict) or not isinstance(
            compose_model.get("name"), str
        ) or not compose_model["name"]:
            raise ManifestError("Docker Compose project identity is invalid")
        project_name = compose_model["name"]
        services_model = compose_model.get("services")
        if not isinstance(services_model, dict) or set(services_model) != ALL_COMPOSE:
            raise ManifestError("Docker Compose service inventory is not exact")
        config_hashes = _config_hashes(compose)
        for compose_service in sorted(ALL_COMPOSE):
            try:
                result = subprocess.run(
                    (*compose, "ps", "--all", "--quiet", compose_service),
                    cwd=REPO,
                    check=True,
                    capture_output=True,
                    text=True,
                )
            except subprocess.CalledProcessError as exc:
                raise ManifestError(
                    f"release container lookup failed: {compose_service}"
                ) from exc
            container_ids = result.stdout.splitlines()
            if len(container_ids) != 1 or not container_ids[0]:
                raise ManifestError(
                    f"release service does not have exactly one container: {compose_service}"
                )
            container_value = _json_command("docker", "inspect", container_ids[0])
            if (
                not isinstance(container_value, list)
                or len(container_value) != 1
                or not isinstance(container_value[0], dict)
            ):
                raise ManifestError("Docker runtime inspection schema is invalid")
            container = container_value[0]
            _verify_compose_container(
                container,
                compose_service=compose_service,
                project_name=project_name,
                config_hash=config_hashes[compose_service],
            )
            if compose_service not in EXPECTED_COMPOSE:
                continue
            expected = by_service[EXPECTED_COMPOSE[compose_service]]
            image_value = _json_command("docker", "image", "inspect", expected)
            if (
                not isinstance(image_value, list)
                or len(image_value) != 1
                or not isinstance(image_value[0], dict)
            ):
                raise ManifestError("Docker image inspection schema is invalid")
            image = image_value[0]
            _verify_container(
                container,
                image,
                compose_service=compose_service,
                expected=expected,
                project_name=project_name,
                config_hash=config_hashes[compose_service],
            )
    except (ManifestError, OSError) as exc:
        print(f"RELEASE DIGEST RUNTIME BLOCKED: {exc}", file=sys.stderr)
        return 1
    print("RELEASE DIGEST RUNTIME PASS: 26 containers / 15 digests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
