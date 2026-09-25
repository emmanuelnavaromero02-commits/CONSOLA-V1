from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
LOCAL = REPO / "infra/docker-compose.yml"
AWS = REPO / "infra/terraform/deploy/docker-compose.aws.yml"
AWS_CARTRIDGES = REPO / "infra/terraform/deploy/docker-compose.cartridges.yml"
AWS_START = REPO / "infra/terraform/deploy/start.sh"
AWS_UPDATE = REPO / "infra/terraform/deploy/update.sh"
AWS_ENTRYPOINT = REPO / "scripts/aws-entrypoint.sh"
AWS_USERDATA = REPO / "infra/terraform/infra/user_data/app.sh.tpl"
VPN_USERDATA = REPO / "infra/terraform/infra/user_data/vpn.sh.tpl"
TERRAFORM_INFRA = REPO / "infra/terraform/infra"
RELEASE_WORKFLOW = REPO / ".github/workflows/release.yml"


def _images(path: Path) -> list[str]:
    return re.findall(r"^\s*image:\s*(\S+)\s*$", path.read_text(), re.MULTILINE)


def _versions_of(images: list[str], prefix: str) -> set[str]:
    out: set[str] = set()
    for img in images:
        if img.startswith(prefix + ":") or img == prefix:
            _, _, tag = img.partition(":")
            out.add(tag or "(no tag)")
    return out


def test_airflow_version_matches_local():
    local_versions = _versions_of(_images(LOCAL), "mode-airflow") \
        or _versions_of(_images(LOCAL), "apache/airflow")
    aws_versions = _versions_of(_images(AWS), "apache/airflow")

    local_upstream = {v.split("-")[0] for v in local_versions}

    if not aws_versions:
        aws_src = AWS.read_text(encoding="utf-8")
        dockerfile = (REPO / "infra/airflow/Dockerfile").read_text(encoding="utf-8")
        assert "/airflow:" in aws_src, "AWS compose must reference the packaged airflow image"
        assert f"FROM apache/airflow:{next(iter(local_upstream))}" in dockerfile
        aws_versions = local_upstream
    assert aws_versions, "AWS compose must reference airflow"
    assert aws_versions == local_upstream, (
        f"Airflow drift: local={local_upstream}, AWS={aws_versions}. "
        f"Either bump AWS to match or update this test to reflect the "
        f"intentional difference."
    )


def test_postgres_version_matches_local():
    local_pg = _versions_of(_images(LOCAL), "pgvector/pgvector")
    aws_pg = _versions_of(_images(AWS), "pgvector/pgvector")
    assert local_pg, "local compose must use pgvector/pgvector"
    assert aws_pg == local_pg, (
        f"Postgres drift: local={local_pg}, AWS={aws_pg}"
    )


def test_postgres_healthchecks_probe_tcp_in_local_and_aws():
    for path in (LOCAL, AWS):
        text = path.read_text(encoding="utf-8")
        assert "pg_isready -h 127.0.0.1 -p 5432 -U postgres -d modecissions" in text
        assert "pg_isready -h 127.0.0.1 -p 5433 -U postgres -d modecissions_gold" in text


def test_superset_version_matches_local():
    local_ss = _versions_of(_images(LOCAL), "apache/superset")
    aws_ss = _versions_of(_images(AWS), "apache/superset")
    assert local_ss, "local compose must use apache/superset"
    assert aws_ss == local_ss, (
        f"Superset drift: local={local_ss}, AWS={aws_ss}"
    )


def test_no_floating_tags_on_third_party_images():
    aws_images = _images(AWS)
    forbidden_tags = ("latest", "main", "edge", "stable")
    offenders: list[str] = []
    for img in aws_images:
        name, _, tag = img.partition(":")
        if not tag:
            offenders.append(f"{img} (no tag)")
        elif tag in forbidden_tags:
            offenders.append(img)
    assert not offenders, (
        f"AWS compose has floating tags: {offenders}. "
        f"Pin to an explicit version."
    )


