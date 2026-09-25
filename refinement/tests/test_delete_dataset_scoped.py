from __future__ import annotations

from pathlib import Path

import re

REFINEMENT_ROOT = Path(__file__).resolve().parents[1]


def _delete_dataset_section() -> str:
    src = (REFINEMENT_ROOT / "app/main.py").read_text(encoding="utf-8")
    marker = 'if tool == "delete_dataset":'
    assert marker in src, "delete_dataset branch is missing from refinement/app/main.py"
    body = src.split(marker, 1)[1]
    cut = re.search(r"\n    if tool == \"", body)
    return body[: cut.start()] if cut else body


def test_delete_dataset_uses_scoped_silver_path():
    body = _delete_dataset_section()
    assert "engine._silver_path(cartridge, name, user_context)" in body, (
        "delete_dataset must derive the MinIO path via engine._silver_path() "
        "with the caller's user_context — otherwise a scoped user could "
        "delete another tenant's silver parquet."
    )


def test_delete_dataset_does_not_hardcode_global_silver_path():
    body = _delete_dataset_section()
    bad_assignment = 'obj_path = f"silver/{cartridge}/{name}/data.parquet"'
    assert bad_assignment not in body, (
        "delete_dataset must not hardcode the global silver path as the "
        "primary deletion target."
    )


def test_delete_dataset_pulls_trusted_user_context():
    body = _delete_dataset_section()
    assert "_trusted_user_context(body, args)" in body, (
        "delete_dataset must build user_context with _trusted_user_context "
        "so the tenant/workspace come from the verified security context, "
        "never from unvalidated args."
    )
