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
    policy_override: dict[str, object] | None = None,
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
        policy_path = POLICY
        if policy_override is not None:
            policy_path = Path(temp_dir) / "release-skip-policy.json"
            policy_path.write_text(json.dumps(policy_override), encoding="utf-8")
        env = {
            **os.environ,
            "CARTRIDGE_TEST_TARGETS": " ".join(cartridge_test_targets or []),
            "CHANGED_FILES_JSON": json.dumps(changed_files),
            "DELETED_FILES_JSON": json.dumps(deleted_files or []),
            "RELEASE_TAG": release_tag or f"v{version}",
            "RELEASE_SKIP_POLICY": str(policy_path),
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
    draft: bool = False,
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
    for raw in args[3:]:
        if raw.startswith("-"):
            break
        path = Path(raw)
        shutil.copyfile(path, remote / path.name)
    Path(os.environ["FAKE_RELEASE_STATE_FILE"]).write_text("draft", encoding="utf-8")
    raise SystemExit(0)
if args[:2] == ["release", "edit"]:
    Path(os.environ["FAKE_RELEASE_STATE_FILE"]).write_text("present", encoding="utf-8")
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
  mode="${FAKE_RELEASE_INSPECTION_MODE}"
  if [[ -s "${FAKE_RELEASE_STATE_FILE}" ]]; then
    mode="$(< "${FAKE_RELEASE_STATE_FILE}")"
  fi
  case "${mode}" in
    present)
      exec "${REAL_PYTHON}" -c '
import json, os
print(json.dumps({"schema_version": 1, "state": "present", "tag": os.environ["RELEASE_TAG"], "immutable": True, "title": os.environ["RELEASE_TAG"], "body": "Automated OMEGA release manifest: 15 images bound to {} and tested with exact source checkout bind mounts.".format(os.environ["GITHUB_SHA"]), "prerelease": "-" in os.environ["RELEASE_TAG"], "target": os.environ["GITHUB_SHA"], "assets": sorted(os.listdir(os.environ["FAKE_GH_REMOTE"]))}, separators=(",", ":")))
'
      ;;
    draft)
      exec "${REAL_PYTHON}" -c '
import json, os
print(json.dumps({"schema_version": 1, "state": "draft", "tag": os.environ["RELEASE_TAG"], "immutable": False, "title": os.environ["RELEASE_TAG"], "body": "Automated OMEGA release manifest: 15 images bound to {} and tested with exact source checkout bind mounts.".format(os.environ["GITHUB_SHA"]), "prerelease": "-" in os.environ["RELEASE_TAG"], "target": os.environ["GITHUB_SHA"], "assets": sorted(os.listdir(os.environ["FAKE_GH_REMOTE"]))}, separators=(",", ":")))
'
      ;;
    absent)
      exec "${REAL_PYTHON}" -c '
import json, os
print(json.dumps({"schema_version": 1, "state": "absent", "tag": os.environ["RELEASE_TAG"], "immutable": None, "title": None, "body": None, "prerelease": None, "target": None, "assets": []}, separators=(",", ":")))
'
      ;;
    error)
      echo "structured lookup transport failure" >&2
      exit 1
      ;;
  esac
fi
if [[ "${1:-}" == "scripts/verify_release_digest_remote.py" || "${1:-}" == "scripts/verify_release_package_visibility.py" ]]; then
  exit 0
