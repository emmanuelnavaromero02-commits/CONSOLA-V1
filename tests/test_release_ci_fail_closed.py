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

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github/workflows/release.yml"
CAPACITY_WORKFLOW = REPO / ".github/workflows/release-runner-capacity.yml"
WAIT_FOR_HEALTH = REPO / "scripts/wait_for_health.sh"
POLICY = REPO / ".github/release-skip-policy.json"
TEST_SKIP_POLICY = REPO / ".github/release-test-skip-policy.json"
NESTED_RELEASE_PYTHON_VARIABLES = {
    "OMEGA_RELEASE_PYTEST_REPORT",
    "OMEGA_RELEASE_PYTEST_NONCE",
    "OMEGA_RELEASE_PYTEST_MODE",
    "OMEGA_RELEASE_PYTEST_OWNER_PID",
    "PYTHONPATH",
}


def _jobs() -> dict[str, object]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]


def _capacity_job() -> dict[str, object]:
    workflow = yaml.safe_load(CAPACITY_WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["release-runner-capacity"]


def _named_step(job: dict[str, object], name: str) -> dict[str, object]:
    return next(step for step in job["steps"] if step.get("name") == name)


def _policy() -> dict[str, object]:
    return json.loads(POLICY.read_text(encoding="utf-8"))


def _test_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )


def _test_git_output(repo: Path, *args: str) -> str:
    result = _test_git(repo, *args)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _test_commit(repo: Path, message: str) -> str:
    (repo / "history.txt").write_text(message, encoding="utf-8")
    assert _test_git(repo, "add", "history.txt").returncode == 0
    result = _test_git(repo, "commit", "-m", message)
    assert result.returncode == 0, result.stderr
    return _test_git_output(repo, "rev-parse", "HEAD")


def _recovery_remote(
    tmp_path: Path,
) -> tuple[
    Path,
    Path,
    Path,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
]:
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    checkout = tmp_path / "checkout"
    seed.mkdir()
    assert _test_git(tmp_path, "init", "--bare", str(remote)).returncode == 0
    assert _test_git(seed, "init", "-q").returncode == 0
    assert _test_git(seed, "config", "user.email", "ci@example.com").returncode == 0
    assert _test_git(seed, "config", "user.name", "CI").returncode == 0
    _test_commit(seed, "base")
    assert _test_git(seed, "tag", "v1.45.209-beta").returncode == 0
    failed_210 = _test_commit(seed, "failed .210")
    assert (
        _test_git(seed, "tag", "-a", "v1.45.210-beta", "-m", "failed .210").returncode
        == 0
    )
    failed_210_object = _test_git_output(seed, "rev-parse", "refs/tags/v1.45.210-beta")
    failed_211 = _test_commit(seed, "failed .211")
    assert (
        _test_git(seed, "tag", "-a", "v1.45.211-beta", "-m", "failed .211").returncode
        == 0
    )
    failed_211_object = _test_git_output(seed, "rev-parse", "refs/tags/v1.45.211-beta")
    failed_212 = _test_commit(seed, "failed .212")
    assert (
        _test_git(seed, "tag", "-a", "v1.45.212-beta", "-m", "failed .212").returncode
        == 0
    )
    failed_212_object = _test_git_output(seed, "rev-parse", "refs/tags/v1.45.212-beta")
    failed_213 = _test_commit(seed, "failed .213")
    assert (
        _test_git(seed, "tag", "-a", "v1.45.213-beta", "-m", "failed .213").returncode
        == 0
    )
    failed_213_object = _test_git_output(seed, "rev-parse", "refs/tags/v1.45.213-beta")
    failed_214 = _test_commit(seed, "failed .214")
    assert (
        _test_git(seed, "tag", "-a", "v1.45.214-beta", "-m", "failed .214").returncode
        == 0
    )
    failed_214_object = _test_git_output(seed, "rev-parse", "refs/tags/v1.45.214-beta")
    failed_215 = _test_commit(seed, "failed .215")
    assert (
        _test_git(seed, "tag", "-a", "v1.45.215-beta", "-m", "failed .215").returncode
        == 0
    )
    failed_215_object = _test_git_output(seed, "rev-parse", "refs/tags/v1.45.215-beta")
    _test_commit(seed, "recovery .216")
    assert (
        _test_git(seed, "tag", "-a", "v1.45.216-beta", "-m", "recovery .216").returncode
        == 0
    )
    assert _test_git(seed, "remote", "add", "origin", str(remote)).returncode == 0
    push = _test_git(seed, "push", "origin", "HEAD:refs/heads/main", "--tags")
    assert push.returncode == 0, push.stderr
    clone = _test_git(
        tmp_path,
        "clone",
        "-q",
        "--branch",
        "v1.45.216-beta",
        str(remote),
        str(checkout),
    )
    assert clone.returncode == 0, clone.stderr
    return (
        remote,
        seed,
        checkout,
        failed_210,
        failed_210_object,
        failed_211,
        failed_211_object,
        failed_212,
        failed_212_object,
        failed_213,
        failed_213_object,
        failed_214,
        failed_214_object,
        failed_215,
        failed_215_object,
    )


def _run_recovery_remote_binding(checkout: Path) -> subprocess.CompletedProcess[str]:
    source = _named_step(
        _jobs()["detect-release-changes"],
        "Bind exact remote recovery tag authority",
    )["run"]
    return subprocess.run(
        ["bash", "-c", source],
        cwd=checkout,
        text=True,
        capture_output=True,
        check=False,
    )


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
    draft_settle_delays: int = 0,
    created_state: str = "draft",
    lookup_error: bool = False,
    extra_env: dict[str, str] | None = None,
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
    Path(os.environ["FAKE_RELEASE_STATE_FILE"]).write_text(
        os.environ["FAKE_CREATED_RELEASE_STATE"], encoding="utf-8"
    )
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
  exec "${REAL_PYTHON}" -I -c '
import json
import os
import sys
from pathlib import Path

repo = Path(os.environ["FAKE_RELEASE_REPO_ROOT"]).resolve(strict=True)
sys.path.insert(0, str(repo))
from scripts.inspect_github_release import inspect_release

