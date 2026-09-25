from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import Any

_FORBIDDEN_SQL = re.compile(
    r"\b(attach|call|copy|create|delete|drop|export|import|insert|install|load|pragma|set|truncate|update|alter)\b",
    re.IGNORECASE,
)
_SINGLE_QUOTED_RE = re.compile(r"'(?:''|[^'])*'", re.DOTALL)
_DOUBLE_QUOTED_RE = re.compile(r'"(?:""|[^"])*"', re.DOTALL)
_SAFE_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ValidationResult:

    def __init__(self, ok: bool, reasons: list[str] | None = None) -> None:
        self.ok = ok
        self.reasons = reasons or []

    def __bool__(self) -> bool:
        return self.ok

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "reasons": list(self.reasons)}


def _base_sql_checks(sql: str) -> list[str]:
    reasons: list[str] = []
    s = (sql or "").strip()
    if not s:
        return ["sql is empty"]
    if not re.match(r"(?is)^\s*(select|with)\b", s):
        reasons.append("sql must start with SELECT or WITH")
    body = _SINGLE_QUOTED_RE.sub("''", s)
    body = _DOUBLE_QUOTED_RE.sub('""', body)
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
    masked_check = _SINGLE_QUOTED_RE.sub("''", sql or "")
    if "load_date" not in masked_check.lower() or "{latest_date}" not in (sql or ""):
        reasons.append("silver sql must filter the latest partition with {latest_date}")
    return ValidationResult(not reasons, reasons)


def validate_gold_sql(sql: str) -> ValidationResult:
    reasons = _base_sql_checks(sql)
    if "{latest_date}" in (sql or ""):
        reasons.append("gold sql must not use the Silver {latest_date} placeholder")
    if "read_parquet" in (sql or "").lower():
        reasons.append("gold sql must read registered silver datasets, not raw parquet")
    return ValidationResult(not reasons, reasons)


def _has_outer_where(text: str) -> bool:
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == '(':
            depth += 1
            i += 1
        elif c == ')':
            depth -= 1
            i += 1
        elif depth == 0 and text[i:i+5].upper() == 'WHERE':
            pre = text[i - 1] if i > 0 else ' '
            post = text[i + 5] if i + 5 < n else ' '
            if not (pre.isalnum() or pre == '_') and not (post.isalnum() or post == '_'):
                return True
            i += 1
        else:
            i += 1
    return False


def repair_sql(sql: str, layer: str, reasons: list[str]) -> str:
    s = (sql or "").strip().replace("\x00", "")

    if layer == "gold":
        s = re.sub(r"(?is)\s+where\s+load_date\s*=\s*'?\{latest_date\}'?", "", s)
        s = s.replace("{latest_date}", "")

    literals: list[str] = []

    def _stash(m: "re.Match[str]") -> str:
        literals.append(m.group(0))
        return f"\x00{len(literals) - 1}\x00"

    masked = _SINGLE_QUOTED_RE.sub(_stash, s)

    masked = re.sub(r"--[^\n]*", "", masked)
    masked = re.sub(r"/\*.*?\*/", "", masked, flags=re.DOTALL)
    masked = masked.replace(";", " ")

    if layer == "silver":
        low = masked.lower()
        if "read_parquet" in low and "{latest_date}" not in masked:
            has_where = _has_outer_where(masked)
            if "load_date" in low:
                fixed, n = re.subn(
                    r"(?i)\bload_date\s*=\s*(?:\x00\d+\x00|\S+)",
                    "load_date = '{latest_date}'",
                    masked,
                )
                if n:
                    masked = fixed
                elif has_where:
                    masked = f"{masked.rstrip()} AND load_date = '{{latest_date}}'"
                else:
                    masked = f"{masked.rstrip()} WHERE load_date = '{{latest_date}}'"
            elif has_where:
                masked = f"{masked.rstrip()} AND load_date = '{{latest_date}}'"
            else:
                masked = f"{masked.rstrip()} WHERE load_date = '{{latest_date}}'"

    def _unstash(m: "re.Match[str]") -> str:
        return literals[int(m.group(1))]

    out = re.sub(r"\x00(\d+)\x00", _unstash, masked)
    return re.sub(r"[ \t]+", " ", out).strip()


