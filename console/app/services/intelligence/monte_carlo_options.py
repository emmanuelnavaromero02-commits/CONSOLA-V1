from __future__ import annotations

from typing import Any, Callable

from app.services.intelligence import monte_carlo_contract


def _risk_adjusted_score(summary: dict[str, Any]) -> float:
    expected = float(summary["expected_value"])
    spread = float(summary["p90"]) - float(summary["p10"])
    breach = float(summary.get("probability_breach_threshold") or 0)
    return expected - (spread * 0.25) - (breach * abs(expected if expected else 1.0))


def compare_options(
    payload: dict[str, Any],
    *,
    run_single: Callable[[dict[str, Any]], dict[str, Any]],
    validation_error: type[ValueError],
    canonical_json: Callable[[Any], str],
) -> dict[str, Any] | None:
    options = payload.get("options")
    if not options:
        return None
    if not isinstance(options, list) or len(options) > 10:
        raise validation_error("options must contain 1 to 10 entries")

    base_seed = int(payload.get("seed", 0))
    option_results: list[dict[str, Any]] = []
    option_ids: set[str] = set()
    normalized_signatures: dict[str, list[str]] = {}
    for option in options:
        if not isinstance(option, dict):
            raise validation_error("each option must be an object")
        option_id = str(option.get("option_id") or "").strip()
        if not option_id or len(option_id) > 120:
            raise validation_error("option_id is required")
        if option_id in option_ids:
            raise validation_error("option_id must be unique")
        option_ids.add(option_id)
        option_payload = {
            **payload,
            "seed": base_seed,
            "input_variables": option.get("input_variables")
            or payload.get("input_variables"),
            "assumptions": option.get("assumptions")
            or payload.get("assumptions")
            or {},
            "options": None,
        }
        result = run_single(option_payload)
        signature = canonical_json(result["normalized_input_variables"])
        normalized_signatures.setdefault(signature, []).append(option_id)
        summary = result["distribution_summary"]
        option_results.append(
            {
                "option_id": option_id,
                "label": option.get("label") or option_id,
                "seed": option_payload["seed"],
                "distribution_summary": summary,
                "sensitivity": result["sensitivity"][:10],
                "normalized_input_variables": result["normalized_input_variables"],
                "assumptions": {
                    **dict(option_payload.get("assumptions") or {}),
                    "model_default_values": monte_carlo_contract.default_assumptions(
                        str(option_payload.get("output_metric") or "net_value"),
                        set(result["normalized_input_variables"]),
                    ),
                },
                "risk_adjusted_score": round(_risk_adjusted_score(summary), 6),
            }
        )

    ranked = sorted(
        option_results,
        key=lambda item: (-item["risk_adjusted_score"], item["option_id"]),
    )
    top_score = ranked[0]["risk_adjusted_score"]
    tied_top = [item for item in ranked if item["risk_adjusted_score"] == top_score]
    equivalent = [ids for ids in normalized_signatures.values() if len(ids) > 1]
    ambiguous = len(tied_top) > 1 or bool(equivalent)
    rank = 0
    prior_score: float | None = None
    ranking = []
    for index, item in enumerate(ranked):
        if prior_score != item["risk_adjusted_score"]:
            rank = index + 1
            prior_score = item["risk_adjusted_score"]
        ranking.append(
            {
                "rank": rank,
                "option_id": item["option_id"],
                "risk_adjusted_score": item["risk_adjusted_score"],
                "expected_value": item["distribution_summary"]["expected_value"],
                "probability_breach_threshold": item["distribution_summary"][
                    "probability_breach_threshold"
                ],
            }
        )
    return {
        "status": "ambiguous" if ambiguous else "ranked",
        "selected_option_id": None if ambiguous else ranked[0]["option_id"],
        "ranking": ranking,
        "options": ranked,
    }
