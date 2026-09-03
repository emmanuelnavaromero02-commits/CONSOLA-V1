from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.services.control_room import grounded_analysis as ga


ROOT = Path(__file__).resolve().parents[2]


def _items(**metadata):
    return [
        {
            "id": 17,
            "source_type": "gold",
            "data": {},
            "metadata": metadata,
        }
    ]


def _ref(path: str, item_id: int = 17) -> list[dict[str, object]]:
    return [{"evidence_item_id": item_id, "path": path}]


def _valid_refs(facts: list[dict]) -> set[tuple[int, str]]:
    return {(int(fact["evidence_item_id"]), str(fact["path"])) for fact in facts}


def _bound_pack(**metadata):
    digest = "a" * 64
    return {
        "id": 9,
        "signal_id": "talent-signal-1",
        "metadata": {
            "readiness_status": "ready",
            "completeness": "complete",
            **metadata,
        },
        "publication_dataset": "sap_successfactors_talent_9box",
        "publication_pipeline_run_id": "pipeline-1",
        "publication_materialization_run_id": "11111111-1111-1111-1111-111111111111",
        "publication_receipt_id": "22222222-2222-2222-2222-222222222222",
        "publication_head_generation": 1,
        "publication_object_uri": "gs://omega-lakehouse/gold/snapshot.parquet",
        "publication_object_version": "generation-1",
        "publication_object_checksum": digest,
        "publication_schema_digest": digest,
        "publication_evidence_digest": digest,
        "publication_row_count": 100,
        "publication_published_at": datetime.now(UTC).isoformat(),
        "publication_outcome_digest": digest,
    }


def test_safe_facts_allow_aggregate_values_and_block_direct_identifiers():
    facts, blockers = ga._safe_facts(
        _items(
            source_row_count=120,
            readiness_status="ready",
            employee_id="E-99",
            email="private@example.test",
        )
    )

    assert {fact["key"] for fact in facts} == {
        "source_row_count",
        "readiness_status",
    }
    assert blockers == ["pii_field_blocked"]


def test_safe_facts_reject_nan_and_infinity():
    facts, _ = ga._safe_facts(_items(count=math.nan, value=math.inf))
    assert facts == []


def test_safe_facts_reject_generic_text_and_identifier_values():
    facts, blockers = ga._safe_facts(
        _items(value="Persona Privada", cohort="private@example.test")
    )

    assert facts == []
    assert blockers == ["unsafe_text_fact_blocked"]


def test_grounded_analyzer_accepts_only_successfactors_talent_packs():
    valid = _bound_pack(
        source_system="sap_successfactors",
        dataset="sap_successfactors_talent_9box",
    )
    wrong_source = _bound_pack(
        source_system="replicon",
        dataset="sap_successfactors_talent_9box",
    )
    wrong_dataset = _bound_pack(
        source_system="sap_successfactors",
        dataset="sap_successfactors_employee_360",
    )

    assert ga._is_supported_talent_pack(valid) is True
    assert ga._is_supported_talent_pack(wrong_source) is False
    assert ga._is_supported_talent_pack(wrong_dataset) is False


def test_generated_claim_rejects_number_without_exact_cited_evidence():
    facts, _ = ga._safe_facts(_items(source_row_count=120, readiness_status="ready"))
    reason = ga._validate_generated_text(
        "source_row_count muestra 121 registros",
        _ref("metadata.source_row_count"),
        facts=facts,
        valid_refs=_valid_refs(facts),
    )
    assert reason == "number_without_exact_evidence"


def test_generated_claim_cannot_restate_even_an_exact_number():
    facts, _ = ga._safe_facts(_items(source_row_count=120, readiness_status="ready"))
    reason = ga._validate_generated_text(
        "source_row_count muestra 120 registros",
        _ref("metadata.source_row_count"),
        facts=facts,
        valid_refs=_valid_refs(facts),
    )
    assert reason == "numeric_claim_requires_deterministic_projection"


def test_observed_numeric_claims_include_population_and_unit():
    facts, _ = ga._safe_facts(_items(source_row_count=120, affected_count=15))
    claims = ga._fact_claims(facts)

    affected = next(
        claim for claim in claims if claim["statement"].startswith("affected_count")
    )
    assert affected["population"] == 120
    assert affected["unit"] == "records"


