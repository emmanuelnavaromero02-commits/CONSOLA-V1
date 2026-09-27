from __future__ import annotations

import typing
from pathlib import Path

from app.schemas.control_room_council import CouncilDisabledReason, CouncilState


CLIENT = (
    Path(__file__).resolve().parents[1]
    / "console-next"
    / "src"
    / "lib"
    / "decisions"
    / "council-client.ts"
)


def test_client_accepts_every_reason_and_state_the_council_sends():
    source = CLIENT.read_text(encoding="utf-8")
    values = (*typing.get_args(CouncilDisabledReason), *typing.get_args(CouncilState))
    assert values
    assert [value for value in values if f'"{value}"' not in source] == []
