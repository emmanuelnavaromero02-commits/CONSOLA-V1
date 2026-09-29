from __future__ import annotations

import pytest

from refinement.app import main as refinement_main


def test_materialization_error_normalizes_s3_listing_http_400(monkeypatch):
    monkeypatch.setattr(
        refinement_main,
        "_log_internal_error",
        lambda *_args, **_kwargs: "req-s3-400",
    )
    status, detail = refinement_main._friendly_duckdb_error(
        RuntimeError(
            "HTTP Error: HTTP GET error on "
            "'/?encoding-type=url&list-type=2&prefix=raw%2Fsap_successfactors%2FCandidate%2F' "
            "(HTTP 400) while reading s3://bucket/raw/sap_successfactors/Candidate/**/*.parquet"
        ),
        "sap_successfactors_candidate_latest",
    )
    assert status == 409
    assert detail["code"] == "s3_storage_list_failed"
    assert "no pudo listar Parquet en S3" in detail["message"]


_UUID_WITH_404 = "4a404f3e-0404-4404-8404-404040404040"


@pytest.mark.parametrize(
    "error_text",
    [
        'HTTP Error: Unable to connect to URL "http://minio:9000/lakehouse/silver/'
        f'replicon/silver_a/tenant_id={_UUID_WITH_404}/data.parquet": 403 (Forbidden).',
        "HTTP Error: HTTP GET error on '/lakehouse/silver/replicon/silver_a/"
        f"tenant_id%3D{_UUID_WITH_404}/data.parquet' (HTTP 403)",
        "HTTP Error: HTTP GET error on '/lakehouse/silver/replicon/silver_a/"
        "data.parquet' (HTTP 503)",
        'HTTP Error: Unable to connect to URL "http://minio:9000/lakehouse/raw/'
        f'replicon/{_UUID_WITH_404}.parquet": 503 (Service Unavailable).',
        "HTTP Error: HTTP HEAD error on '/lakehouse/raw/replicon/x.parquet' (HTTP 401)",
        "IO Error: Connection timed out error for HTTP GET to "
        "'http://minio:9000/lakehouse/silver/replicon/silver_a/data.parquet'",
        "HTTP Error: HTTP GET error on '/lakehouse/raw/replicon/x.parquet' "
        "(HTTP 403) InvalidAccessKeyId",
        "HTTP Error: HTTP GET error on '/wrong-bucket/?encoding-type=url&list-type=2"
        "&prefix=raw%2Freplicon%2F' (HTTP 404) NoSuchBucket",
    ],
)
def test_storage_outages_are_never_missing_source_files(monkeypatch, error_text):
    monkeypatch.setattr(
        refinement_main, "_log_internal_error", lambda *_args, **_kwargs: "req-1"
    )

    status, detail = refinement_main._friendly_duckdb_error(
        RuntimeError(error_text), "silver_a"
    )

    assert status == 503
    assert detail["code"] == "storage_unavailable"
    assert detail["detail"] == "Error interno"
    assert "minio" not in detail["message"]


@pytest.mark.parametrize(
    "error_text, status, code",
    [
        (
            "IO Error: No files found that match the pattern "
            '"s3://lakehouse/raw/replicon/Candidate/**/*.parquet"',
            409,
            "source_files_missing",
        ),
        (
            "HTTP Error: HTTP GET error on '/lakehouse/silver/replicon/silver_a/"
            "data.parquet' (HTTP 404)",
            409,
            "source_files_missing",
        ),
        (
            'HTTP Error: Unable to connect to URL "s3://warehouse/silver/replicon/'
            'silver_a/data.parquet": 404 (Not Found).',
            409,
            "dependency_not_materialized",
        ),
        (
            'IO Error: Cannot open file "s3://lakehouse/silver/replicon/silver_a/'
            f'tenant_id={_UUID_WITH_404}/data.parquet": corrupted footer',
            422,
            "materialization_failed",
        ),
    ],
)
def test_missing_source_codes_require_an_explicit_absence(
    monkeypatch, error_text, status, code
):
    monkeypatch.setattr(
        refinement_main, "_log_internal_error", lambda *_args, **_kwargs: "req-2"
    )

    actual_status, detail = refinement_main._friendly_duckdb_error(
        RuntimeError(error_text), "silver_a"
    )

    assert (actual_status, detail["code"]) == (status, code)
