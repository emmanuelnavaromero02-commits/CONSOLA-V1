from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

import app.main as console_main


def test_build_dag_extract_conf_accepts_safe_conn_id():
    conf = console_main._build_dag_extract_conf(
        "sap_successfactors",
        "PerPerson",
        "incremental",
        {"conn_id": "femsa_sf"},
    )

    assert conf["conn_id"] == "femsa_sf"


def test_build_dag_extract_conf_rejects_unsafe_conn_id():
    with pytest.raises(HTTPException) as exc:
        console_main._build_dag_extract_conf(
            "sap_successfactors",
            "PerPerson",
            "incremental",
            {"conn_id": "../../femsa_sf"},
        )

    assert exc.value.status_code == 400


def test_pipeline_extract_metadata_selects_connection_id():
    source = Path(console_main.__file__).read_text(encoding="utf-8")

    assert "e.connection_id AS connection_id" in source
    assert 'extract_conf["conn_id"] = _normalize_pipeline_conn_id(metadata.get("connection_id"))' in source
