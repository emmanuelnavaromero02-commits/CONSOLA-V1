"""
Cartridge Self-Repair — Level 2: validate + reason-and-repair loop.
==================================================================
Validates every generated artifact (SQL per layer, the assembled blueprint,
and the seed manifest) and, on failure, applies a *reasoned* repair and
re-validates — not an identical retry. Deterministic and offline so it's
unit-testable; the LLM-backed repair (when wired) plugs into ``repair_fn``.

Pieces:
- validate_silver_sql / validate_gold_sql : layer-aware structural checks
  mirroring refinement.llm_sql's Silver contract.
- repair_sql : deterministic fixes for the common, mechanical SQL failures
  (missing latest_date filter, stray trailing semicolon, comment leakage).
- validate_blueprint : checks the Autopilot output is internally consistent
  (entity↔dataset↔dag references resolve, unique names, layer correctness).
- run_repair_loop : generic close-the-loop driver: validate → if invalid,
  repair → re-validate, up to max_attempts, returning a full trace.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

# MUST stay byte-for-byte aligned with refinement/app/llm_sql.py's
# _SQL_FORBIDDEN_RE so SQL that passes self-repair is never rejected by the
# real engine validator at create time (audit round-2 #13).
_FORBIDDEN_SQL = re.compile(
    r"\b(attach|call|copy|create|delete|drop|export|import|insert|install|load|pragma|set|truncate|update|alter)\b",
    re.IGNORECASE,
)
# Same single-quote masking as the engine (handles escaped '' inside literals).
_SINGLE_QUOTED_RE = re.compile(r"'(?:''|[^'])*'", re.DOTALL)
# SQL-safe identifier shape (matches cartridge_service's entity/dataset contract).
_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ValidationResult:
    """Lightweight validation outcome (ok + reasons)."""

    def __init__(self, ok: bool, reasons: list[str] | None = None) -> None:
        self.ok = ok
        self.reasons = reasons or []

    def __bool__(self) -> bool:  # truthy == valid
        return self.ok

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reasons": list(self.reasons)}


# ── SQL validation (layer-aware) ─────────────────────────────────────────────


def _base_sql_checks(sql: str) -> list[str]:
    reasons: list[str] = []
    s = (sql or "").strip()
    if not s:
        return ["sql is empty"]
    if not re.match(r"(?is)^\s*(select|with)\b", s):
        reasons.append("sql must start with SELECT or WITH")
    # Mask string literals FIRST so glob patterns inside paths (e.g.
    # read_parquet('s3://.../**/*.parquet')) don't trip the comment / DML
    # checks — '**/*' literally contains the '/*' block-comment sequence.
    body = _SINGLE_QUOTED_RE.sub("''", s)
    # ANY semicolon is rejected (matches the engine — even a lone trailing ';').
    if ";" in body:
        reasons.append("sql must not contain ';' (multi-statement)")
    if "--" in body or "/*" in body:
        reasons.append("sql must not contain comments")
    if _FORBIDDEN_SQL.search(body):
        reasons.append("sql must not contain DML/DDL statements")
    return reasons


def validate_silver_sql(sql: str) -> ValidationResult:
    reasons = _base_sql_checks(sql)
    low = (sql or "").lower()
    if "read_parquet" not in low:
        reasons.append("silver sql must read parquet sources (read_parquet)")
    if "load_date" not in low or "{latest_date}" not in (sql or ""):
        reasons.append("silver sql must filter the latest partition with {latest_date}")
    return ValidationResult(not reasons, reasons)


def validate_gold_sql(sql: str) -> ValidationResult:
    reasons = _base_sql_checks(sql)
    # Gold must NOT carry Silver-only markers (it reads registered silver tables).
    if "{latest_date}" in (sql or ""):
        reasons.append("gold sql must not use the Silver {latest_date} placeholder")
    if "read_parquet" in (sql or "").lower():
        reasons.append("gold sql must read registered silver datasets, not raw parquet")
    return ValidationResult(not reasons, reasons)


def repair_sql(sql: str, layer: str, reasons: list[str]) -> str:
    """Deterministic mechanical repairs for the common SQL failures.

    Subtractive fixes (comments, semicolons, leaked Silver markers in Gold) AND
    one additive fix: a Silver query missing the latest-partition filter gets a
    ``WHERE load_date = '{latest_date}'`` appended so the loop can actually
    converge instead of aborting as a no-op.
    """
    s = (sql or "").strip()

    # Gold: strip a leaked Silver latest-partition filter FIRST, on the raw
    # string — the '{latest_date}' literal must be matched before it gets
    # stashed below (otherwise the masking hides it). Then drop any stray marker.
    if layer == "gold":
        s = re.sub(r"(?is)\s+where\s+load_date\s*=\s*'?\{latest_date\}'?", "", s)
        s = s.replace("{latest_date}", "")

    # Stash single-quoted literals so the comment/semicolon strips never touch
    # literal content (a literal ';' or '--' must SURVIVE — audit #13).
    literals: list[str] = []

    def _stash(m: "re.Match[str]") -> str:
        literals.append(m.group(0))
        return f"\x00{len(literals) - 1}\x00"

    masked = _SINGLE_QUOTED_RE.sub(_stash, s)

    # strip comments + ALL semicolons (safe now — literals are stashed)
    masked = re.sub(r"--[^\n]*", "", masked)
    masked = re.sub(r"/\*.*?\*/", "", masked, flags=re.DOTALL)
    masked = masked.replace(";", " ")

    if layer == "silver":
        # additive repair: ensure the latest-partition filter is present. Append
        # at the END (a top-level predicate) instead of injecting into the first
        # WHERE — which could be a subquery's, corrupting scope (audit #13).
        low = masked.lower()
        if "read_parquet" in low and ("load_date" not in low or "{latest_date}" not in masked):
            masked = f"{masked.rstrip()} WHERE load_date = '{{latest_date}}'"

    def _unstash(m: "re.Match[str]") -> str:
        return literals[int(m.group(1))]

    out = re.sub(r"\x00(\d+)\x00", _unstash, masked)
    return re.sub(r"[ \t]+", " ", out).strip()


# ── Blueprint validation (internal consistency) ──────────────────────────────


def validate_blueprint(blueprint: dict[str, Any]) -> ValidationResult:
    reasons: list[str] = []
    if not isinstance(blueprint, dict):
        return ValidationResult(False, ["blueprint must be a dict"])

    for key in ("id", "name", "entities", "datasets", "dags"):
        if key not in blueprint:
            reasons.append(f"missing manifest key: {key}")
    if reasons:
        return ValidationResult(False, reasons)

    entity_names = {e.get("entity") or e.get("name") for e in blueprint["entities"]}
    dag_ids = {d.get("dag_id") for d in blueprint["dags"]}
    dataset_names = {d.get("name") for d in blueprint["datasets"]}

    if not entity_names:
        reasons.append("blueprint has no entities")

    # Identifier shape: entity + dataset names must be SQL-safe so the generated
    # SQL and the downstream create_full_cartridge gate never choke (audit #13 #4).
    for nm in entity_names | dataset_names:
        if nm is not None and not _SAFE_IDENT_RE.match(str(nm)):
            reasons.append(f"unsafe identifier '{nm}' (must match {_SAFE_IDENT_RE.pattern})")

    # Every entity must reference an existing dag_id.
    for e in blueprint["entities"]:
        did = e.get("dag_id")
        if did and did not in dag_ids:
            reasons.append(f"entity '{e.get('entity')}' references unknown dag_id '{did}'")

    # Dataset names unique; gold sources should reference a known silver dataset.
    if len(dataset_names) != len(blueprint["datasets"]):
        reasons.append("duplicate dataset names")
    for d in blueprint["datasets"]:
        if d.get("layer") == "gold":
            for src in d.get("sources") or []:
                if src not in dataset_names and src not in entity_names:
                    reasons.append(f"gold dataset '{d.get('name')}' references unknown source '{src}'")

    # Per-layer SQL must validate.
    for d in blueprint["datasets"]:
        layer = d.get("layer")
        if layer == "silver":
            vr = validate_silver_sql(d.get("sql", ""))
        elif layer == "gold":
            vr = validate_gold_sql(d.get("sql", ""))
        else:
            continue
        if not vr.ok:
            reasons.append(f"dataset '{d.get('name')}' ({layer}): {'; '.join(vr.reasons)}")

    return ValidationResult(not reasons, reasons)


# ── Generic close-the-loop driver ────────────────────────────────────────────


def run_repair_loop(
    artifact: Any,
    validate_fn: Callable[[Any], ValidationResult],
    repair_fn: Callable[[Any, list[str]], Any],
    *,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """Validate → (if invalid) repair → re-validate, until ok or attempts run out.

    Guards against a no-op repair: if a repair produces an identical artifact,
    the loop stops early (an identical retry can never converge).
    Returns {ok, artifact, attempts, trace}.
    """
    trace: list[dict[str, Any]] = []
    current = artifact
    for attempt in range(1, max(1, max_attempts) + 1):
        vr = validate_fn(current)
        trace.append({"attempt": attempt, "ok": vr.ok, "reasons": list(vr.reasons)})
        if vr.ok:
            return {"ok": True, "artifact": current, "attempts": attempt, "trace": trace}
        if attempt == max_attempts:
            break
        repaired = repair_fn(current, vr.reasons)
        if repaired == current:
            trace.append({"attempt": attempt, "ok": False, "reasons": ["repair was a no-op; aborting"]})
            break
        current = repaired
    return {"ok": False, "artifact": current, "attempts": len(trace), "trace": trace}


def repair_blueprint_sql(blueprint: dict[str, Any]) -> dict[str, Any]:
    """Run the SQL self-repair loop over every dataset in a blueprint.

    Returns a new blueprint with repaired SQL where possible + a per-dataset report.
    """
    bp = dict(blueprint)
    datasets = []
    report: list[dict[str, Any]] = []
    for d in blueprint.get("datasets", []):
        layer = d.get("layer")
        nd = dict(d)
        if layer in {"silver", "gold"}:
            validate = validate_silver_sql if layer == "silver" else validate_gold_sql
            outcome = run_repair_loop(
                d.get("sql", ""),
                validate,
                lambda sql, reasons, _l=layer: repair_sql(sql, _l, reasons),
                max_attempts=3,
            )
            nd["sql"] = outcome["artifact"]
            report.append({"dataset": d.get("name"), "layer": layer, "ok": outcome["ok"],
                           "attempts": outcome["attempts"]})
        datasets.append(nd)
    bp["datasets"] = datasets
    bp["_selfrepair_report"] = report
    return bp
