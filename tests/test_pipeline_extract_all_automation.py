from __future__ import annotations

import pytest

from app.domains.pipeline.extract_all import fanout_pipeline_extract_all


async def _call_with_optional_user(func, *args, user=None):
    return await func(*args)


@pytest.mark.anyio
async def test_fanout_surfaces_first_entity_automation_notice():
    async def api_pipeline(cartridge):
        return {"pipeline": [{"entity": "Department"}, {"entity": "Project"}]}

    async def api_pipeline_extract(cartridge, entity, body):
        result = {"job_id": f"run-{entity}", "dag_id": f"{cartridge}_extract"}
        if entity == "Department":
            result["automation"] = {
                "was_paused": True,
                "unpaused": True,
                "message_es": "reactivado",
            }
        return result

    payload = await fanout_pipeline_extract_all(
        cartridge="replicon",
        body={},
        user=None,
        api_pipeline=api_pipeline,
        api_pipeline_extract=api_pipeline_extract,
        call_with_optional_user=_call_with_optional_user,
        sync_entity_idempotency_key=lambda base, entity: None,
        error_id_factory=lambda: "err",
    )

    assert payload["automation"] == {
        "was_paused": True,
        "unpaused": True,
        "message_es": "reactivado",
    }
    assert payload["count"] == 2


@pytest.mark.anyio
async def test_fanout_omits_automation_when_no_entity_reports_one():
    async def api_pipeline(cartridge):
        return {"pipeline": [{"entity": "Department"}]}

    async def api_pipeline_extract(cartridge, entity, body):
        return {"job_id": f"run-{entity}"}

    payload = await fanout_pipeline_extract_all(
        cartridge="replicon",
        body={},
        user=None,
        api_pipeline=api_pipeline,
        api_pipeline_extract=api_pipeline_extract,
        call_with_optional_user=_call_with_optional_user,
        sync_entity_idempotency_key=lambda base, entity: None,
        error_id_factory=lambda: "err",
    )

    assert "automation" not in payload
