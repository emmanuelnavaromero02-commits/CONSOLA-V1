from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SIGNING_KEY = "cartridge-signing-key-" + "a" * 40


CARTRIDGE_CONTEXTS = [
    REPO_ROOT / "cartridges" / name / "app" / "core" / "request_context.py"
    for name in (
        "hubspot",
        "replicon",
        "salesforce",
        "sap_hcm",
        "sap_s4hana",
        "sap_successfactors",
    )
]


def _load_request_context(path: Path):
    module_name = "cartridge_request_context_" + path.parts[-4]
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("path", CARTRIDGE_CONTEXTS, ids=lambda path: path.parts[-4])
def test_cartridge_refinement_context_is_signed(monkeypatch, path: Path):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY", "transport-key-" + "b" * 40)

    module = _load_request_context(path)

    ctx = module.refinement_security_context(
        {
            "trusted": True,
            "tenant_id": "tenant-a",
            "workspace_id": "workspace-a",
            "user_id": "user-a",
        }
    )

    assert ctx["trusted"] is True
    assert ctx["tenant_id"] == "tenant-a"
    assert ctx["workspace_id"] == "workspace-a"
    assert ctx["_signature_version"] == "hmac-sha256-v1"
    assert isinstance(ctx["_signed_at"], int)
    assert len(ctx["_signature"]) == 64


@pytest.mark.parametrize("path", CARTRIDGE_CONTEXTS, ids=lambda path: path.parts[-4])
def test_cartridge_inbound_trusted_context_requires_valid_signature(monkeypatch, path: Path):
    monkeypatch.setenv("SECURITY_CONTEXT_SIGNING_KEY", SIGNING_KEY)
    monkeypatch.setenv("INTERNAL_API_KEY", "transport-key-" + "b" * 40)
    module = _load_request_context(path)

    forged = {"trusted": True, "tenant_id": "tenant-a", "workspace_id": "workspace-a"}
    with pytest.raises(module.SecurityContextError):
        module.verify_security_context(forged)

    valid = module._sign_security_context({**forged, "source": "console"})
    verified = module.verify_security_context(
        valid,
        expected_tenant_id="tenant-a",
        expected_workspace_id="workspace-a",
    )
    assert verified["tenant_id"] == "tenant-a"

    tampered = dict(valid)
    tampered["workspace_id"] = "workspace-b"
    with pytest.raises(module.SecurityContextError):
        module.verify_security_context(tampered)

    expired = module._sign_security_context({**forged, "source": "console"})
    expired[module._SIGNED_AT_FIELD] = int(expired[module._SIGNED_AT_FIELD]) - 600
    expired[module._SIGNATURE_FIELD] = module.hmac.new(
        SIGNING_KEY.encode("utf-8"),
        module._canonical_context(expired),
        module.hashlib.sha256,
    ).hexdigest()
    with pytest.raises(module.SecurityContextError):
        module.verify_security_context(expired)

    with pytest.raises(module.SecurityContextError):
        module.verify_security_context(valid, expected_tenant_id="tenant-b")


def test_local_and_aws_cartridge_compose_require_signing_key():
    local = (REPO_ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
    aws_overlay = (
        REPO_ROOT / "infra" / "terraform" / "deploy" / "docker-compose.cartridges.yml"
    ).read_text(encoding="utf-8")

    assert local.count("SECURITY_CONTEXT_SIGNING_KEY: ${SECURITY_CONTEXT_SIGNING_KEY:?") >= 6
    assert "SECURITY_CONTEXT_SIGNING_KEY: ${SECURITY_CONTEXT_SIGNING_KEY:?" in aws_overlay