tag = os.environ["RELEASE_TAG"]
source_sha = os.environ["GITHUB_SHA"]
state_file = Path(os.environ["FAKE_RELEASE_STATE_FILE"])
mode = (
    state_file.read_text(encoding="utf-8").strip()
    if state_file.is_file() and state_file.stat().st_size
    else os.environ["FAKE_RELEASE_INSPECTION_MODE"]
)
delay_file = Path(os.environ["FAKE_DRAFT_SETTLE_DELAY_FILE"])
draft_visible = True
if mode == "draft" and delay_file.is_file():
    remaining = int(delay_file.read_text(encoding="utf-8"))
    if remaining > 0:
        delay_file.write_text(str(remaining - 1), encoding="utf-8")
        draft_visible = False

def release(draft):
    return {
        "id": 99123,
        "tag_name": tag,
        "draft": draft,
        "immutable": not draft,
        "name": tag,
        "body": (
            "Automated OMEGA release manifest: 15 images bound to "
            f"{source_sha} and tested with exact source "
            "checkout bind mounts."
        ),
        "prerelease": "-" in tag,
        "target_commitish": source_sha,
        "assets": [
            {"name": name}
            for name in sorted(os.listdir(os.environ["FAKE_GH_REMOTE"]))
        ],
    }

def fetch(url, _headers):
    if mode == "error":
        raise RuntimeError("structured lookup transport failure")
    if "/releases/tags/" in url:
        return (200, release(False)) if mode == "present" else (404, None)
    if url.endswith("/releases?per_page=100&page=1"):
        return (
            (200, [release(True)])
            if mode == "draft" and draft_visible
            else (200, [])
        )
    raise RuntimeError(f"unexpected fake GitHub API URL: {url}")

print(
    json.dumps(
        inspect_release(
            repository=os.environ["SOURCE_REPOSITORY"],
            tag=tag,
            token=os.environ["GH_TOKEN"],
            fetcher=fetch,
        ),
        separators=(",", ":"),
    )
)
'
fi
if [[ "${1:-}" == "scripts/verify_release_digest_remote.py" || "${1:-}" == "scripts/verify_release_package_visibility.py" ]]; then
  exit 0
