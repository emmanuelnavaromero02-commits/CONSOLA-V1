"""Fase B / GATE 3 — blockers de talent_cpa_scores condicionales por componente.

Antes, cpa_scores emitia el array estatico de los 3 KB
(["KB-COMPETENCIAS blocked","KB-DESEMPENO blocked","KB-ASPIRACION blocked"]) cuando
faltaba CUALQUIERA de C/P/A, marcando KB-DESEMPENO como bloqueado aun con
performance_100 presente (el dato SI existe en prod). El fix lista cada KB solo si
su score falta. NO cambia cpa_status / fit_score / 9-box / C-A / logica SAP.
"""
from __future__ import annotations

import pathlib
import re

import pytest

CPA_SQL = (
    pathlib.Path(__file__).resolve().parents[1]
    / "cartridges/sap_successfactors/datasets/sap_successfactors_talent_cpa_scores.sql"
).read_text(encoding="utf-8")

# Expresion de blockers reproducida del SQL (misma logica; el test estructural de
# abajo garantiza que el archivo no diverja de esta forma condicional).
_BLOCKERS_EXPR = """
    CASE
      WHEN competency_100 IS NULL OR performance_100 IS NULL OR aspiration_100 IS NULL
        THEN to_json(list_filter([
                CASE WHEN competency_100 IS NULL THEN 'KB-COMPETENCIAS blocked' END,
                CASE WHEN performance_100 IS NULL THEN 'KB-DESEMPENO blocked' END,
                CASE WHEN aspiration_100 IS NULL THEN 'KB-ASPIRACION blocked' END
            ], x -> x IS NOT NULL))::VARCHAR
      ELSE '[]'
    END AS blockers
"""


class TestBlockersStructure:
    def test_static_all_three_array_is_gone(self):
        # El array estatico de los 3 KB no debe existir mas.
        assert '"KB-COMPETENCIAS blocked","KB-DESEMPENO blocked","KB-ASPIRACION blocked"' not in CPA_SQL

    def test_each_kb_is_conditional_on_its_own_score(self):
        for score, kb in (
            ("competency_100", "KB-COMPETENCIAS blocked"),
            ("performance_100", "KB-DESEMPENO blocked"),
            ("aspiration_100", "KB-ASPIRACION blocked"),
        ):
            assert re.search(
                rf"WHEN\s+{score}\s+IS NULL\s+THEN\s+'{re.escape(kb)}'", CPA_SQL
            ), f"{kb} debe estar condicionado a {score} IS NULL"

    def test_cpa_status_and_fit_score_unchanged(self):
        # No tocamos cpa_status ni fit_score (siguen requiriendo los 3).
        assert "END AS cpa_status" in CPA_SQL
        assert "'insufficient_data'" in CPA_SQL
        assert "END AS fit_score" in CPA_SQL


class TestBlockersBehavior:
    def _blockers(self, con, c, p, a):
        import json

        sql = (
            f"SELECT {_BLOCKERS_EXPR.replace('AS blockers', '')} "
            "FROM (VALUES (?, ?, ?)) t(competency_100, performance_100, aspiration_100)"
        )
        val = con.execute(sql, [c, p, a]).fetchone()[0]
        return json.loads(val)

    def test_performance_present_never_lists_desempeno(self):
        duckdb = pytest.importorskip("duckdb")
        con = duckdb.connect()
        # Performance presente, C/A faltantes: NO KB-DESEMPENO.
        b = self._blockers(con, None, 40.0, None)
        assert "KB-DESEMPENO blocked" not in b
        assert set(b) == {"KB-COMPETENCIAS blocked", "KB-ASPIRACION blocked"}

    def test_only_missing_components_are_listed(self):
        duckdb = pytest.importorskip("duckdb")
        con = duckdb.connect()
        assert self._blockers(con, 10.0, 40.0, 20.0) == []            # los 3 -> vacio
        assert self._blockers(con, 10.0, None, 20.0) == ["KB-DESEMPENO blocked"]  # solo P falta
        assert set(self._blockers(con, None, None, None)) == {
            "KB-COMPETENCIAS blocked", "KB-DESEMPENO blocked", "KB-ASPIRACION blocked",
        }
