from __future__ import annotations

from pathlib import Path

from app.schemas.control_room_surfaces import ControlRoomExperienceResponse


FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "contracts/fixtures/control-room-experience-v1.json"
)


def test_shared_experience_fixture_matches_pydantic_contract():
    experience = ControlRoomExperienceResponse.model_validate_json(
        FIXTURE.read_text(encoding="utf-8")
    )

    assert experience.schema_version == "control-room-experience/v1"
    assert "scope" not in experience.model_dump()
    assert experience.sections[0].facts[0].metric
    assert experience.sections[0].facts[0].metric.value == 0
