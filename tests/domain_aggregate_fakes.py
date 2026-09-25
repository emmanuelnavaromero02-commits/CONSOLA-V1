from __future__ import annotations

import hashlib
import re
from typing import Any, Callable

import asyncpg

TENANT_A = "11111111-1111-4111-8111-111111111111"
WORKSPACE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
TENANT_B = "22222222-2222-4222-8222-222222222222"
WORKSPACE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

MARKER_RE = re.compile(r"--\s*omega-aggregate:\s*([A-Za-z0-9_.]+)")
PARAM_RE = re.compile(r"\$(\d+)")


def marker_of(sql: str) -> str | None:
    match = MARKER_RE.search(sql)
    return match.group(1) if match else None


def user_for(
    tenant_id: str = TENANT_A, workspace_id: str = WORKSPACE_A
) -> dict[str, str]:
    return {
        "tenant_id": tenant_id,
        "active_tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "active_workspace_id": workspace_id,
    }


class FakeGoldDataset:

    def __init__(
        self,
        name: str,
        columns: dict[str, str] | list[str],
        *,
        run_hex: str | None = None,
        generation: int = 3,
        status: str = "published",
        published_at: Any = None,
        regclass: bool = True,
        head: bool = True,
        tenant_id: str = TENANT_A,
        workspace_id: str = WORKSPACE_A,
    ) -> None:
        self.name = name
        self.tenant_id = tenant_id
        self.workspace_id = workspace_id
        if isinstance(columns, dict):
            self.columns = dict(columns)
        else:
            self.columns = {column: "text" for column in columns}
        self.columns.setdefault("tenant_id", "text")
        self.columns.setdefault("workspace_id", "text")
        self.run_hex = run_hex or hashlib.md5(name.encode("utf-8")).hexdigest()
        self.generation = generation
        self.status = status
        self.published_at = published_at
        self.regclass = regclass
        self.head = head

    @property
    def gold_table(self) -> str:
        if self.status == "published":
            return f"run_{self.run_hex}"
        return f"gold_{self.name}"

    @property
    def schema(self) -> str:
        return "omega_publication_gold" if self.status == "published" else "public"

    @property
    def relation_sql(self) -> str:
        return f'"{self.schema}"."{self.gold_table}"'

    def heads_row(self) -> dict[str, Any]:
        return {
            "run_id": f"run-{self.name}",
            "generation": self.generation,
            "status": self.status,
            "gold_table": self.gold_table,
            "receipt_id": None,
            "object_checksum": None,
            "evidence_digest": None,
            "object_uri": None,
            "object_version": None,
            "schema_digest": None,
        }


class _FakeTransaction:
    def __init__(
        self, owner: "FakeConn", isolation: str | None, readonly: bool
    ) -> None:
        self.owner = owner
        self.isolation = isolation
        self.readonly = readonly

    async def __aenter__(self) -> "_FakeTransaction":
        self.owner.transactions.append(
            {"isolation": self.isolation, "readonly": self.readonly}
        )
        self.owner.depth += 1
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        self.owner.depth -= 1
        return False


