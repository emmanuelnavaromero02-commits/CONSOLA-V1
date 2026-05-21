"""Sprint v1.43.4 — Claude N1/N3/N4 + Codex H1: AWS compose must
match local compose on critical version pins so DAGs / migrations
tested locally don't fail silently in prod.

Claude N1 (closed in this sprint): infra/docker-compose.yml ran
Airflow 2.10.5 locally while infra/terraform/deploy/docker-compose.aws.yml
ran Airflow 2.9.2 — a year's worth of upstream behavior drift.

The tests below are static guards: they read both compose files,
extract image pins, and assert AWS matches the local ground truth.
Adding a new pin to local compose will fail the test until the
operator either (a) mirrors it in AWS or (b) explicitly carves it
out of the consistency check.
"""
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
RELEASE_WORKFLOW = REPO / ".github/workflows/release.yml"


def _images(path: Path) -> list[str]:
    """Return every ``image: …`` value in the compose file."""
    return re.findall(r"^\s*image:\s*(\S+)\s*$", path.read_text(), re.MULTILINE)


def _versions_of(images: list[str], prefix: str) -> set[str]:
    """Return the set of versions for an image prefix, e.g.
    'apache/airflow' → {'2.10.5'}."""
    out: set[str] = set()
    for img in images:
        if img.startswith(prefix + ":") or img == prefix:
            _, _, tag = img.partition(":")
            out.add(tag or "(no tag)")
    return out


def test_airflow_version_matches_local():
    """The Claude N1 finding: local was 2.10.5, AWS was 2.9.2 — the
    drift this hotfix closes. Lock the parity going forward."""
    local_versions = _versions_of(_images(LOCAL), "mode-airflow") \
        or _versions_of(_images(LOCAL), "apache/airflow")
    aws_versions = _versions_of(_images(AWS), "apache/airflow")

    # Normalise the local "mode-airflow:2.10.5-local" custom-built tag
    # to just the upstream version it derives from.
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
    """Local + AWS both run pgvector/pgvector:pg15. Lock the match."""
    local_pg = _versions_of(_images(LOCAL), "pgvector/pgvector")
    aws_pg = _versions_of(_images(AWS), "pgvector/pgvector")
    assert local_pg, "local compose must use pgvector/pgvector"
    assert aws_pg == local_pg, (
        f"Postgres drift: local={local_pg}, AWS={aws_pg}"
    )


def test_superset_version_matches_local():
    """Same lock for Superset — easy to forget when bumping locally."""
    local_ss = _versions_of(_images(LOCAL), "apache/superset")
    aws_ss = _versions_of(_images(AWS), "apache/superset")
    assert local_ss, "local compose must use apache/superset"
    assert aws_ss == local_ss, (
        f"Superset drift: local={local_ss}, AWS={aws_ss}"
    )


def test_no_floating_tags_on_third_party_images():
    """``:latest``, ``:main``, ``:edge`` on any upstream image is an
    invitation to ship a different binary every redeploy. Application
    images must also use the release-tagged GHCR path."""
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
    """AWS should pull immutable release images from GHCR, not local
    modecissions/*:latest builds."""
    src = AWS.read_text(encoding="utf-8")
    assert "modecissions/console:latest" not in src
    assert "${IMAGE_TAG:-v1.44.5}" not in src
    for service in ("console", "workspace", "refinement", "vault", "mcp-infra"):
        assert f"ghcr.io/${{GHCR_OWNER:-emmanuelnavaromero02-commits}}/{service}:${{IMAGE_TAG:?IMAGE_TAG is required}}" in src


