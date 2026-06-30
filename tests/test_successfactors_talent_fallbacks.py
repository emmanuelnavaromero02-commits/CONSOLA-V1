from refinement.app.successfactors_fallbacks import fallback_dataset_for_successfactors


def test_talent_employee_profile_fallback_uses_foundation_gold_snapshot():
    fallback = fallback_dataset_for_successfactors(
        {
            "name": "sap_successfactors_talent_employee_profile",
            "layer": "gold",
            "description": "Talent profile",
        },
        RuntimeError("HTTP Error: 404 (Not Found)"),
    )

    assert fallback is not None
    assert fallback["sources"] == [
        "gold/sap_successfactors/sap_successfactors_employee_360"
    ]
    assert "sap_successfactors_employee_360" in fallback["sql_def"]
    assert "missing_performance" in fallback["sql_def"]
    assert "foundation_ready" in fallback["sql_def"]


def test_talent_chain_fallbacks_keep_downstream_gold_materializable():
    expected_sources = {
        "sap_successfactors_talent_role_profile": [
            "gold/sap_successfactors/sap_successfactors_employee_360"
        ],
        "sap_successfactors_talent_cpa_scores": [
            "gold/sap_successfactors/sap_successfactors_talent_employee_profile"
        ],
        "sap_successfactors_talent_readiness": [
            "gold/sap_successfactors/sap_successfactors_talent_cpa_scores"
        ],
        "sap_successfactors_talent_9box": [
            "gold/sap_successfactors/sap_successfactors_talent_readiness"
        ],
    }

    for dataset, sources in expected_sources.items():
        fallback = fallback_dataset_for_successfactors(
            {"name": dataset, "layer": "gold"},
            RuntimeError("No files found that match read_parquet source"),
        )

        assert fallback is not None
        assert fallback["sources"] == sources
        assert fallback["sql_def"]
