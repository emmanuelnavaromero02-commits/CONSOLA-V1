import pytest
from fastapi import HTTPException

from app.routers import settings_internal


@pytest.mark.asyncio
async def test_salesforce_internal_settings_can_reveal_salesforce_keys(monkeypatch):
    async def fake_get_setting(key, include_secret=False):
        assert key == "salesforce_access_token"
        assert include_secret is True
        return {"key": key, "value": "token"}

    monkeypatch.setattr(settings_internal.settings_service, "get_setting", fake_get_setting)

    result = await settings_internal.reveal_setting_internal(
        "salesforce_access_token",
        x_internal_service="cartridge-salesforce",
    )

    assert result == {"key": "salesforce_access_token", "value": "token"}


@pytest.mark.asyncio
async def test_internal_settings_rejects_cross_cartridge_prefix():
    with pytest.raises(HTTPException) as exc:
        await settings_internal.reveal_setting_internal(
            "salesforce_access_token",
            x_internal_service="cartridge-hubspot",
        )

    assert exc.value.status_code == 403
