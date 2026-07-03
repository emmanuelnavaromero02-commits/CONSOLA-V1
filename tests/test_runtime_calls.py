from __future__ import annotations

import pytest

from app.services.runtime_calls import call_with_optional_user, runtime_user


def test_runtime_user_keeps_dict_and_none():
    user = {"id": "u1", "role": "viewer"}

    assert runtime_user(user) is user
    assert runtime_user(None) is None


def test_runtime_user_coerces_depends_sentinel_for_direct_unit_calls():
    assert runtime_user(object()) == {"role": "owner", "allowed_cartridges": ["*"]}


@pytest.mark.asyncio
async def test_call_with_optional_user_passes_user_when_supported():
    def fn(value, *, user=None):
        return {"value": value, "user": user}

    assert await call_with_optional_user(fn, "ok", user={"id": "u1"}) == {
        "value": "ok",
        "user": {"id": "u1"},
    }


@pytest.mark.asyncio
async def test_call_with_optional_user_skips_user_when_not_supported():
    def fn(value):
        return f"seen:{value}"

    assert await call_with_optional_user(fn, "ok", user={"id": "u1"}) == "seen:ok"


@pytest.mark.asyncio
async def test_call_with_optional_user_awaits_async_result():
    async def fn(value, **kwargs):
        return value, kwargs["user"]

    assert await call_with_optional_user(fn, 7, user={"id": "u1"}) == (
        7,
        {"id": "u1"},
    )
