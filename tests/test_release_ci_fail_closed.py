"""Fail-closed contracts for tag release validation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github/workflows/release.yml"
POLICY = REPO / ".github/release-skip-policy.json"
TEST_SKIP_POLICY = REPO / ".github/release-test-skip-policy.json"


def _jobs() -> dict[str, object]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]


def _named_step(job: dict[str, object], name: str) -> dict[str, object]:
    return next(step for step in job["steps"] if step.get("name") == name)


def _policy() -> dict[str, object]:
    return json.loads(POLICY.read_text(encoding="utf-8"))


def _matching_full_stack_policies(tag: str, changed_files: list[str]) -> list[str]:
    matches = []
    for policy in _policy()["policies"]:
        if policy["gate"] != "full-stack-release-gate":
            continue
        scope = policy["scope"]
        if re.fullmatch(scope["tag_regex"], tag) is None:
            continue
        all_patterns = [re.compile(value) for value in scope["all_changed_paths_match"]]
        any_patterns = [
            re.compile(value) for value in scope["at_least_one_changed_path_match"]
        ]
        if not changed_files or not all(
            any(pattern.fullmatch(path) for pattern in all_patterns)
            for path in changed_files
        ):
            continue
        if not any(
            pattern.fullmatch(path)
            for pattern in any_patterns
            for path in changed_files
        ):
            continue
        matches.append(policy["id"])
    return matches


def _run_policy_step(
    changed_files: list[str],
    *,
    release_tag: str | None = None,
    deleted_files: list[str] | None = None,
    root_test_targets: list[str] | None = None,
    cartridge_test_targets: list[str] | None = None,
    version_override: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, str], str]:
    job = _jobs()["authorize-release-gate-skips"]
    source = _named_step(job, "Authorize applicable release gate skips")["run"]
    version = version_override or (REPO / "VERSION").read_text(encoding="utf-8").strip()
    with tempfile.TemporaryDirectory() as temp_dir:
        run_cwd = REPO
        if version_override is not None:
            run_cwd = Path(temp_dir)
            (run_cwd / "VERSION").write_text(version_override, encoding="utf-8")
        output = Path(temp_dir) / "output"
        summary = Path(temp_dir) / "summary"
        env = {
            **os.environ,
            "CARTRIDGE_TEST_TARGETS": " ".join(cartridge_test_targets or []),
            "CHANGED_FILES_JSON": json.dumps(changed_files),
            "DELETED_FILES_JSON": json.dumps(deleted_files or []),
            "RELEASE_TAG": release_tag or f"v{version}",
            "RELEASE_SKIP_POLICY": str(POLICY),
            "ROOT_TEST_TARGETS": " ".join(root_test_targets or []),
            "GITHUB_OUTPUT": str(output),
            "GITHUB_STEP_SUMMARY": str(summary),
        }
        result = subprocess.run(
            ["bash", "-c", source],
            cwd=run_cwd,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        values = {}
        if output.exists():
            values = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
            )
        summary_text = summary.read_text(encoding="utf-8") if summary.exists() else ""
    return result, values, summary_text


def _canonical_services() -> list[str]:
    matrix = _jobs()["build-and-push"]["strategy"]["matrix"]["include"]
    return [entry["service"] for entry in matrix]


def _run_manifest_step(
    *, missing_service: str | None = None
) -> tuple[subprocess.CompletedProcess[str], dict[str, object] | None, str, str]:
    job = _jobs()["publish-release-manifest"]
    step = _named_step(job, "Assemble and validate release manifest")
    source = step["run"]
    services = json.loads(step["env"]["EXPECTED_SERVICES_JSON"])
    release_tag = "v1.45.210-beta"
    source_sha = "a" * 40
    owner = "omega-owner"
    repository = f"{owner}/omega"
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        fragments = root / "fragments"
        output_dir = root / "output"
        action_output = root / "action-output"
        summary = root / "summary"
        fragments.mkdir()
        for index, service in enumerate(services, start=1):
            if service == missing_service:
                continue
            image = f"ghcr.io/{owner}/{service}"
            digest = f"sha256:{index:064x}"
            fragment = {
                "schema_version": 1,
                "service": service,
                "image": image,
                "release_tag": release_tag,
                "source_sha": source_sha,
                "digest": digest,
                "release_reference": f"{image}:{release_tag}@{digest}",
                "sha_reference": f"{image}:sha-{source_sha}@{digest}",
            }
            (fragments / f"{service}.release-digest.json").write_text(
                json.dumps(fragment, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        env = {
            **os.environ,
            "DIGEST_FRAGMENTS_DIR": str(fragments),
            "EXPECTED_SERVICES_JSON": json.dumps(services),
            "GHCR_OWNER": owner,
            "RELEASE_MANIFEST_DIR": str(output_dir),
            "RELEASE_TAG": release_tag,
            "SOURCE_REPOSITORY": repository,
            "SOURCE_SHA": source_sha,
            "GITHUB_OUTPUT": str(action_output),
            "GITHUB_STEP_SUMMARY": str(summary),
        }
        result = subprocess.run(
            ["bash", "-c", source],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        manifest = None
        checksum = ""
        if action_output.exists():
            values = dict(
                line.split("=", 1)
                for line in action_output.read_text(encoding="utf-8").splitlines()
            )
            manifest_path = Path(values["manifest_path"])
            checksum_path = Path(values["checksum_path"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            checksum = checksum_path.read_text(encoding="utf-8")
        summary_text = (
            summary.read_text(encoding="utf-8") if summary.exists() else ""
        )
    return result, manifest, checksum, summary_text


def _run_fresh_image_preflight(
    *, sha_mode: str, release_mode: str
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    source = _named_step(
        _jobs()["build-and-push"], "Refuse pre-existing image tags"
    )["run"]
    image = "ghcr.io/omega-owner/console"
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        fake_bin = root / "bin"
        fake_bin.mkdir()
        log = root / "docker.log"
        docker = fake_bin / "docker"
        docker.write_text(
            """#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with Path(os.environ["FAKE_DOCKER_LOG"]).open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\\n")
