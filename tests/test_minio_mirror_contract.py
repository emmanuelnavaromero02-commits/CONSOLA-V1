from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OWNER = "emmanuelnavaromero02-commits"
MINIO_TAG = "RELEASE.2024-12-18T13-15-44Z"
MINIO_COMMIT = "16f8cf1c52f0a77eeb8f7565aaf7f7df12454583"
MC_TAG = "RELEASE.2024-11-21T17-21-54Z"
MC_COMMIT = "1681e4497c09d7438a34e846f76dbde972ab7daf"
MIRROR_REVISION = "r1"
MIRROR_UID = "10001:10001"
MINIO_IMAGE = f"ghcr.io/{OWNER}/omega-minio:{MINIO_TAG}"
MC_IMAGE = f"ghcr.io/{OWNER}/omega-mc:{MC_TAG}"
WITHDRAWN_DIGEST = "sha256:1dce27c494a16bae114774f1cec295493f3613142713130c2d22dd5696be6ad3"
LOGIN_ACTION = "docker/login-action@c94ce9fb468520275223c153574b00df6fe4bcc9"

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _yaml(relative: str) -> dict:
    return yaml.safe_load(_text(relative))


def _minio_digest() -> str:
    workflow = _text(".github/workflows/control-room-postgres-rls.yml")
    match = re.search(r'minio_digest="(sha256:[0-9a-f]{64})"', workflow)
    assert match, "control-room-postgres-rls.yml must pin the mirror digest"
    digest = match.group(1)
    assert digest != WITHDRAWN_DIGEST, "still pinning the withdrawn quay.io digest"
    return digest


def test_dockerfile_and_mirror_workflow_pin_the_same_releases_and_commits():
    dockerfile = _text("infra/images/minio/Dockerfile")
    for name, value in (
        ("MINIO_TAG", MINIO_TAG), ("MINIO_COMMIT", MINIO_COMMIT),
        ("MC_TAG", MC_TAG), ("MC_COMMIT", MC_COMMIT),
    ):
        assert f"ARG {name}={value}" in dockerfile, f"Dockerfile must default {name} to {value}"
    assert 'test "$(git -C minio rev-parse HEAD)" = "${MINIO_COMMIT}"' in dockerfile
    assert 'test "$(git -C mc rev-parse HEAD)" = "${MC_COMMIT}"' in dockerfile
    assert "--depth 1 --branch \"${MINIO_TAG}\"" in dockerfile and "--depth 1 --branch \"${MC_TAG}\"" in dockerfile
    assert re.search(r"^ARG RUNTIME_BASE=docker\.io/library/alpine:[0-9.]+@sha256:[0-9a-f]{64}$", dockerfile, re.M)
    assert "apk add -U --no-cache ca-certificates curl" in dockerfile
    assert "curlimages" not in dockerfile

    workflow = _yaml(".github/workflows/mirror-minio.yml")
    triggers = workflow.get("on", workflow.get(True))
    assert set(triggers) == {"workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read", "packages": "write"}
    env = workflow["env"]
    assert (env["MINIO_TAG"], env["MINIO_COMMIT"], env["MC_TAG"], env["MC_COMMIT"]) == (
        MINIO_TAG, MINIO_COMMIT, MC_TAG, MC_COMMIT
    )
    assert env["MIRROR_REVISION"] == MIRROR_REVISION
    steps = workflow["jobs"]["build-and-publish"]["steps"]
    pushes = [s for s in steps if str(s.get("uses", "")).startswith("docker/build-push-action@")]
    assert {(s["with"]["target"], s["with"]["tags"]) for s in pushes} == {
        (
            "minio",
            "ghcr.io/${{ github.repository_owner }}/omega-minio:"
            "${{ env.MINIO_TAG }}-${{ env.MIRROR_REVISION }}",
        ),
        (
            "mc",
            "ghcr.io/${{ github.repository_owner }}/omega-mc:"
            "${{ env.MC_TAG }}-${{ env.MIRROR_REVISION }}",
        ),
    }
    assert all(s["with"]["platforms"] == "linux/amd64,linux/arm64" and s["with"]["push"] is True for s in pushes)
    verify = next(s for s in steps if s.get("name", "").startswith("Verify the published images"))
    assert 'grep -F "minio version ${MINIO_TAG} (commit-id=${MINIO_COMMIT})"' in verify["run"]
    assert 'grep -F "mc version ${MC_TAG} (commit-id=${MC_COMMIT})"' in verify["run"]
    assert "--entrypoint /usr/bin/curl" in verify["run"]
    assert "| head" not in verify["run"]
    assert 'minio_ref="ghcr.io/${owner}/omega-minio:${MINIO_TAG}-${MIRROR_REVISION}"' in verify["run"]
    assert 'mc_ref="ghcr.io/${owner}/omega-mc:${MC_TAG}-${MIRROR_REVISION}"' in verify["run"]