fi
exec "${REAL_PYTHON}" "$@"
""",
            encoding="utf-8",
        )
        python_wrapper.chmod(0o755)
        sleep = fake_bin / "sleep"
        sleep.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        sleep.chmod(0o755)
        git = fake_bin / "git"
        git.write_text(
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            "if [[ ${1:-} == fetch ]]; then exit 0; fi\n"
            'if [[ ${1:-} == rev-parse ]]; then echo "${GITHUB_SHA}"; exit 0; fi\n'
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
            "FAKE_RELEASE_REPO_ROOT": str(REPO),
            "FAKE_CREATED_RELEASE_STATE": created_state,
            "FAKE_DRAFT_SETTLE_DELAY_FILE": str(root / "draft-settle-delay"),
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
        env.update(extra_env or {})
        for variable in NESTED_RELEASE_PYTHON_VARIABLES:
            env.pop(variable, None)
        if draft:
            (root / "release-state").write_text("draft", encoding="utf-8")
        if draft_settle_delays:
            (root / "draft-settle-delay").write_text(
                str(draft_settle_delays), encoding="utf-8"
            )
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
    assert {policy["id"] for policy in policies} == {"production-readiness-stress-beta"}
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
    assert "io.omega.release.service=${{ matrix.service }}" in build["with"]["labels"]
    assert "manifest:io.omega.release.service=" in build["with"]["annotations"]
    assert "manifest[" not in build["with"]["annotations"]
    assert "index:" not in build["with"]["annotations"]
    assert (
        "io.omega.release.run-id=${{ github.run_id }}" in build["with"]["annotations"]
    )
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
    download = next(
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/download-artifact@")
    )

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
    assert (
        download["with"]["name"]
        == "${{ needs.assemble-release-manifest.outputs.artifact_name }}"
    )
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


def test_release_asset_fixture_never_executes_repo_sitecustomize(
    tmp_path: Path,
) -> None:
    sentinel = tmp_path / "sitecustomize-executed"
    shadow = REPO / "sitecustomize.py"
    assert not shadow.exists()
    shadow.write_text(
        "import os\n"
        "from pathlib import Path\n"
        "Path(os.environ['REPO_SITECUSTOMIZE_SENTINEL']).write_text('executed')\n",
        encoding="utf-8",
    )
    manifest_name = "omega-release-manifest-v1.45.210-beta.json"
    try:
        result, _calls = _run_release_asset_step(
            {
                manifest_name: b"canonical-manifest\n",
                f"{manifest_name}.sha256": b"canonical-checksum\n",
            },
            extra_env={
                "PYTHONPATH": str(REPO),
                "OMEGA_RELEASE_PYTEST_REPORT": str(tmp_path / "outer-report.json"),
                "OMEGA_RELEASE_PYTEST_NONCE": "a" * 64,
                "OMEGA_RELEASE_PYTEST_MODE": "execute",
                "OMEGA_RELEASE_PYTEST_OWNER_PID": "1",
                "REPO_SITECUSTOMIZE_SENTINEL": str(sentinel),
            },
        )
    finally:
        shadow.unlink(missing_ok=True)

    assert result.returncode == 0, result.stderr
    assert not sentinel.exists()


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


def test_actual_inspector_settles_absent_to_created_draft_then_publishes():
    result, calls = _run_release_asset_step(None, draft_settle_delays=2)

    assert result.returncode == 0, result.stderr
    creates = [call for call in calls if call[:2] == ["release", "create"]]
    assert len(creates) == 1
    assert "--verify-tag" in creates[0]
    assert "--draft" in creates[0]
    assert "--clobber" not in creates[0]
    edits = [call for call in calls if call[:2] == ["release", "edit"]]
    assert edits == [["release", "edit", "v1.45.210-beta", "--draft=false"]]


def test_post_create_ambiguous_lookup_blocks_without_publication_or_retry():
    result, calls = _run_release_asset_step(None, created_state="error")

    assert result.returncode != 0
    assert "lookup failed ambiguously" in result.stdout + result.stderr
    assert not any(call[:2] == ["release", "edit"] for call in calls)


def test_post_create_perpetual_absence_exhausts_without_duplicate_create():
    result, calls = _run_release_asset_step(None, created_state="absent")

    assert result.returncode != 0
    assert "did not become discoverable" in result.stdout + result.stderr
    assert sum(call[:2] == ["release", "create"] for call in calls) == 1
    assert not any(call[:2] == ["release", "edit"] for call in calls)


def test_post_create_present_race_blocks_without_editing_again():
    result, calls = _run_release_asset_step(None, created_state="present")

    assert result.returncode != 0
    assert "changed publication state" in result.stdout + result.stderr
    assert sum(call[:2] == ["release", "create"] for call in calls) == 1
    assert not any(call[:2] == ["release", "edit"] for call in calls)


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
    assert before_index < names.index(
        "Re-verify tested digest graphs before publication"
    )
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
    job = _jobs()["detect-release-changes"]
    binding = _named_step(job, "Bind exact remote recovery tag authority")
    step = next(step for step in job["steps"] if step.get("id") == "previous")
    assert job["steps"].index(binding) < job["steps"].index(step)
    assert binding["if"] == "github.ref_name == 'v1.45.216-beta'"
    for needle in (
        "git fetch --no-tags --force --atomic origin",
        "+refs/tags/v1.45.216-beta:${authority}/current",
        "+refs/tags/v1.45.210-beta:${authority}/failed-0",
        "+refs/tags/v1.45.211-beta:${authority}/failed-1",
        "+refs/tags/v1.45.212-beta:${authority}/failed-2",
        "+refs/tags/v1.45.213-beta:${authority}/failed-3",
        "+refs/tags/v1.45.214-beta:${authority}/failed-4",
        "+refs/tags/v1.45.215-beta:${authority}/failed-5",
        "+refs/tags/v1.45.209-beta:${authority}/base",
        'git update-ref -d "${authority}/${name}"',
        'git show-ref --verify --quiet "${authority}/${name}"',
    ):
        assert needle in binding["run"]
    assert binding["run"].count("git fetch --no-tags --force --atomic origin") == 1
    assert binding["run"].count("+refs/tags/") == 8
    assert (
        binding["run"].count(
            "for name in current failed-0 failed-1 failed-2 failed-3 failed-4 failed-5 base; do"
        )
        == 2
    )
    assert "scripts/select_previous_release.py" in step["run"]
    assert '--repository "${SOURCE_REPOSITORY}"' in step["run"]
    assert step["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    assert step["env"]["SOURCE_REPOSITORY"] == "${{ github.repository }}"
    assert "git describe" not in step["run"]


@pytest.mark.parametrize(
    "deleted_tag",
    [
        "v1.45.209-beta",
        "v1.45.210-beta",
        "v1.45.211-beta",
        "v1.45.212-beta",
        "v1.45.213-beta",
        "v1.45.214-beta",
        "v1.45.215-beta",
        "v1.45.216-beta",
    ],
)
def test_remote_recovery_binding_rejects_any_deleted_tag_atomically(
    deleted_tag: str,
    tmp_path: Path,
) -> None:
    (
        remote,
        _seed,
        checkout,
        _failed_210,
        _failed_210_object,
        _failed_211,
        _failed_211_object,
        _failed_212,
        _failed_212_object,
        _failed_213,
        _failed_213_object,
        _failed_214,
        _failed_214_object,
        _failed_215,
        _failed_215_object,
    ) = _recovery_remote(tmp_path)
    stale_object = _test_git_output(checkout, "rev-parse", f"refs/tags/{deleted_tag}")
    assert (
        _test_git(remote, "update-ref", "-d", f"refs/tags/{deleted_tag}").returncode
        == 0
    )

    result = _run_recovery_remote_binding(checkout)

    assert result.returncode != 0
    assert (
        _test_git_output(checkout, "rev-parse", f"refs/tags/{deleted_tag}")
        == stale_object
    )
    for name in (
        "current",
        "failed-0",
        "failed-1",
        "failed-2",
        "failed-3",
        "failed-4",
        "failed-5",
        "base",
    ):
        assert (
            _test_git(
                checkout,
                "show-ref",
                "--verify",
                f"refs/omega-release-authority/v1.45.216-beta/{name}",
            ).returncode
            != 0
        )


def test_remote_recovery_binding_replaces_stale_checkout_with_moved_tag(
    tmp_path: Path,
) -> None:
    (
        remote,
        seed,
        checkout,
        _failed_210,
        failed_210_object,
        _failed_211,
        _failed_211_object,
        _failed_212,
        _failed_212_object,
        _failed_213,
        _failed_213_object,
        _failed_214,
        _failed_214_object,
        _failed_215,
        _failed_215_object,
    ) = _recovery_remote(tmp_path)
    assert _test_git(seed, "tag", "-d", "v1.45.210-beta").returncode == 0
    assert (
        _test_git(
            seed,
            "tag",
            "-a",
            "v1.45.210-beta",
            "-m",
            "moved",
            "HEAD",
        ).returncode
        == 0
    )
    push = _test_git(seed, "push", "--force", "origin", "refs/tags/v1.45.210-beta")
    assert push.returncode == 0, push.stderr
    moved_object = _test_git_output(remote, "rev-parse", "refs/tags/v1.45.210-beta")
    assert moved_object != failed_210_object

    result = _run_recovery_remote_binding(checkout)

    assert result.returncode == 0, result.stderr
    authority = "refs/omega-release-authority/v1.45.216-beta/failed-0"
    assert _test_git_output(checkout, "rev-parse", authority) == moved_object
    assert _test_git_output(checkout, "rev-parse", "refs/tags/v1.45.210-beta") == (
        failed_210_object
    )


def test_remote_recovery_binding_preserves_lightweight_remote_identity(
    tmp_path: Path,
) -> None:
    (
        _remote,
        seed,
        checkout,
        failed_210,
        failed_210_object,
        _failed_211,
        _failed_211_object,
        _failed_212,
        _failed_212_object,
        _failed_213,
        _failed_213_object,
        _failed_214,
        _failed_214_object,
        _failed_215,
        _failed_215_object,
    ) = _recovery_remote(tmp_path)
    assert _test_git(seed, "tag", "-d", "v1.45.210-beta").returncode == 0
    assert _test_git(seed, "tag", "v1.45.210-beta", failed_210).returncode == 0
    push = _test_git(seed, "push", "--force", "origin", "refs/tags/v1.45.210-beta")
    assert push.returncode == 0, push.stderr

    result = _run_recovery_remote_binding(checkout)

    assert result.returncode == 0, result.stderr
    authority = "refs/omega-release-authority/v1.45.216-beta/failed-0"
    assert _test_git_output(checkout, "rev-parse", authority) == failed_210
    assert _test_git_output(checkout, "cat-file", "-t", authority) == "commit"
    assert _test_git_output(checkout, "rev-parse", "refs/tags/v1.45.210-beta") == (
        failed_210_object
    )


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
    assert _named_step(
        _jobs()["digest-full-stack-gate"], "Verify sealed release test harness"
    )
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
        'git show "${GITHUB_SHA}:${runner_disk_path}"',
        'cmp --silent "${committed_seal}" "${seal_path}"',
        'cmp --silent "${committed_verifier}" "${verifier_path}"',
        'cmp --silent "${committed_secure_output}" "${secure_output_path}"',
        'cmp --silent "${committed_docker_lock}" "${docker_lock_path}"',
        'cmp --silent "${committed_runner_disk}" "${runner_disk_path}"',
        'echo "sha256=${seal_sha256}" >> "${GITHUB_OUTPUT}"',
        'echo "verifier_sha256=${verifier_sha256}" >> "${GITHUB_OUTPUT}"',
        'echo "secure_output_sha256=${secure_output_sha256}" >> "${GITHUB_OUTPUT}"',
        'echo "docker_lock_sha256=${docker_lock_sha256}" >> "${GITHUB_OUTPUT}"',
        'echo "runner_disk_sha256=${runner_disk_sha256}" >> "${GITHUB_OUTPUT}"',
    ):
        assert needle in authority["run"]
    assert "GITHUB_ENV" not in authority["run"]

    expected = "${{ steps.harness_authority.outputs.sha256 }}"
    expected_verifier = "${{ steps.harness_authority.outputs.verifier_sha256 }}"
    for step_name in (
        "Verify sealed release test harness",
        "Run all final gates against exact digest stack",
        "Verify harness seal immediately after final gates",
    ):
        step = _named_step(full_stack, step_name)
        assert step["env"]["OMEGA_RELEASE_TEST_HARNESS_SHA256"] == expected
        assert (
            step["env"]["OMEGA_RELEASE_TEST_HARNESS_VERIFIER_SHA256"]
            == expected_verifier
        )
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
        assert step["env"]["OMEGA_RELEASE_TEST_HARNESS_SHA256"] == expected
        assert (
            step["env"]["OMEGA_RELEASE_TEST_HARNESS_VERIFIER_SHA256"]
            == expected_verifier
        )
        assert "sha256sum scripts/verify_release_test_harness.py" in step["run"]

    production_readiness = (REPO / "scripts/production_readiness.sh").read_text(
        encoding="utf-8"
    )
    assert "verify_release_harness" in production_readiness
    assert "scripts/verify_release_test_harness.py" in production_readiness


def test_validate_release_has_history_and_prepares_pull_never_root_runtime():
    validate = _jobs()["validate-release"]
    steps = validate["steps"]
    checkout = next(
        step
        for step in steps
        if str(step.get("uses", "")).startswith("actions/checkout@")
    )
    assert checkout["with"]["fetch-depth"] == 0

    postgres = _named_step(
        validate, "Preload PostgreSQL images for pull-never release tests"
    )
    minio = _named_step(
        validate, "Preload exact MinIO image for pull-never release tests"
    )
    assert "for ref in postgres:15 postgres:15.18" in postgres["run"]
    assert 'docker pull "${ref}"' in postgres["run"]
    assert 'docker image inspect "${ref}" >/dev/null' in postgres["run"]
    minio_source = minio["run"]
    assert 'docker pull "${minio_ref}"' in minio_source
    assert 'docker tag "${minio_ref}" "${minio_tag}"' in minio_source
    assert (
        "sha256:1dce27c494a16bae114774f1cec295493f3613142713130c2d22dd5696be6ad3"
        in minio_source
    )
    assert "RepoDigests" in minio_source
    assert minio_source.count("docker image inspect") >= 3
    assert "|| true" not in minio_source

    prepare = _named_step(
        validate, "Prepare hermetic DuckDB extension cache for detected root tests"
    )
    assert prepare["if"] == (
        "needs.detect-release-changes.outputs.root_tests == 'true'"
    )
    assert prepare["id"] == "root_duckdb_cache"
    assert prepare["env"]["PYTHON_BIN"] == "python"
    assert prepare["env"]["REFINEMENT_IMAGE"] == (
        "refinement:release-validation-${{ github.sha }}"
    )
    assert prepare["shell"] == "bash"
    prepare_source = prepare["run"]
    for needle in (
        'mktemp -d "${RUNNER_TEMP}/omega-release-duckdb.XXXXXX"',
        'DUCKDB_TEST_HOME="${cache_root}/home"',
        'DUCKDB_CACHE_MANIFEST="${cache_root}/extensions.sha256"',
        "scripts/prepare_refinement_duckdb_ci.sh",
        'chmod -R a-w "${DUCKDB_TEST_HOME}/.duckdb"',
        'chmod a-w "${DUCKDB_CACHE_MANIFEST}"',
        "manifest_sha256=",
        "home=%s",
        "manifest=%s",
        "manifest_sha256=%s",
    ):
        assert needle in prepare_source
    assert "/tmp/refinement-duckdb-home" not in prepare_source

    early_harness = _named_step(
        validate, "Verify release test harness before runtime preparation"
    )
    assert early_harness["env"]["OMEGA_RELEASE_TEST_HARNESS_SHA256"] == (
        "${{ steps.harness_authority.outputs.sha256 }}"
    )
    assert "python3 -I scripts/verify_release_test_harness.py" in early_harness["run"]

    root_tests = _named_step(validate, "Run detected root tests")
    assert root_tests["env"]["HOME"] == ("${{ steps.root_duckdb_cache.outputs.home }}")
    for name in (
        "Static release tests",
        "Run detected root tests",
        "Run detected cartridge tests",
    ):
        assert "env -u GITHUB_ENV -u GITHUB_PATH" in _named_step(validate, name)["run"]
    verify = _named_step(
        validate, "Verify hermetic DuckDB extension cache after root tests"
    )
    assert verify["if"] == (
        "always() && needs.detect-release-changes.outputs.root_tests == 'true'"
    )
    assert verify["env"]["HOME"] == "${{ steps.root_duckdb_cache.outputs.home }}"
    assert verify["env"]["DUCKDB_CACHE_MANIFEST"] == (
        "${{ steps.root_duckdb_cache.outputs.manifest }}"
    )
    assert verify["env"]["EXPECTED_MANIFEST_SHA256"] == (
        "${{ steps.root_duckdb_cache.outputs.manifest_sha256 }}"
    )
    assert (
        verify["env"]
        | {
            "BASH_ENV": "/dev/null",
            "ENV": "/dev/null",
            "LD_AUDIT": "",
            "LD_LIBRARY_PATH": "",
            "LD_PRELOAD": "",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        }
        == verify["env"]
    )
    for needle in (
        'test -s "${DUCKDB_CACHE_MANIFEST}"',
        'test ! -L "${DUCKDB_CACHE_MANIFEST}"',
        "EXPECTED_MANIFEST_SHA256",
        'find "${HOME}/.duckdb" ! -type d ! -type f',
        'find "${HOME}/.duckdb" -type f -print0',
        "LC_ALL=C sort -z",
        "xargs -0 sha256sum",
        'cmp --silent "${DUCKDB_CACHE_MANIFEST}" "${actual_manifest}"',
    ):
        assert needle in verify["run"]

    assert steps.index(early_harness) < steps.index(postgres)
    assert steps.index(postgres) < steps.index(minio) < steps.index(prepare)
    assert steps.index(minio) < steps.index(
        _named_step(validate, "Static release tests")
    )
    assert steps.index(prepare) < steps.index(root_tests)
    assert steps.index(root_tests) < steps.index(verify)


def test_exact_minio_preload_blocks_digest_or_tag_substitution(tmp_path: Path) -> None:
    source = _named_step(
        _jobs()["validate-release"],
        "Preload exact MinIO image for pull-never release tests",
    )["run"]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake = fake_bin / "docker"
    fake.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
tag='minio/minio:RELEASE.2024-12-18T13-15-44Z'
digest='sha256:1dce27c494a16bae114774f1cec295493f3613142713130c2d22dd5696be6ad3'
ref="${tag}@${digest}"
case "${1:-}" in
  pull) [[ "${2:-}" == "${ref}" ]] ;;
  tag) [[ "${2:-}" == "${ref}" && "${3:-}" == "${tag}" ]] ;;
  image)
    [[ "${2:-}" == inspect ]]
    if [[ "$*" == *RepoDigests* ]]; then
      if [[ "${FAKE_BAD_DIGEST:-0}" == 1 ]]; then
        printf '["minio/minio@sha256:%064d"]\n' 0
      else
        printf '["minio/minio@%s"]\n' "${digest}"
      fi
    elif [[ "$*" == *'.Id'* ]]; then
      if [[ "$*" == *"${tag}"* && "$*" != *"${ref}"* && "${FAKE_BAD_TAG:-0}" == 1 ]]; then
        printf 'sha256:substituted\n'
      else
        printf 'sha256:exact\n'
      fi
    fi
    ;;
  *) exit 91 ;;
esac
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    base_env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}"}

    def run(**extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", source],
            cwd=REPO,
            env={**base_env, **extra},
            text=True,
            capture_output=True,
            check=False,
        )

    assert run().returncode == 0
    assert run(FAKE_BAD_DIGEST="1").returncode != 0
    assert run(FAKE_BAD_TAG="1").returncode != 0


def test_duckdb_cache_verifier_blocks_manifest_content_and_topology_drift(
    tmp_path: Path,
) -> None:
    verify = _named_step(
        _jobs()["validate-release"],
        "Verify hermetic DuckDB extension cache after root tests",
    )
    cache_home = tmp_path / "home"
    extension_dir = cache_home / ".duckdb/extensions/v1.2.2/linux_amd64_gcc4"
    extension_dir.mkdir(parents=True)
    extension = extension_dir / "httpfs.duckdb_extension"
    extension.write_bytes(b"reviewed")
    manifest = tmp_path / "extensions.sha256"
    manifest.write_text(
        f"{hashlib.sha256(extension.read_bytes()).hexdigest()}  {extension}\n",
        encoding="utf-8",
    )
    expected = hashlib.sha256(manifest.read_bytes()).hexdigest()
    env = {
        **os.environ,
        "BASH_ENV": verify["env"]["BASH_ENV"],
        "ENV": verify["env"]["ENV"],
        "HOME": str(cache_home),
        "LD_AUDIT": verify["env"]["LD_AUDIT"],
        "LD_LIBRARY_PATH": verify["env"]["LD_LIBRARY_PATH"],
        "LD_PRELOAD": verify["env"]["LD_PRELOAD"],
        "DUCKDB_CACHE_MANIFEST": str(manifest),
        "EXPECTED_MANIFEST_SHA256": expected,
        "PATH": verify["env"]["PATH"],
        "RUNNER_TEMP": str(tmp_path),
    }

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", verify["run"]],
            cwd=REPO,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    assert run().returncode == 0
    extension.write_bytes(b"mutated")
    assert run().returncode != 0
    extension.write_bytes(b"reviewed")
    (extension_dir / "unexpected.duckdb_extension").write_bytes(b"extra")
    assert run().returncode != 0
    (extension_dir / "unexpected.duckdb_extension").unlink()
    (extension_dir / "alias.duckdb_extension").symlink_to(extension)
    assert run().returncode != 0


def test_failed_210_through_215_releases_are_preserved_and_version_moves_forward():
    assert (REPO / "VERSION").read_text(encoding="utf-8").strip() == ("1.45.216-beta")
    evidence = (
        REPO / "docs/release-evidence/omega-f2-digest-release-gate.md"
    ).read_text(encoding="utf-8")
    flattened_evidence = " ".join(evidence.split())
    current_seal_sha256 = hashlib.sha256(
        (REPO / ".github/release-test-harness-seal.json").read_bytes()
    ).hexdigest()
    assert current_seal_sha256 in flattened_evidence
    for needle in (
        "v1.45.212-beta",
        "v1.45.213-beta",
        "v1.45.214-beta",
        "v1.45.215-beta",
        "v1.45.216-beta",
        "v1.45.211-beta",
        "v1.45.210-beta",
        "31801477645",
        "31809737977",
        "31823738299",
        "31831837077",
        "6c70e0067eb44dd991d355b3e5cab663300c790b",
        "2429e9a2bdab13ff00740fe318009fd5b101850d",
        "cadf0b28b771257bc6cb9129cf8b4cd72ef5adff",
        "cc0873e4d86bb5bd2f183a003d43ff8a0970df8c",
        "5e3bbc3d1475486bbc0ddabb57b440210ac0782c",
        "dd882bc08bb445d1446f9cbbe313448b24827720",
        "926330d8e1e2067e4e56429fb91e4585ffa4eb43",
        "4bcfda1811d4cbe0511624e0d5cd9c1f5205926b",
        "31851541639",
        "f40a3ab516689343514411806318cffd4f67c3bd",
        "82a7e45adff10b4877b1bfb0e6acab4206744c60",
        "Docker control environment is forbidden after release lock: COMPOSE_FILE",
        "13 failed, 689 passed, 18 errors",
        "1 failed, 720 passed, 16 errors",
        "Freeze trusted Playwright and Docker gate runtimes",
        "PermissionError",
        "no space left on device",
        "no preflight, image build, manifest, digest gate, or release assets ran",
    ):
        assert needle in flattened_evidence

    failed_214_offset = evidence.index("31843803006")
    failed_214_context = evidence[
        max(0, failed_214_offset - 500) : failed_214_offset + 2_500
    ]
    for needle in (
        "v1.45.214-beta",
        "cea2ec51314248daf26710cfe8a94a483d7287eb",
        "325170ff109e88860df857c8615f05b059698fec",
        "dependency failed to start: container mode_airflow is unhealthy",
        "publish-release-manifest",
        "was skipped",
        "no GitHub Release",
    ):
        assert needle in failed_214_context

    failed_215_offset = flattened_evidence.index("31851541639")
    failed_215_context = flattened_evidence[
        max(0, failed_215_offset - 500) : failed_215_offset + 3_500
    ]
    for needle in (
        "v1.45.215-beta",
        "f40a3ab516689343514411806318cffd4f67c3bd",
        "82a7e45adff10b4877b1bfb0e6acab4206744c60",
        "Wait for exact digest stack basic readiness",
        "240 seconds",
        "exit 97",
        "Docker control environment is forbidden after release lock: COMPOSE_FILE",
        "false `starting` states",
        "Airflow webserver and scheduler were healthy",
        "3259df40b06c312b39ad4d8ad0c1731e98b2f2f986165b850d7e766c3ff54393",
        "all 15 candidate images remained private and tagless (`tags: []`)",
        "zero canonical `v1.45.215-beta` tags across the 15 packages",
        "publish-release-manifest",
        "was skipped",
        "GitHub Release remained `404`",
    ):
        assert needle in failed_215_context


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
        "/opt/omega-release-runtime/docker-config",
        "scripts/normalize_release_runtime_permissions.py",
        "/opt/omega-release-runtime/browsers",
        "--trusted-config %q",
        "sudo /bin/chown -R root:root",
        "sudo /bin/chmod -R a-w tests-e2e",
        "--print-runtime-sha256",
        'echo "playwright_sha256=${playwright_sha256}"',
        'echo "trusted_path=/opt/omega-release-runtime/bin:${PATH}"',
    ):
        assert needle in runtime["run"]
    source = runtime["run"]
    assert source.index("sudo /bin/cp -a") < source.index(
        "scripts/normalize_release_runtime_permissions.py"
    )
    assert source.index("scripts/normalize_release_runtime_permissions.py") < (
        source.index("sudo /bin/chown -R root:root")
    )
    assert source.index("sudo /bin/chown -R root:root") < source.index(
        "--print-runtime-sha256"
    )
    assert (
        "chmod -R a-w \\\n            tests-e2e /opt/omega-release-runtime"
        not in source
    )
    assert "GITHUB_PATH" not in render["run"]
    assert "compose[0]=/opt/omega-release-runtime/bin/docker" in render["run"]
    assert gate["env"]["OMEGA_RELEASE_PLAYWRIGHT_RUNTIME_SHA256"] == (
        "${{ steps.gate_runtime_authority.outputs.playwright_sha256 }}"
    )
    assert gate["env"]["PATH"] == (
        "${{ steps.gate_runtime_authority.outputs.trusted_path }}"
    )


def test_digest_gate_prepares_and_rechecks_bounded_runner_disk_budget() -> None:
    job = _jobs()["digest-full-stack-gate"]
    assert job["runs-on"] == "ubuntu-24.04"
    steps = job["steps"]
    names = [step.get("name") for step in steps]
    freeze_name = "Freeze trusted Playwright and Docker gate runtimes"
    reclaim_name = "Prepare GitHub-hosted release disk budget"
    app_budget_name = "Verify disk budget before application pulls"
    pull_name = "Pull and verify all 15 exact digest references"
    infra_budget_name = "Verify disk budget before infrastructure pulls"
    render_name = "Render and start hybrid digest/source release stack"
    assert (
        names.index(freeze_name)
        < names.index(reclaim_name)
        < names.index(app_budget_name)
    )
    assert names.index(app_budget_name) + 1 == names.index(pull_name)
    assert (
        names.index(pull_name)
        < names.index(infra_budget_name)
        < names.index(render_name)
    )

    runtime = _named_step(job, freeze_name)
    for needle in (
        "scripts/prepare_release_runner_disk.py",
        "/opt/omega-release-runtime/prepare_release_runner_disk.py",
        "${OMEGA_RELEASE_RUNNER_DISK_SHA256}",
        "-o root -g root -m 0444",
    ):
        assert needle in runtime["run"]
    assert runtime["env"]["OMEGA_RELEASE_RUNNER_DISK_SHA256"] == (
        "${{ steps.harness_authority.outputs.runner_disk_sha256 }}"
    )

    reclaim = _named_step(job, reclaim_name)
    assert reclaim["env"]["BASH_ENV"] == "/dev/null"
    assert reclaim["env"]["ENV"] == "/dev/null"
    assert reclaim["env"]["PYTHONPATH"] == ""
    assert reclaim["env"]["LD_PRELOAD"] == ""
    reclaim_source = reclaim["run"]
    for needle in (
        "/opt/omega-release-runtime/prepare_release_runner_disk.py",
        "/usr/bin/docker --config /opt/omega-release-runtime/docker-config info",
        "{{.DockerRootDir}}",
        '== "/var/lib/docker"',
        "/usr/bin/sudo /usr/bin/env -i",
        '/usr/bin/python3 -I "${helper}" reclaim',
    ):
        assert needle in reclaim_source
    for forbidden in ("docker system prune", "docker builder prune", "rm -rf"):
        assert forbidden not in reclaim_source

    app_budget = _named_step(job, app_budget_name)
    assert "check --phase pre-pull" in app_budget["run"]
    assert "/usr/bin/env -i" in app_budget["run"]
    assert "scripts/prepare_release_runner_disk.py" not in app_budget["run"]

    infra_budget = _named_step(job, infra_budget_name)
    assert "check --phase pre-infra" in infra_budget["run"]
    assert "/usr/bin/env -i" in infra_budget["run"]
    assert "scripts/prepare_release_runner_disk.py" not in infra_budget["run"]

    render = _named_step(job, render_name)["run"]
    pre_compose = render.index("check --phase pre-compose")
    assert render.rindex('docker pull "${ref}"') < pre_compose
    assert pre_compose < render.index("docker image ls --no-trunc --digests")
    assert pre_compose < render.index('"${compose[@]}" up -d --no-build --pull never')


def test_digest_failure_diagnostics_capture_each_container_without_masking(
    tmp_path: Path,
) -> None:
    step = _named_step(
        _jobs()["digest-full-stack-gate"], "Digest-stack logs on failure"
    )
    assert step["if"] == "failure()"
    source = step["run"]
    assert "xargs" not in source
    assert "docker compose" in source and "ps --all" in source and "ps -aq" in source
    assert "docker ps" not in source
    assert "docker inspect --type container" in source
    assert "--format '{{json .State.Health}}'" in source
    assert ".Config.Env" not in source
    assert 'docker logs --tail 120 "${container_id}"' in source
    assert "LC_ALL=C sort -u" in source
    assert "set +e" in source
    assert source.rstrip().endswith("exit 0")

    workspace = tmp_path / "checkout"
    for relative in (
        "infra/docker-compose.yml",
        "infra/docker-compose.dev.yml",
        "infra/terraform-gcp/release/docker-compose.release.yml",
    ):
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO / relative).read_bytes())
    (workspace / "infra/.env").write_text("TEST_ONLY=1\n", encoding="utf-8")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls = tmp_path / "docker.calls"
    real_docker = tmp_path / "docker-real"
    real_docker.write_text(
        """#!/usr/bin/env bash
