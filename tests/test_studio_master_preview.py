from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def studio_preview():
    os.environ["APP_ENV"] = "test"
    os.environ["INTERNAL_API_KEY"] = "x" * 64
    console_dir = str(REPO / "console")
    sys.path[:] = [p for p in sys.path if p != console_dir]
    sys.path.insert(0, console_dir)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]
    from app.services import studio_preview as service

    return service


@pytest.mark.asyncio
async def test_master_preview_returns_real_data(studio_preview):
    async def datasets():
        return [{
            "name": "employee_master",
            "layer": "gold",
            "cartridge": "replicon",
            "sources": ["raw/replicon/Employee"],
        }]

    async def invoke(tool, args):
        assert tool == "query_dataset"
        assert args["name"] == "employee_master"
        return {"data": [{"employee_id": "E-1", "name": "Ana"}]}

    result = await studio_preview.preview_master(
        entity="Employee",
        cartridge="replicon",
        limit=100,
        user_context={"tenant_id": "t1"},
        list_datasets=datasets,
        invoke_refinement=invoke,
    )

    assert result["dataset"] == "employee_master"
    assert result["rows"][0]["employee_id"] == "E-1"
    assert result["source"] == "refinement.query_dataset"


@pytest.mark.asyncio
async def test_master_preview_empty_entity_returns_clear_error(studio_preview):
    async def datasets():
        return []

    async def invoke(tool, args):  # pragma: no cover - must not be called
        raise AssertionError("query_dataset should not run without a registered Master dataset")

    with pytest.raises(HTTPException) as exc:
        await studio_preview.preview_master(
            entity="Employee",
            cartridge="replicon",
            limit=100,
            user_context={},
            list_datasets=datasets,
            invoke_refinement=invoke,
        )

    assert exc.value.status_code == 404
    assert "No Gold dataset registered" in exc.value.detail


@pytest.mark.asyncio
async def test_master_preview_validates_entity_name_input(studio_preview):
    async def datasets():
        return []

    async def invoke(tool, args):  # pragma: no cover
        return {}

    with pytest.raises(HTTPException) as exc:
        await studio_preview.preview_master(
            entity="../etc/passwd",
            cartridge="replicon",
            limit=100,
            user_context={},
            list_datasets=datasets,
            invoke_refinement=invoke,
        )

    assert exc.value.status_code == 400
