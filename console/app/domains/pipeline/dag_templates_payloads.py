from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def dag_templates_payload(*, dag_templates_service: Any) -> dict:
    return {"templates": dag_templates_service.get_all()}


def dag_template_code_payload(
    *,
    template_id: str,
    cartridge: str,
    entity: str,
    dag_templates_service: Any,
) -> dict:
    code = dag_templates_service.get_code(template_id, cartridge, entity)
    if code is None:
        raise HTTPException(404, f"Template '{template_id}' not found")
    return {"id": template_id, "cartridge": cartridge, "entity": entity, "code": code}
