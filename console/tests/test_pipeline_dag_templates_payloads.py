from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.domains.pipeline.dag_templates_payloads import (
    dag_template_code_payload,
    dag_templates_payload,
)


class FakeDagTemplates:
    def get_all(self):
        return [{"id": "full"}]

    def get_code(self, template_id, cartridge, entity):
        if template_id == "missing":
            return None
        return f"# {cartridge}/{entity}/{template_id}"


def test_dag_templates_payload_lists_templates():
    result = dag_templates_payload(dag_templates_service=FakeDagTemplates())

    assert result == {"templates": [{"id": "full"}]}


def test_dag_template_code_payload_returns_template_code():
    result = dag_template_code_payload(
        template_id="full",
        cartridge="sap_successfactors",
        entity="User",
        dag_templates_service=FakeDagTemplates(),
    )

    assert result == {
        "id": "full",
        "cartridge": "sap_successfactors",
        "entity": "User",
        "code": "# sap_successfactors/User/full",
    }


def test_dag_template_code_payload_rejects_missing_template():
    with pytest.raises(HTTPException) as exc:
        dag_template_code_payload(
            template_id="missing",
            cartridge="sap_successfactors",
            entity="User",
            dag_templates_service=FakeDagTemplates(),
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Template 'missing' not found"
