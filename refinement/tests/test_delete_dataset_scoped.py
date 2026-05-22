"""Phase-0 P0 regression: delete_dataset must use a tenant/workspace-scoped
silver path, never the global one.

This is a source-level test (the refinement service requires DuckDB + MinIO
to instantiate). It guarantees that the regression cannot land again
without somebody also editing this test.
"""
from __future__ import annotations

from pathlib import Path

import re

REFINEMENT_ROOT = Path(__file__).resolve().parents[1]


def _delete_dataset_section() -> str:
    src = (REFINEMENT_ROOT / "app/main.py").read_text(encoding="utf-8")
    marker = 'if tool == "delete_dataset":'
    assert marker in src, "delete_dataset branch is missing from refinement/app/main.py"
    body = src.split(marker, 1)[1]
    # Stop at the next top-level `if tool ==` branch so we only inspect
    # delete_dataset.
    cut = re.search(r"\n    if tool == \"", body)
    return body[: cut.start()] if cut else body


def test_delete_dataset_uses_scoped_silver_path():
    """`engine._silver_path(cartridge, name, user_context)` must be called
    so the deletion targets ONLY the caller's tenant/workspace partition."""
    body = _delete_dataset_section()
    assert "engine._silver_path(cartridge, name, user_context)" in body, (
        "delete_dataset must derive the MinIO path via engine._silver_path() "
        "with the caller's user_context — otherwise a scoped user could "
        "delete another tenant's silver parquet."
    )


def test_delete_dataset_does_not_hardcode_global_silver_path():
    """The old global path must be gone from the active code path. We allow
    it as a STRICT fallback string only when the helper does not produce an
    s3:// URL we can parse."""
    body = _delete_dataset_section()
    # The legacy assignment was a one-liner:
    #   obj_path = f"silver/{cartridge}/{name}/data.parquet"
    bad_assignment = 'obj_path = f"silver/{cartridge}/{name}/data.parquet"'
    assert bad_assignment not in body, (
        "delete_dataset must not hardcode the global silver path as the "
        "primary deletion target."
    )


def test_delete_dataset_pulls_trusted_user_context():
    """The user_context must come from `_trusted_user_context(body, args)`,
    NOT from raw client-provided fields."""
    body = _delete_dataset_section()
    assert "_trusted_user_context(body, args)" in body, (
        "delete_dataset must build user_context with _trusted_user_context "
        "so the tenant/workspace come from the verified security context, "
        "never from unvalidated args."
    )