set -u
if [[ "${1:-}" == "--config" ]]; then
  [[ "$#" -ge 3 ]] || exit 69
  shift 2
fi
printf 'command=%s argc=%s last=%s\n' "${1:-}" "$#" "${!#:-}" >> "${FAKE_DOCKER_CALLS}"
case "${1:-}" in
  compose)
    if [[ " $* " == *" ps -aq "* ]]; then
      printf '%s' "${FAKE_COMPOSE_IDS:-}"
    elif [[ " $* " == *" ps --all --no-trunc "* ]]; then
      printf 'compose diagnostics\n'
    else
      exit 70
    fi
    ;;
  inspect)
    [[ "$#" -eq 6 ]] || exit 72
    printf 'health for %s\n' "${!#}"
    ;;
  logs)
    [[ "$#" -eq 4 ]] || exit 73
    printf 'logs for %s\n' "${!#}"
    [[ "${!#}" != "container-a" ]] || exit 74
    ;;
  *)
    exit 75
    ;;
esac
""",
        encoding="utf-8",
    )
    real_docker.chmod(0o755)
    trusted_config = tmp_path / "trusted-docker-config"
    trusted_config.mkdir()
    trusted_config.chmod(0o555)
    locked_docker = fake_bin / "docker"
    locked_docker.write_text(
        """#!/usr/bin/env bash
