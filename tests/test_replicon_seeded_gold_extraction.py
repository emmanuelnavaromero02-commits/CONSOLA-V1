from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_replicon_airflow_dag_short_circuits_seeded_gold_before_http_client() -> None:
    source = (REPO / "cartridges/replicon/dags/replicon_extract.py").read_text(encoding="utf-8")

    assert "def _is_seeded_gold_conn_id" in source
    assert "def _is_seeded_gold_connection" in source
    assert "def _seeded_gold_result" in source
    assert "seed_only" in source
    assert "no external Replicon API extraction was run" in source

    direct_seed_branch = source.index("if _is_seeded_gold_conn_id(conn_id):")
    vault_reveal = source.index("base_url, connection, resolved_conn_id = _resolve_connection(entity, conn_id, security_context)")
    seed_branch = source.index("if _is_seeded_gold_connection(base_url, connection):")
    external_client = source.index("client = _RepliconClient(base_url, connection)")

    assert direct_seed_branch < vault_reveal
    assert seed_branch < external_client


def test_replicon_client_short_circuits_seeded_gold_extract_before_api_post() -> None:
    source = (REPO / "cartridges/replicon/app/core/replicon_client.py").read_text(encoding="utf-8")

    extract_table = source[source.index("    def extract_table") :]
    seed_branch = extract_table.index("if self._is_seeded_gold_connection():")
    external_post = extract_table.index("extract_id = self._create_extract")

    assert seed_branch < external_post