def test_generated_claim_rejects_irrelevant_citation():
    facts, _ = ga._safe_facts(_items(readiness_status="ready"))
    reason = ga._validate_generated_text(
        "La rotación voluntaria aumentó",
        _ref("metadata.readiness_status"),
        facts=facts,
        valid_refs=_valid_refs(facts),
    )
    assert reason == "citation_not_relevant"


def test_generated_claim_rejects_cross_pack_reference():
    facts, _ = ga._safe_facts(_items(readiness_status="ready"))
    reason = ga._validate_generated_text(
        "readiness_status está ready",
        _ref("metadata.readiness_status", 99),
        facts=facts,
        valid_refs=_valid_refs(facts),
    )
    assert reason == "cross_pack_or_missing_evidence"


def test_generated_claim_rejects_direct_identifier_even_with_valid_citation():
    facts, _ = ga._safe_facts(_items(readiness_status="ready"))
    reason = ga._validate_generated_text(
        "readiness_status corresponde a private@example.test",
        _ref("metadata.readiness_status"),
        facts=facts,
        valid_refs=_valid_refs(facts),
    )
    assert reason == "pii_in_generated_output"


def test_prompt_has_no_tools_or_automatic_employment_actions():
    system, messages = ga._prompt(
        [{"evidence_item_id": 17, "path": "metadata.count", "key": "count", "value": 3}]
    )
    assert "SOLO JSON" in system
    assert "evidence_refs" in system
    assert "evidence_item_id y path exacto" in system
    assert "no propongas promociones" in system
    assert "recommendation_only" in messages[0]["content"]


def test_safe_facts_ignore_non_gold_and_suppress_small_cells(monkeypatch):
    monkeypatch.setenv("CONTROL_ROOM_MIN_AGGREGATE_CELL_SIZE", "5")
    items = _items(source_row_count=100, affected_count=1)
    items.append(
        {
            "id": 18,
            "source_type": "document",
            "data": {"source_row_count": 999},
            "metadata": {},
        }
    )

    facts, blockers = ga._safe_facts(items)

    assert {(fact["key"], fact["value"]) for fact in facts} == {
        ("source_row_count", 100)
    }
    assert blockers == ["small_cell_suppressed"]


def test_safe_facts_reject_free_text_cohort_name():
    facts, blockers = ga._safe_facts(_items(source_row_count=100, cohort="Juan Perez"))

    assert [fact["key"] for fact in facts] == ["source_row_count"]
    assert blockers == ["unsafe_text_fact_blocked"]


def test_generated_claim_rejects_citation_without_safe_fact():
    reason = ga._validate_generated_text(
        "revisar evidencia",
        _ref("metadata.readiness_status"),
        facts=[],
        valid_refs=set(),
    )
    assert reason == "cross_pack_or_missing_evidence"


@pytest.mark.parametrize(
    "statement",
    (
        "readiness_status recomienda promoción",
        "readiness_status recommends promotion",
        "readiness_status terminate employee",
        "readiness_status adjust compensation",
        "readiness_status increase salary",
        "readiness_status iniciar PIP",
    ),
)
def test_generated_claim_rejects_employment_actions_in_both_languages(statement):
    facts, _ = ga._safe_facts(_items(readiness_status="ready"))
    assert (
        ga._validate_generated_text(
            statement,
            _ref("metadata.readiness_status"),
            facts=facts,
            valid_refs=_valid_refs(facts),
        )
        == "prohibited_employment_action"
    )


def test_generated_claim_rejects_arbitrary_concept_despite_token_overlap():
    facts, _ = ga._safe_facts(_items(readiness_status="ready"))
    reason = ga._validate_generated_text(
        "readiness_status ready demuestra favoritismo sistémico",
        _ref("metadata.readiness_status"),
        facts=facts,
        valid_refs=_valid_refs(facts),
    )
    assert reason == "unsupported_generated_concept"


def test_grounded_verifier_accepts_evidence_bound_hypothesis_and_option_only():
    facts, _ = ga._safe_facts(_items(readiness_status="ready"))

    assert (
        ga._validate_generated_text(
            "Revisar readiness_status ready antes de decidir",
            _ref("metadata.readiness_status"),
            facts=facts,
            valid_refs=_valid_refs(facts),
        )
        is None
    )
    assert (
        ga._validate_generated_text(
            "Validar datos de readiness_status ready",
            _ref("metadata.readiness_status"),
            facts=facts,
            valid_refs=_valid_refs(facts),
        )
        is None
    )
    assert (
        ga._validate_generated_text(
            "readiness_status ready demuestra favoritismo sistémico",
            _ref("metadata.readiness_status"),
            facts=facts,
            valid_refs=_valid_refs(facts),
        )
        == "unsupported_generated_concept"
    )


