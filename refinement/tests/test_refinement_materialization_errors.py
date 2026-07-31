from __future__ import annotations

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
