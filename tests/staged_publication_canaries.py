TENANT_A = "11111111-1111-1111-1111-111111111111"
WORKSPACE_A = "22222222-2222-2222-2222-222222222222"
TENANT_B = "33333333-3333-3333-3333-333333333333"
WORKSPACE_B = "44444444-4444-4444-4444-444444444444"

CANARIES = (
    "C1_object_pending",
    "C2_gold_prepared_then_external_failure",
    "C3_lineage_failure",
    "C4_catalog_failure",
    "C5_cas_failure",
    "C6_materialization_replay",
    "C7_concurrent_distinct_runs",
    "C8_run_digest_mismatch",
    "C9_head_versioned_cache",
    "C10_scope_and_reader_dml",
    "C11_legacy_drop_negative_control",
    "C12_upgrade_and_rerun",
)
