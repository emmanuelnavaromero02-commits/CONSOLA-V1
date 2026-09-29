from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

import pytest

from app.services import access_tokens


class _SqlError(Exception):
    def __init__(self, sqlstate: str) -> None:
        super().__init__("database said something technical")
        self.sqlstate = sqlstate


class FakePool:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.row: dict | None = None
        self.rows: list[dict] = []
        self.value: object = None
        self.error: Exception | None = None

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, args))
        if self.error:
            raise self.error
        return self.row

    async def fetch(self, sql, *args):
        self.calls.append((sql, args))
        return self.rows

    async def fetchval(self, sql, *args):
        self.calls.append((sql, args))
        return self.value


@pytest.fixture()
def pool(monkeypatch):
    fake = FakePool()

    async def get_pool():
        return fake

    monkeypatch.setattr(access_tokens._auth, "pool", get_pool)
    return fake


def test_generated_tokens_are_strict_base62_with_256_bits():
    tokens = {access_tokens.generate_token() for _ in range(200)}
    assert len(tokens) == 200
    for token in tokens:
        assert access_tokens.looks_like_token(token)
        assert token.startswith("omega_pat_")
        assert len(token) == len("omega_pat_") + 43
    assert 62 ** 43 >= 2 ** 256


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "omega_pat_",
        "omega_pat_" + "a" * 42,
        "omega_pat_" + "a" * 44,
        "omega_pat_" + "a" * 42 + "-",
        " omega_pat_" + "a" * 43,
        "omega_pat_" + "a" * 43 + "\n",
        "OMEGA_PAT_" + "a" * 43,
        "Bearer omega_pat_" + "a" * 43,
    ],
)
def test_looks_like_token_is_a_strict_full_match(value):
    assert access_tokens.looks_like_token(value) is False


def test_hash_is_sha256_hex_and_prefix_is_short():
    token = access_tokens.generate_token()
    assert access_tokens.hash_token(token) == hashlib.sha256(token.encode()).hexdigest()
    assert access_tokens.display_prefix(token) == token[:14]
    assert len(access_tokens.display_prefix(token)) == 14


def test_scopes_normalize_and_action_scope_requires_run_or_write():
    assert access_tokens.normalize_scopes("lectura") == ["lectura"]
    assert access_tokens.normalize_scopes("acciones") == ["acciones", "lectura"]
    with pytest.raises(access_tokens.AccessTokenError):
        access_tokens.normalize_scopes("admin")
    assert access_tokens.allowed_scopes(frozenset({"datasets.read"})) == ["lectura"]
    assert access_tokens.allowed_scopes(frozenset({"pipelines.run"})) == ["lectura", "acciones"]
    assert access_tokens.allowed_scopes(frozenset({"apps.write"})) == ["lectura", "acciones"]


@pytest.mark.asyncio
async def test_create_sends_only_hash_and_prefix_to_the_database(pool):
    workspace_id = str(uuid.uuid4())
    token_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    pool.row = {
        "token_id": token_id,
        "tenant_id": uuid.uuid4(),
        "workspace_id": uuid.UUID(workspace_id),
        "token_name": "Asistente",
        "token_prefix": "omega_pat_abcd",
        "scopes": ["lectura"],
        "created_at": now,
        "expires_at": now,
    }
    created = await access_tokens.create_token(
        user_id=7,
        workspace_id=workspace_id,
        workspace_name="Operaciones",
        name="  Asistente   ",
        scopes=["lectura"],
        days=30,
    )
    sql, args = pool.calls[0]
    assert "omega_auth_create_access_token" in sql
    assert args[0] == 7
    assert args[1] == uuid.UUID(workspace_id)
    assert args[2] == "Asistente"
    assert args[3] == access_tokens.hash_token(created.secret)
    assert args[4] == created.secret[:14]
    assert created.secret not in [str(item) for item in args]
    assert created.token["id"] == str(token_id)
    assert created.token["espacio_de_trabajo"] == {"id": workspace_id, "nombre": "Operaciones"}
    assert created.secret not in repr(created)
    assert "token_hash" not in created.token


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sqlstate", "status"),
    [("22023", 400), ("42501", 403), ("53400", 409)],
)
async def test_create_maps_database_errors_to_spanish(pool, sqlstate, status):
    pool.error = _SqlError(sqlstate)
    with pytest.raises(access_tokens.AccessTokenError) as exc:
        await access_tokens.create_token(
            user_id=7,
            workspace_id=str(uuid.uuid4()),
            workspace_name=None,
            name="x",
            scopes=["lectura"],
            days=7,
        )
    assert exc.value.status_code == status
    assert "technical" not in exc.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "days"), [("", 30), ("x" * 81, 30), ("ok", 45)])
