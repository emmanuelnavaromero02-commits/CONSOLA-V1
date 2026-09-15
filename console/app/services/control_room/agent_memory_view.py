"""Public view over the shared agent memory.

The Mission 4 counterpart of ``domain_kpis``: it turns the service result from
``intelligence.agent_memory`` into the payload that crosses the internal read
bridge to mcp-infra and, through it, to an agent's LLM.

Two things are deliberately dropped on the way out:

* ``detail`` — free-form JSON written by another agent. It is useful to
  console-side callers and is a prompt-injection surface for a model, so it stays
  behind the boundary.
* the recording agent's id and slug — replaced by its display name, because the
  public projection redacts identifiers and a name is what a reader needs anyway.

``subject`` deliberately does cross as-is. It is the key two agents agree on
(``cost_center_budget``), so an agent that reads a finding can ask about the same
subject later; every subject in use was checked against the projection's copy
filter and survives it unchanged.
"""

from __future__ import annotations

from typing import Any

from app.services.intelligence import agent_memory

MEMORY_VIEW = "agent_memory"


def public_finding(finding: agent_memory.SharedFinding) -> dict[str, Any]:
    return {
        "subject": finding.subject,
        "finding_type": finding.finding_type,
        "summary": finding.summary,
        "severity": finding.severity,
        "created_at": finding.created_at,
        "expires_at": finding.expires_at,
        "recorded_by": finding.agent_name,
    }


def public_payload(result: agent_memory.SharedFindingsResult) -> dict[str, Any]:
    """Project a read result for the LLM.

    ``status`` is carried through unchanged so a missing table stays
    distinguishable from an empty memory: ``unavailable`` means "could not look",
    ``ready`` with ``count == 0`` means "looked, nobody has recorded anything".
    """
    notes = list(result.notes)
    if result.status == agent_memory.STATUS_READY and result.count == 0:
        notes.append(
            "ningun agente ha registrado hallazgos activos sobre este tema"
        )
    return {
        "view": MEMORY_VIEW,
        "status": result.status,
        "supported": result.supported,
        "proxy_note": result.proxy_note,
        "error": result.error,
        "subject": result.subject,
        "count": result.count,
        "findings": [public_finding(item) for item in result.findings],
        "evidence_refs": result.evidence_refs,
        "notes": notes,
    }


async def agent_memory_read(
    user: dict | None,
    *,
    subject: str | None = None,
    limit: int = agent_memory.DEFAULT_FINDINGS_LIMIT,
) -> dict[str, Any]:
    """Active findings for a subject, projected for the LLM."""
    result = await agent_memory.read_shared_findings(
        user, subject=subject, limit=limit
    )
    return public_payload(result)


__all__ = ("MEMORY_VIEW", "agent_memory_read", "public_finding", "public_payload")