def test_aws_application_images_use_ghcr_release_tags():
    src = AWS.read_text(encoding="utf-8")
    assert "modecissions/console:latest" not in src
    assert "${IMAGE_TAG:-v1.44.5}" not in src
    for service in ("console", "workspace", "refinement", "vault", "mcp-infra"):
        assert f"ghcr.io/${{GHCR_OWNER:-emmanuelnavaromero02-commits}}/{service}:${{IMAGE_TAG:?IMAGE_TAG is required}}" in src


def test_aws_cartridge_overlay_ships_release_images():
    src = AWS_CARTRIDGES.read_text(encoding="utf-8")
    assert "${IMAGE_TAG:-v1.44.5}" not in src
    expected = {
        "replicon": "replicon",
        "hubspot": "hubspot",
        "salesforce": "salesforce",
        "sap-hcm": "sap_hcm",
        "sap-s4hana": "sap_s4hana",
        "sap-successfactors": "sap_successfactors",
        "sap-b1": "sap_b1",
    }
    for service, image in expected.items():
        assert f"{service}:" in src
        assert f"ghcr.io/${{GHCR_OWNER:-emmanuelnavaromero02-commits}}/{image}:${{IMAGE_TAG:?IMAGE_TAG is required}}" in src


def test_userdata_checks_out_requested_deploy_ref_before_start():
    src = AWS_USERDATA.read_text(encoding="utf-8")
    assert "git -C /opt/modecissions fetch --tags --force --prune origin" in src
    assert "git -C /opt/modecissions checkout --detach ${deploy_ref}" in src


def test_deploy_scripts_reject_mismatched_release_tag_refs():
    for path in (AWS_START, AWS_UPDATE, AWS_ENTRYPOINT):
        src = path.read_text(encoding="utf-8")
        assert "assert_release_refs_coherent" in src
        assert "must match IMAGE_TAG" in src
        assert "is_release_tag" in src


def test_start_script_requires_service_account_passwords():
    src = AWS_START.read_text(encoding="utf-8")
    assert "check_var SUPERSET_SERVICE_PASSWORD" in src


def test_update_script_uses_cartridge_overlay_for_service_updates():
    src = AWS_UPDATE.read_text(encoding="utf-8")
    assert "COMPOSE_FILES=(-f docker-compose.aws.yml)" in src
    assert "COMPOSE_FILES+=(-f docker-compose.cartridges.yml)" in src
    assert 'COMPOSE_PROGRESS="${COMPOSE_PROGRESS:-quiet}"' in src
    assert 'docker compose "${COMPOSE_FILES[@]}" pull --quiet "$1"' in src
    assert 'docker compose "${COMPOSE_FILES[@]}" up -d --force-recreate "$1"' in src