@pytest.mark.parametrize(
    "statement", ("readiness_status complete", "readiness_status blocked complete")
)
def test_generated_claim_cannot_mix_values_between_paths_of_the_same_item(statement):
    facts, _ = ga._safe_facts(
        _items(readiness_status="blocked", completeness="complete")
    )

    reason = ga._validate_generated_text(
        statement,
        _ref("metadata.readiness_status"),
        facts=facts,
        valid_refs=_valid_refs(facts),
    )

    assert reason == "citation_value_mismatch"


def test_generated_schema_requires_explicit_item_path_references():
    with pytest.raises(ValidationError):
        ga._GeneratedAnalysis.model_validate(
            {
                "hypotheses": [
                    {
                        "statement": "readiness_status ready",
                        "evidence_item_ids": [17],
                    }
                ]
            }
        )

    parsed = ga._GeneratedAnalysis.model_validate(
        {
            "hypotheses": [
                {
                    "statement": "readiness_status ready",
                    "evidence_refs": _ref("metadata.readiness_status"),
                }
            ]
        }
    )
    assert parsed.hypotheses[0].evidence_refs[0].path == "metadata.readiness_status"


def test_computed_claim_is_recalculated_with_versioned_formula():
    facts, _ = ga._safe_facts(_items(population_total=100, ready_count=80))

    claims = ga._computed_claims(facts)

    assert len(claims) == 1
    assert claims[0]["value"] == 0.8
    assert claims[0]["population"] == 100
    assert claims[0]["formula"] == "ready_count / population_total"
    assert claims[0]["ruleset_version"] == ga.RULESET_VERSION


@pytest.mark.parametrize(
    ("population_key", "expected_formula"),
    (
        ("population", "ready_count / population"),
        ("source_row_count", "ready_count / source_row_count"),
        ("row_count", "ready_count / row_count"),
    ),
)
def test_computed_claim_formula_names_the_exact_denominator(
    population_key, expected_formula
):
    facts, _ = ga._safe_facts(_items(**{population_key: 20, "ready_count": 15}))

    claims = ga._computed_claims(facts)

    assert claims[0]["formula"] == expected_formula
    assert claims[0]["evidence_paths"] == [
        "metadata.ready_count",
        f"metadata.{population_key}",
    ]


def test_public_evidence_paths_are_allowlisted():
    assert ga._public_evidence_paths(
        ["data.ready_count", "metadata.population_total", "private.email", "data.x-y"]
    ) == ["data.ready_count", "metadata.population_total"]


def test_public_evidence_refs_preserve_pairs_and_reject_ambiguous_arrays():
    assert ga._public_evidence_refs(
        [17, 17], ["metadata.readiness_status", "metadata.completeness"]
    ) == [
        {"evidence_item_id": 17, "path": "metadata.completeness"},
        {"evidence_item_id": 17, "path": "metadata.readiness_status"},
    ]
    assert ga._public_evidence_refs(
        [17], ["metadata.readiness_status", "metadata.completeness"]
    ) == []


def test_freshness_uses_oldest_gold_snapshot_not_pack_creation_time():
    old = datetime.now(UTC) - timedelta(days=2)
    recent = datetime.now(UTC) - timedelta(minutes=5)
    pack = {
        "created_at": datetime.now(UTC),
        "metadata": {"materialized_at": recent.isoformat()},
    }
    items = _items(materialized_at=old.isoformat(), source_row_count=100)

    assert ga._evidence_as_of(pack, items, {}) == old


def test_pack_identity_is_resolved_from_dashboard_intelligence_payload():
    assert (
        ga._pack_id(
            {
                "intelligence": {"evidence_pack": {"id": 41}},
                "metadata": {"intelligence": {"evidence_pack": {"id": 42}}},
            }
        )
        == 41
    )


def test_source_validation_fails_closed_on_partial_or_timestampless_gold():
    now = datetime.now(UTC)
    pack = _bound_pack(materialized_at=now.isoformat())
    partial = _items(
        materialized_at=now.isoformat(),
        source_row_count=100,
        data_status="partial",
    )
    facts, _ = ga._safe_facts(partial)
    blockers = ga._validate_source(pack, partial, {}, facts, now)
    assert "gold_item_not_ready" in blockers

    timestampless = _items(source_row_count=100, readiness_status="ready")
    facts, _ = ga._safe_facts(timestampless)
    blockers = ga._validate_source(pack, timestampless, {}, facts, now)
    assert "gold_item_timestamp_missing" in blockers


