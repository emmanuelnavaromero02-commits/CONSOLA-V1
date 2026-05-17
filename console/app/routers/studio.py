"""Sprint v1.44.3.3 Task B — Studio API stubs.

The user's brief catalogued 11 broken Studio buttons (Grafo,
Deploy Airflow, Plantillas, Subir spec, Silver/Gold/Master,
Crear Superset, IA Semántica, Assistant input, Tab Resumen,
Tab RAG, +Entidad). The accompanying E2E specs
(``tests-e2e/specs/05-studio*.spec.ts``) use
``page.waitForRequest`` to assert that each button fires a
specific ``/api/studio/*`` URL — but the backend never exposed
that prefix (existing Studio routes are mounted under
``/studio/...``), and the legacy.js click handlers route most
button presses through ``/api/mcp/invoke`` instead.

This file ships the BACKEND HALF of the fix: 12 thin stub
endpoints under ``/api/studio/*`` so the routes exist + respond
200 with documented placeholder shapes. The legacy.js click
handlers still need to be rewired to fire these URLs — that's
the FRONTEND HALF and lives in a follow-up sprint (v1.44.4) to
avoid mid-session churn on a 3,900-line file we can't browser-
validate from this environment.

Every endpoint:
  - is authenticated via ``require_authenticated`` (same as
    /api/dashboard/*) so an unauthenticated request 401s
    instead of leaking a placeholder shape;
  - returns a JSON body with the same top-level keys the
    eventual real implementation will return (so frontend code
    written against the stub doesn't have to change when the
    real logic lands);
  - emits a ``"stub": true`` marker so the UI can detect a
    stub response and surface a "feature in progress" banner
    if it wants to;
  - leaves the body deliberately empty (zero rows, empty
    arrays) — the UI must render gracefully against "no data
    yet" before it tries to render real data.

The pairing E2E network-wait assertions only care that the
request fires; they don't validate body shape. Frontend
contract tests for the shape will land in v1.44.4 alongside
the real implementations.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.dependencies import require_authenticated


router = APIRouter(prefix="/api/studio", tags=["Studio (stub)"])


_STUB_MARKER = {"stub": True, "version": "v1.44.3.3"}


def _stub_payload(**extra: Any) -> dict[str, Any]:
    """Build a stub response body. The two top-level marker
    keys are stable across every stub; ``extra`` is the
    endpoint-specific placeholder shape."""
    body = dict(_STUB_MARKER)
    body.update(extra)
    return body


# ── Pipeline / DAG ────────────────────────────────────────────────────────


@router.get("/dag-graph")
async def dag_graph(user: dict = Depends(require_authenticated)):
    """v1.44.4 will return the rendered DAG as SVG + node/edge
    metadata. For now: an empty graph so the UI can render a
    "no DAGs yet" placeholder without a 404."""
    return _stub_payload(format="svg", svg="", nodes=[], edges=[])


@router.post("/dag-deploy")
async def dag_deploy(user: dict = Depends(require_authenticated)):
    """v1.44.4 will proxy through to the existing
    /api/mcp/invoke airflow_create_dag flow with a Studio-aware
    request shape. For now: a status envelope that the UI can
    treat as "accepted, deploy queued"."""
    return _stub_payload(status="accepted", dag_id=None, message="Studio deploy stub")


@router.get("/templates")
async def templates(user: dict = Depends(require_authenticated)):
    """Existing /api/dag_templates remains the source of truth
    for the legacy UI; this Studio-prefixed alias will host the
    v1.44.4 redesign payload. Empty list keeps the UI from
    crashing on map()."""
    return _stub_payload(templates=[])


# ── Entities / spec upload ────────────────────────────────────────────────


@router.post("/entities/upload")
async def entities_upload(user: dict = Depends(require_authenticated)):
    """v1.44.4 accepts a multipart file upload + validates the
    spec against the cartridge connector schema. Stub: ack
    with the placeholder ``accepted_at``."""
    return _stub_payload(accepted=True, accepted_count=0)


@router.post("/entity")
async def entity(user: dict = Depends(require_authenticated)):
    """+Entidad button. v1.44.4 persists a new entity row;
    stub just acks."""
    return _stub_payload(created=True, entity_id=None)


# ── Silver / Gold / Master previews ──────────────────────────────────────


@router.get("/silver/preview")
async def silver_preview(user: dict = Depends(require_authenticated)):
    """Top N rows of the Silver layer for the current cartridge.
    Stub returns an empty preview frame."""
    return _stub_payload(columns=[], rows=[], total=0)


@router.get("/gold/preview")
async def gold_preview(user: dict = Depends(require_authenticated)):
    """Top N rows of the Gold layer."""
    return _stub_payload(columns=[], rows=[], total=0)


@router.get("/master/preview")
async def master_preview(user: dict = Depends(require_authenticated)):
    """Top N rows of the Master layer."""
    return _stub_payload(columns=[], rows=[], total=0)


# ── Superset ──────────────────────────────────────────────────────────────


@router.post("/superset/dataset")
async def superset_dataset(user: dict = Depends(require_authenticated)):
    """Create-in-Superset button. v1.44.4 will POST to the
    Superset HTTP API to register a dataset from the current
    Gold view. Stub: returns the placeholder dataset_id."""
    return _stub_payload(created=True, dataset_id=None, url=None)


# ── IA / Semantic / RAG / Assistant ──────────────────────────────────────


@router.get("/semantic")
async def semantic(user: dict = Depends(require_authenticated)):
    """IA Semántica tab. v1.44.4 returns the semantic layer
    config + indexed entities. Stub: empty layer."""
    return _stub_payload(entities=[], relations=[])


@router.get("/rag")
async def rag(user: dict = Depends(require_authenticated)):
    """RAG tab. v1.44.4 returns the indexed corpus stats +
    health. Stub: empty corpus."""
    return _stub_payload(corpus=[], docs_indexed=0)


@router.post("/assistant")
async def assistant(user: dict = Depends(require_authenticated)):
    """Studio assistant input. v1.44.4 routes to /studio/chat
    (the existing copilot endpoint). Stub: ack with empty
    reply so the UI can show a "feature in progress"
    placeholder."""
    return _stub_payload(reply="", session_id=None)