async def test_create_rejects_bad_input_before_the_database(pool, name, days):
    with pytest.raises(access_tokens.AccessTokenError) as exc:
        await access_tokens.create_token(
            user_id=7,
            workspace_id=str(uuid.uuid4()),
            workspace_name=None,
            name=name,
            scopes=["lectura"],
            days=days,
        )
    assert exc.value.status_code == 400
    assert pool.calls == []


@pytest.mark.asyncio
async def test_resolve_never_queries_malformed_tokens(pool):
    assert await access_tokens.resolve_token("omega_pat_short", "1.2.3.4") is None
    assert await access_tokens.resolve_token("eyJhbGciOiJIUzI1NiJ9.x.y", None) is None
    assert pool.calls == []


@pytest.mark.asyncio
async def test_resolve_passes_hash_and_bounded_ip(pool):
    token = access_tokens.generate_token()
    pool.row = {
        "token_id": uuid.uuid4(),
        "user_id": 7,
        "email": "a@example.invalid",
        "user_name": "Ana",
        "role": "user",
        "user_tenant_id": None,
        "tenant_id": uuid.uuid4(),
        "workspace_id": uuid.uuid4(),
        "workspace_name": "Operaciones",
        "token_name": "Asistente",
        "token_prefix": token[:14],
        "scopes": ["lectura"],
        "expires_at": datetime.now(timezone.utc),
        "status": "activo",
    }
    resolved = await access_tokens.resolve_token(token, "x" * 200)
    sql, args = pool.calls[0]
    assert "omega_auth_resolve_access_token" in sql
    assert args == (access_tokens.hash_token(token), "x" * 64)
    assert resolved["status"] == "activo"
    assert resolved["scopes"] == ["lectura"]
    assert token not in str(resolved)
    pool.row = {**pool.row, "status": "otro"}
    assert await access_tokens.resolve_token(token, None) is None


@pytest.mark.asyncio
async def test_list_and_revoke_are_scoped_to_the_user(pool):
    now = datetime.now(timezone.utc)
    pool.rows = [
        {
            "token_id": uuid.uuid4(),
            "workspace_id": uuid.uuid4(),
            "workspace_name": "Operaciones",
            "token_name": "Asistente",
            "token_prefix": "omega_pat_abcd",
            "scopes": ["lectura"],
            "created_at": now,
            "expires_at": now,
            "last_used_at": None,
            "revoked_at": None,
            "revoked_reason": None,
            "status": "activo",
        }
    ]
    listed = await access_tokens.list_tokens(7)
    assert listed[0]["estado"] == "activo"
    assert set(listed[0]) >= {"id", "nombre", "espacio_de_trabajo", "alcances", "vence_en", "ultimo_uso_en"}
    token_id = str(uuid.uuid4())
    workspace_id = uuid.uuid4()
    pool.row = {
        "token_id": uuid.UUID(token_id),
        "workspace_id": workspace_id,
        "token_prefix": "omega_pat_abcd",
        "scopes": ["lectura"],
        "expires_at": now,
    }
    revoked = await access_tokens.revoke_token(7, token_id)
    assert revoked == {
        "id": token_id,
        "token_prefix": "omega_pat_abcd",
        "alcances": ["lectura"],
        "espacio_de_trabajo": {"id": str(workspace_id)},
        "vence_en": now.isoformat(),
    }
    assert pool.calls[-1] == (
        "SELECT * FROM omega_auth_revoke_access_token($1, $2)",
        (7, uuid.UUID(token_id)),
    )
    pool.row = None
    assert await access_tokens.revoke_token(7, token_id) is None
    calls = len(pool.calls)
    assert await access_tokens.revoke_token(7, "not-a-uuid") is None
    assert len(pool.calls) == calls