@pytest.mark.asyncio
async def test_collector_attestation_rejects_any_evidence_mutation(monkeypatch):
    monkeypatch.setenv("CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID", "grounding-test-key")
    monkeypatch.setenv(
        "CONTROL_ROOM_EVIDENCE_SIGNING_KEY",
        "grounding-test-secret-that-is-longer-than-thirty-two-bytes",
    )
    monkeypatch.delenv("CONTROL_ROOM_EVIDENCE_SIGNING_PREVIOUS_KEYS", raising=False)
    document = {"evidence_pack_id": 9, "facts": [{"count": 10}]}
    attestation = ga._attest(document)
    pack = {
        "sealed_at": datetime.now(UTC),
        "attestation_key_id": attestation["key_id"],
        "attestation_signature": attestation["signature"],
        "attestation_digest": ga._digest(document),
    }

    assert await ga._seal_or_verify_pack(None, pack, document) == attestation
    with pytest.raises(ValueError, match="digest mismatch"):
        await ga._seal_or_verify_pack(
            None,
            pack,
            {"evidence_pack_id": 9, "facts": [{"count": 11}]},
        )


def test_verifier_output_attestation_binds_claim_ledger_and_digest(monkeypatch):
    monkeypatch.setenv("CONTROL_ROOM_EVIDENCE_SIGNING_KEY_ID", "output-test-key")
    monkeypatch.setenv(
        "CONTROL_ROOM_EVIDENCE_SIGNING_KEY",
        "output-test-secret-that-is-longer-than-thirty-two-bytes",
    )
    claim = {
        "id": "89cf39ba-0af1-437e-a48e-bad9b7b560e4",
        "claim_key": "option:0",
        "claim_type": "option",
        "statement": "Validar readiness_status",
        "value": "Revisar datos",
        "unit": None,
        "population": None,
        "evidence_item_ids": [17],
        "evidence_paths": ["data.readiness_status"],
        "formula": None,
        "ruleset_version": None,
        "verification_status": "verified",
        "verification_reason": "references_verified_aggregate_evidence",
    }
    document = ga._verified_output_document(
        [claim], blockers=[], model=ga.MODEL, ruleset_version=ga.RULESET_VERSION
    )
    attestation = ga._attest_verified_output(document)

    assert ga._verified_output_attestation_valid(
        document,
        {"verifier_attestation": attestation},
        attestation["digest"],
    )
    tampered = ga._verified_output_document(
        [{**claim, "statement": "Texto alterado"}],
        blockers=[],
        model=ga.MODEL,
        ruleset_version=ga.RULESET_VERSION,
    )
    assert not ga._verified_output_attestation_valid(
        tampered,
        {"verifier_attestation": attestation},
        attestation["digest"],
    )


