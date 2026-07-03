from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.studio.validation import (
    clean_dag_id,
    clean_filename,
    clean_identifier,
    valid_identifier,
)


def test_valid_identifier_matches_studio_contract():
    assert valid_identifier("sap_successfactors")
    assert valid_identifier("_private_1")
    assert not valid_identifier("1bad")
    assert not valid_identifier("bad-name")
    assert not valid_identifier("")


def test_clean_identifier_raises_studio_http_error():
    with pytest.raises(HTTPException) as exc:
        clean_identifier("bad-name", label="entity")

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid entity: use letters, numbers and underscores only"


def test_clean_filename_sanitizes_path_and_empty_values():
    assert clean_filename("../My Spec!.yaml") == "My_Spec_.yaml"
    assert clean_filename("...") == "spec.yaml"
    assert clean_filename(None) == "spec.yaml"


def test_clean_dag_id_requires_safe_identifier():
    assert clean_dag_id("sap_successfactors_extract") == "sap_successfactors_extract"
    with pytest.raises(HTTPException) as exc:
        clean_dag_id("_bad")

    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid dag_id: use letters, numbers and underscores only"
