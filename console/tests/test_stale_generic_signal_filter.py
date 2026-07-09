"""Fase A — predicado que clasifica la señal generic-Gold *stale* (clase pre-#475).

El mismo predicado alimenta el filtro de lectura (list_signals /
_persisted_intelligence_items) y la purga quirúrgica. Debe capturar TODA la basura
observada en prod (generic_user_id/department_id/company_id/display_order/
source_row_count con entidad = UUID de tenant, y generic_headcount sobre gold
transversal) SIN tocar señales legítimas ni señales genéricas legítimas futuras.

Nota clave (verificada en prod): ``intelligence_signals.metadata`` NO persiste un
``control_origin``; el prefijo ``generic_`` del metric ES el marcador de origen
(sólo el fallback genérico nombra métricas ``generic_<campo>``). Por eso el
predicado decide por el prefijo + (campo estructural | entidad UUID), sin gate de
control_origin.
"""
from __future__ import annotations

from app.services.intelligence.gold_control_room import is_stale_generic_signal

TENANT_UUID = "d5d95d5e-0326-4f36-b04f-2a3b77ed61d2"


class TestStaleGenericGarbage:
    def test_generic_identifier_metrics_are_garbage(self):
        # Estructural (_id): basura aunque la entidad NO sea UUID.
        for metric in ("generic_user_id", "generic_department_id", "generic_company_id"):
            assert is_stale_generic_signal(metric, TENANT_UUID) is True
            assert is_stale_generic_signal(metric, "CORP_SVCS") is True

    def test_generic_structural_exact_metrics_are_garbage(self):
        for metric in ("generic_display_order", "generic_source_row_count", "generic_row_count"):
            assert is_stale_generic_signal(metric, "anything") is True

    def test_generic_kpi_metric_with_uuid_entity_is_garbage(self):
        # headcount NO es estructural, pero la entidad = UUID de scope delata la basura.
        assert is_stale_generic_signal("generic_headcount", TENANT_UUID) is True

    def test_uppercase_uuid_entity_is_detected(self):
        assert is_stale_generic_signal("generic_headcount", TENANT_UUID.upper()) is True

    def test_matches_without_control_origin(self):
        # intelligence_signals no trae control_origin; el metric generic_ estructural basta.
        assert is_stale_generic_signal("generic_user_id", TENANT_UUID) is True


WORKSPACE_UUID = "4a6e9743-d54e-46ff-a023-111f06572c42"
OTHER_BUSINESS_UUID = "11111111-2222-3333-4444-555555555555"


class TestScopeUuidBranch:
    """La rama entidad-UUID: con scope_ids sólo matchea el UUID de tenant/workspace
    (la basura real), nunca un UUID de negocio ajeno al scope (fix falso positivo)."""

    def test_scope_uuid_entity_is_garbage(self):
        assert is_stale_generic_signal("generic_headcount", TENANT_UUID, (TENANT_UUID, WORKSPACE_UUID)) is True
        assert is_stale_generic_signal("generic_headcount", WORKSPACE_UUID, (TENANT_UUID, WORKSPACE_UUID)) is True

    def test_non_scope_business_uuid_is_not_garbage(self):
        # KPI real cuya entidad de negocio es un UUID que NO es scope: NO se barre.
        assert is_stale_generic_signal("generic_headcount", OTHER_BUSINESS_UUID, (TENANT_UUID, WORKSPACE_UUID)) is False

    def test_structural_metric_ignores_scope(self):
        # Estructural es basura aunque la entidad sea un UUID de negocio ajeno.
        assert is_stale_generic_signal("generic_user_id", OTHER_BUSINESS_UUID, (TENANT_UUID, WORKSPACE_UUID)) is True

    def test_no_scope_falls_back_to_any_uuid(self):
        # Sin scope_ids (lector conservador): cualquier UUID en metric generic_ oculta.
        assert is_stale_generic_signal("generic_headcount", OTHER_BUSINESS_UUID, None) is True


class TestLegitimateSignalsNotFiltered:
    def test_legitimate_generic_kpi_is_not_garbage(self):
        # KPI real (no estructural) con entidad de negocio real (no UUID) = legítima.
        assert is_stale_generic_signal("generic_revenue", "MANU") is False
        assert is_stale_generic_signal("generic_headcount", "Region-Norte") is False

    def test_contract_metric_is_not_garbage(self):
        # Métrica contratada (no empieza por generic_) nunca es basura.
        assert is_stale_generic_signal("talent_headcount_by_cohort", TENANT_UUID) is False
        assert is_stale_generic_signal("talent_fit_score", "emp-1") is False

    def test_empty_or_none_metric_is_not_garbage(self):
        assert is_stale_generic_signal(None, TENANT_UUID) is False
        assert is_stale_generic_signal("", TENANT_UUID) is False


class TestReadFilterExcludesGarbageBundle:
    """Simula la lista que devuelve el backend y aplica el MISMO filtro que
    list_signals/_persisted_intelligence_items (por metric + entity_id, sin
    control_origin) para probar el efecto de negocio."""

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