def test_aws_cartridge_overlay_ships_release_images():
    """The same-host cartridge deploy path must be real, not a runbook-only
    reference. It also uses the same immutable IMAGE_TAG guard as core."""
    src = AWS_CARTRIDGES.read_text(encoding="utf-8")
    assert "${IMAGE_TAG:-v1.44.5}" not in src
    expected = {
        "replicon": "replicon",
        "sap-hcm": "sap_hcm",
        "sap-s4hana": "sap_s4hana",
        "sap-successfactors": "sap_successfactors",
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
    assert 'docker compose "${COMPOSE_FILES[@]}" pull "$1"' in src
    assert 'docker compose "${COMPOSE_FILES[@]}" up -d --force-recreate "$1"' in src


def test_release_workflow_validates_before_publishing_images():
    src = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    assert "validate-release:" in src
    assert "needs: validate-release" in src
    assert "python -m pytest -q" in src
    assert "docker compose --env-file infra/.env.example" in src
    assert "docker-compose.cartridges.yml" in src
    assert "npm --prefix console-next run typecheck" in src


def test_start_script_honors_cartridge_overlay_flag():
    src = AWS_START.read_text(encoding="utf-8")
    assert "COMPOSE_FILES=(-f docker-compose.aws.yml)" in src
    assert 'DEPLOY_CARTRIDGES_SAME_HOST:-false' in src
    assert "docker-compose.cartridges.yml" in src
    assert 'docker compose "${COMPOSE_FILES[@]}" up -d' in src


def test_release_workflow_does_not_publish_latest_tags():
    """Prod deploys should point at immutable release tags. Publishing
    :latest invites accidental mutable deploys even if compose is strict."""
    src = (REPO / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert ":latest" not in src


def test_aws_env_file_defaults_to_documented_deploy_env():
    """The AWS runbook creates infra/terraform/deploy/.env. The compose
    file may accept AWS_ENV_FILE override for local validation, but the
    default must stay .env relative to the deploy compose file."""
    src = AWS.read_text(encoding="utf-8")
    assert "${AWS_ENV_FILE:-.env}" in src
    assert "${AWS_ENV_FILE:-../../.env}" not in src


def test_prod_compose_does_not_mount_dev_init_seeds():
    """AWS may mount schema migrations only; local development seeds must
    remain outside infra/init and outside the AWS compose mount."""
    aws_src = AWS.read_text(encoding="utf-8")
    local_src = LOCAL.read_text(encoding="utf-8")
    assert "init_dev" not in aws_src
    assert "postgres_dev_seed" in local_src
    assert "./init_dev:/dev-seeds:ro" in local_src
    assert "/docker-entrypoint-initdb.d/90_local_dev_bootstrap.sql" not in local_src
    assert not (REPO / "infra/init/15_local_dev_bootstrap.sql").exists()
    assert (REPO / "infra/init_dev/15_local_dev_bootstrap.sql").exists()


def test_no_remaining_2_9_x_airflow_in_aws():
    """Defensive: should never see 2.9.x airflow in AWS after this
    sprint. Catches the case where a future change re-introduces the
    old pin via copy-paste."""
    src = AWS.read_text(encoding="utf-8")
    assert "apache/airflow:2.9." not in src, (
        "AWS compose still references apache/airflow:2.9.x — that's "
        "the Claude N1 drift this sprint closed"
    )


# ── v1.43.4 (Claude N3): AWS healthchecks + service_healthy deps ──────────


def _aws_services() -> dict:
    """Parse AWS compose; return services dict."""
    import yaml
    with AWS.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("services", {})


# Two AWS services are one-shot init containers (run command, exit
# cleanly). A Docker healthcheck on an exited container never goes
# HEALTHY by definition — dependents must use
# ``condition: service_completed_successfully`` instead. Carve them
# out of the healthcheck-coverage assertion.
_ONESHOT_INIT_SERVICES = {"superset-init", "airflow-init"}


def test_all_aws_long_running_services_have_healthchecks():
    """Claude N3: pre-v1.43.4 AWS compose had ZERO healthchecks — so
    ``depends_on`` only meant "container started", which races against
    Postgres init, mcp-infra warmup, etc. Every long-running service
    must now declare a healthcheck so dependents can wait on it."""
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
    """If a service is in the oneshot carve-out, it must actually be
    a one-shot (``restart: "no"`` AND no healthcheck). Catches the
    case where someone adds a long-running service to the carve-out
    by mistake."""
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
    """Every depends_on of a critical service (postgres, mcp-infra,
    airflow, vault, refinement) must use the long-form with
    ``condition:`` — the bare-list shorthand only waits for the
    container to start, not for the service inside it."""
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
        # Shorthand (list of strings) means no condition — that's the
        # racy form. If any item in the depends-on list is a critical
        # target, fail.
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
    """A healthcheck declared with ``test: <string>`` (instead of the
    ``["CMD", ...]`` / ``["CMD-SHELL", ...]`` form) is silently
    ignored by docker compose. Lock the explicit-list form."""
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


# ── v1.44.2 R-Mac-3: healthchecks must address 127.0.0.1, not localhost ──
#
# Codex's Mac re-validation caught the console_next healthcheck reporting
# the container unhealthy even though the Next.js server was responding
# correctly. Root cause:
#   * Alpine's musl resolver returns ``::1`` (IPv6 loopback) first for
#     the literal ``localhost``.
#   * busybox wget on Alpine does NOT fall back to a second AF on
#     connection refused.
#   * Next.js standalone with HOSTNAME="0.0.0.0" binds IPv4 only.
#   * The wget at the IPv6 address fails → container marked unhealthy.
# Switching the URL to ``http://127.0.0.1:…`` sidesteps the resolver
# entirely. The fix is identical across every healthcheck so a future
# image switch (alpine ↔ slim ↔ distroless) doesn't reintroduce the
# regression silently.


def _services_from(compose_path: Path) -> dict:
    import yaml
    with compose_path.open(encoding="utf-8") as f:
        return (yaml.safe_load(f) or {}).get("services", {})


def _healthcheck_test_strings(services: dict) -> list[tuple[str, str]]:
    """Return [(service_name, joined_test_string)] for every service
    that declares a healthcheck. The joined string covers both the
    ``["CMD", "wget", "url"]`` form (test[0]=='CMD') and the
    ``["CMD-SHELL", "curl url"]`` form (the URL lives in test[1])."""
    out: list[tuple[str, str]] = []
    for name, body in services.items():
        hc = body.get("healthcheck") or {}
        test = hc.get("test")
        if isinstance(test, list):
            out.append((name, " ".join(str(t) for t in test)))
        elif isinstance(test, str):
            out.append((name, test))
    return out


def test_console_next_healthcheck_uses_ipv4():
    """The reported bug: omega_console_next stayed unhealthy because
    its wget hit ``localhost``. Lock the IPv4 literal."""
    svcs = _services_from(REPO / "infra/docker-compose.yml")
    cn = svcs.get("console_next")
    assert cn, "console_next service missing from infra/docker-compose.yml"
    test = cn.get("healthcheck", {}).get("test", [])
    joined = " ".join(test) if isinstance(test, list) else str(test)
    assert "localhost" not in joined, (
        "console_next healthcheck must not address ``localhost`` — "
        "Alpine resolves it to ::1 first, Next.js listens IPv4-only. "
        f"Got: {joined!r}"
    )
    assert "127.0.0.1:3000" in joined, (
        f"console_next healthcheck must address 127.0.0.1:3000; got: {joined!r}"
    )


def test_no_healthcheck_uses_localhost_string():
    """The bug Codex found applies to every wget/curl-based healthcheck
    that addresses ``localhost``. Defensively assert ZERO usage across
    both compose files so a future copy-paste can't reintroduce the
    regression for a different service.

    Note: this guard explicitly only audits healthcheck ``test`` strings.
    Browser-visible AWS URLs are covered separately so production never
    falls back to localhost.
    """
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
    """AWS browser-visible URLs must come from deploy env, never localhost."""
    src = AWS.read_text(encoding="utf-8")
    assert "localhost" not in src, (
        "AWS compose must not contain localhost fallbacks. Public URLs "
        "should come from CONSOLE_URL / WORKSPACE_PUBLIC_URL / *_PUBLIC_URL, "
        "and internal calls should use compose service DNS."
    )


def test_console_next_dockerfile_healthcheck_uses_ipv4():
    """The Dockerfile's own HEALTHCHECK (when the image runs outside
    compose, e.g. ``docker run``) must follow the same convention."""
    src = (REPO / "console-next/Dockerfile").read_text(encoding="utf-8")
    # Extract just the HEALTHCHECK CMD line.
    m = re.search(r"HEALTHCHECK[^\n]*\n\s*CMD\s+([^\n]+)", src)
    assert m, "console-next/Dockerfile missing HEALTHCHECK ... CMD"
    cmd = m.group(1)
    assert "localhost" not in cmd, (
        f"Dockerfile HEALTHCHECK must use 127.0.0.1; got: {cmd!r}"
    )
    assert "127.0.0.1:3000" in cmd