def test_release_workflow_validates_before_publishing_images():
    src = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "detect-release-changes:" in src
    assert "authorize-release-gate-skips:" in src
    assert "validate-release:" in src
    assert "digest-full-stack-gate:" in src
    assert "preflight-release-packages:" in src
    assert "build-and-push:" in src
    assert "needs: detect-release-changes" in src
    assert (
        "needs: [detect-release-changes, authorize-release-gate-skips, "
        "validate-release]" in src
    )
    assert (
        "needs: [detect-release-changes, authorize-release-gate-skips, "
        "validate-release, preflight-release-packages]" in src
    )
    assert (
        "needs: [authorize-release-gate-skips, build-and-push, "
        "assemble-release-manifest]"
        in src
    )
    assert "needs.digest-full-stack-gate.result == 'success'" in src
    assert "needs.preflight-release-packages.result == 'success'" in src
    assert "make production-readiness" in src
    readiness = (REPO / "scripts/production_readiness.sh").read_text(encoding="utf-8")
    for gate in ("make smoke", "make e2e", "make acceptance"):
        assert gate in readiness
    assert 'OMEGA_RELEASE_PUBLISH_ONLY: "1"' in src
    assert "OMEGA_PRODUCTION_READINESS_SKIP_STRESS=1" not in src
    assert "production_readiness_stress_action" in src
    assert "production_readiness_stress_skip_authorized" in src
    assert "production_readiness_stress_policy_id" in src
    assert 'GITHUB_REF_NAME}" == *"beta"*' not in src
    for requirements in (
        "tests/requirements.txt",
        "console/requirements.txt",
        "vault/requirements.txt",
        "mcp-infra/requirements.txt",
        "tests/stress/requirements.txt",
    ):
        assert f"-r {requirements}" in src
    assert "python3 -I scripts/run_release_pytest.py -q" in src
    assert "docker compose --env-file infra/.env.example" in src
    assert "docker-compose.cartridges.yml" in src
    assert "npm --prefix console-next run typecheck" in src
    assert "npm --prefix console-next run export:copy" in src
    digest_gate = src.split("  digest-full-stack-gate:", 1)[1].split(
        "  publish-release-manifest:", 1
    )[0]
    assert "Render and start hybrid digest/source release stack" in digest_gate
    assert "--no-build --pull never" in digest_gate
    assert "OMEGA_RELEASE_DIGEST_STACK: \"1\"" in digest_gate


def test_release_gate_pauses_scheduled_airflow_dags_in_ci():
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    dev_override = (REPO / "infra/docker-compose.dev.yml").read_text(encoding="utf-8")

    assert "AIRFLOW_DAGS_ARE_PAUSED_AT_CREATION=true" in workflow
    assert "AIRFLOW_DAGS_ARE_PAUSED_AT_CREATION" in dev_override
    assert "AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION" in dev_override
    release_stack = workflow.split("Bootstrap digest release stack", 1)[1].split(
        "Download the canonical candidate manifest", 1
    )[0]
    assert "AIRFLOW_DAGS_ARE_PAUSED_AT_CREATION=true" in release_stack


def test_release_gate_writes_host_urls_for_playwright_e2e():
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    release_stack = workflow.split("Bootstrap digest release stack", 1)[1].split(
        "Download the canonical candidate manifest", 1
    )[0]
    for env_line in (
        "AIRFLOW_URL=http://127.0.0.1:8082",
        "SUPERSET_URL=http://127.0.0.1:8088",
        "MINIO_CONSOLE_URL=http://127.0.0.1:9001",
        "MAILHOG_URL=http://127.0.0.1:8025",
        "HUBSPOT_URL=http://127.0.0.1:8210",
        "REPLICON_URL=http://127.0.0.1:8201",
        "SAP_HCM_URL=http://127.0.0.1:8202",
        "SAP_SF_URL=http://127.0.0.1:8203",
        "SAP_S4_URL=http://127.0.0.1:8204",
    ):
        assert env_line in release_stack


def test_scheduled_airflow_dags_honor_release_pause_flag():
    scheduled_dags = (
        REPO / "airflow/dags/agent_runner.py",
        REPO / "airflow/dags/entity_scheduler.py",
        REPO / "airflow/dags/replicon_ses_inbox_import.py",
        REPO / "cartridges/replicon/dags/replicon_ses_inbox_import.py",
    )

    for path in scheduled_dags:
        src = path.read_text(encoding="utf-8")
        assert "AIRFLOW_DAGS_ARE_PAUSED_AT_CREATION" in src, path
        assert "AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION" in src, path
        assert "is_paused_upon_creation=_pause_scheduled_dag_on_creation()" in src, path


def test_start_script_honors_cartridge_overlay_flag():
    src = AWS_START.read_text(encoding="utf-8")
    assert "COMPOSE_FILES=(-f docker-compose.aws.yml)" in src
    assert 'DEPLOY_CARTRIDGES_SAME_HOST:-true' in src
    assert "docker-compose.cartridges.yml" in src
    assert 'docker compose "${COMPOSE_FILES[@]}" up -d' in src


