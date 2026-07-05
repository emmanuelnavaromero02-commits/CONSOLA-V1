import pytest

from app.domains.copilot.llm_keys import (
    llm_key_set_payload,
    llm_key_status_payload,
    llm_secret_keys,
)


def test_llm_secret_keys_reads_flat_keys_and_secret_objects():
    keys = llm_secret_keys(
        {
            "keys": ["anthropic_api_key", "", None],
            "secrets": [{"key": "openai_api_key"}, {"key": ""}, "ignored"],
        }
    )

    assert keys == {"anthropic_api_key", "openai_api_key"}


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = b"{}"

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _FakeVaultClient:
    last_put = None

    def __init__(self, *_, **__):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get(self, _url):
        return _FakeResponse(payload={"keys": ["anthropic_api_key"]})

    async def put(self, url, json):
        _FakeVaultClient.last_put = {"url": url, "json": json}
        return _FakeResponse()


@pytest.mark.asyncio
async def test_llm_key_status_payload_reads_vault_keys():
    payload = await llm_key_status_payload(
        vault_scope="tenant/workspace/llm",
        vault_url="http://vault",
        vault_headers={"x-user": "u1"},
        http_client_factory=_FakeVaultClient,
    )

    assert payload == {"provider": "anthropic", "configured": True, "scope": "llm"}


@pytest.mark.asyncio
async def test_llm_key_set_payload_writes_secret_and_audits_without_key_value():
    events = []

    async def record_event(*args, **kwargs):
        events.append({"args": args, "kwargs": kwargs})

    payload = await llm_key_set_payload(
        body={"value": "sk-ant-test"},
        user={"id": "u1", "email": "u@example.com", "active_tenant_id": "t1"},
        vault_scope="tenant/workspace/llm",
        vault_url="http://vault",
        vault_headers={"x-user": "u1"},
        audit_record_event=record_event,
        http_client_factory=_FakeVaultClient,
    )

    assert payload == {"provider": "anthropic", "configured": True, "scope": "llm"}
    assert _FakeVaultClient.last_put["json"] == {"value": "sk-ant-test"}
    assert events[0]["args"][2] == "copilot.llm_key.upsert"
    assert "sk-ant-test" not in repr(events)
