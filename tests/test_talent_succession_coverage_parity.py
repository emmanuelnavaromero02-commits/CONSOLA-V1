from __future__ import annotations

import json
import random
from pathlib import Path

import duckdb

from refinement.app.successfactors_succession_fallbacks import (
    succession_coverage_without_nominations,
)


ROOT = Path(__file__).resolve().parents[1]
GOLD = (
    ROOT / "cartridges/sap_successfactors/datasets/sap_successfactors_talent_succession_coverage.sql"
).read_text(encoding="utf-8")
CRITICAL = ["High", "Crítica", "MUY_ALTA", "Key Position", "critical"]
NOT_CRITICAL = ["Low", "Medium", "Non-Critical", "Normal", "No crítica"]
UNRECOGNIZED = ["3", "Strategic", "Alta prioridad", "Not Key"]
ACTIVE = ["Active", "APPROVED", "nominado"]
CLOSED = ["Rejected", "Inactive", "closed"]
UNKNOWN = ["Pending", "1", None, "Draft"]


def _oracle(positions: list[tuple], nominations: list[tuple], available: bool) -> tuple[str, int | None]:
    if not positions:
        return "posiciones_no_extraidas", None
    active = [p for p in positions if (p[3] or "").upper() not in ("I", "INACTIVE")]
    if not active:
        return "sin_posiciones_activas", None
    known_ids = {p[0] for p in positions}
    critical: dict[str, bool | None] = {}
    unrecognized = missing = 0
    for position_id, _name, value, _status in active:
        label = (value or "").strip()
        if not label:
            missing += 1
            critical[position_id] = None
        elif label in CRITICAL:
            critical[position_id] = True
        elif label in NOT_CRITICAL:
            critical[position_id] = False
        else:
            unrecognized += 1
            critical[position_id] = None
    critical_total = sum(1 for value in critical.values() if value is True)
    if missing == len(active):
        return "criticidad_no_encontrada", None
    if unrecognized:
        return "criticidad_no_reconocida", None
    if critical_total == 0 and missing:
        return "criticidad_incompleta", None
    if not available:
        return "sucesion_no_extraida", None
    targeted = [n for n in nominations if n[1] is not None]
    if not targeted:
        return "sin_nominaciones", None
    if not any(n[1] in known_ids for n in targeted):
        return "nominaciones_sin_cruce", None

    def state(status):
        return "active" if status in ACTIVE else "closed" if status in CLOSED else "unknown"

    open_unmatched = any(n[1] not in known_ids and state(n[2]) != "closed" for n in targeted)
    count = 0
    unknown = False
    for position_id, is_critical in critical.items():
        if is_critical is not True:
            continue
        states = {state(n[2]) for n in targeted if n[1] == position_id}
        if "active" in states:
            continue
        if "unknown" in states or open_unmatched:
            unknown = True
        else:
            count += 1
    if unknown:
        return ("nominaciones_cruce_parcial" if open_unmatched else "estado_nominacion_no_reconocido"), None
    if count == 0 and missing:
        return "criticidad_incompleta", None
    return "count", count