exec "${LOCK_TEST_PYTHON}" "${LOCK_TEST_SCRIPT}" \
  --workspace "${LOCK_TEST_WORKSPACE}" \
  --real "${LOCK_TEST_REAL_DOCKER}" \
  --trusted-config "${LOCK_TEST_DOCKER_CONFIG}" -- "$@"
""",
        encoding="utf-8",
    )
    locked_docker.chmod(0o755)
    clean_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COMPOSE_", "DOCKER_"))
    }
    runtime_env = {
        **clean_env,
        "FAKE_COMPOSE_IDS": "container-b\ncontainer-a\ncontainer-b\n",
        "FAKE_DOCKER_CALLS": str(calls),
        "LOCK_TEST_DOCKER_CONFIG": str(trusted_config),
        "LOCK_TEST_PYTHON": sys.executable,
        "LOCK_TEST_REAL_DOCKER": str(real_docker),
        "LOCK_TEST_SCRIPT": str(REPO / "scripts/release_docker_lock.py"),
        "LOCK_TEST_WORKSPACE": str(workspace),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
    }
    result = subprocess.run(
        ["bash", "-c", source],
        cwd=workspace,
        env=runtime_env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    recorded = calls.read_text(encoding="utf-8").splitlines()
    compose_calls = [line for line in recorded if line.startswith("command=compose ")]
    inspect_calls = [line for line in recorded if line.startswith("command=inspect ")]
    log_calls = [line for line in recorded if line.startswith("command=logs ")]
    assert len(compose_calls) == 2
    assert not any(line.startswith("command=ps ") for line in recorded)
    assert inspect_calls == [
        "command=inspect argc=6 last=container-a",
        "command=inspect argc=6 last=container-b",
    ]
    assert log_calls == [
        "command=logs argc=4 last=container-a",
        "command=logs argc=4 last=container-b",
    ]
    assert "health for container-a" in result.stdout
    assert "health for container-b" in result.stdout
    assert "logs for container-a" in result.stdout
    assert "logs for container-b" in result.stdout

    calls.unlink()
    empty_result = subprocess.run(
        ["bash", "-c", source],
        cwd=workspace,
        env={**runtime_env, "FAKE_COMPOSE_IDS": ""},
        text=True,
        capture_output=True,
        check=False,
    )
    assert empty_result.returncode == 0, empty_result.stderr
    empty_recorded = calls.read_text(encoding="utf-8").splitlines()
    assert (
        len([line for line in empty_recorded if line.startswith("command=compose ")])
        == 2
    )
    assert not any(
        line.startswith(("command=inspect ", "command=logs "))
        for line in empty_recorded
    )


def _run_wait_for_health_with_release_lock(
    tmp_path: Path, *, compose_file: str
) -> subprocess.CompletedProcess[str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    real_docker = tmp_path / "docker-real"
    real_docker.write_text(
        """#!/usr/bin/env bash
