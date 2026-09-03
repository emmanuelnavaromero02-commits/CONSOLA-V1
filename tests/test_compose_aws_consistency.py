"""Sprint v1.43.1 — Codex P0-4: AWS compose either includes the
cartridges or documents that they're deployed separately, and the
Airflow services are threaded with the cartridge URL env vars so the
v1.43.1-hardened DAGs can resolve them.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
AWS_COMPOSE = REPO / "infra/terraform/deploy/docker-compose.aws.yml"
AWS_CARTRIDGES = REPO / "infra/terraform/deploy/docker-compose.cartridges.yml"
RUNBOOK     = REPO / "infra/terraform/deploy/DEPLOY-RUNBOOK.md"


def _doc():
    return yaml.safe_load(AWS_COMPOSE.read_text(encoding="utf-8"))


def _cartridge_doc():
    return yaml.safe_load(AWS_CARTRIDGES.read_text(encoding="utf-8"))


def test_aws_compose_either_includes_or_documents_cartridges():
    """Two acceptable outcomes:
      (a) the compose declares the cartridge services, OR
      (b) the runbook documents they ship separately.

    Anything else is the audit finding — operator deploys Airflow
    with DAGs that call hosts that don't resolve.
    """
    doc = _doc()
    cartridges = ("replicon", "hubspot", "sap_hcm", "sap_s4hana", "sap_successfactors",
                  "sap-hcm", "sap-s4hana", "sap-successfactors")
    declared = set(doc.get("services", {}).keys()) & set(cartridges)

    runbook_text = RUNBOOK.read_text(encoding="utf-8")
    documented = (
        "Cartridges deployed separately" in runbook_text
        and "does NOT include" in runbook_text
    )

    assert declared or documented, (
        "AWS compose has neither cartridge services declared nor a "
        "DEPLOY-RUNBOOK explanation. Operator would deploy DAGs that "
        "call hosts that don't resolve."
    )


def test_default_cartridge_urls_resolve_to_declared_aws_services():
    """Every default Docker hostname handed to Airflow must exist in the
    combined AWS compose service set. Otherwise the UI shows getaddrinfo
    failures such as ``Temporary failure in name resolution``.
    """
    raw = AWS_COMPOSE.read_text(encoding="utf-8")
    services = set(_doc().get("services", {})) | set(_cartridge_doc().get("services", {}))
    expected = {
        "replicon",
        "hubspot",
        "salesforce",
        "banxico",
        "inegi",
        "sec-edgar",
        "sap-hcm",
        "sap-s4hana",
    }
    for hostname in expected:
        assert f"http://{hostname}:" in raw
        assert hostname in services, f"{hostname} is used in AWS URLs but has no service"


def test_same_host_cartridges_are_internal_only():
    """The same-host overlay is for Docker bridge DNS, not public ports."""
    services = _cartridge_doc().get("services", {})
    for name in ("replicon", "hubspot", "salesforce", "banxico", "inegi", "sec-edgar", "sap-hcm", "sap-s4hana"):
        assert name in services
        assert "ports" not in services[name], f"{name} must not publish host ports in AWS"


def test_aws_compose_passes_cartridge_url_env_vars_to_airflow():
    """The v1.43.1-hardened SAP DAGs read SAP_*_URL / REPLICON_URL from
    the worker env. Compose must thread these into BOTH the airflow
    webserver and the scheduler so the DAG behaves the same in either
    component that imports it."""
    raw = AWS_COMPOSE.read_text(encoding="utf-8")
    for env_var in ("SAP_HCM_URL", "SAP_S4HANA_URL",
                    "REPLICON_URL", "HUBSPOT_URL",
                    "SALESFORCE_URL", "BANXICO_URL", "INEGI_URL", "SEC_EDGAR_URL"):
        # At least twice — airflow + airflow-scheduler.
        assert raw.count(env_var) >= 2, (
            f"{env_var} should be set on both airflow + airflow-scheduler "
            f"in docker-compose.aws.yml"
        )


def test_aws_storage_provider_is_explicit_and_allows_instance_roles():
    """AWS must not be inferred as MinIO when EC2 supplies credentials by role."""
    core = _doc().get("services", {})
    cartridges = _cartridge_doc().get("services", {})

    for service in ("console", "refinement", "mcp-infra", "sap-successfactors"):
        env = core[service]["environment"]
        assert env["LAKEHOUSE_PROVIDER"] == "s3"
        assert "amazonaws.com" in env["LAKEHOUSE_ENDPOINT"]
        # Empty static keys are intentional: the runtime must select IMDSv2.
        assert env["MINIO_ACCESS_KEY"] == "${AWS_ACCESS_KEY_ID:-}"
        assert env["MINIO_SECRET_KEY"] == "${AWS_SECRET_ACCESS_KEY:-}"

    for service in ("airflow", "airflow-scheduler"):
        env = core[service]["environment"]
        assert env["LAKEHOUSE_PROVIDER"] == "s3"
        assert env["AIRFLOW_VAR_LAKEHOUSE_PROVIDER"] == "s3"
        assert env["MINIO_ACCESS_KEY"] == "${AWS_ACCESS_KEY_ID:-}"
        assert env["MINIO_SECRET_KEY"] == "${AWS_SECRET_ACCESS_KEY:-}"

    for service, definition in cartridges.items():
        env = definition.get("environment", {})
        if "MINIO_ENDPOINT" not in env:
            continue
        assert env["LAKEHOUSE_PROVIDER"] == "s3", service
        assert "amazonaws.com" in env["LAKEHOUSE_ENDPOINT"], service
        assert env["MINIO_ACCESS_KEY"] == "${AWS_ACCESS_KEY_ID:-}", service
        assert env["MINIO_SECRET_KEY"] == "${AWS_SECRET_ACCESS_KEY:-}", service
        assert env["AWS_ACCESS_KEY_ID"] == "${AWS_ACCESS_KEY_ID:-}", service
        assert env["AWS_SECRET_ACCESS_KEY"] == "${AWS_SECRET_ACCESS_KEY:-}", service
        assert env["AWS_SESSION_TOKEN"] == "${AWS_SESSION_TOKEN:-}", service


def test_aws_compose_validates_as_yaml():
    """The compose YAML must parse (catches the silly typo case)."""
    doc = _doc()
    assert isinstance(doc, dict)
    assert "services" in doc


def test_aws_compose_minio_not_latest_tag():
    """A pre-existing audit posture: production images should be
    pinned. If MinIO ever flips to ``:latest`` the deploy turns
    non-reproducible. Pin-only, not pin-which-tag."""
    doc = _doc()
    services = doc.get("services", {})
    for name, svc in services.items():
        image = (svc or {}).get("image", "")
        if isinstance(image, str) and image.startswith("minio/minio"):
            assert ":latest" not in image, (
                f"service {name} pins minio/minio:latest — pin a real tag"
            )


def test_runbook_describes_cartridge_deploy_options():
    """The runbook must show the operator how to handle the cartridge
    deployment — either the same-host escape hatch or the separate
    cluster pattern."""
    txt = RUNBOOK.read_text(encoding="utf-8")
    assert "Same host" in txt or "same host" in txt
    assert "Separate cluster" in txt or "separate cluster" in txt
    # And the verification block so operators can spot a misconfigured
    # SAP_*_URL before a DAG run.
    assert "airflow dags list-import-errors" in txt