class FakeConn:

    def __init__(
        self,
        *,
        datasets: dict[str, FakeGoldDataset] | None = None,
        answers: dict[str, Any] | None = None,
    ) -> None:
        self.datasets = datasets or {}
        self.answers: dict[str, Any] = answers or {}
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []
        self.transactions: list[dict[str, Any]] = []
        self.scope: tuple[Any, ...] | None = None
        self.closed = False
        self.dsn: str | None = None
        self.depth = 0

    def is_in_transaction(self) -> bool:
        return self.depth > 0

    def transaction(
        self, isolation: str | None = None, readonly: bool = False
    ) -> _FakeTransaction:
        return _FakeTransaction(self, isolation, readonly)

    async def execute(self, sql: str, *args: Any) -> str:
        self.calls.append(("execute", sql, args))
        if "set_config('app.tenant_id'" in sql:
            self.scope = args
        return "SELECT 1"

    async def fetchrow(self, sql: str, *args: Any) -> Any:
        self.calls.append(("fetchrow", sql, args))
        if "dataset_publication_heads" in sql and "materialization_runs" in sql:
            dataset = self._head_for(args)
            return dataset.heads_row() if dataset is not None else None
        return self._answer(sql, args)

    def _head_for(self, args: tuple[Any, ...]) -> FakeGoldDataset | None:
        tenant_id, workspace_id, dataset_name = args[0], args[1], args[2]
        dataset = self.datasets.get(str(dataset_name))
        if dataset is None or not dataset.head:
            return None
        if (dataset.tenant_id, dataset.workspace_id) != (
            str(tenant_id),
            str(workspace_id),
        ):
            return None
        return dataset

    async def fetchval(self, sql: str, *args: Any) -> Any:
        self.calls.append(("fetchval", sql, args))
        if "to_regclass" in sql:
            for dataset in self.datasets.values():
                if dataset.relation_sql == args[0]:
                    return dataset.relation_sql if dataset.regclass else None
            return None
        if "published_at" in sql and "dataset_publication_heads" in sql:
            dataset = self._head_for(args)
            return dataset.published_at if dataset is not None else None
        return self._answer(sql, args)

    async def fetch(self, sql: str, *args: Any) -> Any:
        self.calls.append(("fetch", sql, args))
        if "information_schema.columns" in sql:
            for dataset in self.datasets.values():
                if (dataset.schema, dataset.gold_table) == (args[0], args[1]):
                    return [
                        {"column_name": name, "data_type": data_type}
                        for name, data_type in dataset.columns.items()
                    ]
            return []
        return self._answer(sql, args)

    async def close(self) -> None:
        self.closed = True


    def _answer(self, sql: str, args: tuple[Any, ...]) -> Any:
        marker = marker_of(sql)
        if marker is None:
            raise AssertionError(
                f"aggregate SQL without omega-aggregate marker: {sql[:160]!r}"
            )
        if marker not in self.answers:
            raise AssertionError(f"no fake answer registered for marker {marker!r}")
        self._check_parameters(marker, sql, args)
        value = self.answers[marker]
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value(sql, args)
        return value

    @staticmethod
    def _check_parameters(marker: str, sql: str, args: tuple[Any, ...]) -> None:
        referenced = {int(number) for number in PARAM_RE.findall(sql)}
        expected = set(range(1, len(args) + 1))
        if referenced != expected:
            raise AssertionError(
                f"{marker}: SQL references ${sorted(referenced)} but "
                f"{len(args)} argument(s) were bound (expected ${sorted(expected)})"
            )

    def sql_for(self, marker: str) -> str:
        for _method, sql, _args in self.calls:
            if marker_of(sql) == marker:
                return sql
        raise AssertionError(f"marker {marker!r} was never executed")

    def args_for(self, marker: str) -> tuple[Any, ...]:
        for _method, sql, args in self.calls:
            if marker_of(sql) == marker:
                return args
        raise AssertionError(f"marker {marker!r} was never executed")

    def markers(self) -> list[str]:
        return [marker_of(sql) for _m, sql, _a in self.calls if marker_of(sql)]


class FakePool:

    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn
        self.acquired = 0

    def acquire(self) -> "_FakeAcquire":
        self.acquired += 1
        return _FakeAcquire(self.conn)


class _FakeAcquire:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn

    async def __aenter__(self) -> FakeConn:
        return self.conn

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def install_gold_connect(
    monkeypatch, conn: FakeConn, *, dsn: str = "postgresql://gold-test/db"
) -> Callable:
    calls: list[dict[str, Any]] = []

    async def fake_connect(target: str, **kwargs: Any) -> FakeConn:
        calls.append({"dsn": target, **kwargs})
        conn.dsn = target
        return conn

    monkeypatch.setenv("GOLD_DATABASE_URL", dsn)
    monkeypatch.setattr(asyncpg, "connect", fake_connect)
    return lambda: calls


def install_console_pool(monkeypatch, module: Any, conn: FakeConn) -> FakePool:
    pool = FakePool(conn)

    async def fake_pool() -> FakePool:
        return pool

    monkeypatch.setattr(module.auth, "pool", fake_pool)
    return pool