set -u
if [[ "${1:-}" == "--config" ]]; then
  [[ "$#" -ge 3 ]] || exit 96
  shift 2
fi
case "${1:-}" in
  inspect)
    printf 'healthy\n'
    ;;
  ps)
    ;;
  compose)
    printf 'compose diagnostics\n'
    ;;
  logs)
    printf 'container diagnostics\n'
    ;;
  *)
    exit 98
    ;;
esac
""",
        encoding="utf-8",
    )
    real_docker.chmod(0o755)
    trusted_config = tmp_path / "trusted-docker-config"
    trusted_config.mkdir()
    trusted_config.chmod(0o555)
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/usr/bin/env bash
exec "${LOCK_TEST_PYTHON}" "${LOCK_TEST_SCRIPT}" \
  --workspace "${LOCK_TEST_WORKSPACE}" \
  --real "${LOCK_TEST_REAL_DOCKER}" \
  --trusted-config "${LOCK_TEST_DOCKER_CONFIG}" -- "$@"
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    curl = fake_bin / "curl"
    curl.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    curl.chmod(0o755)
    clean_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COMPOSE_", "DOCKER_"))
    }
    return subprocess.run(
        ["bash", str(WAIT_FOR_HEALTH)],
        cwd=REPO,
        env={
            **clean_env,
            "COMPOSE_FILE": compose_file,
            "LOCK_TEST_DOCKER_CONFIG": str(trusted_config),
            "LOCK_TEST_PYTHON": sys.executable,
            "LOCK_TEST_REAL_DOCKER": str(real_docker),
            "LOCK_TEST_SCRIPT": str(REPO / "scripts/release_docker_lock.py"),
            "LOCK_TEST_WORKSPACE": str(REPO),
            "OMEGA_WAIT_FULL_STACK": "1",
            "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
            "WAIT_READY_STREAK": "1",
            "WAIT_SLEEP_SECONDS": "1",
            "WAIT_TIMEOUT_SECONDS": "1",
        },
        text=True,
        capture_output=True,
        check=False,
    )


def test_wait_for_health_does_not_turn_exported_empty_compose_file_into_poison(
    tmp_path: Path,
) -> None:
    result = _run_wait_for_health_with_release_lock(tmp_path, compose_file="")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[wait_for_health] Stack ready." in result.stdout
    assert "console=starting" not in result.stdout
    assert "RELEASE DOCKER LOCK BLOCKED" not in result.stdout + result.stderr


def test_wait_for_health_preserves_nonempty_compose_file_as_lock_poison(
    tmp_path: Path,
) -> None:
    result = _run_wait_for_health_with_release_lock(
        tmp_path,
        compose_file="/tmp/attacker-compose.yml",
    )

    assert result.returncode == 97
    assert "=starting" not in result.stdout
    assert "[wait_for_health] Waiting" in result.stdout
    assert "[wait_for_health] ERROR" not in result.stdout
    assert "RELEASE DOCKER LOCK BLOCKED" in result.stdout + result.stderr


def test_release_runner_capacity_canary_reproduces_post_install_budget() -> None:
    job = _capacity_job()
    assert job["runs-on"] == "ubuntu-24.04"
    assert job["timeout-minutes"] == 45
    install = _named_step(job, "Reproduce digest-gate dependency footprint")["run"]
    release_install = _named_step(
        _jobs()["digest-full-stack-gate"],
        "Install digest-stack gate dependencies",
    )["run"]
    for needle in (
        "-r tests/requirements.txt",
        "-r console/requirements.txt",
        "-r refinement/requirements.txt",
        "-r vault/requirements.txt",
        "-r workspace/requirements.txt",
        "-r mcp-infra/requirements.txt",
        "-r tests/stress/requirements.txt",
        "for req in cartridges/*/requirements.txt",
        "npm --prefix tests-e2e ci",
        "playwright install --with-deps chromium",
    ):
        assert needle in install
        assert needle in release_install

    authority = _named_step(
        job, "Freeze source-bound capacity authority and browser copy"
    )["run"]
    for needle in (
        'git show "${GITHUB_SHA}:${helper_path}"',
        'git show "${GITHUB_SHA}:${normalizer_path}"',
        'cmp --silent "${committed_helper}" "${helper_path}"',
        'cmp --silent "${committed_normalizer}" "${normalizer_path}"',
        "/opt/omega-release-runtime/prepare_release_runner_disk.py",
        "/opt/omega-release-runtime/normalize_release_runtime_permissions.py",
        "-o root -g root -m 0444",
        '"${RUNNER_TEMP}/omega-playwright-browsers.install/."',
    ):
        assert needle in authority

    proof = _named_step(job, "Prove exact post-install reclaim budget")
    assert proof["env"]["BASH_ENV"] == "/dev/null"
    assert proof["env"]["PYTHONPATH"] == ""
    for needle in (
        "/usr/bin/docker --config /opt/omega-release-runtime/docker-config info",
        '== "/var/lib/docker"',
        "/usr/bin/sudo /usr/bin/env -i",
        '/usr/bin/python3 -I "${helper}" reclaim',
        "check --phase pre-pull",
    ):
        assert needle in proof["run"]
    for forbidden in ("docker system prune", "docker builder prune", "rm -rf"):
        assert forbidden not in CAPACITY_WORKFLOW.read_text(encoding="utf-8")


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
