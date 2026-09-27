from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.domains.pipeline.extract_config import apply_user_scope_to_dag_conf
from app.domains.studio.entity_mutations import (
    ENTITY_DAG_PARAMS_MAX_BYTES,
    RESERVED_DAG_PARAM_KEYS,
    validate_entity_dag_params,
)
from app.services import cartridge_service


REPO = Path(__file__).resolve().parents[2]


def _airflow_reserved_keys() -> set[str]:
    tree = ast.parse((REPO / "airflow" / "dags" / "cartridge_run_admission.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "RESERVED_CONF_KEYS" for target in node.targets
        ):
            return set(ast.literal_eval(node.value.args[0]))
    raise AssertionError("RESERVED_CONF_KEYS not found")


def test_console_and_scheduler_agree_on_reserved_keys():
    assert set(RESERVED_DAG_PARAM_KEYS) == _airflow_reserved_keys()


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, {}),
        ("", {}),
        ({"target": "foundation"}, {"target": "foundation"}),
        ('{"file_pattern": "*.csv", "skiprows": 2}', {"file_pattern": "*.csv", "skiprows": 2}),
    ],
)
def test_ordinary_dag_params_are_accepted(value, expected):
    assert validate_entity_dag_params(value) == expected


@pytest.mark.parametrize("key", sorted(RESERVED_DAG_PARAM_KEYS) + ["_signature", "__class__"])
def test_reserved_keys_are_rejected(key):
    with pytest.raises(HTTPException) as error:
        validate_entity_dag_params({"target": "all", key: "x"})
    assert error.value.status_code == 400
    assert error.value.detail == "dag_params cannot set reserved keys"


@pytest.mark.parametrize("value", [[], ["tenant_id"], 7, "[1, 2]", "{not json", '"text"'])
def test_non_object_dag_params_are_rejected(value):
    with pytest.raises(HTTPException) as error:
        validate_entity_dag_params(value)
    assert error.value.status_code == 400


def test_oversized_dag_params_are_rejected():
    fits = {"blob": "a" * (ENTITY_DAG_PARAMS_MAX_BYTES - len('{"blob":""}'))}
    assert len(json.dumps(fits, separators=(",", ":")).encode()) == ENTITY_DAG_PARAMS_MAX_BYTES
    assert validate_entity_dag_params(fits) == fits
    with pytest.raises(HTTPException) as error:
        validate_entity_dag_params({"blob": fits["blob"] + "ñ"})
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_the_single_entity_write_path_refuses_reserved_keys_before_the_database(monkeypatch):
    async def no_database():
        raise AssertionError("the database must not be reached")

    monkeypatch.setattr(cartridge_service, "_pg", no_database)
    with pytest.raises(HTTPException) as error:
        await cartridge_service.upsert_entity(
            "sap_successfactors",
            "__foundation_cycle__",
            dag_params={"target": "foundation", "security_context": {"trusted": True}},
        )
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_the_single_entity_write_path_stores_validated_json(monkeypatch):
    executed: list[tuple] = []

    class _Conn:
        async def fetchval(self, *args):
            return 1

        async def execute(self, sql, *args):
            executed.append((sql, args))

        async def close(self):
            return None

    async def fake_pg():
        return _Conn()

    monkeypatch.setattr(cartridge_service, "_pg", fake_pg)
    await cartridge_service.upsert_entity("hubspot", "deals", dag_params='{"target": "all"}')
    sql, args = executed[0]
    assert "dag_params=$3::jsonb" in sql
    assert json.loads(args[2]) == {"target": "all"}


def test_unscoped_users_cannot_forward_a_context_to_airflow():
    scoped = apply_user_scope_to_dag_conf(
        {"entity": "User", "security_context": {"trusted": True, "tenant_id": "t"}},
        {"id": 1},
        security_context_builder=lambda _user: {"trusted": True, "role": "super_admin"},
    )
    assert "security_context" not in scoped
    assert scoped["entity"] == "User"


def test_scoped_users_forward_only_their_own_context():
    own = {"tenant_id": "tenant-1", "workspace_id": "workspace-1", "user_id": 1}
    scoped = apply_user_scope_to_dag_conf(
        {"entity": "User", "security_context": {"trusted": True, "tenant_id": "tenant-2"}},
        {"id": 1},
        security_context_builder=lambda _user: own,
    )
    assert scoped["security_context"] is own