def test_mirror_images_run_as_uid_10001():
    dockerfile = _text("infra/images/minio/Dockerfile")
    minio_stage, mc_stage = dockerfile.split(" AS minio\n", 1)[1].split(" AS mc\n", 1)
    assert dockerfile.count(f"USER {MIRROR_UID}") == 2
    for stage, home in ((minio_stage, "/home/minio"), (mc_stage, "/home/mc")):
        assert "addgroup -S -g 10001 " in stage
        assert "adduser -S -D -u 10001 " in stage and f"-h {home} " in stage
        assert f"install -d -o 10001 -g 10001 -m 0700 {home}" in stage
        assert stage.index(f"install -d -o 10001 -g 10001 -m 0700 {home}") < stage.index(
            f"USER {MIRROR_UID}"
        )
    data_dir = "install -d -o 10001 -g 10001 -m 0750 /data"
    assert minio_stage.index(data_dir) < minio_stage.index(f"USER {MIRROR_UID}")
    assert minio_stage.index(data_dir) < minio_stage.index('VOLUME ["/data"]')
    assert minio_stage.index(f"USER {MIRROR_UID}") < minio_stage.index("ENTRYPOINT")
    assert mc_stage.index(f"USER {MIRROR_UID}") < mc_stage.index("ENTRYPOINT")

    steps = _yaml(".github/workflows/mirror-minio.yml")["jobs"]["build-and-publish"]["steps"]
    verify = next(s for s in steps if s.get("name", "").startswith("Verify the published images"))["run"]
    uid_check = 'test "$(id -u):$(id -g)" = "10001:10001"'
    assert verify.count(uid_check) == 2
    assert f"--entrypoint /bin/sh \"${{minio_ref}}\" -c '{uid_check} && test -w /data && test -w \"$HOME\"'" in verify
    assert f"--entrypoint /bin/sh \"${{mc_ref}}\" -c '{uid_check} && test -w \"$HOME\"'" in verify
    assert 'cid="$(docker run -d "${minio_ref}" server /data)"' in verify
    assert "curl -fsS http://127.0.0.1:9000/minio/health/live" in verify
    assert 'docker rm -f "${cid}"' in verify
    assert verify.index('docker rm -f "${cid}"') < verify.index('test "${healthy}" = 1')


def test_non_root_minio_data_stays_writable_everywhere_it_runs():
    for workflow in (".github/workflows/release.yml", ".github/workflows/e2e.yml"):
        text = _text(workflow)
        assert "mkdir -p data/lakehouse\n          sudo chown 10001:10001 data/lakehouse\n" in text, workflow

    makefile = _text("Makefile")
    target = re.search(r"^lakehouse-dir:\n((?:\t.*\n)+)", makefile, re.M)
    assert target, "Makefile must define lakehouse-dir"
    assert "mkdir -p data/lakehouse" in target.group(1)
    assert "sudo chown -R 10001:10001 data/lakehouse" in target.group(1)
    assert '"$$(uname -s)" = Linux' in target.group(1)
    assert re.search(r"^\.PHONY:.*\blakehouse-dir\b", makefile, re.M)
    for name in ("up", "up-core", "verify-release"):
        body = re.search(rf"^{re.escape(name)}:\n((?:\t.*\n)+)", makefile, re.M)
        assert body and "$(MAKE) lakehouse-dir" in body.group(1), name
    verify_release = re.search(r"^verify-release:\n((?:\t.*\n)+)", makefile, re.M).group(1)
    assert verify_release.index("$(MAKE) lakehouse-dir") < verify_release.index(" up -d ")
    assert "mkdir -p data/lakehouse" not in makefile.replace(target.group(1), "")

    assert "--tmpfs /data:rw,noexec,nosuid,size=128m,mode=1777" in _text("scripts/ci_replicon_minio_smoke.sh")
    e2e_minio = _yaml("infra/e2e/compose.infrastructure.yml")["services"]["minio"]
    assert e2e_minio["tmpfs"] == ["/data:rw,nosuid,noexec,size=128m,mode=1777"]

    stress = _text("scripts/run_stress.sh")
    assert "rm -rf data/lakehouse" not in stress
    assert '"${COMPOSE[@]}" exec -T minio rm -rf /data/lakehouse/silver/hubspot' in stress
    assert '"${COMPOSE[@]}" exec -T minio rm -rf /data/lakehouse/gold/hubspot' in stress


