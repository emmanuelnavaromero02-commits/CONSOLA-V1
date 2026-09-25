from __future__ import annotations

from app.services.intelligence.gold_control_room import is_stale_generic_signal

TENANT_UUID = "d5d95d5e-0326-4f36-b04f-2a3b77ed61d2"


class TestStaleGenericGarbage:
    def test_generic_identifier_metrics_are_garbage(self):
        for metric in ("generic_user_id", "generic_department_id", "generic_company_id"):
            assert is_stale_generic_signal(metric, TENANT_UUID) is True
            assert is_stale_generic_signal(metric, "CORP_SVCS") is True

    def test_generic_structural_exact_metrics_are_garbage(self):
        for metric in ("generic_display_order", "generic_source_row_count", "generic_row_count"):
            assert is_stale_generic_signal(metric, "anything") is True

    def test_generic_kpi_metric_with_uuid_entity_is_garbage(self):
        assert is_stale_generic_signal("generic_headcount", TENANT_UUID) is True

    def test_uppercase_uuid_entity_is_detected(self):
        assert is_stale_generic_signal("generic_headcount", TENANT_UUID.upper()) is True

    def test_matches_without_control_origin(self):
        assert is_stale_generic_signal("generic_user_id", TENANT_UUID) is True


WORKSPACE_UUID = "4a6e9743-d54e-46ff-a023-111f06572c42"
OTHER_BUSINESS_UUID = "11111111-2222-3333-4444-555555555555"


class TestScopeUuidBranch:

    def test_scope_uuid_entity_is_garbage(self):
        assert is_stale_generic_signal("generic_headcount", TENANT_UUID, (TENANT_UUID, WORKSPACE_UUID)) is True
        assert is_stale_generic_signal("generic_headcount", WORKSPACE_UUID, (TENANT_UUID, WORKSPACE_UUID)) is True

    def test_non_scope_business_uuid_is_not_garbage(self):
        assert is_stale_generic_signal("generic_headcount", OTHER_BUSINESS_UUID, (TENANT_UUID, WORKSPACE_UUID)) is False

    def test_structural_metric_ignores_scope(self):
        assert is_stale_generic_signal("generic_user_id", OTHER_BUSINESS_UUID, (TENANT_UUID, WORKSPACE_UUID)) is True

    def test_no_scope_falls_back_to_any_uuid(self):
        assert is_stale_generic_signal("generic_headcount", OTHER_BUSINESS_UUID, None) is True


class TestLegitimateSignalsNotFiltered:
    def test_legitimate_generic_kpi_is_not_garbage(self):
        assert is_stale_generic_signal("generic_revenue", "MANU") is False
        assert is_stale_generic_signal("generic_headcount", "Region-Norte") is False

    def test_contract_metric_is_not_garbage(self):
        assert is_stale_generic_signal("talent_headcount_by_cohort", TENANT_UUID) is False
        assert is_stale_generic_signal("talent_fit_score", "emp-1") is False

    def test_empty_or_none_metric_is_not_garbage(self):
        assert is_stale_generic_signal(None, TENANT_UUID) is False
        assert is_stale_generic_signal("", TENANT_UUID) is False


class TestReadFilterExcludesGarbageBundle:

    def _filter(self, signals: list[dict]) -> list[dict]:
        return [
            s
            for s in signals
            if not is_stale_generic_signal(s.get("metric"), s.get("entity_id"))
        ]

    def test_bundle_drops_garbage_keeps_legit(self):
        signals = [
            {"metric": "generic_user_id", "entity_id": TENANT_UUID},
            {"metric": "generic_display_order", "entity_id": TENANT_UUID},
            {"metric": "generic_headcount", "entity_id": TENANT_UUID},
            {"metric": "generic_company_id", "entity_id": TENANT_UUID},
            {"metric": "talent_headcount_by_cohort", "entity_id": "MANU"},
            {"metric": "talent_fit_score", "entity_id": "emp-1"},
        ]
        kept = self._filter(signals)
        assert {s["metric"] for s in kept} == {"talent_headcount_by_cohort", "talent_fit_score"}
        assert len(kept) == 2
