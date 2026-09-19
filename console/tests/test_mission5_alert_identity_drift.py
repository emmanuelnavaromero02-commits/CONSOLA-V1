"""Mission 5: console attests the exact item mcp-infra raises.

A ticket is bound at mint time to an ``item_id`` console derives itself. If that
derivation ever drifts from ``agent_runtime._monitor_alert_args`` (what the
monitor sends) or from mcp-infra ``_dedup_item_id`` (what gets stored), every
attestation silently stops matching and Control Room goes empty again. These
tests fail first instead.
"""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from app.domains.agentops.domain_monitor_support import monitor_block_for
from app.domains.agentops.domain_monitors import DOMAIN_MONITOR_SPECS
from app.domains.agentops.successfactors_talent_monitor import (
    successfactors_talent_monitor_contract,
)
from app.services import agent_runtime
from app.services.control_room import evidence_tickets

ROOT = Path(__file__).resolve().parents[2]
TENANT = "11111111-1111-4111-8111-111111111111"
WORKSPACE = "22222222-2222-4222-8222-222222222222"
AGENT = "33333333-3333-4333-8333-333333333333"


def _mcp_infra_dedup_item_id():
    source = (ROOT / "mcp-infra/app/tools/control_room.py").read_text(encoding="utf-8")
    node = next(
        item
        for item in ast.parse(source).body
        if isinstance(item, ast.FunctionDef) and item.name == "_dedup_item_id"
    )
    namespace = {"json": json, "hashlib": hashlib}
    code = compile(ast.Module(body=[node], type_ignores=[]), "mcp_dedup", "exec")
    exec(code, namespace)  # noqa: S102 # nosec B102 - a function lifted from repo source
    return namespace["_dedup_item_id"]


def _agent(cartridge_id: str, slug: str) -> agent_runtime.Agent:
    return agent_runtime.Agent(
        id=AGENT,
        cartridge_id=cartridge_id,
        slug=slug,
        name="Monitor",
        description="",
        instructions="",
        personality="",
        allowed_tools=[],
        rag_filter={},
        model="m",
        max_tokens=1,
        temperature=0.0,
        extra={},
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
    )


def _cases():
    for spec in DOMAIN_MONITOR_SPECS:
        yield (
            spec.key,
            spec.cartridge_id,
            spec.slug,
            monitor_block_for(spec),
            {"wisdom_bit_id": spec.wisdom_bit_id, "signals": {"count": 2, "items": [{}, {}]}},
        )
    _tools, _rag, extra = successfactors_talent_monitor_contract()
    yield (
        "talent",
        "sap_successfactors",
        "successfactors-talent-monitor",
        extra["monitor"],
        {"wisdom_bit_id": "WB-TALENTO", "signals": {"count": 4, "items": [{}] * 4}},
    )
    yield (
        "bare_contract",
        "replicon",
        "custom-monitor",
        {"engine": "wisdom_bit"},
        {"wisdom_bit_id": "WB-X", "signals": [{}, {}, {}]},
    )
    yield (
        "padded_keys",
        "replicon",
        "custom-monitor",
        {"dedup_key": "  replicon:custom:WB-X  ", "dataset": " dataset_x ", "alert_type": "t "},
        {"signals": {"count": 1, "items": [{}]}},
    )


@pytest.mark.parametrize(
    ("case", "cartridge_id", "slug", "contract", "payload"),
    list(_cases()),
    ids=[case[0] for case in _cases()],
)
def test_console_item_id_equals_the_id_mcp_infra_stores(
    case, cartridge_id, slug, contract, payload
):
    args = agent_runtime._monitor_alert_args(
        agent=_agent(cartridge_id, slug),
        run_id=41,
        contract=contract,
        payload=payload,
        scheduled_fire_at="2026-09-15T08:00:00+00:00",
        engine_results=[],
    )
    stored_item_id = _mcp_infra_dedup_item_id()(
        agent_id=AGENT,
        workspace_id=WORKSPACE,
        # mcp-infra's _as_safe_key strips before deduplicating.
        alert_type=args["alert_type"].strip(),
        entity_key=args["entity_key"].strip(),
        source_dataset=args["source_dataset"].strip(),
    )

    identity = evidence_tickets.monitor_alert_identity(
        agent_id=AGENT,
        workspace_id=WORKSPACE,
        cartridge_id=cartridge_id,
        slug=slug,
        contract=contract,
        payload=payload,
    )

    assert identity is not None
    assert identity.item_id == stored_item_id
    assert identity.source_dataset == args["source_dataset"].strip()
    assert (
        evidence_tickets.monitor_signal_count(payload)
        == agent_runtime._monitor_signal_count(payload)
        == args["metrics"]["signal_count"]
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"signals": None},
        {"signals": {"count": "7"}},
        {"signals": {"count": "x"}},
        {"signals": {"items": [{}, {}]}},
        {"signals": [{}]},
        {"signals": "many"},
    ],
)
def test_signal_count_mirrors_the_runtime(payload):
    assert evidence_tickets.monitor_signal_count(payload) == (
        agent_runtime._monitor_signal_count(payload)
    )


def test_keys_mcp_infra_would_refuse_are_never_attested():
    identity = evidence_tickets.monitor_alert_identity(
        agent_id=AGENT,
        workspace_id=WORKSPACE,
        cartridge_id="replicon",
        slug="custom-monitor",
        contract={"dedup_key": "clave con espacios"},
        payload={},
    )

    assert identity is None