def validate_blueprint(blueprint: dict[str, Any]) -> ValidationResult:
    reasons: list[str] = []
    if not isinstance(blueprint, dict):
        return ValidationResult(False, ["blueprint must be a dict"])

    for key in ("id", "name", "entities", "datasets", "dags"):
        if key not in blueprint:
            reasons.append(f"missing manifest key: {key}")
    if reasons:
        return ValidationResult(False, reasons)

    entity_names = {e.get("entity") or e.get("name") for e in blueprint["entities"] if isinstance(e, dict)} - {None}
    dag_ids = {d.get("dag_id") for d in blueprint["dags"] if isinstance(d, dict)} - {None}
    dataset_names = {d.get("name") for d in blueprint["datasets"] if isinstance(d, dict)} - {None}

    if not entity_names:
        reasons.append("blueprint has no entities")

    for nm in entity_names | dataset_names:
        if nm is not None and not _SAFE_IDENT_RE.match(str(nm)):
            reasons.append(f"unsafe identifier '{nm}' (must match {_SAFE_IDENT_RE.pattern})")

    for e in blueprint["entities"]:
        if not isinstance(e, dict):
            continue
        did = e.get("dag_id")
        if did is not None and did not in dag_ids:
            reasons.append(f"entity '{e.get('entity')}' references unknown dag_id '{did}'")

    if len(dataset_names) != len(blueprint["datasets"]):
        reasons.append("duplicate dataset names")
    for d in blueprint["datasets"]:
        if not isinstance(d, dict):
            continue
        if d.get("layer") == "gold":
            srcs = d.get("sources") or []
            if not srcs:
                reasons.append(f"gold dataset '{d.get('name')}' has no declared sources")
            for src in srcs:
                if src not in dataset_names and src not in entity_names:
                    reasons.append(f"gold dataset '{d.get('name')}' references unknown source '{src}'")

    for d in blueprint["datasets"]:
        if not isinstance(d, dict):
            continue
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


def run_repair_loop(
    artifact: Any,
    validate_fn: Callable[[Any], ValidationResult],
    repair_fn: Callable[[Any, list[str]], Any],
    *,
    max_attempts: int = 3,
) -> dict[str, Any]:
    trace: list[dict[str, Any]] = []
    current = artifact
    effective_max = max(1, max_attempts)
    for attempt in range(1, effective_max + 1):
        vr = validate_fn(current)
        trace.append({"attempt": attempt, "ok": vr.ok, "reasons": list(vr.reasons)})
        if vr.ok:
            return {"ok": True, "artifact": current, "attempts": attempt, "trace": trace}
        if attempt == effective_max:
            break
        repaired = repair_fn(current, vr.reasons)
        if repaired == current:
            trace.append({"attempt": attempt, "ok": False, "reasons": ["repair was a no-op; aborting"]})
            break
        current = repaired
    return {"ok": False, "artifact": current, "attempts": len(trace), "trace": trace}


def repair_blueprint_sql(blueprint: dict[str, Any]) -> dict[str, Any]:
    bp = copy.deepcopy(blueprint)
    datasets = []
    report: list[dict[str, Any]] = []
    for d in bp.get("datasets", []):
        layer = d.get("layer")
        if layer in {"silver", "gold"}:
            validate = validate_silver_sql if layer == "silver" else validate_gold_sql
            outcome = run_repair_loop(
                d.get("sql", ""),
                validate,
                lambda sql, reasons, _l=layer: repair_sql(sql, _l, reasons),
                max_attempts=3,
            )
            d["sql"] = outcome["artifact"]
            report.append({"dataset": d.get("name"), "layer": layer, "ok": outcome["ok"],
                           "attempts": outcome["attempts"]})
        datasets.append(d)
    bp["datasets"] = datasets
    bp["_selfrepair_report"] = report
    return bp
