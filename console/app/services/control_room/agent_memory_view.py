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
    result = await agent_memory.read_shared_findings(
        user, subject=subject, limit=limit
    )
    return public_payload(result)


__all__ = ("MEMORY_VIEW", "agent_memory_read", "public_finding", "public_payload")