reference = args[3]
mode = (
    os.environ["FAKE_SHA_MODE"]
    if ":sha-" in reference
    else os.environ["FAKE_RELEASE_MODE"]
)
if mode == "absent":
    print(f"{reference}: manifest unknown", file=sys.stderr)
    raise SystemExit(1)
if mode == "builder-phrase":
    print("builder instance manifest unknown", file=sys.stderr)
    raise SystemExit(1)
if mode == "generic-not-found":
    print("repository not found", file=sys.stderr)
    raise SystemExit(1)
if mode == "transport":
    print("connection reset", file=sys.stderr)
    raise SystemExit(1)
print(json.dumps({"digest": "sha256:" + "b" * 64}))
raise SystemExit(0)
""",
            encoding="utf-8",
        )
        docker.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "RUNNER_TEMP": str(root),
            "IMAGE": image,
            "RELEASE_TAG": "v1.45.210-beta",
            "SERVICE": "console",
            "SOURCE_SHA": "a" * 40,
            "FAKE_DOCKER_LOG": str(log),
            "FAKE_SHA_MODE": sha_mode,
            "FAKE_RELEASE_MODE": release_mode,
        }
        result = subprocess.run(
            ["bash", "-c", source],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        invocations = [
            json.loads(line)
            for line in log.read_text(encoding="utf-8").splitlines()
        ]
    return result, invocations


def _run_release_asset_step(
    remote_assets: dict[str, bytes] | None,
    *,
    lookup_error: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    source = _named_step(
        _jobs()["publish-release-manifest"], "Attach manifest to GitHub Release"
    )["run"]
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        fake_bin = root / "bin"
        remote = root / "remote"
        fake_bin.mkdir()
        remote.mkdir()
        manifest = root / "omega-release-manifest-v1.45.210-beta.json"
        checksum = root / "omega-release-manifest-v1.45.210-beta.json.sha256"
        manifest.write_bytes(b"canonical-manifest\n")
        checksum.write_bytes(b"canonical-checksum\n")
        if remote_assets is not None:
            for name, content in remote_assets.items():
                (remote / name).write_bytes(content)
        log = root / "gh.log"
        gh = fake_bin / "gh"
        gh.write_text(
            """#!/usr/bin/env python3
import json
import os
import shutil
import sys
from pathlib import Path

args = sys.argv[1:]
with Path(os.environ["FAKE_GH_LOG"]).open("a", encoding="utf-8") as fh:
    fh.write(json.dumps(args) + "\\n")
remote = Path(os.environ["FAKE_GH_REMOTE"])
if args[:2] == ["release", "download"]:
    pattern = args[args.index("--pattern") + 1]
    target = Path(args[args.index("--dir") + 1])
    target.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(remote / pattern, target / pattern)
    raise SystemExit(0)
if args[:2] == ["release", "upload"]:
    for raw in args[3:]:
        path = Path(raw)
        shutil.copyfile(path, remote / path.name)
    raise SystemExit(0)
if args[:2] == ["release", "create"]:
    raise SystemExit(0)
