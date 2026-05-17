"""Sprint v1.44.3.3 Task B — Studio stub endpoint contracts.

12 ``/api/studio/*`` endpoints exist as authenticated stubs so
the E2E ``waitForRequest('/api/studio/*')`` assertions in
``tests-e2e/specs/05-studio*.spec.ts`` resolve once the legacy
JS click handlers are rewired (planned for v1.44.4). This file
pins:

  - all 12 routes exist and respond 200 to an authenticated
    request,
  - every response carries the ``{"stub": true, "version":
    "v1.44.3.3"}`` marker so the UI / monitoring can detect a
    stub response,
  - every shape includes the documented placeholder keys so
    real-implementation code written against the stub doesn't
    have to change when v1.44.4 lands.

Live ASGI stack via ``starlette.testclient.TestClient`` —
no docker, no DB. The auth dependency is monkeypatched at the
app level so each test gets a synthetic user without
round-tripping through the real cookie / JWT layer.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def client():
    """Live TestClient with require_authenticated overridden so
    the stubs return their happy-path body."""
    os.environ["APP_ENV"] = "test"
    os.environ["INTERNAL_API_KEY"] = "x" * 64
    os.environ.setdefault(
        "FIELD_ENCRYPTION_KEY",
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa=",
    )

    console_dir = str(REPO / "console")
    _SIBLINGS = ("/cartridges/", "/refinement", "/vault", "/workspace")
    sys.path[:] = [p for p in sys.path if not any(s in p for s in _SIBLINGS) and p != console_dir]
    sys.path.insert(0, console_dir)
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            del sys.modules[name]

    from starlette.testclient import TestClient

    from app.main import app
    from app.dependencies import require_authenticated

    # Stub user — same shape the real dependency returns.
    app.dependency_overrides[require_authenticated] = lambda: {
        "id": "test-user",
        "email": "stub@local.ai",
        "role": "admin",
    }
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(require_authenticated, None)


# ── Catalogue: path × verb × required placeholder keys ──────────────────


STUBS = [
    ("GET",  "/api/studio/dag-graph",        {"format", "svg", "nodes", "edges"}),
    ("POST", "/api/studio/dag-deploy",       {"status", "dag_id", "message"}),
    ("GET",  "/api/studio/templates",        {"templates"}),
    ("POST", "/api/studio/entities/upload",  {"accepted", "accepted_count"}),
    ("POST", "/api/studio/entity",           {"created", "entity_id"}),
    ("GET",  "/api/studio/silver/preview",   {"columns", "rows", "total"}),
    ("GET",  "/api/studio/gold/preview",     {"columns", "rows", "total"}),
    ("GET",  "/api/studio/master/preview",   {"columns", "rows", "total"}),
    ("POST", "/api/studio/superset/dataset", {"created", "dataset_id", "url"}),
    ("GET",  "/api/studio/semantic",         {"entities", "relations"}),
    ("GET",  "/api/studio/rag",              {"corpus", "docs_indexed"}),
    ("POST", "/api/studio/assistant",        {"reply", "session_id"}),
]


@pytest.mark.parametrize(("verb", "path", "_keys"), STUBS)
def test_stub_returns_200_authenticated(client, verb, path, _keys):
    """Each stub must respond 200 to an authenticated request.
    The E2E ``waitForRequest`` doesn't check status, but if a
    real user clicks a Studio button against the stub it should
    see a clean ack, not a 405 / 500."""
    r = client.request(verb, path)
    assert r.status_code == 200, (
        f"{verb} {path} returned {r.status_code} — expected 200.\n"
        f"Body: {r.text[:300]}"
    )


@pytest.mark.parametrize(("verb", "path", "_keys"), STUBS)
def test_stub_carries_stub_marker(client, verb, path, _keys):
    """Every stub body must include ``{"stub": true, "version":
    "v1.44.3.3"}`` so the UI / monitoring can distinguish a
    stub response from real data. If the marker disappears
    after the real implementation lands, this test must be
    explicitly removed alongside that commit — the breakage
    forces a reviewer to acknowledge the stub graduating to
    production."""
    body = client.request(verb, path).json()
    assert body.get("stub") is True, (
        f"{verb} {path} dropped the ``stub: true`` marker.\n"
        f"Body: {body}"
    )
    assert body.get("version") == "v1.44.3.3", (
        f"{verb} {path} stub version drifted from v1.44.3.3.\n"
        f"Body: {body}"
    )


@pytest.mark.parametrize(("verb", "path", "keys"), STUBS)
def test_stub_includes_placeholder_shape(client, verb, path, keys):
    """The placeholder shape exists so real-implementation code
    written against the stub doesn't have to change when
    v1.44.4 lands. Pin the keys explicitly so a typo doesn't
    silently break the contract."""
    body = client.request(verb, path).json()
    missing = keys - set(body)
    assert not missing, (
        f"{verb} {path} stub missing placeholder keys {missing}.\n"
        f"Got: {sorted(body)}"
    )


def test_stub_requires_authentication(client):
    """Spot-check ONE endpoint with the auth override removed
    — confirms ``require_authenticated`` is actually wired up
    (not just declared) so a stub never leaks a placeholder
    shape to anonymous clients."""
    from app.dependencies import require_authenticated
    from app.main import app

    # Remove the override for this test only.
    override = app.dependency_overrides.pop(require_authenticated, None)
    try:
        r = client.get("/api/studio/dag-graph")
        assert r.status_code in (401, 403), (
            f"Unauthenticated GET /api/studio/dag-graph returned "
            f"{r.status_code}. Expected 401 / 403 — the stub MUST "
            f"NOT leak a placeholder shape to anonymous clients."
        )
    finally:
        if override is not None:
            app.dependency_overrides[require_authenticated] = override


def test_all_12_endpoints_covered():
    """Sentinel: the brief commits to exactly 12 stubs. If
    someone adds a 13th endpoint to the catalogue without
    updating this count, force them to acknowledge the
    expansion."""
    assert len(STUBS) == 12, (
        f"STUBS catalogue has {len(STUBS)} entries — brief specifies "
        f"exactly 12. If this is intentional (v1.44.4 expansion), "
        f"update this assert + the studio.py module docstring."
    )
