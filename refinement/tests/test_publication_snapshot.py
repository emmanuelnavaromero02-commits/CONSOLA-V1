from __future__ import annotations

from types import SimpleNamespace

import psycopg2
import pytest

from refinement.app import publication_snapshot

PublicationScope = publication_snapshot.PublicationScope


class _Cursor:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self.queries: list[str] = []
        self.params: list[object] = []
        self.rows = rows or []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, query: str, params: object) -> None:
        self.queries.append(query)
        self.params.append(params)

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows


class _Connection:
    def __init__(self, rows: list[tuple[object, ...]] | None = None) -> None:
        self.cursor_instance = _Cursor(rows)
        self.session: dict[str, object] = {}
        self.committed = False
        self.closed = False

    def set_session(self, **kwargs: object) -> None:
        self.session = kwargs

    def cursor(self) -> _Cursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


def test_snapshot_database_connection_has_bounded_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fail_connect(database_url: str, **kwargs: object) -> None:
        captured["database_url"] = database_url
        captured["kwargs"] = kwargs
        raise psycopg2.OperationalError("database unavailable")

    monkeypatch.setattr(publication_snapshot.psycopg2, "connect", fail_connect)
    resolver = publication_snapshot.PublicationSnapshotResolver(
        database_url="postgresql://reader@postgres-gold:5433/gold"
    )
    scope = PublicationScope("tenant", "workspace", "dataset", "gold")

    with pytest.raises(psycopg2.OperationalError, match="database unavailable"):
        resolver._read(scope)

    assert captured["database_url"] == (
        "postgresql://reader@postgres-gold:5433/gold"
    )
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["connect_timeout"] == 2


def test_snapshot_batch_uses_one_repeatable_read_connection_and_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection()
    connect_calls: list[tuple[str, dict[str, object]]] = []

    def connect(database_url: str, **kwargs: object) -> _Connection:
        connect_calls.append((database_url, kwargs))
        return connection

    monkeypatch.setattr(publication_snapshot.psycopg2, "connect", connect)
    resolver = publication_snapshot.PublicationSnapshotResolver(
        database_url="postgresql://reader@postgres-gold:5433/gold"
    )
    datasets = [
        {"name": "orders", "layer": "silver"},
        {"name": "revenue", "layer": "gold"},
        {"name": "orders", "layer": "silver"},
    ]

    snapshots = resolver.published_snapshots(
        datasets,
        {"tenant_id": "tenant", "workspace_id": "workspace"},
    )

    assert snapshots == [None, None, None]
    assert connect_calls == [
        (
            "postgresql://reader@postgres-gold:5433/gold",
            {"connect_timeout": 2},
        )
    ]
    snapshot_calls = [
        (query, params)
        for query, params in zip(
            connection.cursor_instance.queries,
            connection.cursor_instance.params,
            strict=True,
        )
        if "omega_publication.dataset_publication_heads" in query
    ]
    assert len(snapshot_calls) == 1
    assert snapshot_calls[0][1] == (
        ["orders", "revenue"],
        ["silver", "gold"],
        "tenant",
        "workspace",
    )
    normalized_query = " ".join(snapshot_calls[0][0].split())
    assert "unnest(%s::text[], %s::text[])" in normalized_query
    assert connection.session == {
        "readonly": True,
        "isolation_level": "REPEATABLE READ",
    }
    assert connection.committed
    assert connection.closed


def test_empty_snapshot_batch_does_not_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        publication_snapshot.psycopg2,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("empty batch must not connect")
        ),
    )
    resolver = publication_snapshot.PublicationSnapshotResolver(
        database_url="postgresql://reader@postgres-gold:5433/gold"
    )

    assert resolver.published_snapshots(
        [], {"tenant_id": "tenant", "workspace_id": "workspace"}
    ) == []


def test_snapshot_batch_rejects_mixed_scope_before_connect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        publication_snapshot.psycopg2,
        "connect",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("mixed scope must fail before connecting")
        ),
    )
    resolver = publication_snapshot.PublicationSnapshotResolver(
        database_url="postgresql://reader@postgres-gold:5433/gold"
    )

    with pytest.raises(ValueError, match="batch scope mismatch"):
        resolver._read_many(
            [
                PublicationScope("tenant", "workspace-a", "orders", "silver"),
                PublicationScope("tenant", "workspace-b", "orders", "silver"),
            ]
        )


def _legacy_row(dataset: str, layer: str, run_id: str) -> tuple[object, ...]:
    return (
        dataset,
        layer,
        run_id,
        1,
        None,
        "legacy_unverified",
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )


def test_snapshot_batch_maps_out_of_order_rows_and_partial_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection(
        [
            _legacy_row("revenue", "gold", "run-revenue"),
            _legacy_row("orders", "silver", "run-orders"),
        ]
    )
    monkeypatch.setattr(
        publication_snapshot.psycopg2,
        "connect",
        lambda *_args, **_kwargs: connection,
    )
    resolver = publication_snapshot.PublicationSnapshotResolver(
        database_url="postgresql://reader@postgres-gold:5433/gold"
    )
    scopes = [
        PublicationScope("tenant", "workspace", "orders", "silver"),
        PublicationScope("tenant", "workspace", "missing", "gold"),
        PublicationScope("tenant", "workspace", "revenue", "gold"),
    ]

    snapshots = resolver._read_many(scopes)

    assert snapshots[scopes[0]] is not None
    assert snapshots[scopes[0]].run_id == "run-orders"
    assert snapshots[scopes[1]] is None
    assert snapshots[scopes[2]] is not None
    assert snapshots[scopes[2]].run_id == "run-revenue"


def test_snapshot_batch_rejects_unrequested_cross_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Connection([_legacy_row("orders", "gold", "run-cross-pair")])
    monkeypatch.setattr(
        publication_snapshot.psycopg2,
        "connect",
        lambda *_args, **_kwargs: connection,
    )
    resolver = publication_snapshot.PublicationSnapshotResolver(
        database_url="postgresql://reader@postgres-gold:5433/gold"
    )

    with pytest.raises(RuntimeError, match="batch result mismatch"):
        resolver._read_many(
            [
                PublicationScope("tenant", "workspace", "orders", "silver"),
                PublicationScope("tenant", "workspace", "revenue", "gold"),
            ]
        )


def test_snapshot_batch_preserves_order_and_validates_unique_scopes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = publication_snapshot.PublicationSnapshotResolver(
        database_url="postgresql://reader@postgres-gold:5433/gold"
    )
    calls = {"authority": 0, "object": 0}
    snapshots_by_layer = {
        layer: SimpleNamespace(
            validate_snapshot=lambda: calls.__setitem__(
                "authority", calls["authority"] + 1
            )
        )
        for layer in ("silver", "gold")
    }

    def read_many(scopes: list[PublicationScope]) -> dict[PublicationScope, object]:
        return {scope: snapshots_by_layer[scope.layer] for scope in scopes}

    monkeypatch.setattr(resolver, "_read_many", read_many)
    monkeypatch.setattr(
        resolver,
        "_validate_object",
        lambda _snapshot: calls.__setitem__("object", calls["object"] + 1),
    )
    silver = {"name": "orders", "layer": "silver"}
    gold = {"name": "orders", "layer": "gold"}

    snapshots = resolver.published_snapshots(
        [silver, gold, silver],
        {"tenant_id": "tenant", "workspace_id": "workspace"},
    )

    assert snapshots == [
        snapshots_by_layer["silver"],
        snapshots_by_layer["gold"],
        snapshots_by_layer["silver"],
    ]
    assert calls == {"authority": 2, "object": 2}