def test_grounded_analysis_migration_is_scoped_and_immutable():
    sql = (ROOT / "infra/init/99zzzzg_control_room_grounded_analysis.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS agent_handoffs" in sql
    assert "CREATE TABLE IF NOT EXISTS agent_claims" in sql
    assert "CREATE TABLE IF NOT EXISTS agent_rule_proposals" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "omega_rls_workspace_matches(tenant_id, workspace_id)" in sql
    assert "verified agent handoff is immutable" in sql
    assert "claim evidence is outside the bound evidence pack" in sql
    assert "activated_at IS NULL" in sql
    assert "verified handoff requires sealed evidence" in sql
    assert "agent_handoffs_active_lease_check" in sql
    assert "verified handoff requires completed scoped agent runs" in sql
    assert "agent handoff evidence identity is immutable" in sql
    assert "verified claim evidence is outside the bound pack" in sql
    assert "agent rule proposals are paused by server policy" in sql
    assert "GRANT SELECT ON agent_rule_proposals TO omega_console" in sql
    assert "BEFORE INSERT OR UPDATE OR DELETE ON agent_handoffs" in sql
    assert "agent handoff must start ready and unverified" in sql
    assert "verified output must be reconstructed from claims" in sql
    assert "verified handoff requires verifier attestation" in sql
    assert "agent_claims_exact_evidence_refs" in sql
    assert "unnest(\n                                  claim.evidence_item_ids,\n                                  claim.evidence_paths" in sql
    assert "split_part(ref.evidence_path, '.', 2)" in sql
    assert "COALESCE(jsonb_agg" in sql
    assert "Never infer authority from a legacy source name" in sql
    assert "SET cartridge_id = cartridge.id" not in sql
    assert "SET cartridge_id = dataset.cartridge" not in sql
    # Fresh Docker initialization executes SQL directly, so the migration must
    # self-register.  The day-two runner subsequently supplies its checksum.
    assert "INSERT INTO schema_migrations" in sql
    assert "'99zzzzg_control_room_grounded_analysis.sql'" in sql
    assert "ON CONFLICT (filename) DO NOTHING" in sql


def test_all_generated_output_is_derived_from_verified_claims():
    source = (
        ROOT / "console/app/services/control_room/grounded_analysis.py"
    ).read_text()
    envelope_body = source.split("async def _envelope(", 1)[1].split(
        "def _fact_claims", 1
    )[0]

    assert 'for claim in public_claims' in envelope_body
    assert 'claim["claim_type"] == "hypothesis"' in envelope_body
    assert '_json_array(data.get("hypotheses"))' not in envelope_body
    assert '_json_array(data.get("options"))' not in envelope_body
    assert '_json_array(data.get("assumptions"))' not in envelope_body
    assert 'claim["claim_type"] == "option"' in envelope_body
    assert 'claim["claim_type"] == "assumption"' in envelope_body
    assert "_verified_output_attestation_valid(" in envelope_body
    assert "claim_exact_evidence_validation_failed" in envelope_body


def test_router_exposes_post_and_get_analysis_contract():
    source = (ROOT / "console/app/routers/control_room.py").read_text()
    assert '"/items/{item_id}/analysis"' in source
    assert "status_code=202" in source
    assert "grounded_analysis.create_item_analysis" in source
    assert "grounded_analysis.get_item_analysis" in source
    assert "BackgroundTasks" in source
    assert "grounded_analysis.process_item_analysis" in source
    get_body = source.split("async def control_room_get_item_analysis(", 1)[1].split(
        '@router.get(\n    "/items/{item_id}/impact"', 1
    )[0]
    assert "BackgroundTasks" not in get_body
    assert "process_item_analysis" not in get_body


def test_rule_proposal_mutations_are_not_exposed_this_release():
    source = (ROOT / "console/app/routers/control_room.py").read_text()

    assert '"/rule-proposals"' not in source
    assert '"/rule-proposals/{proposal_id}/review"' not in source
    assert "grounded_analysis.create_rule_proposal" not in source
    assert "grounded_analysis.review_rule_proposal" not in source
    assert "activate_rule_proposal" not in source


def test_grounded_producer_is_real_anthropic_analysis_or_fails_closed():
    source = (
        ROOT / "console/app/services/control_room/grounded_analysis.py"
    ).read_text()

    assert 'provider != "anthropic"' in source
    assert "configured_model != MODEL" in source
    assert '"analysis_model_provider_not_allowed"' in source
    assert "'producer_role', 'talent_analyst'" in source
    assert "producer_run_id = $2" in source


@pytest.mark.asyncio
async def test_get_analysis_reuses_item_visibility_before_handoff_lookup(monkeypatch):
    denied = HTTPException(404, "control room item not found")
    item_lookup = AsyncMock(side_effect=denied)
    pool_lookup = AsyncMock()
    monkeypatch.setattr(ga.control_room_service, "get_item", item_lookup)
    monkeypatch.setattr(ga.auth, "pool", pool_lookup)

    with pytest.raises(HTTPException) as exc:
        await ga.get_item_analysis("private-owner-item", {"id": 8})

    assert exc.value is denied
    item_lookup.assert_awaited_once_with("private-owner-item", {"id": 8})
    pool_lookup.assert_not_awaited()


def test_explicit_analysis_id_is_bound_to_the_requested_item():
    source = (
        ROOT / "console/app/services/control_room/grounded_analysis.py"
    ).read_text()
    get_body = source.split("async def get_item_analysis(", 1)[1].split(
        "__all__", 1
    )[0]

    assert "await control_room_service.get_item(item_id, user)" in get_body
    assert "AND item_id = $3" in get_body
    assert "AND id = $4::uuid" in get_body


@pytest.mark.asyncio
async def test_agent_runner_consumer_recovers_ready_jobs_without_user_poll(monkeypatch):
    pool = object()
    scope = {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "workspace_id": "22222222-2222-2222-2222-222222222222",
    }
    reconcile = AsyncMock(return_value=1)
    ready = AsyncMock(
        side_effect=[["33333333-3333-3333-3333-333333333333"], []]
    )
    process = AsyncMock()
    monkeypatch.setattr(ga.auth, "pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(ga, "_active_analysis_scopes", AsyncMock(return_value=[scope]))
    monkeypatch.setattr(ga, "reconcile_abandoned_analyses", reconcile)
    monkeypatch.setattr(ga, "_ready_analysis_ids", ready)
    monkeypatch.setattr(ga, "process_item_analysis", process)

    result = await ga.process_pending_analyses(limit=5)

    assert result == {
        "status": "ready",
        "dispatched": 1,
        "reconciled": 1,
        "failures": [],
    }
    worker_context = reconcile.await_args.args[0]
    assert worker_context["active_tenant_id"] == scope["tenant_id"]
    assert worker_context["active_workspace_id"] == scope["workspace_id"]
    assert ready.await_count == 2
    assert all(
        call.args == (pool, worker_context) and call.kwargs == {"limit": 1}
        for call in ready.await_args_list
    )
    process.assert_awaited_once_with(
        "33333333-3333-3333-3333-333333333333", worker_context
    )


def test_analysis_scope_start_rotates_each_agent_runner_window():
    scopes = [
        {"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
        {"tenant_id": "tenant-b", "workspace_id": "workspace-b"},
        {"tenant_id": "tenant-c", "workspace_id": "workspace-c"},
    ]
    first_window = datetime(2026, 9, 2, 12, 55, tzinfo=UTC)

    first = ga._round_robin_analysis_scopes(scopes, at=first_window)
    second = ga._round_robin_analysis_scopes(
        scopes, at=first_window + timedelta(minutes=5)
    )

    assert second == [*first[1:], first[0]]


@pytest.mark.asyncio
async def test_pending_analysis_budget_is_round_robin_across_scopes(monkeypatch):
    pool = object()
    scopes = [
        {"tenant_id": "tenant-a", "workspace_id": "workspace-a"},
        {"tenant_id": "tenant-b", "workspace_id": "workspace-b"},
    ]
    ready_count = {"workspace-a": 0, "workspace-b": 0}

    async def ready(_pool, user, *, limit):
        assert _pool is pool
        assert limit == 1
        workspace = user["workspace_id"]
        ready_count[workspace] += 1
        return [f"{workspace}-handoff-{ready_count[workspace]}"]

    reconcile = AsyncMock(return_value=0)
    process = AsyncMock()
    monkeypatch.setattr(ga.auth, "pool", AsyncMock(return_value=pool))
    monkeypatch.setattr(ga, "_active_analysis_scopes", AsyncMock(return_value=scopes))
    monkeypatch.setattr(
        ga, "_round_robin_analysis_scopes", lambda values: list(values)
    )
    monkeypatch.setattr(ga, "reconcile_abandoned_analyses", reconcile)
    monkeypatch.setattr(ga, "_ready_analysis_ids", AsyncMock(side_effect=ready))
    monkeypatch.setattr(ga, "process_item_analysis", process)

    result = await ga.process_pending_analyses(limit=4)

    assert result == {
        "status": "ready",
        "dispatched": 4,
        "reconciled": 0,
        "failures": [],
    }
    assert [call.args[0] for call in process.await_args_list] == [
        "workspace-a-handoff-1",
        "workspace-b-handoff-1",
        "workspace-a-handoff-2",
        "workspace-b-handoff-2",
    ]
    assert [call.kwargs["limit"] for call in reconcile.await_args_list] == [2, 2]


def test_request_path_only_enqueues_and_worker_owns_model_call():
    source = (
        ROOT / "console/app/services/control_room/grounded_analysis.py"
    ).read_text()
    create_body = source.split("async def create_item_analysis(", 1)[1].split(
        "def analysis_worker_context", 1
    )[0]
    worker_body = source.split("async def process_item_analysis(", 1)[1].split(
        "async def get_item_analysis", 1
    )[0]

    assert "await caller(" not in create_body
    assert 'status = "insufficient_data" if blockers else "ready"' in create_body
    assert "await caller(" in worker_body
    assert "lease_owner" in worker_body
    assert "analysis_run_id" in worker_body
    assert "verifier_run_id" in worker_body