def test_release_workflow_does_not_publish_latest_tags():
    src = (REPO / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert ":latest" not in src


def test_aws_env_file_defaults_to_documented_deploy_env():
    src = AWS.read_text(encoding="utf-8")
    assert "${AWS_ENV_FILE:-.env}" in src
    assert "${AWS_ENV_FILE:-../../.env}" not in src


def test_aws_console_mounts_cartridges_read_only():
    src = AWS.read_text(encoding="utf-8")
    assert re.search(
        r"^\s*-\s+/opt/modecissions/cartridges:/registry/cartridges:ro\s*$",
        src,
        re.MULTILINE,
    )
    assert not re.search(
        r"^\s*-\s+/opt/modecissions/cartridges:/registry/cartridges\s*$",
        src,
        re.MULTILINE,
    )


def test_aws_mcp_infra_mounts_cartridges_read_only_for_studio_source_sync():
    src = AWS.read_text(encoding="utf-8")
    marker = "container_name: mode_mcp_infra"
    start = src.index(marker)
    end = src.index("\n  superset-init:", start)
    section = src[start:end]

    assert "/opt/modecissions/cartridges:/registry/cartridges:ro" in section
    assert "/opt/modecissions/airflow/dags:/opt/airflow/dags" in section


def test_vpn_admin_password_hash_is_required_not_hardcoded():
    user_data = VPN_USERDATA.read_text(encoding="utf-8")
    variables = (TERRAFORM_INFRA / "variables.tf").read_text(encoding="utf-8")
    ec2_vpn = (TERRAFORM_INFRA / "ec2_vpn.tf").read_text(encoding="utf-8")

    assert "PASSWORD_HASH=$$2b$$" not in user_data
    assert "PASSWORD_HASH=${vpn_password_hash}" in user_data
    assert 'variable "vpn_admin_password_hash"' in variables
    assert "sensitive   = true" in variables
    assert 'replace(var.vpn_admin_password_hash, "$", "$$")' in ec2_vpn


def test_prod_compose_does_not_mount_dev_init_seeds():
    aws_src = AWS.read_text(encoding="utf-8")
    local_src = LOCAL.read_text(encoding="utf-8")
    assert "init_dev" not in aws_src
    assert "postgres_dev_seed" in local_src
    assert "./init_dev:/dev-seeds:ro" in local_src
    assert "/docker-entrypoint-initdb.d/90_local_dev_bootstrap.sql" not in local_src
    assert not (REPO / "infra/init/15_local_dev_bootstrap.sql").exists()
    assert (REPO / "infra/init_dev/15_local_dev_bootstrap.sql").exists()


def test_no_remaining_2_9_x_airflow_in_aws():
    src = AWS.read_text(encoding="utf-8")
    assert "apache/airflow:2.9." not in src, (
        "AWS compose still references apache/airflow:2.9.x — that's "
        "the N1 drift this sprint closed"
    )


def _aws_services() -> dict:
    import yaml
    with AWS.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("services", {})


_ONESHOT_INIT_SERVICES = {"superset-init", "airflow-init"}


def test_all_aws_long_running_services_have_healthchecks():
    svcs = _aws_services()
    missing = [
        name for name, body in svcs.items()
        if name not in _ONESHOT_INIT_SERVICES
        and "healthcheck" not in body
    ]
    assert not missing, (
        f"AWS compose services missing healthchecks: {missing}. "
        f"Either add ``healthcheck:`` or mark them as oneshot init."
    )


def test_aws_oneshot_services_are_explicitly_oneshot():
    svcs = _aws_services()
    for name in _ONESHOT_INIT_SERVICES:
        if name not in svcs:
            continue
        body = svcs[name]
        assert body.get("restart") == "no", (
            f"{name} is in oneshot carve-out but restart != 'no' "
            f"(got: {body.get('restart')!r})"
        )


def test_critical_aws_dependencies_use_service_healthy():
    svcs = _aws_services()
    critical_targets = {
        "postgres",
        "postgres_gold",
        "mcp-infra",
        "airflow",
        "vault",
        "refinement",
        "mailhog",
    }
    offenders: list[str] = []
    for name, body in svcs.items():
        deps = body.get("depends_on")
        if deps is None:
            continue
        if isinstance(deps, list):
            racy = [d for d in deps if d in critical_targets]
            if racy:
                offenders.append(
                    f"{name} → {racy} (bare list; needs condition: "
                    f"service_healthy)"
                )
        elif isinstance(deps, dict):
            for target, spec in deps.items():
                if target not in critical_targets:
                    continue
                if not isinstance(spec, dict):
                    offenders.append(
                        f"{name} → {target} (spec is {spec!r}; needs "
                        f"a dict with 'condition')"
                    )
                    continue
                cond = spec.get("condition")
                if cond not in (
                    "service_healthy",
                    "service_completed_successfully",
                ):
                    offenders.append(
                        f"{name} → {target} (condition={cond!r}; "
                        f"must be service_healthy)"
                    )
    assert not offenders, (
        "AWS compose has racy depends_on entries:\n  "
        + "\n  ".join(offenders)
    )


def test_aws_healthcheck_test_command_is_well_formed():
    svcs = _aws_services()
    bad: list[str] = []
    for name, body in svcs.items():
        hc = body.get("healthcheck")
        if not hc:
            continue
        test = hc.get("test")
        if not isinstance(test, list) or not test:
            bad.append(f"{name}: test={test!r}")
            continue
        head = test[0]
        if head not in ("CMD", "CMD-SHELL", "NONE"):
            bad.append(f"{name}: test[0]={head!r} (must be CMD/CMD-SHELL/NONE)")
    assert not bad, (
        "AWS compose healthchecks with bad test form:\n  "
        + "\n  ".join(bad)
    )


def _services_from(compose_path: Path) -> dict:
    import yaml
    with compose_path.open(encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("services", {})


def _healthcheck_test_strings(services: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for name, body in services.items():
        hc = body.get("healthcheck") or {}
        test = hc.get("test")
        if isinstance(test, list):
            out.append((name, " ".join(str(t) for t in test)))
        elif isinstance(test, str):
            out.append((name, test))
    return out


def test_console_next_runtime_removed_from_compose_and_release():
    local = (REPO / "infra/docker-compose.yml").read_text(encoding="utf-8")
    aws = AWS.read_text(encoding="utf-8")
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    dockerfile = REPO / "console-next/Dockerfile"
    assert "console_next:" not in local
    assert "console_next:" not in aws
    assert "3000:3000" not in local
    assert "3000:3000" not in aws
    assert "service: console-next" not in workflow
    assert not dockerfile.exists()


def test_no_healthcheck_uses_localhost_string():
    bad: list[str] = []
    for path in (
        REPO / "infra/docker-compose.yml",
        REPO / "infra/terraform/deploy/docker-compose.aws.yml",
        REPO / "infra/terraform/deploy/docker-compose.cartridges.yml",
    ):
        svcs = _services_from(path)
        for name, joined in _healthcheck_test_strings(svcs):
            if "localhost" in joined:
                bad.append(f"{path.name} → {name}: {joined}")
    assert not bad, (
        "Healthchecks must address 127.0.0.1 (or service DNS name), "
        "never ``localhost`` — IPv6-preferring resolvers + IPv4-only "
        "listeners produce silent unhealthy states. Offenders:\n  "
        + "\n  ".join(bad)
    )


def test_aws_compose_does_not_default_public_urls_to_localhost():
    src = AWS.read_text(encoding="utf-8")
    assert "localhost" not in src, (
        "AWS compose must not contain localhost fallbacks. Public URLs "
        "should come from CONSOLE_URL / WORKSPACE_PUBLIC_URL / *_PUBLIC_URL, "
        "and internal calls should use compose service DNS."
    )