fi
exec "${REAL_PYTHON}" "$@"
""",
            encoding="utf-8",
        )
        python_wrapper.chmod(0o755)
        git = fake_bin / "git"
        git.write_text(
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            "if [[ ${1:-} == fetch ]]; then exit 0; fi\n"
            "if [[ ${1:-} == rev-parse ]]; then echo \"${GITHUB_SHA}\"; exit 0; fi\n"
            "exit 2\n",
            encoding="utf-8",
        )
        git.chmod(0o755)
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
            "GITHUB_REPOSITORY": "omega-owner/omega",
            "GITHUB_REPOSITORY_OWNER": "omega-owner",
            "GITHUB_REF_NAME": "v1.45.210-beta",
            "GITHUB_RUN_ID": "99123",
            "GITHUB_ACTOR": "omega-bot",
            "GHCR_OWNER": "omega-owner",
            "FULL_STACK_ACTION": "run",
            "FULL_STACK_POLICY_ID": "none",
            "FAKE_GH_LOG": str(log),
            "FAKE_GH_REMOTE": str(remote),
            "FAKE_RELEASE_STATE_FILE": str(root / "release-state"),
            "FAKE_RELEASE_ASSETS": json.dumps(
                sorted(remote_assets) if remote_assets is not None else []
            ),
            "FAKE_RELEASE_INSPECTION_MODE": (
                "error"
                if lookup_error
                else "draft"
                if draft
                else "absent"
                if remote_assets is None
                else "present"
            ),
            "REAL_PYTHON": sys.executable,
        }
        if draft:
            (root / "release-state").write_text("draft", encoding="utf-8")
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


def test_exact_digest_stack_cannot_be_skipped_before_publication():
    jobs = _jobs()
    policy = jobs["authorize-release-gate-skips"]
    full_stack = jobs["digest-full-stack-gate"]
    build = jobs["build-and-push"]
    assembly = jobs["assemble-release-manifest"]
    publish = jobs["publish-release-manifest"]

    assert policy["needs"] == "detect-release-changes"
    assert "full_stack_action" not in full_stack["if"]
    assert "authorize-release-gate-skips" in full_stack["needs"]
    assert "authorize-release-gate-skips" in build["needs"]
    assert assembly["needs"] == ["build-and-push"]
    condition = publish["if"]
    assert "needs.digest-full-stack-gate.result == 'success'" in condition
    assert "needs.digest-full-stack-gate.result == 'skipped'" not in condition
    assert "release_full_stack" not in build["if"]


def test_release_skip_registry_is_explicit_bounded_and_unambiguous():
    registry = _policy()
    assert registry["schema_version"] == 1
    assert set(registry) == {"schema_version", "policies"}

    policies = registry["policies"]
    assert {policy["id"] for policy in policies} == {
        "production-readiness-stress-beta"
    }
    assert len({policy["id"] for policy in policies}) == len(policies)
    for policy in policies:
        assert set(policy) == {"id", "gate", "owner", "reason", "scope"}
        assert policy["owner"].strip()
        assert policy["reason"].strip()
        assert policy["gate"] == "production-readiness-stress"
        tag_regex = policy["scope"]["tag_regex"]
        assert tag_regex.startswith("^") and tag_regex.endswith("$")
        re.compile(tag_regex)
        assert set(policy["scope"]) == {"tag_regex"}


def test_no_policy_can_skip_the_exact_digest_stack() -> None:
    assert _matching_full_stack_policies("v1.45.210-beta", ["VERSION"]) == []
    assert "full_stack_action" not in _jobs()["digest-full-stack-gate"]["if"]


def test_policy_job_rejects_tag_version_drift_and_records_skip_evidence():
    job = _jobs()["authorize-release-gate-skips"]
    step = _named_step(job, "Authorize applicable release gate skips")
    source = step["run"]

    assert step["env"]["RELEASE_TAG"] == "${{ github.ref_name }}"
    assert step["env"]["RELEASE_SKIP_POLICY"] == (".github/release-skip-policy.json")
    assert 'release_tag != f"v{version}"' in source
    assert "exactly the stress policy" in source
    assert "digest-full-stack-gate" in source
    assert "GITHUB_STEP_SUMMARY" in source


def test_policy_runtime_authorizes_only_the_matching_bounded_scope():
    result, values, summary = _run_policy_step(["VERSION"])
    assert result.returncode == 0, result.stderr
    assert values["production_readiness_stress_action"] == "skip"
    assert values["production_readiness_stress_skip_authorized"] == "true"
    assert values["production_readiness_stress_policy_id"] == (
        "production-readiness-stress-beta"
    )
    assert "AUTHORIZED SKIP" in summary
    assert "owner=`release-engineering`" in summary
    assert "digest-full-stack-gate`: **RUN (unskippable)**" in summary


def test_beta_stress_policy_does_not_depend_on_changed_paths():
    result, values, summary = _run_policy_step(
        ["VERSION", "scripts/apply_db_migrations.sh"]
    )
    assert result.returncode == 0, result.stderr
    assert values["production_readiness_stress_action"] == "skip"
    assert "digest-full-stack-gate`: **RUN (unskippable)**" in summary


def test_policy_runtime_fails_before_decision_when_tag_and_version_drift():
    result, values, summary = _run_policy_step(["VERSION"], release_tag="v9.9.9-beta")
    assert result.returncode != 0
    assert "release tag/version mismatch" in result.stderr
    assert values == {}
    assert summary == ""


def test_stress_skip_policy_identity_and_beta_scope_are_exact() -> None:
    mutations = (
        ("scope", {"tag_regex": "^.*$"}),
        ("scope", {"tag_regex": r"^v[0-9.]+-rc\..*$"}),
        ("owner", "attacker"),
        ("reason", "skip everything"),
    )
    for field, value in mutations:
        registry = _policy()
        registry["policies"][0][field] = value
        result, values, summary = _run_policy_step(
            ["VERSION"], policy_override=registry
        )
        assert result.returncode != 0
        assert values == {}
        assert summary == ""


def test_beta_stress_skip_comes_from_the_policy_not_tag_substring_logic():
    jobs = _jobs()
    policy_job = jobs["authorize-release-gate-skips"]
    full_stack = jobs["digest-full-stack-gate"]
    step = _named_step(full_stack, "Run all final gates against exact digest stack")

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
    assert 'scripts/run_release_pytest.py -q "${targets[@]}"' in root_tests["run"]

    cartridge_tests = _named_step(validate, "Run detected cartridge tests")
    assert cartridge_tests["if"] == (
        "needs.detect-release-changes.outputs.cartridge_tests == 'true'"
    )
    assert cartridge_tests["env"]["CARTRIDGE_TEST_TARGETS"] == (
        "${{ needs.detect-release-changes.outputs.cartridge_test_targets }}"
    )
    assert '[[ -n "${CARTRIDGE_TEST_TARGETS}" ]]' in cartridge_tests["run"]
    assert 'scripts/run_release_pytest.py -q "${targets[@]}"' in cartridge_tests["run"]


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
    assert "release_digest_chain.py candidate-state" in candidate["run"]
    assert '--run-id "${GITHUB_RUN_ID}"' in candidate["run"]
    assert "GITHUB_RUN_ATTEMPT" not in candidate["run"]
    assert build["id"] == "build"
    assert build["if"] == "steps.candidate.outputs.action == 'build'"
    assert "tags" not in build["with"]
    assert build["with"]["outputs"] == (
        "type=image,name=${{ steps.meta.outputs.image }},push-by-digest=true,"
        "name-canonical=true,push=true,oci-mediatypes=true"
    )
    assert build["with"]["provenance"] is False
    assert build["with"]["platforms"] == "linux/amd64"
    assert ":${{ github.ref_name }}" not in str(build)
    assert ":sha-${{ github.sha }}" not in str(build)
    assert ":latest" not in str(build)
    assert "io.omega.release.service=${{ matrix.service }}" in build["with"][
        "labels"
    ]
    assert "manifest:io.omega.release.service=" in build["with"]["annotations"]
    assert "manifest[" not in build["with"]["annotations"]
    assert "index:" not in build["with"]["annotations"]
    assert "io.omega.release.run-id=${{ github.run_id }}" in build["with"]["annotations"]
    assert receipt["if"] == "steps.candidate.outputs.action == 'build'"
    assert '--digest "${{ steps.build.outputs.digest }}"' in receipt["run"]
    assert "release_digest_chain.py write-receipt" in receipt["run"]
    assert upload["with"]["name"] == "release-image-candidate-${{ matrix.service }}"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["overwrite"] is False
    assert build_job["steps"][-1] == upload


def test_manifest_job_is_bound_to_successful_build_and_exact_inventory():
    job = _jobs()["publish-release-manifest"]
    privacy = _named_step(job, "Verify canonical release packages remain private")
    remote = _named_step(job, "Re-verify tested digest graphs before publication")
    download = next(step for step in job["steps"] if str(step.get("uses", "")).startswith("actions/download-artifact@"))

    assert "digest-full-stack-gate" in job["needs"]
    assert "needs.digest-full-stack-gate.result == 'success'" in job["if"]
    assert "needs.digest-full-stack-gate.result == 'skipped'" not in job["if"]
    assert privacy["env"] == {
        "GITHUB_TOKEN": "${{ github.token }}",
        "GHCR_OWNER": "${{ github.repository_owner }}",
    }
    assert "scripts/verify_release_package_visibility.py" in privacy["run"]
    assert '--owner "${GHCR_OWNER}"' in privacy["run"]
    assert "--owner-kind user" in privacy["run"]
    assert "gh api" not in privacy["run"]
    assert download["with"]["name"] == "${{ needs.assemble-release-manifest.outputs.artifact_name }}"
    assert "verify_release_digest_remote.py" in remote["run"]
    assert "release_image_promotion.py" not in WORKFLOW.read_text(encoding="utf-8")


def test_manifest_runtime_accepts_exactly_fifteen_bound_digest_fragments():
    source = (REPO / "scripts/release_digest_chain.py").read_text(encoding="utf-8")
    assert '"schema_version": 2' in source
    assert '"kind": "omega-release-manifest"' in source
    assert '"digest_reference": f"{image}@{digest}"' in source
    assert '"release_reference"' not in source


def test_manifest_runtime_fails_closed_when_any_image_is_missing():
    source = (REPO / "scripts/release_digest_chain.py").read_text(encoding="utf-8")
    assert "candidate receipt is absent" in source
    assert "for service in CANONICAL_SERVICES" in source


def test_validated_manifest_is_uploaded_and_attached_to_a_github_release():
    job = _jobs()["publish-release-manifest"]
    release = _named_step(job, "Attach manifest to GitHub Release")
    published = _named_step(job, "Verify GitHub Release is published")

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
    assert 'gh release upload "${RELEASE_TAG}" "${missing_assets[@]}"' in source
    assert "--clobber" not in source
    assert "gh release download" in source
    assert "cmp --silent" in source
    assert "GitHub Release asset differs" in source
    assert "duplicate canonical GitHub Release asset" in source
    assert "gh release create" in source
    assert "--draft" in source
    assert 'gh release edit "${RELEASE_TAG}" --draft=false' in source
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
    assert "GitHub Release asset differs" in result.stdout + result.stderr
    assert not any(call[:2] == ["release", "upload"] for call in calls)
    assert not any("--clobber" in call for call in calls)


def test_existing_release_with_extra_asset_is_noncanonical_and_immutable():
    manifest_name = "omega-release-manifest-v1.45.210-beta.json"
    checksum_name = f"{manifest_name}.sha256"
    result, calls = _run_release_asset_step(
        {
            manifest_name: b"canonical-manifest\n",
            checksum_name: b"canonical-checksum\n",
            "unexpected-build-log.txt": b"not part of the release contract\n",
        }
    )

    assert result.returncode != 0
    assert "non-canonical asset" in result.stdout + result.stderr
    assert not any(call[:2] == ["release", "upload"] for call in calls)


def test_published_immutable_release_missing_assets_blocks_without_mutation():
    result, calls = _run_release_asset_step({})

    assert result.returncode != 0
    assert "missing a canonical asset" in result.stdout + result.stderr
    assert not any(call[:2] == ["release", "upload"] for call in calls)


def test_absent_release_is_created_without_overwrite_flags():
    result, calls = _run_release_asset_step(None)

    assert result.returncode == 0, result.stderr
    creates = [call for call in calls if call[:2] == ["release", "create"]]
    assert len(creates) == 1
    assert "--verify-tag" in creates[0]
    assert "--draft" in creates[0]
    assert "--clobber" not in creates[0]
    edits = [call for call in calls if call[:2] == ["release", "edit"]]
    assert edits == [["release", "edit", "v1.45.210-beta", "--draft=false"]]


def test_partial_draft_is_recovered_without_clobber_then_published():
    manifest_name = "omega-release-manifest-v1.45.210-beta.json"
    result, calls = _run_release_asset_step(
        {manifest_name: b"canonical-manifest\n"}, draft=True
    )

    assert result.returncode == 0, result.stderr
    assert not any(call[:2] == ["release", "create"] for call in calls)
    uploads = [call for call in calls if call[:2] == ["release", "upload"]]
    assert len(uploads) == 1
    assert "--clobber" not in uploads[0]
    assert [call for call in calls if call[:2] == ["release", "edit"]] == [
        ["release", "edit", "v1.45.210-beta", "--draft=false"]
    ]


def test_ambiguous_release_lookup_never_falls_back_to_create():
    result, calls = _run_release_asset_step(None, lookup_error=True)

    assert result.returncode != 0
    assert "lookup failed ambiguously" in result.stdout + result.stderr
    assert not any(call[:2] == ["release", "create"] for call in calls)


def test_package_privacy_gate_precedes_every_manifest_publication_step():
    job = _jobs()["publish-release-manifest"]
    names = [step.get("name") for step in job["steps"]]
    before_index = names.index("Verify canonical release packages remain private")
    attach_index = names.index("Attach manifest to GitHub Release")
    final_index = names.index("Final private and reachable release evidence")
    assert before_index < names.index("Re-verify tested digest graphs before publication")
    assert before_index < attach_index < final_index


def test_registry_privacy_is_proved_before_any_registry_mutation_and_after_build():
    jobs = _jobs()
    preflight = jobs["preflight-release-packages"]
    build = jobs["build-and-push"]
    publish = jobs["publish-release-manifest"]

    assert preflight["permissions"] == {"contents": "read", "packages": "read"}
    assert "preflight-release-packages" in build["needs"]
    assert "needs.preflight-release-packages.result == 'success'" in build["if"]
    assert _named_step(preflight, "Verify canonical release packages are private")
    assert _named_step(publish, "Verify canonical release packages remain private")
    assert _named_step(publish, "Final private and reachable release evidence")


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
        "packages": "read",
    }
    for name in (
        "detect-release-changes",
        "authorize-release-gate-skips",
        "validate-release",
        "assemble-release-manifest",
        "digest-full-stack-gate",
    ):
        assert jobs[name].get("permissions", {}).get("packages") != "write"


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

    assert "release_digest_chain.py candidate-state" in source
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
    source = _named_step(
        _jobs()["authorize-release-gate-skips"],
        "Authorize applicable release gate skips",
    )["run"]
    assert "digest-full-stack-gate" in source


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
    full_stack = _jobs()["digest-full-stack-gate"]
    for job in (validate, full_stack):
        assert "PYTEST_PLUGINS" not in job["env"]
        assert job["env"]["OMEGA_RELEASE_TEST_SKIP_POLICY"] == (
            "${{ github.workspace }}/.github/release-test-skip-policy.json"
        )
    assert _named_step(validate, "Verify declared release test skips")
    assert _named_step(full_stack, "Verify sealed release test harness")
    assert "run_release_playwright.py" in (REPO / "scripts/run-e2e.sh").read_text(
        encoding="utf-8"
    )
    assert _named_step(_jobs()["digest-full-stack-gate"], "Verify sealed release test harness")
    for key in (
        "E2E_REQUIRE_STACK",
        "OMEGA_ENABLE_E2E_SMOKE",
        "OMEGA_ENABLE_LIVE_STACK_TESTS",
    ):
        assert full_stack["env"][key] == "1"


def test_release_harness_authority_is_bound_to_the_source_sha_and_propagated():
    jobs = _jobs()
    full_stack = jobs["digest-full-stack-gate"]
    steps = full_stack["steps"]
    checkout_index = next(
        index
        for index, step in enumerate(steps)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    authority = _named_step(full_stack, "Bind release test harness seal to source SHA")
    assert steps[checkout_index + 1] == authority
    assert authority["id"] == "harness_authority"
    for needle in (
        'git show "${GITHUB_SHA}:${seal_path}"',
        'git show "${GITHUB_SHA}:${verifier_path}"',
        'git show "${GITHUB_SHA}:${secure_output_path}"',
        'git show "${GITHUB_SHA}:${docker_lock_path}"',
        'cmp --silent "${committed_seal}" "${seal_path}"',
        'cmp --silent "${committed_verifier}" "${verifier_path}"',
        'cmp --silent "${committed_secure_output}" "${secure_output_path}"',
        'cmp --silent "${committed_docker_lock}" "${docker_lock_path}"',
        'echo "sha256=${seal_sha256}" >> "${GITHUB_OUTPUT}"',
        'echo "verifier_sha256=${verifier_sha256}" >> "${GITHUB_OUTPUT}"',
        'echo "secure_output_sha256=${secure_output_sha256}" >> "${GITHUB_OUTPUT}"',
        'echo "docker_lock_sha256=${docker_lock_sha256}" >> "${GITHUB_OUTPUT}"',
    ):
        assert needle in authority["run"]
    assert "GITHUB_ENV" not in authority["run"]

    expected = "${{ steps.harness_authority.outputs.sha256 }}"
    expected_verifier = (
        "${{ steps.harness_authority.outputs.verifier_sha256 }}"
    )
    for step_name in (
        "Verify sealed release test harness",
        "Run all final gates against exact digest stack",
        "Verify harness seal immediately after final gates",
    ):
        step = _named_step(full_stack, step_name)
        assert step["env"][
            "OMEGA_RELEASE_TEST_HARNESS_SHA256"
        ] == expected
        assert step["env"][
            "OMEGA_RELEASE_TEST_HARNESS_VERIFIER_SHA256"
        ] == expected_verifier
        assert "sha256sum scripts/verify_release_test_harness.py" in step["run"]

    validate = jobs["validate-release"]
    validate_steps = validate["steps"]
    validate_checkout = next(
        index
        for index, step in enumerate(validate_steps)
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert validate_steps[validate_checkout + 1]["name"] == (
        "Bind release test harness seal to source SHA"
    )
    for step_name in (
        "Static release tests",
        "Run detected root tests",
        "Run detected cartridge tests",
    ):
        step = _named_step(validate, step_name)
        assert step["env"][
            "OMEGA_RELEASE_TEST_HARNESS_SHA256"
        ] == expected
        assert step["env"][
            "OMEGA_RELEASE_TEST_HARNESS_VERIFIER_SHA256"
        ] == expected_verifier
        assert "sha256sum scripts/verify_release_test_harness.py" in step["run"]

    production_readiness = (
        REPO / "scripts/production_readiness.sh"
    ).read_text(encoding="utf-8")
    assert "verify_release_harness" in production_readiness
    assert "scripts/verify_release_test_harness.py" in production_readiness


def test_final_release_outputs_are_exclusive_and_compared_to_action_hashes():
    job = _jobs()["digest-full-stack-gate"]
    gates = _named_step(job, "Run all final gates against exact digest stack")
    final = _named_step(
        job, "Re-verify runtime digests and real-data readiness after all gates"
    )
    assert job["steps"].index(gates) < job["steps"].index(final)
    assert final["env"]["OMEGA_RELEASE_SECURE_OUTPUT_SHA256"] == (
        "${{ steps.harness_authority.outputs.secure_output_sha256 }}"
    )
    source = final["run"]
    for needle in (
        'mktemp -d "${RUNNER_TEMP}/omega-release-final.XXXXXX"',
        'chmod 0700 "${FINAL_OUTPUT_DIR}"',
        "/usr/bin/python3 -I scripts/secure_release_output.py",
        '--expected-sha256 "${EXPECTED_COMPOSE_SHA256}"',
        '--expected-sha256 "${EXPECTED_IMAGE_INVENTORY_SHA256}"',
        '--baseline "${RUNNER_TEMP}/omega-release-compose.json"',
        '--baseline "${RUNNER_TEMP}/omega-release-image-inventory.lock"',
        '--compose-config "${FINAL_COMPOSE}"',
    ):
        assert needle in source
    assert "omega-release-compose-final.json" not in source
    assert "omega-release-image-inventory.post-gates" not in source


def test_digest_gate_uses_immutable_docker_and_playwright_authorities() -> None:
    job = _jobs()["digest-full-stack-gate"]
    runtime = _named_step(job, "Freeze trusted Playwright and Docker gate runtimes")
    render = _named_step(job, "Render and start hybrid digest/source release stack")
    gate = _named_step(job, "Run all final gates against exact digest stack")
    assert runtime["id"] == "gate_runtime_authority"
    for needle in (
        "/opt/omega-release-runtime/release_docker_lock.py",
        "/opt/omega-release-runtime/bin/docker",
        "sudo /bin/chown -R root:root",
        "sudo /bin/chmod -R a-w",
        "--print-runtime-sha256",
        'echo "playwright_sha256=${playwright_sha256}"',
        'echo "trusted_path=/opt/omega-release-runtime/bin:${PATH}"',
    ):
        assert needle in runtime["run"]
    assert "GITHUB_PATH" not in render["run"]
    assert "compose[0]=/opt/omega-release-runtime/bin/docker" in render["run"]
    assert gate["env"]["OMEGA_RELEASE_PLAYWRIGHT_RUNTIME_SHA256"] == (
        "${{ steps.gate_runtime_authority.outputs.playwright_sha256 }}"
    )
    assert gate["env"]["PATH"] == (
        "${{ steps.gate_runtime_authority.outputs.trusted_path }}"
    )


def test_untrusted_gate_cannot_persist_file_command_or_shell_poison() -> None:
    job = _jobs()["digest-full-stack-gate"]
    gate = _named_step(job, "Run all final gates against exact digest stack")
    final = _named_step(
        job, "Re-verify runtime digests and real-data readiness after all gates"
    )
    for step in (gate, final):
        assert step["env"]["BASH_ENV"] == "/dev/null"
        assert step["env"]["ENV"] == "/dev/null"
        assert step["env"]["PYTHONPATH"] == ""
        assert step["env"]["PYTHONHOME"] == ""
        assert step["env"]["LD_PRELOAD"] == ""
        assert step["env"]["LD_AUDIT"] == ""
    assert gate["env"]["PATH"] == (
        "${{ steps.gate_runtime_authority.outputs.trusted_path }}"
    )
    assert final["env"]["PATH"] == (
        "/opt/omega-release-runtime/bin:/usr/local/sbin:/usr/local/bin:"
        "/usr/sbin:/usr/bin:/sbin:/bin"
    )
    for needle in (
        'gate_env_stamp="$(file_command_stamp "${GITHUB_ENV}")"',
        'gate_path_stamp="$(file_command_stamp "${GITHUB_PATH}")"',
        "trap verify_gate_file_commands EXIT",
        "-u GITHUB_ENV -u GITHUB_PATH",
        "-u PYTHONHOME -u PYTHONPATH -u PYTHONSTARTUP",
        "-u LD_PRELOAD -u LD_AUDIT -u LD_LIBRARY_PATH",
    ):
        assert needle in gate["run"]
    verifier = "/usr/bin/python3 -I scripts/verify_release_test_harness.py"
    assert verifier in final["run"]
    assert final["run"].index(verifier) < final["run"].index(
        "scripts/secure_release_output.py"
    )


def test_gate_file_command_trap_rejects_a_bash_env_poison_write(
    tmp_path: Path,
) -> None:
    gate = _named_step(
        _jobs()["digest-full-stack-gate"],
        "Run all final gates against exact digest stack",
    )
    source = gate["run"]
    start = source.index("file_command_stamp() {")
    end = source.index("release_make() {")
    trap_source = source[start:end]
    if sys.platform == "darwin":
        trap_source = trap_source.replace(
            "/usr/bin/stat -Lc '%d:%i:%f:%u:%g' --",
            "/usr/bin/stat -f '%d:%i:%p:%u:%g' --",
        ).replace(
            "/usr/bin/sha256sum --",
            "/usr/bin/shasum -a 256 --",
        )
    github_env = tmp_path / "github-env"
    github_path = tmp_path / "github-path"
    github_env.write_bytes(b"")
    github_path.write_bytes(b"")
    poison = tmp_path / "exit-zero"
    sentinel = tmp_path / "poison-executed"
    poison.write_text(
        f"#!/bin/bash\nprintf executed >{sentinel!s}\nexit 0\n",
        encoding="utf-8",
    )
    poison.chmod(0o755)
    result = subprocess.run(
        [
            "bash",
            "-c",
            trap_source
            + f"printf '%s\\n' 'BASH_ENV={poison!s}' >> \"${{GITHUB_ENV}}\"\n",
        ],
        cwd=REPO,
        env={
            **os.environ,
            "BASH_ENV": "/dev/null",
            "ENV": "/dev/null",
            "GITHUB_ENV": str(github_env),
            "GITHUB_PATH": str(github_path),
        },
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 98, result.stdout + result.stderr
    assert "modified a GitHub environment file command" in result.stdout
    assert not sentinel.exists()


def test_dead_full_stack_skip_outputs_are_absent() -> None:
    policy = _jobs()["authorize-release-gate-skips"]
    assert all(not key.startswith("full_stack") for key in policy["outputs"])
    assert "full-stack-release-gate" not in WORKFLOW.read_text(encoding="utf-8")
