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
        summary_text = summary.read_text(encoding="utf-8") if summary.exists() else ""
    return result, manifest, checksum, summary_text


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


def test_matrix_builds_only_untagged_candidates_and_seals_receipts_last():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    build_job = _jobs()["build-and-push"]
    candidate = _named_step(build_job, "Resolve exact candidate receipt for this run")
    build = _named_step(
        build_job, "Build and push untagged candidate for ${{ matrix.service }}"
    )
    receipt = _named_step(build_job, "Verify candidate and write canonical receipt")
    upload = _named_step(build_job, "Upload immutable candidate receipt last")

    assert workflow["concurrency"] == {
        "group": "release-images-${{ github.sha }}",
        "cancel-in-progress": False,
    }
    assert candidate["env"] == {"GITHUB_TOKEN": "${{ github.token }}"}
    assert "release_image_promotion.py candidate-state" in candidate["run"]
    assert '--run-id "${GITHUB_RUN_ID}"' in candidate["run"]
    assert "GITHUB_RUN_ATTEMPT" not in candidate["run"]
    assert build["id"] == "build"
    assert build["if"] == "steps.candidate.outputs.action == 'build'"
    assert "tags" not in build["with"]
    assert build["with"]["outputs"] == (
        "type=image,name=${{ steps.meta.outputs.image }},push-by-digest=true,"
        "name-canonical=true,push=true,oci-mediatypes=true"
    )
    assert build["with"]["provenance"] == "mode=min"
    assert build["with"]["platforms"] == "linux/amd64"
    assert ":${{ github.ref_name }}" not in str(build)
    assert ":sha-${{ github.sha }}" not in str(build)
    assert ":latest" not in str(build)
    assert "io.omega.release.service=${{ matrix.service }}" in build["with"][
        "labels"
    ]
    for annotation_level in (
        "index:",
        "manifest[linux/amd64]:",
        "manifest-descriptor[linux/amd64]:",
    ):
        assert annotation_level in build["with"]["annotations"]
    assert receipt["if"] == "steps.candidate.outputs.action == 'build'"
    assert '--digest "${{ steps.build.outputs.digest }}"' in receipt["run"]
    assert "release_image_promotion.py write-receipt" in receipt["run"]
    assert upload["with"]["name"] == "release-image-candidate-${{ matrix.service }}"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["overwrite"] is False
    assert build_job["steps"][-1] == upload


def test_manifest_job_is_bound_to_successful_build_and_exact_inventory():
    job = _jobs()["publish-release-manifest"]
    privacy = _named_step(job, "Re-verify canonical release packages are private")
    prepare = _named_step(job, "Prepare canonical recoverable promotion intent")
    seal = _named_step(job, "Seal immutable promotion intent before registry mutation")
    promote = _named_step(job, "Promote exact candidate manifests recoverably")
    verify = _named_step(job, "Verify all final release references")
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
    assert "release_image_promotion.py prepare-intent" in prepare["run"]
    assert '--run-id "${GITHUB_RUN_ID}"' in prepare["run"]
    assert seal["if"] == "steps.intent.outputs.action == 'upload'"
    assert seal["with"]["name"] == "omega-release-promotion-intent"
    assert seal["with"]["overwrite"] is False
    assert "release_image_promotion.py promote" in promote["run"]
    assert "release_image_promotion.py verify-final" in verify["run"]
    assert json.loads(assemble["env"]["EXPECTED_SERVICES_JSON"]) == (
        _canonical_services()
    )
    assert "len(expected) != 15" in assemble["run"]
    assert "len(paths) != 15" in assemble["run"]
    assert "set(by_service) != set(expected)" in assemble["run"]
    assert "release tag mismatch" in assemble["run"]
    assert "source SHA mismatch" in assemble["run"]
    assert upload["with"]["name"] == ("omega-release-manifest-${{ github.ref_name }}")
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
    assert [image["service"] for image in manifest["images"]] == (_canonical_services())
    manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
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
    published = _named_step(job, "Verify GitHub Release is published")

    assert job["permissions"] == {
        "actions": "read",
        "contents": "write",
        "packages": "write",
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
    assert "scripts/inspect_github_release.py" in published["run"]
    assert 'payload.get("state") != "present"' in published["run"]
    assert "draft=false VERIFIED" in published["run"]
    names = [step.get("name") for step in job["steps"]]
    assert names.index("Attach manifest to GitHub Release") < names.index(
        "Verify GitHub Release is published"
    )


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
    before_index = names.index("Re-verify canonical release packages are private")
    promote_index = names.index("Promote exact candidate manifests recoverably")
    after_index = names.index("Verify canonical release packages remain private")

    for guarded_step in (
        "Prepare canonical recoverable promotion intent",
        "Seal immutable promotion intent before registry mutation",
        "Promote exact candidate manifests recoverably",
    ):
        assert before_index < names.index(guarded_step)
    assert promote_index < after_index
    for guarded_step in (
        "Verify all final release references",
        "Assemble and validate release manifest",
        "Upload canonical release manifest artifact",
        "Attach manifest to GitHub Release",
    ):
        assert after_index < names.index(guarded_step)


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
    assert _named_step(publish, "Verify canonical release packages remain private")


def test_release_jobs_use_least_privilege_for_package_writes():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = _jobs()

    assert workflow["permissions"] == {"contents": "read"}
    assert jobs["build-and-push"]["permissions"] == {
        "actions": "read",
        "contents": "read",
        "packages": "write",
    }
    assert jobs["publish-release-manifest"]["permissions"] == {
        "actions": "read",
        "contents": "write",
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


def test_candidate_recovery_uses_structured_oci_and_never_buildx_prose():
    build = _jobs()["build-and-push"]
    candidate = _named_step(build, "Resolve exact candidate receipt for this run")
    image_build = _named_step(
        build, "Build and push untagged candidate for ${{ matrix.service }}"
    )
    source = candidate["run"]

    assert "release_image_promotion.py candidate-state" in source
    assert "imagetools inspect" not in source
    assert "not found" not in source.lower()
    assert "manifest unknown" not in source.lower()
    assert image_build["if"] == "steps.candidate.outputs.action == 'build'"


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

    runtime_by_runner = {scope["runner"]: scope for scope in registry["runtime_scopes"]}
    assert set(runtime_by_runner) == {"pytest", "playwright"}
    for runner, scope in runtime_by_runner.items():
        assert set(scope["scope"]) == {"environment", "authorizations"}
        assert scope["scope"]["environment"] == "release"
        assert isinstance(scope["scope"]["authorizations"], list)
        assert "paths" not in scope["scope"]
        expected = (
            {"nodeid", "phase", "category", "reason"}
            if runner == "pytest"
            else {
                "project",
                "spec_file",
                "spec_title",
                "test_id",
                "category",
                "reason",
            }
        )
        assert all(
            set(authorization) == expected
            for authorization in scope["scope"]["authorizations"]
        )

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
    for key in (
        "E2E_REQUIRE_STACK",
        "OMEGA_ENABLE_E2E_SMOKE",
        "OMEGA_ENABLE_LIVE_STACK_TESTS",
    ):
        assert full_stack["env"][key] == "1"


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
        result, values, _summary = _run_policy_step(["VERSION", existing], **kwargs)
        assert result.returncode == 0, result.stderr
        assert values["full_stack_action"] == "run"

    retired = "cartridges/retired_connector/tests/test_contract.py"
    result, values, _summary = _run_policy_step(["VERSION", retired])
    assert result.returncode == 0, result.stderr
    assert values["full_stack_action"] == "run"
