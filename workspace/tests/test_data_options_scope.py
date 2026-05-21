from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_MAIN = ROOT / "app" / "main.py"


def test_dataset_options_enforces_workspace_visibility_before_query():
    src = WORKSPACE_MAIN.read_text(encoding="utf-8")
    match = re.search(r"async def api_data_options[\s\S]*?preview_transform", src)
    assert match, "api_data_options handler not found"
    body = match.group(0)
    assert "_validate_dataset_name(dataset)" in body
    assert "await _assert_dataset_visible(user, dataset)" in body
    assert body.find("await _assert_dataset_visible(user, dataset)") < body.find("preview_transform")
