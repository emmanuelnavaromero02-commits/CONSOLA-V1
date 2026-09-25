from __future__ import annotations

from typing import Any

from app.services import (
    cartridge_autopilot,
    cartridge_intent,
    cartridge_introspect_router,
    cartridge_selfrepair,
)


def _pattern_family(kind: str) -> str:
    k = str(kind or "").lower()
    if k in {"rest_sample", "openapi", "graphql", "rest", "file_csv"}:
        return "rest"
    if k == "odata":
        return "odata"
    if k == "sql":
        return "sql"
    if k == "soap":
        return "soap"
    return "rest"


def plan_from_descriptor(
    descriptor: dict[str, Any],
    *,
    cartridge_id: str,
    name: str,
    domain: str = "custom",
) -> dict[str, Any]:
    if not isinstance(descriptor, dict):
        return {
            "ok": False,
            "reason": "descriptor must be an object",
            "source_kind": None,
            "pattern": cartridge_introspect_router.detect_source_pattern({}),
        }
    entities, kind = cartridge_introspect_router.extract_entities(descriptor)
    pattern = cartridge_introspect_router.detect_source_pattern(
        descriptor,
        [f for e in entities for f in e.get("fields", [])],
    )

    if not entities:
        return {
            "ok": False,
            "reason": "introspection produced no entities; provide a spec/metadata/sample",
            "source_kind": kind,
            "pattern": pattern,
        }

    blueprint = cartridge_autopilot.build_blueprint(
        cartridge_id=cartridge_id,
        name=name,
        entities=entities,
        pattern=_pattern_family(kind),
        category=domain,
    )

    repaired = cartridge_selfrepair.repair_blueprint_sql(blueprint)
    validation = cartridge_selfrepair.validate_blueprint(repaired)

    return {
        "ok": validation.ok,
        "blueprint": repaired,
        "summary": cartridge_autopilot.summarize_blueprint(repaired),
        "source_kind": kind,
        "pattern": pattern,
        "build_pattern": cartridge_intent.recall_pattern(
            _pattern_family(kind), descriptor.get("auth_type") or "bearer"
        ),
        "suggested_analytics": cartridge_intent.suggest_analytics(domain),
        "validation": validation.to_dict(),
        "repair_report": repaired.get("_selfrepair_report", []),
    }


def plan_from_intent(text: str, descriptor: dict[str, Any]) -> dict[str, Any]:
    intent = cartridge_intent.parse_build_intent(text)
    if not intent["actionable"] or not intent["primary_source"]:
        return {
            "ok": False,
            "reason": "could not identify a source in the request",
            "intent": intent,
        }
    src = intent["primary_source"]
    plan = plan_from_descriptor(
        descriptor,
        cartridge_id=src["id"],
        name=src["alias"].title(),
        domain=src.get("domain", "custom"),
    )
    plan["intent"] = intent
    if intent.get("cross_source"):
        plan["unbuilt_sources"] = [s["id"] for s in intent["sources"][1:]]
        plan["note"] = "cross-source request: built primary source only"
    if "forecast" in intent["outputs"]:
        plan["highlighted_analytics"] = [
            a for a in plan.get("suggested_analytics", [])
            if "forecast" in (a.get("name") or "") or "forecast" in (a.get("desc") or "").lower()
        ]
    return plan
