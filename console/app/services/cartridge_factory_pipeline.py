"""
Cartridge Factory Pipeline — the Level 1→4 orchestrator.
========================================================
Ties the whole "from a sentence to a validated cartridge" flow together:

    intent (NL)  ->  introspect (universal)  ->  autopilot (build blueprint)
    ->  self-repair (validate + fix)  ->  domain analytics suggestions
    ->  a build plan ready for create_full_cartridge (with a final validation gate)

Pure/deterministic so the full chain is unit-testable offline. The only thing
left for the live wiring is (a) the real HTTP fetch of the source descriptor
and (b) the DB write via create_full_cartridge + the real dry-run — both of
which need the running stack. Everything that decides WHAT to build is here and
is verified end-to-end in tests.
"""
from __future__ import annotations

from typing import Any

from app.services import (
    cartridge_autopilot,
    cartridge_intent,
    cartridge_introspect_router,
    cartridge_selfrepair,
)


def _pattern_family(kind: str) -> str:
    """Map an introspect source-kind to the build-pattern family used by
    cartridge_intent.recall_pattern (rest_sample/openapi/graphql -> rest)."""
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
    """Run introspect → autopilot → self-repair on a source descriptor.

    Returns a build plan: {ok, blueprint, summary, source_kind, pattern,
    suggested_analytics, validation, repair_report}.
    """
    if not isinstance(descriptor, dict):
        return {
            "ok": False,
            "reason": "descriptor must be an object",
            "source_kind": None,
            "pattern": None,
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
        pattern=kind,
        category=domain,
    )

    # Self-repair every SQL artifact, then validate the whole blueprint.
    repaired = cartridge_selfrepair.repair_blueprint_sql(blueprint)
    validation = cartridge_selfrepair.validate_blueprint(repaired)

    return {
        "ok": validation.ok,
        "blueprint": repaired,
        "summary": cartridge_autopilot.summarize_blueprint(repaired),
        "source_kind": kind,
        "pattern": pattern,
        # Map the introspect kind (rest_sample/openapi/graphql/...) to the
        # pattern family the memory is keyed on so recall isn't silently None.
        "build_pattern": cartridge_intent.recall_pattern(
            _pattern_family(kind), descriptor.get("auth_type") or "bearer"
        ),
        "suggested_analytics": cartridge_intent.suggest_analytics(domain),
        "validation": validation.to_dict(),
        "repair_report": repaired.get("_selfrepair_report", []),
    }


def plan_from_intent(text: str, descriptor: dict[str, Any]) -> dict[str, Any]:
    """Level 4 entry: parse NL intent, then build the plan for the primary source.

    ``descriptor`` carries the introspection material (spec/metadata/sample/csv)
    for the source named in the text — fetched live by the caller.
    """
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
    # If the user asked to cross sources we only build the primary — signal the rest.
    if intent.get("cross_source"):
        plan["unbuilt_sources"] = [s["id"] for s in intent["sources"][1:]]
        plan["note"] = "cross-source request: built primary source only"
    # Surface domain analytics tied to the requested outputs. Only spotlight true
    # matches (no fallback to the whole list, which would defeat the highlight).
    if "forecast" in intent["outputs"]:
        plan["highlighted_analytics"] = [
            a for a in plan.get("suggested_analytics", [])
            if "forecast" in (a.get("name") or "") or "forecast" in (a.get("desc") or "").lower()
        ]
    return plan