raise SystemExit(2)
""",
            encoding="utf-8",
        )
        gh.chmod(0o755)
        python_wrapper = fake_bin / "python3"
        python_wrapper.write_text(
            """#!/bin/bash
set -euo pipefail
if [[ "${1:-}" == "scripts/inspect_github_release.py" ]]; then
  case "${FAKE_RELEASE_INSPECTION_MODE}" in
    present)
      exec "${REAL_PYTHON}" -c '
import json, os
print(json.dumps({"schema_version": 1, "state": "present", "tag": os.environ["RELEASE_TAG"], "assets": json.loads(os.environ["FAKE_RELEASE_ASSETS"])}, separators=(",", ":")))
'
      ;;
    absent)
      exec "${REAL_PYTHON}" -c '
import json, os
print(json.dumps({"schema_version": 1, "state": "absent", "tag": os.environ["RELEASE_TAG"], "assets": []}, separators=(",", ":")))
'
      ;;
    error)
      echo "structured lookup transport failure" >&2
      exit 1
      ;;
  esac
fi
exec "${REAL_PYTHON}" "$@"
""",
            encoding="utf-8",
        )
        python_wrapper.chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "CHECKSUM_PATH": str(checksum),
            "GH_TOKEN": "test-token",
            "MANIFEST_PATH": str(manifest),
            "RELEASE_TAG": "v1.45.210-beta",
            "SOURCE_REPOSITORY": "omega-owner/omega",
            "RUNNER_TEMP": str(root),
            "GITHUB_SHA": "a" * 40,
            "FAKE_GH_LOG": str(log),
            "FAKE_GH_REMOTE": str(remote),
            "FAKE_RELEASE_ASSETS": json.dumps(
                sorted(remote_assets) if remote_assets is not None else []
            ),
            "FAKE_RELEASE_INSPECTION_MODE": (
                "error"
                if lookup_error
                else "absent"
                if remote_assets is None
                else "present"
            ),
            "REAL_PYTHON": sys.executable,
        }
        result = subprocess.run(
            ["bash", "-c", source],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        invocations = (
            [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
            if log.exists()
            else []
        )
    return result, invocations


def test_full_stack_skip_is_policy_gated_and_image_publish_fails_closed():
    jobs = _jobs()
    policy = jobs["authorize-release-gate-skips"]
    full_stack = jobs["full-stack-release-gate"]
    preflight = jobs["preflight-release-packages"]
    build = jobs["build-and-push"]

    assert policy["needs"] == "detect-release-changes"
    assert full_stack["if"] == (
        "needs.authorize-release-gate-skips.outputs.full_stack_action == 'run'"
    )
    assert "authorize-release-gate-skips" in full_stack["needs"]
    assert "full-stack-release-gate" in build["needs"]
    assert "authorize-release-gate-skips" in build["needs"]
    for guarded_job in (preflight, build):
        condition = guarded_job["if"]
        for required in (
            "needs.authorize-release-gate-skips.result == 'success'",
            "needs.authorize-release-gate-skips.outputs.full_stack_action == 'run'",
            "needs.full-stack-release-gate.result == 'success'",
            "needs.authorize-release-gate-skips.outputs.full_stack_action == 'skip'",
            "needs.authorize-release-gate-skips.outputs.full_stack_skip_authorized == 'true'",
            "needs.authorize-release-gate-skips.outputs.full_stack_policy_id != 'none'",
            "needs.authorize-release-gate-skips.outputs.production_readiness_stress_action == 'skip'",
            "needs.authorize-release-gate-skips.outputs.production_readiness_stress_skip_authorized == 'true'",
            "needs.authorize-release-gate-skips.outputs.production_readiness_stress_policy_id != 'none'",
            "needs.full-stack-release-gate.result == 'skipped'",
        ):
            assert required in condition
    assert "release_full_stack" not in build["if"]


def test_release_skip_registry_is_explicit_bounded_and_unambiguous():
    registry = _policy()
    assert registry["schema_version"] == 1
    assert set(registry) == {"schema_version", "policies"}

    policies = registry["policies"]
    assert {policy["id"] for policy in policies} == {
        "full-stack-release-metadata-only",
        "full-stack-release-contract-tests-only",
        "production-readiness-stress-beta",
    }
    assert len({policy["id"] for policy in policies}) == len(policies)
    for policy in policies:
        assert set(policy) == {"id", "gate", "owner", "reason", "scope"}
        assert policy["owner"].strip()
        assert policy["reason"].strip()
        assert policy["gate"] in {
            "full-stack-release-gate",
            "production-readiness-stress",
        }
        tag_regex = policy["scope"]["tag_regex"]
        assert tag_regex.startswith("^") and tag_regex.endswith("$")
        re.compile(tag_regex)
        if policy["gate"] == "full-stack-release-gate":
            assert policy["scope"]["all_changed_paths_match"]
            assert policy["scope"]["at_least_one_changed_path_match"]
            for key in (
                "all_changed_paths_match",
                "at_least_one_changed_path_match",
            ):
                for pattern in policy["scope"][key]:
                    assert pattern.startswith("^") and pattern.endswith("$")
                    re.compile(pattern)


def test_only_declared_non_runtime_deltas_can_skip_full_stack():
    tag = "v1.45.210-beta"
    assert _matching_full_stack_policies(tag, ["VERSION"]) == [
        "full-stack-release-metadata-only"
    ]
    assert _matching_full_stack_policies(tag, ["VERSION", "docs/release.md"]) == [
        "full-stack-release-metadata-only"
    ]
    assert _matching_full_stack_policies(
        tag, ["VERSION", "tests/test_release_contract.py"]
    ) == ["full-stack-release-contract-tests-only"]
    assert _matching_full_stack_policies(
        tag, ["VERSION", "cartridges/replicon/tests/test_contract.py"]
    ) == ["full-stack-release-contract-tests-only"]

    for unsafe_delta in (
        ["VERSION", "scripts/apply_db_migrations.sh"],
        ["VERSION", "infra/terraform-gcp/release/ghcr-auth-run.sh"],
        ["VERSION", ".github/workflows/release.yml"],
        ["VERSION", "console/app/main.py"],
        ["VERSION", "console-next/src/app/page.tsx"],
        ["VERSION", "tests-e2e/specs/release.spec.ts"],
        ["VERSION", "tests/test_contract.py", "console/app/main.py"],
    ):
        assert _matching_full_stack_policies(tag, unsafe_delta) == []


def test_policy_job_rejects_tag_version_drift_and_records_skip_evidence():
    job = _jobs()["authorize-release-gate-skips"]
    step = _named_step(job, "Authorize applicable release gate skips")
    source = step["run"]

    assert step["env"]["CHANGED_FILES_JSON"] == (
        "${{ needs.detect-release-changes.outputs.changed_files }}"
    )
    assert step["env"]["RELEASE_TAG"] == "${{ github.ref_name }}"
    assert step["env"]["RELEASE_SKIP_POLICY"] == (".github/release-skip-policy.json")
    assert 'release_tag != f"v{version}"' in source
    assert "ambiguous skip policies" in source
    assert "default fail-closed" in source
    assert "GITHUB_STEP_SUMMARY" in source


def test_policy_runtime_authorizes_only_the_matching_bounded_scope():
    result, values, summary = _run_policy_step(["VERSION"])
    assert result.returncode == 0, result.stderr
    assert values["full_stack_action"] == "skip"
    assert values["full_stack_skip_authorized"] == "true"
    assert values["full_stack_policy_id"] == "full-stack-release-metadata-only"
    assert values["production_readiness_stress_action"] == "skip"
    assert values["production_readiness_stress_skip_authorized"] == "true"
    assert values["production_readiness_stress_policy_id"] == (
        "production-readiness-stress-beta"
    )
    assert "AUTHORIZED SKIP" in summary
    assert "owner=`release-engineering`" in summary


def test_policy_runtime_defaults_to_required_for_any_runtime_delta():
    result, values, summary = _run_policy_step(
        ["VERSION", "scripts/apply_db_migrations.sh"]
    )
    assert result.returncode == 0, result.stderr
    assert values["full_stack_action"] == "run"
    assert values["full_stack_skip_authorized"] == "false"
    assert values["full_stack_policy_id"] == "none"
    assert "`full-stack-release-gate`: **RUN** (default fail-closed)" in summary


def test_policy_runtime_fails_before_decision_when_tag_and_version_drift():
    result, values, summary = _run_policy_step(["VERSION"], release_tag="v9.9.9-beta")
    assert result.returncode != 0
    assert "release tag/version mismatch" in result.stderr
    assert values == {}
    assert summary == ""


def test_beta_stress_skip_comes_from_the_policy_not_tag_substring_logic():
    jobs = _jobs()
    policy_job = jobs["authorize-release-gate-skips"]
    full_stack = jobs["full-stack-release-gate"]
    step = _named_step(full_stack, "Production-readiness gate")

    assert policy_job["outputs"]["production_readiness_stress_action"] == (
        "${{ steps.policy.outputs.production_readiness_stress_action }}"
    )
    assert step["env"]["STRESS_ACTION"] == (
        "${{ needs.authorize-release-gate-skips.outputs.production_readiness_stress_action }}"
    )
    assert "STRESS_SKIP_AUTHORIZED" in step["env"]
    assert "STRESS_POLICY_ID" in step["env"]
    assert '== *"beta"*' not in step["run"]
    assert "OMEGA_PRODUCTION_READINESS_SKIP_STRESS=1" in step["run"]
    assert "production-readiness stress skip is not authorized" in step["run"]


def test_validate_release_runs_every_test_target_resolved_by_detector():
    jobs = _jobs()
    detector_outputs = jobs["detect-release-changes"]["outputs"]
    validate = jobs["validate-release"]

    for output in (
        "root_test_targets",
        "cartridge_test_targets",
        "cartridge_requirement_paths",
    ):
        assert detector_outputs[output] == f"${{{{ steps.detect.outputs.{output} }}}}"

    root_tests = _named_step(validate, "Run detected root tests")
    assert (
        root_tests["if"] == "needs.detect-release-changes.outputs.root_tests == 'true'"
    )
    assert root_tests["env"]["ROOT_TEST_TARGETS"] == (
        "${{ needs.detect-release-changes.outputs.root_test_targets }}"
    )
    assert '[[ -n "${ROOT_TEST_TARGETS}" ]]' in root_tests["run"]
    assert 'python -m pytest -q "${targets[@]}"' in root_tests["run"]

    cartridge_tests = _named_step(validate, "Run detected cartridge tests")
    assert cartridge_tests["if"] == (
        "needs.detect-release-changes.outputs.cartridge_tests == 'true'"
    )
    assert cartridge_tests["env"]["CARTRIDGE_TEST_TARGETS"] == (
        "${{ needs.detect-release-changes.outputs.cartridge_test_targets }}"
    )
    assert '[[ -n "${CARTRIDGE_TEST_TARGETS}" ]]' in cartridge_tests["run"]
    assert 'python -m pytest -q "${targets[@]}"' in cartridge_tests["run"]


def test_release_image_inventory_is_exactly_the_canonical_fifteen():
    services = _canonical_services()

    assert services == [
        "console",
        "workspace",
        "refinement",
        "vault",
        "mcp-infra",
        "airflow",
        "replicon",
        "hubspot",
        "banxico",
        "inegi",
        "sec_edgar",
        "sap_hcm",
        "sap_s4hana",
        "sap_successfactors",
        "salesforce",
    ]
    assert len(services) == len(set(services)) == 15


def test_each_image_is_tagged_by_release_and_sha_and_exports_its_digest():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    build_job = _jobs()["build-and-push"]
    preexisting = _named_step(build_job, "Refuse pre-existing image tags")
    build = _named_step(build_job, "Build and push ${{ matrix.service }}")
    resolved = _named_step(build_job, "Resolve and verify final image digest")
    fragment = _named_step(build_job, "Write image digest fragment")
    upload = _named_step(build_job, "Upload image digest fragment")

    assert workflow["concurrency"] == {
        "group": "release-images-${{ github.sha }}",
        "cancel-in-progress": False,
    }
    assert "docker buildx imagetools inspect" in preexisting["run"]
    assert "manifest unknown" in preexisting["run"]
    assert "manifest unknown|not found" not in preexisting["run"]
    assert "registry lookup failed ambiguously" in preexisting["run"]
    assert "pre-existing release image tag is forbidden" in preexisting["run"]
    assert build["id"] == "build"
    assert "if" not in build
    tags = build["with"]["tags"]
    assert "${{ steps.meta.outputs.image }}:${{ steps.meta.outputs.tag }}" in tags
    assert "${{ steps.meta.outputs.image }}:sha-${{ github.sha }}" in tags
    assert resolved["id"] == "resolved_image"
    assert resolved["env"]["BUILT_DIGEST"] == "${{ steps.build.outputs.digest }}"
    assert "REUSED" not in resolved["env"]
    assert "REUSED_DIGEST" not in resolved["env"]
    assert "final SHA tag digest mismatch" in resolved["run"]
    assert "final release tag digest mismatch" in resolved["run"]
    assert fragment["env"] == {
        "DIGEST": "${{ steps.resolved_image.outputs.digest }}",
        "IMAGE": "${{ steps.meta.outputs.image }}",
        "RELEASE_TAG": "${{ github.ref_name }}",
        "SERVICE": "${{ matrix.service }}",
        "SOURCE_SHA": "${{ github.sha }}",
    }
    assert 're.fullmatch(r"sha256:[0-9a-f]{64}", digest)' in fragment["run"]
    assert 're.fullmatch(r"[0-9a-f]{40}", source_sha)' in fragment["run"]
    assert "release_reference" in fragment["run"]
    assert "sha_reference" in fragment["run"]
    assert upload["with"]["name"] == "release-image-digest-${{ matrix.service }}"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["overwrite"] is True


def test_existing_sha_image_blocks_fresh_release_before_build():
    result, calls = _run_fresh_image_preflight(
        sha_mode="present", release_mode="absent"
    )

    assert result.returncode != 0
    assert "pre-existing release image tag is forbidden" in result.stdout + result.stderr
    assert len(calls) == 1


def test_structurally_absent_tags_authorize_exactly_one_fresh_build_path():
    result, calls = _run_fresh_image_preflight(
        sha_mode="absent", release_mode="absent"
    )

    assert result.returncode == 0, result.stderr
    assert "fresh build is required" in result.stdout
    assert len(calls) == 2


def test_absent_sha_never_overwrites_an_existing_release_tag():
    result, calls = _run_fresh_image_preflight(
        sha_mode="absent", release_mode="present"
    )

    assert result.returncode != 0
    assert "pre-existing release image tag is forbidden" in result.stdout + result.stderr
    assert len(calls) == 2


def test_ambiguous_or_generic_not_found_never_falls_back_to_a_build():
    for mode in ("transport", "generic-not-found", "builder-phrase"):
        result, calls = _run_fresh_image_preflight(
            sha_mode=mode, release_mode="absent"
        )

        assert result.returncode != 0
        assert "lookup failed ambiguously" in result.stdout + result.stderr
        assert len(calls) == 1


def test_manifest_job_is_bound_to_successful_build_and_exact_inventory():
    job = _jobs()["publish-release-manifest"]
    privacy = _named_step(job, "Re-verify canonical release packages are private")
    download = _named_step(job, "Download image digest fragments")
    assemble = _named_step(job, "Assemble and validate release manifest")
    upload = _named_step(job, "Upload canonical release manifest artifact")

    assert "build-and-push" in job["needs"]
    assert job["if"] == "always() && needs.build-and-push.result == 'success'"
    assert privacy["env"] == {
        "GITHUB_TOKEN": "${{ github.token }}",
        "GHCR_OWNER": "${{ github.repository_owner }}",
    }
    assert "scripts/verify_release_package_visibility.py" in privacy["run"]
    assert '--owner "${GHCR_OWNER}"' in privacy["run"]
    assert "--owner-kind user" in privacy["run"]
    assert "gh api" not in privacy["run"]
    assert download["with"] == {
        "pattern": "release-image-digest-*",
        "path": "release-manifest-input",
        "merge-multiple": True,
    }
    assert json.loads(assemble["env"]["EXPECTED_SERVICES_JSON"]) == (
        _canonical_services()
    )
    assert "len(expected) != 15" in assemble["run"]
    assert "len(paths) != 15" in assemble["run"]
    assert "set(by_service) != set(expected)" in assemble["run"]
    assert "release tag mismatch" in assemble["run"]
    assert "source SHA mismatch" in assemble["run"]
    assert upload["with"]["name"] == (
        "omega-release-manifest-${{ github.ref_name }}"
    )
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["overwrite"] is True


def test_manifest_runtime_accepts_exactly_fifteen_bound_digest_fragments():
    result, manifest, checksum, summary = _run_manifest_step()
    assert result.returncode == 0, result.stderr
    assert manifest is not None
    assert manifest["schema_version"] == 1
    assert manifest["repository"] == "omega-owner/omega"
    assert manifest["release_tag"] == "v1.45.210-beta"
    assert manifest["source_sha"] == "a" * 40
    assert [image["service"] for image in manifest["images"]] == (
        _canonical_services()
    )
    manifest_bytes = (
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    expected_hash = hashlib.sha256(manifest_bytes).hexdigest()
    assert checksum == (
        f"{expected_hash}  omega-release-manifest-v1.45.210-beta.json\n"
    )
    assert "Images: **15/15 VERIFIED**" in summary
    assert expected_hash in summary


def test_manifest_runtime_fails_closed_when_any_image_is_missing():
    result, manifest, checksum, summary = _run_manifest_step(
        missing_service="salesforce"
    )
    assert result.returncode != 0
    assert "fragment count must be exactly 15, got 14" in result.stderr
    assert manifest is None
    assert checksum == ""
    assert summary == ""


def test_validated_manifest_is_uploaded_and_attached_to_a_github_release():
    job = _jobs()["publish-release-manifest"]
    release = _named_step(job, "Attach manifest to GitHub Release")

    assert job["permissions"] == {
        "actions": "read",
        "contents": "write",
        "packages": "read",
    }
    assert release["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert release["env"]["SOURCE_REPOSITORY"] == "${{ github.repository }}"
    source = release["run"]
    assert "scripts/inspect_github_release.py" in source
    assert "release not found" not in source
    assert 'gh release upload "${RELEASE_TAG}"' in source
    assert "--clobber" not in source
    assert "gh release download" in source
    assert "cmp --silent" in source
    assert "immutable GitHub Release asset differs" in source
    assert "duplicate canonical GitHub Release asset" in source
    assert "gh release create" in source
    assert "--verify-tag" in source
    assert "--prerelease" in source


def test_identical_existing_release_assets_are_compared_without_mutation():
    manifest_name = "omega-release-manifest-v1.45.210-beta.json"
    checksum_name = f"{manifest_name}.sha256"
    result, calls = _run_release_asset_step(
        {
            manifest_name: b"canonical-manifest\n",
            checksum_name: b"canonical-checksum\n",
        }
    )

    assert result.returncode == 0, result.stderr
    assert "byte-identical" in result.stdout
    assert sum(call[:2] == ["release", "download"] for call in calls) == 2
    assert not any(call[:2] == ["release", "upload"] for call in calls)
    assert not any(call[:2] == ["release", "create"] for call in calls)


def test_mismatched_existing_release_asset_fails_without_overwrite():
    manifest_name = "omega-release-manifest-v1.45.210-beta.json"
    checksum_name = f"{manifest_name}.sha256"
    result, calls = _run_release_asset_step(
        {
            manifest_name: b"different-manifest\n",
            checksum_name: b"canonical-checksum\n",
        }
    )

    assert result.returncode != 0
    assert "immutable GitHub Release asset differs" in result.stdout + result.stderr
    assert not any(call[:2] == ["release", "upload"] for call in calls)
    assert not any("--clobber" in call for call in calls)


def test_absent_release_assets_are_uploaded_without_clobber():
    result, calls = _run_release_asset_step({})

    assert result.returncode == 0, result.stderr
    uploads = [call for call in calls if call[:2] == ["release", "upload"]]
    assert len(uploads) == 1
    assert "--clobber" not in uploads[0]


def test_absent_release_is_created_without_overwrite_flags():
    result, calls = _run_release_asset_step(None)

    assert result.returncode == 0, result.stderr
    creates = [call for call in calls if call[:2] == ["release", "create"]]
    assert len(creates) == 1
    assert "--verify-tag" in creates[0]
    assert "--clobber" not in creates[0]


def test_ambiguous_release_lookup_never_falls_back_to_create():
    result, calls = _run_release_asset_step(None, lookup_error=True)

    assert result.returncode != 0
    assert "lookup failed ambiguously" in result.stdout + result.stderr
    assert not any(call[:2] == ["release", "create"] for call in calls)


def test_package_privacy_gate_precedes_every_manifest_publication_step():
    job = _jobs()["publish-release-manifest"]
    names = [step.get("name") for step in job["steps"]]
    privacy_index = names.index("Re-verify canonical release packages are private")

    for guarded_step in (
        "Download image digest fragments",
        "Assemble and validate release manifest",
        "Upload canonical release manifest artifact",
        "Attach manifest to GitHub Release",
    ):
        assert privacy_index < names.index(guarded_step)


def test_registry_privacy_is_proved_before_any_registry_mutation_and_after_build():
    jobs = _jobs()
    preflight = jobs["preflight-release-packages"]
    build = jobs["build-and-push"]
    publish = jobs["publish-release-manifest"]

    assert preflight["permissions"] == {"contents": "read", "packages": "read"}
    assert "preflight-release-packages" in build["needs"]
    assert "needs.preflight-release-packages.result == 'success'" in build["if"]
    assert _named_step(preflight, "Verify canonical release packages are private")
    assert _named_step(publish, "Re-verify canonical release packages are private")


def test_release_jobs_use_least_privilege_for_package_writes():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = _jobs()

    assert workflow["permissions"] == {"contents": "read"}
    assert jobs["build-and-push"]["permissions"] == {
        "contents": "read",
        "packages": "write",
    }
    for name in (
        "detect-release-changes",
        "authorize-release-gate-skips",
        "validate-release",
        "full-stack-release-gate",
    ):
        assert "packages" not in jobs[name].get("permissions", {})


def test_release_tag_must_point_to_the_exact_canonical_main_commit():
    step = _named_step(
        _jobs()["detect-release-changes"],
        "Verify release tag is exact canonical main",
    )
    source = step["run"]
    assert "refs/remotes/origin/main^{commit}" in source
    assert "refs/tags/${GITHUB_REF_NAME}^{commit}" in source
    assert "${GITHUB_SHA}^{commit}" in source
    assert "release tag is not exact origin/main" in source


def test_previous_release_selection_requires_canonical_release_evidence():
    step = next(
        step
        for step in _jobs()["detect-release-changes"]["steps"]
        if step.get("id") == "previous"
    )
    assert "scripts/select_previous_release.py" in step["run"]
    assert '--repository "${SOURCE_REPOSITORY}"' in step["run"]
    assert step["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    assert step["env"]["SOURCE_REPOSITORY"] == "${{ github.repository }}"
    assert "git describe" not in step["run"]


def test_every_build_is_fresh_and_only_manifest_unknown_means_absent():
    build = _jobs()["build-and-push"]
    preexisting = _named_step(build, "Refuse pre-existing image tags")
    source = preexisting["run"]
    image_build = _named_step(build, "Build and push ${{ matrix.service }}")

    assert "manifest unknown" in source
    assert "|not found" not in source
    assert "reused" not in source
    assert "Resolve immutable SHA image" not in [
        step.get("name") for step in build["steps"]
    ]
    assert "if" not in image_build


def test_rc_and_ga_stress_can_never_be_hidden_inside_a_skipped_full_stack_job():
    for version in ("1.45.210-rc.1", "1.45.210"):
        result, values, _summary = _run_policy_step(
            ["VERSION"], release_tag=f"v{version}", version_override=version
        )
        assert result.returncode == 0, result.stderr
        assert values["production_readiness_stress_action"] == "run"
        assert values["full_stack_action"] == "run"
        assert values["full_stack_skip_authorized"] == "false"
        assert values["full_stack_policy_id"] == "none"
    source = _named_step(
        _jobs()["authorize-release-gate-skips"],
        "Authorize applicable release gate skips",
    )["run"]
    assert "requires the enclosing full-stack gate" in source


def test_release_test_skips_are_versioned_and_enforced_for_pytest_and_playwright():
    assert TEST_SKIP_POLICY.exists()
    registry = json.loads(TEST_SKIP_POLICY.read_text(encoding="utf-8"))
    assert registry["schema_version"] == 1
    assert {scope["runner"] for scope in registry["source_scopes"]} == {
        "pytest",
        "playwright",
    }
    for scope in [*registry["source_scopes"], *registry["runtime_scopes"]]:
        assert scope["owner"].strip()
        assert scope["reason"].strip()
        assert scope["scope"]

    validate = _jobs()["validate-release"]
    full_stack = _jobs()["full-stack-release-gate"]
    for job in (validate, full_stack):
        assert job["env"]["PYTEST_PLUGINS"] == "scripts.verify_test_skip_policy"
        assert job["env"]["OMEGA_RELEASE_TEST_SKIP_POLICY"] == (
            "${{ github.workspace }}/.github/release-test-skip-policy.json"
        )
    assert _named_step(validate, "Verify declared release test skips")
    assert _named_step(full_stack, "Verify declared release test skips")
    assert _named_step(full_stack, "Verify Playwright skip report")


def test_test_only_full_stack_skip_requires_targets_and_forbids_deletions():
    detect_outputs = _jobs()["detect-release-changes"]["outputs"]
    policy_step = _named_step(
        _jobs()["authorize-release-gate-skips"],
        "Authorize applicable release gate skips",
    )
    assert "deleted_files" in detect_outputs
    assert "DELETED_FILES_JSON" in policy_step["env"]
    assert "ROOT_TEST_TARGETS" in policy_step["env"]
    assert "CARTRIDGE_TEST_TARGETS" in policy_step["env"]
    assert "forbid_deleted_paths" in policy_step["run"]
    assert "requires_detected_test_targets" in policy_step["run"]

    existing = "tests/test_release_ci_fail_closed.py"
    result, values, _summary = _run_policy_step(
        ["VERSION", existing], root_test_targets=[existing]
    )
    assert result.returncode == 0, result.stderr
    assert values["full_stack_action"] == "skip"

    for kwargs in (
        {},
        {"deleted_files": [existing], "root_test_targets": [existing]},
    ):
        result, values, _summary = _run_policy_step(
            ["VERSION", existing], **kwargs
        )
        assert result.returncode == 0, result.stderr
        assert values["full_stack_action"] == "run"

    retired = "cartridges/retired_connector/tests/test_contract.py"
    result, values, _summary = _run_policy_step(["VERSION", retired])
    assert result.returncode == 0, result.stderr
    assert values["full_stack_action"] == "run"
