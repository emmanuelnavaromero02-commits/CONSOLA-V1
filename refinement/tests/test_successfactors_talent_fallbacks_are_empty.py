from __future__ import annotations

import pytest

from refinement.app.successfactors_talent_empty_fallbacks import TALENT_EMPTY_FALLBACK_SQL


@pytest.mark.parametrize("dataset", sorted(TALENT_EMPTY_FALLBACK_SQL))
def test_a_missing_talent_source_publishes_no_rows(dataset):
    sql = TALENT_EMPTY_FALLBACK_SQL[dataset]
    assert sql.strip().splitlines()[-1].strip() == "WHERE FALSE"
    assert "generated_at" in sql