def test_composes_run_the_mirror_images():
    local = _yaml("infra/docker-compose.yml")["services"]
    e2e = _yaml("infra/e2e/compose.infrastructure.yml")["services"]
    for services, label in ((local, "infra/docker-compose.yml"), (e2e, "infra/e2e/compose.infrastructure.yml")):
        assert services["minio"]["image"] == MINIO_IMAGE, label
        assert services["minio-init"]["image"] == MC_IMAGE, label
        assert "curl -fsS http://127.0.0.1:9000/minio/health/live" in " ".join(services["minio"]["healthcheck"]["test"])


def test_every_pull_by_digest_uses_the_same_index_digest():
    digest = _minio_digest()
    pinned = f"{MINIO_IMAGE}@{digest}"
    assert f'minio_image="{pinned}"' in _text("scripts/ci_replicon_minio_smoke.sh")
    assert f'minio_image="{pinned}"' in _text("scripts/run_refinement_duckdb_offline_smoke.sh")
    for workflow in (".github/workflows/control-room-postgres-rls.yml", ".github/workflows/release.yml"):
        text = _text(workflow)
        assert f'minio_tag="{MINIO_IMAGE}"' in text, workflow
        assert f'minio_digest="{digest}"' in text, workflow
    release = _text(".github/workflows/release.yml")
    assert f'minio_repo_digest="ghcr.io/{OWNER}/omega-minio@${{minio_digest}}"' in release
    assert digest in _text("tests/test_refinement_duckdb_extensions.py")
    assert digest in _text("tests/test_release_ci_fail_closed.py")
    assert WITHDRAWN_DIGEST not in _text("tests/test_refinement_duckdb_extensions.py")


def test_no_consumer_still_points_at_the_withdrawn_registry():
    allowed = {
        "infra/images/minio/Dockerfile",
        ".github/workflows/mirror-minio.yml",
        "tests/test_minio_mirror_contract.py",
        "tests/test_compose_aws_consistency.py",
    }
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in {".yml", ".yaml", ".py", ".sh", ".json", ".tftpl"}:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative in allowed or "node_modules" in relative or relative.startswith(("data/", "console/app/static/")):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "quay.io/minio" in text or WITHDRAWN_DIGEST in text:
            offenders.append(relative)
    assert offenders == [], offenders


def _login_precedes_pull(job: dict, pull_step_name: str) -> None:
    steps = job["steps"]
    names = [s.get("name", "") for s in steps]
    login_index = next(i for i, s in enumerate(steps) if s.get("uses") == f"{LOGIN_ACTION} # v3" or s.get("uses") == LOGIN_ACTION)
    pull_index = names.index(pull_step_name)
    assert login_index < pull_index, f"GHCR login must run before {pull_step_name!r}"
    login = steps[login_index]["with"]
    assert login == {
        "registry": "ghcr.io",
        "username": "${{ github.actor }}",
        "password": "${{ secrets.GITHUB_TOKEN }}",
    }
    assert job["permissions"] == {"contents": "read", "packages": "read"}


def test_jobs_that_pull_the_private_packages_authenticate_with_their_own_token():
    rls = _yaml(".github/workflows/control-room-postgres-rls.yml")["jobs"]
    _login_precedes_pull(rls["control-room-postgres-rls"], "Preload exact offline smoke images")
    _login_precedes_pull(rls["operational-truth-e2e"], "Run real operational truth E2E")
    release = _yaml(".github/workflows/release.yml")["jobs"]
    _login_precedes_pull(release["validate-release"], "Preload exact MinIO image for pull-never release tests")
