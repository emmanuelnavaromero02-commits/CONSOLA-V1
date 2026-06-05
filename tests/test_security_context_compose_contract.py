from __future__ import annotations

from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
REQUIRED = "${SECURITY_CONTEXT_SIGNING_KEY:?SECURITY_CONTEXT_SIGNING_KEY is required}"
CORE_SERVICES = ("console", "workspace", "refinement", "vault", "mcp-infra")


def _services(path: Path) -> dict:
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("services", {})


def test_local_core_services_require_security_context_signing_key():
    services = _services(REPO / "infra" / "docker-compose.yml")

    for service in CORE_SERVICES:
        assert services[service]["environment"]["SECURITY_CONTEXT_SIGNING_KEY"] == REQUIRED


def test_aws_core_services_require_security_context_signing_key():
    services = _services(REPO / "infra" / "terraform" / "deploy" / "docker-compose.aws.yml")

    for service in CORE_SERVICES:
        assert services[service]["environment"]["SECURITY_CONTEXT_SIGNING_KEY"] == REQUIRED


def test_workspace_trusted_contexts_are_signed_before_cross_service_calls():
    workspace_main = (REPO / "workspace" / "app" / "main.py").read_text(encoding="utf-8")
    assistant = (REPO / "workspace" / "app" / "services" / "consumer_assistant.py").read_text(encoding="utf-8")
    helper = (REPO / "workspace" / "app" / "services" / "security_context.py").read_text(encoding="utf-8")

    assert "from app.services.security_context import sign_security_context" in workspace_main
    assert "from app.services.security_context import sign_security_context" in assistant
    assert "return sign_security_context({" in workspace_main
    assert "return sign_security_context({" in assistant
    assert "SECURITY_CONTEXT_SIGNING_KEY must be distinct from" in helper
    assert "_signed_at" in helper
    assert "_signature_version" in helper