def _gold_rows(con, tmp_path: Path, positions: list[tuple], nominations: list[tuple], available: bool) -> list[dict]:
    con.execute(
        "CREATE OR REPLACE TABLE p (tenant_id VARCHAR, workspace_id VARCHAR, position_id VARCHAR, "
        "position_name VARCHAR, department VARCHAR, criticality VARCHAR, is_vacant BOOLEAN, effective_status VARCHAR)"
    )
    for position in positions:
        con.execute("INSERT INTO p VALUES ('t', 'w', ?, ?, 'D', ?, NULL, ?)", list(position))
    con.execute(
        "CREATE OR REPLACE TABLE n (tenant_id VARCHAR, workspace_id VARCHAR, nomination_id VARCHAR, "
        "user_id VARCHAR, target_position VARCHAR, readiness VARCHAR, nomination_status VARCHAR)"
    )
    for index, nomination in enumerate(nominations):
        con.execute("INSERT INTO n VALUES ('t', 'w', ?, ?, ?, 'Ready Now', ?)", [f"N{index}", *nomination])
    position_path = tmp_path / "p.parquet"
    nomination_path = tmp_path / "n.parquet"
    con.table("p").write_parquet(str(position_path))
    con.table("n").write_parquet(str(nomination_path))
    sql = GOLD if available else succession_coverage_without_nominations(GOLD)
    for name, path in (
        ("sap_successfactors_position_latest", position_path),
        ("sap_successfactors_successionnomination_latest", nomination_path),
    ):
        sql = sql.replace(f"'s3://{{bucket}}/silver/sap_successfactors/{name}/**/*.parquet'", f"'{path}'")
    cursor = con.execute(sql)
    columns = [item[0] for item in cursor.description]
    return json.loads(json.dumps([dict(zip(columns, row)) for row in cursor.fetchall()], default=str))


def _case(rng: random.Random) -> tuple[list[tuple], list[tuple], bool]:
    positions = []
    for index in range(rng.randint(0, 5)):
        pool = [None, None, "  "] + CRITICAL + NOT_CRITICAL + (UNRECOGNIZED if rng.random() < 0.3 else [])
        positions.append(
            (f"P{index}", f"Puesto {index}", rng.choice(pool), rng.choice([None, "A", "A", "I", "inactive"]))
        )
    targets = [p[0] for p in positions] + ["PX", None]
    nominations = [
        (
            f"u{rng.randint(0, 3)}",
            rng.choice(targets),
            rng.choice(ACTIVE + CLOSED + (UNKNOWN if rng.random() < 0.4 else [])),
        )
        for _ in range(rng.randint(0, 5))
    ]
    return positions, nominations, rng.random() < 0.85


_FIXED_CASES = [
    (
        [("P1", "Gerente", "High", "A"), ("P2", "Jefe", None, "A")],
        [("u1", "P1", "Active")],
        True,
    ),
    (
        [("P1", "Gerente", "High", "A"), ("P2", "Jefe", None, "A"), ("P3", "Director", "critical", "A")],
        [("u1", "P1", "Active")],
        True,
    ),
]


def test_gold_total_and_console_gating_agree_with_an_independent_oracle(tmp_path):
    from app.services.control_room import api

    rng = random.Random(269)
    seen: set[str] = set()
    con = duckdb.connect()
    cases = _FIXED_CASES + [_case(rng) for _ in range(400)]
    for iteration, (positions, nominations, available) in enumerate(cases):
        rows = _gold_rows(con, tmp_path, positions, nominations, available)
        result = {"status": "ready" if rows else "empty", "rows": rows[:50]}
        motivo = api._sf_talent_confianza_vacantes_motivo(result)
        vacantes = api._sf_talent_confianza_vacantes(result)
        totals = {row["critical_without_nominee_total"] for row in rows}
        expected, count = _oracle(positions, nominations, available)
        context = (iteration, positions, nominations, available, motivo, totals)
        seen.add(expected)

        assert len(totals) <= 1, context
        gold_total = next(iter(totals)) if totals else None
        assert (gold_total is None) == (motivo is not None), context
        if expected == "count":
            assert motivo is None, context
            assert gold_total == count == vacantes["count"], context
        else:
            assert motivo == expected, context
            assert vacantes is None, context
    con.close()

    assert {
        "count",
        "posiciones_no_extraidas",
        "sin_posiciones_activas",
        "criticidad_no_encontrada",
        "criticidad_no_reconocida",
        "criticidad_incompleta",
        "sucesion_no_extraida",
        "sin_nominaciones",
        "nominaciones_sin_cruce",
        "nominaciones_cruce_parcial",
        "estado_nominacion_no_reconocido",
    } <= seen
